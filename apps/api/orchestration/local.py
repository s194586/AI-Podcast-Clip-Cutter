from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from apps.pipeline.events import redact_text

from ..db.database import init_database, session_scope
from ..db.models import utc_now
from ..db.repositories import JobRepository, ProjectRepository
from ..services.project_service import (
    PROJECT_ROOT,
    ProjectNotFoundError,
    ensure_project_workspace,
    get_project_status,
    safe_relative_path,
)
from .base import (
    LOCAL_PIPELINE_JOB_TYPE,
    PIPELINE_JOB_TYPES,
    JobResult,
    PipelineStatus,
    ProjectAlreadyRunningError,
    ProjectOrchestratorNotFoundError,
)
from .stage_parser import (
    message_for_stage,
    parse_manager_stage,
    parse_structured_pipeline_event,
    progress_for_stage,
)

PIPELINE_JOB_TYPE = LOCAL_PIPELINE_JOB_TYPE
ACTIVE_STATUSES = {"queued", "running"}
SECRET_LINE_RE = re.compile(
    r"(?i)(gemini_api_key|api[_-]?key|password|secret|token)\s*=\s*([^\s]+)"
)

PopenFactory = Callable[..., subprocess.Popen]


@dataclass
class WorkerState:
    thread: threading.Thread | None = None
    process: subprocess.Popen | None = None
    cancel_requested: bool = False


