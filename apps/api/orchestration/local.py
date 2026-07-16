from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from apps.review_agent.service import ReviewAgentService

from ..db.database import init_database, session_scope
from ..db.models import utc_now
from ..db.repositories import JobRepository, ProjectRepository
from ..services.legacy_import_service import import_candidate_file_into_project
from ..services.project_service import (
    PROJECT_ROOT,
    ProjectNotFoundError,
    ensure_project_workspace,
    get_project_status,
    safe_relative_path,
)
from .base import JobResult, PipelineStatus, ProjectAlreadyRunningError, ProjectOrchestratorNotFoundError
from .stage_parser import message_for_stage, parse_manager_stage, progress_for_stage

PIPELINE_JOB_TYPE = "local_pipeline"
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
                if project.id in self._workers or job_repo.active_for_project(project.id, PIPELINE_JOB_TYPE) is not None:
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
                    process.terminate()

        now = utc_now()
        with session_scope() as session:
            project_repo = ProjectRepository(session)
            job_repo = JobRepository(session)
            project = project_repo.get(int(project_id))
            if project is None:
                raise ProjectOrchestratorNotFoundError(f"Unknown project_id: {project_id}")
            active_job = job_repo.active_for_project(project.id, PIPELINE_JOB_TYPE)
            if active_job is not None:
                job_repo.update_state(
                    active_job,
                    status="cancelled",
                    current_stage="cancelled",
                    progress=progress_for_stage("cancelled"),
                    finished_at=now,
                    error_message="Cancelled by user.",
                )
            project_repo.update_flow_state(
                project,
                status="cancelled",
                current_stage="cancelled",
                progress_percent=progress_for_stage("cancelled"),
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
                job_repo.update_state(
                    job,
                    status="failed",
                    current_stage="failed",
                    progress=progress_for_stage("failed"),
                    finished_at=now,
                    error_message=message,
                )
                project = project_repo.get(job.project_id)
                if project is not None and project.status in ACTIVE_STATUSES:
                    project_repo.update_flow_state(
                        project,
                        status="failed",
                        current_stage="failed",
                        progress_percent=progress_for_stage("failed"),
                        error_message=message,
                        completed_at=now,
                    )
                recovered += 1
        return recovered

    def _run_job(self, project_id: int, job_id: int) -> None:
        try:
            self._execute_job(project_id, job_id)
        finally:
            with self._lock:
                self._workers.pop(project_id, None)

    def _execute_job(self, project_id: int, job_id: int) -> None:
        project_data = self._prepare_running_state(project_id, job_id)
        workspace = self._resolve_project_path(str(project_data["workspace_path"]))
        log_path = self._resolve_project_path(str(project_data["log_path"]))
        command = self._build_manager_command(project_data["source_url"], workspace)
        self._append_log(log_path, f"$ {' '.join(_display_command_part(part) for part in command)}\n")

        process = self.popen_factory(
            command,
            cwd=str(self.project_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            shell=False,
            env=self._subprocess_env(),
        )
        self._set_process(project_id, job_id, process)

        assert process.stdout is not None
        for raw_line in process.stdout:
            line = _redact_log_line(raw_line)
            self._append_log(log_path, line)
            stage = parse_manager_stage(line)
            if stage in {"downloading", "transcribing", "validating_transcript", "generating_candidates"}:
                self._mark_running_stage(project_id, job_id, stage)

        exit_code = process.wait()
        if self._is_cancel_requested(project_id):
            self._mark_cancelled(project_id, job_id)
            return
        if exit_code != 0:
            self._mark_failed(project_id, job_id, f"manager.py exited with code {exit_code}.", exit_code=exit_code)
            return

        self._mark_running_stage(project_id, job_id, "importing_candidates")
        self._append_log(log_path, "\nImporting candidate clips into SQLite.\n")
        imported = self._import_candidates(project_id, workspace)
        if not imported:
            self._mark_failed(project_id, job_id, "No candidate clips were imported from the project workspace.", exit_code=exit_code)
            return

        if bool(project_data["auto_review"]):
            self._mark_running_stage(project_id, job_id, "reviewing_with_ai")
            self._append_log(log_path, "Reviewing imported clips with configured AI boundary reviewer.\n")
            try:
                review_result = ReviewAgentService(project_root=self.project_root).review_project_clips(
                    project_id=project_id,
                    apply_safe_suggestions=True,
                )
                self._append_log(log_path, f"AI review completed: {review_result}\n")
            except Exception as exc:
                self._append_log(log_path, f"AI review failed: {_safe_error_message(exc)}\n")
                self._mark_failed(project_id, job_id, f"AI boundary review failed: {_safe_error_message(exc)}", exit_code=exit_code)
                return
        else:
            self._append_log(log_path, "Auto review disabled; project is ready for manual review.\n")

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
                current_stage="downloading",
                progress=progress_for_stage("downloading"),
                log_path=safe_relative_path(log_path, project_root=self.project_root),
                started_at=now,
                error_message=None,
            )
            project_repo.update_flow_state(
                project,
                status="running",
                current_stage="downloading",
                progress_percent=progress_for_stage("downloading"),
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

    def _build_manager_command(self, source_url: str, workspace: Path) -> list[str]:
        command = [
            sys.executable,
            str(self.project_root / "manager.py"),
            "--url",
            str(source_url),
            "--workspace-dir",
            str(workspace),
            "--analysis-only",
            "--ai-mode",
            "local_only",
            "--subtitle-checker-mode",
            "local_only",
        ]
        return command

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

    def _mark_running_stage(self, project_id: int, job_id: int, stage: str) -> None:
        progress = progress_for_stage(stage)
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

    def _mark_ready(self, project_id: int, job_id: int, *, exit_code: int | None = None) -> None:
        now = utc_now()
        with session_scope() as session:
            project_repo = ProjectRepository(session)
            job_repo = JobRepository(session)
            project = project_repo.get(project_id)
            job = job_repo.get(job_id)
            if project is None or job is None:
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
            if job is not None:
                job_repo.update_state(
                    job,
                    status="failed",
                    current_stage="failed",
                    progress=progress_for_stage("failed"),
                    finished_at=now,
                    exit_code=exit_code,
                    process_id=None,
                    error_message=message,
                )
            if project is not None:
                project_repo.update_flow_state(
                    project,
                    status="failed",
                    current_stage="failed",
                    progress_percent=progress_for_stage("failed"),
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
                job_repo.update_state(
                    job,
                    status="cancelled",
                    current_stage="cancelled",
                    progress=progress_for_stage("cancelled"),
                    finished_at=now,
                    process_id=None,
                    error_message="Cancelled by user.",
                )
            if project is not None:
                project_repo.update_flow_state(
                    project,
                    status="cancelled",
                    current_stage="cancelled",
                    progress_percent=progress_for_stage("cancelled"),
                    error_message="Cancelled by user.",
                    completed_at=now,
                )

    def _import_candidates(self, project_id: int, workspace: Path) -> bool:
        with session_scope() as session:
            project = import_candidate_file_into_project(
                session,
                project_id=project_id,
                project_root=self.project_root,
                workspace_root=workspace,
            )
            return project is not None

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


def _status_dict_for_project(project_id: int) -> dict[str, Any]:
    try:
        return get_project_status(project_id)
    except ProjectNotFoundError as exc:
        raise ProjectOrchestratorNotFoundError(str(exc)) from exc


def _redact_log_line(line: str) -> str:
    redacted = SECRET_LINE_RE.sub(r"\1=<redacted>", str(line))
    return redacted


def _safe_error_message(exc: Exception) -> str:
    return _redact_log_line(str(exc)).strip() or exc.__class__.__name__


def _display_command_part(value: str) -> str:
    text = str(value)
    if " " in text:
        return f'"{text}"'
    return text