class LocalPipelineOrchestrator:
    _workers: dict[int, WorkerState] = {}
    _lock = threading.Lock()

    def __init__(
        self,
        *,
        project_root: Path = PROJECT_ROOT,
        popen_factory: PopenFactory | None = None,
        run_inline: bool = False,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.popen_factory = popen_factory or subprocess.Popen
        self.run_inline = run_inline

    @classmethod
    def reset_for_tests(cls) -> None:
        with cls._lock:
            cls._workers.clear()

    def start_project(self, project_id: int) -> JobResult:
        init_database()
        with self._lock:
            with session_scope() as session:
                project_repo = ProjectRepository(session)
                job_repo = JobRepository(session)
                project = project_repo.get(int(project_id))
                if project is None:
                    raise ProjectOrchestratorNotFoundError(f"Unknown project_id: {project_id}")
                if project.id in self._workers or job_repo.active_for_project_types(project.id, PIPELINE_JOB_TYPES) is not None:
                    raise ProjectAlreadyRunningError(f"Project {project.id} already has an active local pipeline run.")

                workspace = ensure_project_workspace(project.id, project_root=self.project_root)
                log_path = workspace / "logs" / "pipeline.log"
                log_path.parent.mkdir(parents=True, exist_ok=True)
                if not log_path.exists():
                    log_path.write_text("", encoding="utf-8")

                job = job_repo.create(
                    project_id=project.id,
                    job_type=PIPELINE_JOB_TYPE,
                    status="queued",
                    current_stage="waiting",
                    progress=progress_for_stage("waiting"),
                    log_path=safe_relative_path(log_path, project_root=self.project_root),
                    orchestrator_type="local",
                )
                project.workspace_path = safe_relative_path(workspace, project_root=self.project_root)
                project.auto_review = bool(project.auto_review)
                project_repo.update_flow_state(
                    project,
                    status="queued",
                    current_stage="waiting",
                    progress_percent=progress_for_stage("waiting"),
                    error_message=None,
                    completed_at=None,
                )
                job_id = job.id

            state = WorkerState()
            self._workers[int(project_id)] = state

        if self.run_inline:
            self._run_job(int(project_id), job_id)
        else:
            thread = threading.Thread(
                target=self._run_job,
                args=(int(project_id), job_id),
                name=f"local-pipeline-project-{project_id}",
                daemon=True,
            )
            state.thread = thread
            thread.start()

        return JobResult(
            project_id=int(project_id),
            job_id=job_id,
            status="queued",
            stage="waiting",
            progress_percent=progress_for_stage("waiting"),
            message=message_for_stage("waiting"),
        )

    def get_status(self, project_id: int) -> PipelineStatus:
        status = _status_dict_for_project(int(project_id))
        return PipelineStatus(
            project_id=int(status["project_id"]),
            status=str(status["status"]),
            stage=str(status["stage"]),
            progress_percent=float(status["progress_percent"]),
            message=str(status["message"]),
            error_message=status.get("error_message"),
            started_at=status.get("started_at"),
            updated_at=status.get("updated_at"),
            completed_at=status.get("completed_at"),
            job_id=(status.get("job") or {}).get("id"),
            log_path=(status.get("job") or {}).get("log_path"),
        )

    def cancel_project(self, project_id: int) -> PipelineStatus:
        init_database()
        worker = None
        with self._lock:
            worker = self._workers.get(int(project_id))
            if worker is not None:
                worker.cancel_requested = True
                process = worker.process
                if process is not None and process.poll() is None:
                    self._terminate_process_tree(process)

        now = utc_now()
        with session_scope() as session:
            project_repo = ProjectRepository(session)
            job_repo = JobRepository(session)
            project = project_repo.get(int(project_id))
            if project is None:
                raise ProjectOrchestratorNotFoundError(f"Unknown project_id: {project_id}")
            active_job = job_repo.active_for_project(project.id, PIPELINE_JOB_TYPE)
            if active_job is not None:
                cancelled_progress = float(active_job.progress or 0.0)
                job_repo.update_state(
                    active_job,
                    status="cancelled",
                    current_stage="cancelled",
                    progress=cancelled_progress,
                    finished_at=now,
                    process_id=None,
                    error_message="Cancelled by user.",
                )
            cancelled_progress = float(project.progress_percent or 0.0)
            project_repo.update_flow_state(
                project,
                status="cancelled",
                current_stage="cancelled",
                progress_percent=cancelled_progress,
                error_message="Cancelled by user.",
                completed_at=now,
            )
        return self.get_status(project_id)

    def read_project_log_tail(self, project_id: int, *, tail: int = 200) -> dict[str, Any]:
        log_path = self._latest_log_path(project_id)
        line_count = max(1, min(int(tail or 200), 1000))
        if log_path is None or not log_path.exists():
            return {"project_id": int(project_id), "tail": line_count, "lines": []}
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        return {"project_id": int(project_id), "tail": line_count, "lines": lines[-line_count:]}

    def recover_orphaned_jobs(self) -> int:
        init_database()
        recovered = 0
        message = "Interrupted: server restarted before the local pipeline process completed."
        now = utc_now()
        with self._lock:
            active_worker_ids = set(self._workers)
        with session_scope() as session:
            project_repo = ProjectRepository(session)
            job_repo = JobRepository(session)
            for job in job_repo.list_active(PIPELINE_JOB_TYPE):
                if job.project_id in active_worker_ids:
                    continue
                failed_progress = float(job.progress or 0.0)
                job_repo.update_state(
                    job,
                    status="failed",
                    current_stage="failed",
                    progress=failed_progress,
                    finished_at=now,
                    error_message=message,
                )
                project = project_repo.get(job.project_id)
                if project is not None and project.status in ACTIVE_STATUSES:
                    failed_progress = float(project.progress_percent or 0.0)
                    project_repo.update_flow_state(
                        project,
                        status="failed",
                        current_stage="failed",
                        progress_percent=failed_progress,
                        error_message=message,
                        completed_at=now,
                    )
                recovered += 1
        return recovered

    def _run_job(self, project_id: int, job_id: int) -> None:
        try:
            self._execute_job(project_id, job_id)
        except Exception as exc:
            self._mark_failed(
                project_id,
                job_id,
                f"Project pipeline worker failed: {_safe_error_message(exc)}",
            )
        finally:
            with self._lock:
                self._workers.pop(project_id, None)

    def _execute_job(self, project_id: int, job_id: int) -> None:
        project_data = self._prepare_running_state(project_id, job_id)
        workspace = self._resolve_project_path(str(project_data["workspace_path"]))
        log_path = self._resolve_project_path(str(project_data["log_path"]))
        command = self._build_pipeline_command(
            project_id,
            project_data["source_url"],
            workspace,
            auto_review=bool(project_data["auto_review"]),
        )
        self._append_log(log_path, f"$ {_display_command(command)}\n")

        popen_options: dict[str, Any] = {
            "cwd": str(self.project_root),
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "bufsize": 1,
            "shell": False,
            "env": self._subprocess_env(),
        }
        if os.name == "nt":
            popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_options["start_new_session"] = True
        process = self.popen_factory(
            command,
            **popen_options,
        )
        self._set_process(project_id, job_id, process)

        structured_event_seen = False
        pipeline_completed = False
        failure_message: str | None = None
        assert process.stdout is not None
        try:
            for raw_line in process.stdout:
                line = _redact_log_line(raw_line)
                self._append_log(log_path, line)
                event = parse_structured_pipeline_event(line)
                if event is not None:
                    structured_event_seen = True
                    if event.event == "stage_failed":
                        failure_message = event.message
                    if event.event == "pipeline_completed" and event.success:
                        pipeline_completed = True
                    self._apply_pipeline_event(project_id, job_id, event)
                    continue
                stage = parse_manager_stage(line)
                if stage in {"downloading", "transcribing", "validating_transcript", "generating_candidates"}:
                    self._mark_running_stage(project_id, job_id, stage)
        finally:
            process.stdout.close()

        exit_code = process.wait()
        if self._is_cancel_requested(project_id):
            self._mark_cancelled(project_id, job_id)
            return
        if exit_code != 0:
            self._mark_failed(
                project_id,
                job_id,
                failure_message or f"Project pipeline exited with code {exit_code}.",
                exit_code=exit_code,
            )
            return
        if structured_event_seen and not pipeline_completed:
            self._mark_failed(
                project_id,
                job_id,
                "Project pipeline exited without a successful completion event.",
                exit_code=exit_code,
            )
            return
        self._mark_ready(project_id, job_id, exit_code=exit_code)

    def _prepare_running_state(self, project_id: int, job_id: int) -> dict[str, Any]:
        now = utc_now()
        with session_scope() as session:
            project_repo = ProjectRepository(session)
            job_repo = JobRepository(session)
            project = project_repo.get(project_id)
            job = job_repo.get(job_id)
            if project is None or job is None:
                raise ProjectOrchestratorNotFoundError(f"Unknown project/job: {project_id}/{job_id}")
            workspace = ensure_project_workspace(project.id, project_root=self.project_root)
            log_path = workspace / "logs" / "pipeline.log"
            project.workspace_path = safe_relative_path(workspace, project_root=self.project_root)
            job_repo.update_state(
                job,
                status="running",
                current_stage="waiting",
                progress=progress_for_stage("waiting"),
                log_path=safe_relative_path(log_path, project_root=self.project_root),
                started_at=now,
                error_message=None,
            )
            project_repo.update_flow_state(
                project,
                status="running",
                current_stage="waiting",
                progress_percent=progress_for_stage("waiting"),
                error_message=None,
                started_at=now,
                completed_at=None,
            )
            return {
                "source_url": project.source_url,
                "auto_review": bool(project.auto_review),
                "workspace_path": project.workspace_path,
                "log_path": job.log_path,
            }

    def _build_pipeline_command(
        self,
        project_id: int,
        source_url: str,
        workspace: Path,
        *,
        auto_review: bool,
    ) -> list[str]:
        return [
            sys.executable,
            "-m",
            "apps.pipeline.entrypoint",
            "--project-id",
            str(int(project_id)),
            "--source-url",
            str(source_url),
            "--workspace-dir",
            str(workspace),
            "--repository-root",
            str(self.project_root),
            "--auto-review" if auto_review else "--no-auto-review",
            "--subtitle-checker-mode",
            "local_only",
        ]

    def _subprocess_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        return env

    def _set_process(self, project_id: int, job_id: int, process: subprocess.Popen) -> None:
        with self._lock:
            worker = self._workers.get(project_id)
            if worker is not None:
                worker.process = process
        with session_scope() as session:
            job = JobRepository(session).get(job_id)
            if job is not None:
                JobRepository(session).update_state(job, process_id=process.pid)

    def _is_cancel_requested(self, project_id: int) -> bool:
        with self._lock:
            worker = self._workers.get(project_id)
            return bool(worker and worker.cancel_requested)

    def _mark_running_stage(
        self,
        project_id: int,
        job_id: int,
        stage: str,
        *,
        progress_percent: float | None = None,
    ) -> None:
        progress = (
            max(0.0, min(100.0, float(progress_percent)))
            if progress_percent is not None
            else progress_for_stage(stage)
        )
        with session_scope() as session:
            project_repo = ProjectRepository(session)
            job_repo = JobRepository(session)
            project = project_repo.get(project_id)
            job = job_repo.get(job_id)
            if project is None or job is None:
                return
            if project.status == "cancelled" or job.status == "cancelled":
                return
            job_repo.update_state(job, status="running", current_stage=stage, progress=progress)
            project_repo.update_flow_state(
                project,
                status="running",
                current_stage=stage,
                progress_percent=progress,
                error_message=None,
            )

    def _apply_pipeline_event(self, project_id: int, job_id: int, event) -> None:
        if event.event in {
            "review_clip_started",
            "review_clip_completed",
            "review_clip_manual",
            "review_clip_failed",
        }:
            self._mark_running_stage(
                project_id,
                job_id,
                "reviewing_with_ai",
                progress_percent=event.progress_percent,
            )
            return
        if event.event in {"stage_started", "stage_progress", "stage_completed"}:
            if event.stage in {
                "waiting",
                "downloading",
                "transcribing",
                "validating_transcript",
                "generating_candidates",
                "importing_candidates",
                "reviewing_with_ai",
            }:
                self._mark_running_stage(
                    project_id,
                    job_id,
                    event.stage,
                    progress_percent=event.progress_percent,
                )
            return
        if event.event == "pipeline_cancelled":
            self._mark_cancelled(project_id, job_id)
            return
        if event.event == "stage_failed":
            self._mark_failed(project_id, job_id, event.message)
            return
        if event.event == "pipeline_completed" and event.success is False:
            self._mark_failed(project_id, job_id, event.message)

    def _mark_ready(self, project_id: int, job_id: int, *, exit_code: int | None = None) -> None:
        now = utc_now()
        with session_scope() as session:
            project_repo = ProjectRepository(session)
            job_repo = JobRepository(session)
            project = project_repo.get(project_id)
            job = job_repo.get(job_id)
            if project is None or job is None:
                return
            if project.status == "cancelled" or job.status == "cancelled":
                return
            job_repo.update_state(
                job,
                status="completed",
                current_stage="ready",
                progress=progress_for_stage("ready"),
                finished_at=now,
                exit_code=exit_code,
                process_id=None,
                error_message=None,
            )
            project_repo.update_flow_state(
                project,
                status="ready",
                current_stage="ready",
                progress_percent=progress_for_stage("ready"),
                error_message=None,
                completed_at=now,
            )

    def _mark_failed(self, project_id: int, job_id: int, message: str, *, exit_code: int | None = None) -> None:
        now = utc_now()
        with session_scope() as session:
            project_repo = ProjectRepository(session)
            job_repo = JobRepository(session)
            project = project_repo.get(project_id)
            job = job_repo.get(job_id)
            if (project is not None and project.status == "cancelled") or (
                job is not None and job.status == "cancelled"
            ):
                return
            if job is not None:
                failed_progress = float(job.progress or 0.0)
                job_repo.update_state(
                    job,
                    status="failed",
                    current_stage="failed",
                    progress=failed_progress,
                    finished_at=now,
                    exit_code=exit_code,
                    process_id=None,
                    error_message=message,
                )
            if project is not None:
                failed_progress = float(project.progress_percent or 0.0)
                project_repo.update_flow_state(
                    project,
                    status="failed",
                    current_stage="failed",
                    progress_percent=failed_progress,
                    error_message=message,
                    completed_at=now,
                )

    def _mark_cancelled(self, project_id: int, job_id: int) -> None:
        now = utc_now()
        with session_scope() as session:
            project_repo = ProjectRepository(session)
            job_repo = JobRepository(session)
            project = project_repo.get(project_id)
            job = job_repo.get(job_id)
            if job is not None:
                cancelled_progress = float(job.progress or 0.0)
                job_repo.update_state(
                    job,
                    status="cancelled",
                    current_stage="cancelled",
                    progress=cancelled_progress,
                    finished_at=now,
                    process_id=None,
                    error_message="Cancelled by user.",
                )
            if project is not None:
                cancelled_progress = float(project.progress_percent or 0.0)
                project_repo.update_flow_state(
                    project,
                    status="cancelled",
                    current_stage="cancelled",
                    progress_percent=cancelled_progress,
                    error_message="Cancelled by user.",
                    completed_at=now,
                )

    def _latest_log_path(self, project_id: int) -> Path | None:
        init_database()
        with session_scope() as session:
            job = JobRepository(session).latest_for_project(int(project_id), PIPELINE_JOB_TYPE)
            if job is None or not job.log_path:
                return None
            log_path = self._resolve_project_path(job.log_path)
        workspace = ensure_project_workspace(int(project_id), project_root=self.project_root).resolve()
        logs_dir = (workspace / "logs").resolve()
        try:
            log_path.relative_to(logs_dir)
        except ValueError:
            return None
        return log_path

    def _append_log(self, log_path: Path, text: str) -> None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8", errors="replace") as file_handle:
            file_handle.write(_redact_log_line(text))

    def _resolve_project_path(self, value: str | Path) -> Path:
        path = Path(value)
        return path.resolve() if path.is_absolute() else (self.project_root / path).resolve()

    def _terminate_process_tree(self, process: subprocess.Popen) -> None:
        try:
            if os.name == "nt":
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            process.wait(timeout=3.0)
            return
        except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
            pass

        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
            )
            return
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            if process.poll() is None:
                process.kill()


def _status_dict_for_project(project_id: int) -> dict[str, Any]:
    try:
        return get_project_status(project_id)
    except ProjectNotFoundError as exc:
        raise ProjectOrchestratorNotFoundError(str(exc)) from exc


def _redact_log_line(line: str) -> str:
    redacted = SECRET_LINE_RE.sub(r"\1=<redacted>", str(line))
    return redact_text(redacted)


def _safe_error_message(exc: Exception) -> str:
    return _redact_log_line(str(exc)).strip() or exc.__class__.__name__


def _display_command(command: list[str]) -> str:
    displayed: list[str] = []
    mask_next = False
    for value in command:
        if mask_next:
            displayed.append("<source-url>")
            mask_next = False
            continue
        text = str(value)
        displayed.append(f'"{text}"' if " " in text else text)
        mask_next = text == "--source-url"
    return " ".join(displayed)
