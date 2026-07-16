import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select

from apps.api.db.database import configure_database, init_database, session_scope
from apps.api.db.models import Clip, ClipEvaluation, Job, Project
from apps.api.db.repositories import JobRepository, ProjectRepository
from apps.api.main import app
from apps.api.orchestration.local import LocalPipelineOrchestrator
from apps.api.orchestration.service import recover_orphaned_jobs
from apps.api.orchestration.stage_parser import parse_manager_stage, progress_for_stage
from apps.api.services import project_service
from apps.pipeline.config import PipelineConfig
from apps.pipeline.context import PipelineContext
from apps.pipeline.events import PipelineEvent
from apps.pipeline.persistence import ProjectStateEventSink
from apps.pipeline.profiles import project_pipeline_stages
from apps.pipeline.runner import PipelineRunner
from apps.pipeline.stages.ready import MarkProjectReadyStage
from apps.pipeline.stages.review_candidates import ReviewCandidatesStage
from apps.review_agent.schemas import GeminiBoundaryDecision


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _write_candidate_workspace(workspace: Path) -> None:
    (workspace / "input").mkdir(parents=True, exist_ok=True)
    (workspace / "transcripts").mkdir(parents=True, exist_ok=True)
    (workspace / "metadata").mkdir(parents=True, exist_ok=True)
    (workspace / "input" / "source.mp4").write_bytes(b"fake media")
    (workspace / "transcripts" / "final_transcript.json").write_text(
        json.dumps(
            {
                "segments": [
                    {"start": 10.0, "end": 20.0, "text": "A useful setup."},
                    {"start": 20.0, "end": 45.0, "text": "A useful standalone podcast point."},
                ]
            }
        ),
        encoding="utf-8",
    )
    (workspace / "top_windows.json").write_text(
        json.dumps(
            {
                "top_windows": [
                    {
                        "id": "clip_001",
                        "start": 20.0,
                        "end": 45.0,
                        "summary": "Useful podcast point",
                        "text": "A useful standalone podcast point.",
                        "local_score": 0.91,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


class FakeSuccessfulPopen:
    calls = []
    stdout_handles = []

    def __init__(self, command, **kwargs):
        self.command = command
        self.kwargs = kwargs
        self.pid = 4321
        self.returncode = 0
        FakeSuccessfulPopen.calls.append((command, kwargs))
        workspace = Path(command[command.index("--workspace-dir") + 1])
        project_root = Path(command[command.index("--repository-root") + 1])
        project_id = int(command[command.index("--project-id") + 1])
        _write_candidate_workspace(workspace)
        from apps.api.services.legacy_import_service import import_candidate_file_into_project

        with session_scope() as session:
            import_candidate_file_into_project(
                session,
                project_id=project_id,
                project_root=project_root,
                workspace_root=workspace,
            )
        events = [
            PipelineEvent(
                event="stage_started",
                stage=stage,
                message=stage,
            ).to_marker()
            for stage in (
                "downloading",
                "transcribing",
                "validating_transcript",
                "generating_candidates",
                "importing_candidates",
            )
        ]
        events.append(
            PipelineEvent(
                event="pipeline_completed",
                stage="ready",
                message="Pipeline completed successfully.",
                progress_percent=100.0,
                success=True,
            ).to_marker()
        )
        self.stdout = io.StringIO(
            "\n".join(
                [
                    "CUDA transcription unavailable; falling back to CPU int8.",
                    *events,
                ]
            )
            + "\n"
        )
        FakeSuccessfulPopen.stdout_handles.append(self.stdout)

    def wait(self):
        return self.returncode

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15


class FakeFailingPopen(FakeSuccessfulPopen):
    def __init__(self, command, **kwargs):
        self.command = command
        self.kwargs = kwargs
        self.pid = 4322
        self.returncode = 7
        FakeSuccessfulPopen.calls.append((command, kwargs))
        self.stdout = io.StringIO(
            "\n".join(
                [
                    PipelineEvent(
                        event="stage_started",
                        stage="transcribing",
                        message="Transcribing podcast",
                    ).to_marker(),
                    PipelineEvent(
                        event="stage_failed",
                        stage="transcribing",
                        message="Offline transcription fixture failed.",
                        success=False,
                        error_category="TranscriptionStageError",
                    ).to_marker(),
                    PipelineEvent(
                        event="pipeline_completed",
                        stage="transcribing",
                        message="Offline transcription fixture failed.",
                        success=False,
                        error_category="TranscriptionStageError",
                    ).to_marker(),
                ]
            )
            + "\n"
        )


class FakeReviewFailingPopen:
    def __init__(self, command, **kwargs):
        self.command = command
        self.kwargs = kwargs
        self.pid = 4323
        self.returncode = 8
        FakeSuccessfulPopen.calls.append((command, kwargs))
        self.stdout = io.StringIO(
            "\n".join(
                [
                    PipelineEvent(
                        event="review_clip_completed",
                        stage="reviewing_with_ai",
                        message="Reviewed clip 1 of 5",
                        progress_percent=87.0,
                        metadata={"clip_id": "clip_001", "index": 1, "total": 5},
                    ).to_marker(),
                    PipelineEvent(
                        event="stage_failed",
                        stage="reviewing_with_ai",
                        message="Automatic boundary review exceeded its configured batch timeout.",
                        success=False,
                        error_category="ReviewStageError",
                    ).to_marker(),
                    PipelineEvent(
                        event="pipeline_completed",
                        stage="reviewing_with_ai",
                        message="Automatic boundary review exceeded its configured batch timeout.",
                        success=False,
                        error_category="ReviewStageError",
                    ).to_marker(),
                ]
            )
            + "\n"
        )

    def wait(self):
        return self.returncode

    def poll(self):
        return self.returncode


class FakeCancelledReviewPopen(FakeReviewFailingPopen):
    def __init__(self, command, **kwargs):
        self.command = command
        self.kwargs = kwargs
        self.pid = 4324
        self.returncode = 130
        FakeSuccessfulPopen.calls.append((command, kwargs))
        self.stdout = io.StringIO(
            "\n".join(
                [
                    PipelineEvent(
                        event="review_clip_completed",
                        stage="reviewing_with_ai",
                        message="Reviewed clip 2 of 5",
                        progress_percent=89.0,
                        metadata={"clip_id": "clip_002", "index": 2, "total": 5},
                    ).to_marker(),
                    PipelineEvent(
                        event="pipeline_cancelled",
                        stage="reviewing_with_ai",
                        message="Pipeline cancelled by user.",
                        progress_percent=89.0,
                        success=False,
                        error_category="PipelineCancelled",
                    ).to_marker(),
                ]
            )
            + "\n"
        )


class ProjectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_url = _sqlite_url(self.root / "test.db")
        os.environ["PODCAST_CUTTER_DB_URL"] = self.db_url
        os.environ["PODCAST_CUTTER_PROJECT_ROOT"] = str(self.root)
        os.environ["PIPELINE_ORCHESTRATOR"] = "local"
        os.environ["CLIP_REVIEW_MODE"] = "local_stub"
        configure_database(self.db_url)
        init_database()
        LocalPipelineOrchestrator.reset_for_tests()
        FakeSuccessfulPopen.calls = []
        FakeSuccessfulPopen.stdout_handles = []

    def tearDown(self):
        LocalPipelineOrchestrator.reset_for_tests()
        configure_database("sqlite:///:memory:")
        for key in (
            "PODCAST_CUTTER_DB_URL",
            "PODCAST_CUTTER_PROJECT_ROOT",
            "PIPELINE_ORCHESTRATOR",
            "CLIP_REVIEW_MODE",
            "GEMINI_API_KEY",
            "GEMINI_MODEL",
        ):
            os.environ.pop(key, None)
        self.tempdir.cleanup()

    def _create_project(self, *, auto_review: bool = True, source_url: str = "https://www.youtube.com/watch?v=test") -> int:
        project = project_service.create_project(
            source_url=source_url,
            title="Flow project",
            auto_review=auto_review,
            project_root=self.root,
        )
        return int(project["id"])

    def _pipeline_context(self, project_id: int, *, auto_review: bool) -> PipelineContext:
        return PipelineContext(
            project_id=project_id,
            source_url="https://example.com/watch",
            workspace_path=self.root / "data" / "projects" / str(project_id) / "workspace",
            repository_root=self.root,
            auto_review=auto_review,
            analysis_only=True,
            config=PipelineConfig(ai_mode="local_only", subtitle_checker_mode="local_only"),
        )

    def test_project_creation_creates_isolated_workspace(self):
        project_id = self._create_project()

        workspace = self.root / "data" / "projects" / str(project_id) / "workspace"

        self.assertTrue((workspace / "input").is_dir())
        self.assertTrue((workspace / "metadata").is_dir())
        self.assertTrue((workspace / "transcripts").is_dir())
        self.assertTrue((workspace / "cuts" / "raw").is_dir())
        self.assertTrue((workspace / "cuts" / "subtitles").is_dir())
        self.assertTrue((workspace / "outputs").is_dir())
        self.assertTrue((workspace / "logs").is_dir())

    def test_two_projects_use_different_workspaces(self):
        first_id = self._create_project(source_url="https://example.com/one")
        second_id = self._create_project(source_url="https://example.com/two")

        first = project_service.get_project(first_id)
        second = project_service.get_project(second_id)

        self.assertNotEqual(first["workspace_path"], second["workspace_path"])
        self.assertTrue(first["workspace_path"].endswith(f"data/projects/{first_id}/workspace"))
        self.assertTrue(second["workspace_path"].endswith(f"data/projects/{second_id}/workspace"))

    def test_start_endpoint_returns_without_blocking(self):
        project_id = self._create_project(auto_review=False)

        with patch.object(LocalPipelineOrchestrator, "_run_job", return_value=None):
            with TestClient(app) as client:
                response = client.post(f"/projects/{project_id}/start")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["job"]["status"], "queued")
        with session_scope() as session:
            job = session.scalars(select(Job)).one()
            self.assertEqual(job.status, "queued")

    def test_duplicate_active_start_returns_409(self):
        project_id = self._create_project(auto_review=False)

        with patch.object(LocalPipelineOrchestrator, "_run_job", return_value=None):
            with TestClient(app) as client:
                first = client.post(f"/projects/{project_id}/start")
                second = client.post(f"/projects/{project_id}/start")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 409)

    def test_project_create_endpoint_validates_url_and_does_not_auto_start_by_default(self):
        with TestClient(app) as client:
            invalid = client.post("/projects", json={"source_url": "not-a-url"})
            valid = client.post("/projects", json={"source_url": "https://example.com/watch", "auto_review": False})

        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(valid.status_code, 200)
        project = valid.json()["project"]
        self.assertEqual(project["status"], "created")
        with session_scope() as session:
            self.assertEqual(len(list(session.scalars(select(Job)).all())), 0)

    def test_local_orchestrator_uses_safe_subprocess_arguments(self):
        url = "https://example.com/watch?v=one&x=two;Remove-Item"
        project_id = self._create_project(auto_review=False, source_url=url)
        orchestrator = LocalPipelineOrchestrator(
            project_root=self.root,
            popen_factory=FakeSuccessfulPopen,
            run_inline=True,
        )

        orchestrator.start_project(project_id)

        command, kwargs = FakeSuccessfulPopen.calls[0]
        workspace_arg = Path(command[command.index("--workspace-dir") + 1])
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(command[1:3], ["-m", "apps.pipeline.entrypoint"])
        self.assertEqual(command[command.index("--source-url") + 1], url)
        self.assertTrue(FakeSuccessfulPopen.stdout_handles[0].closed)
        self.assertEqual(command.count(url), 1)
        self.assertEqual(Path(kwargs["cwd"]), self.root)
        self.assertEqual(workspace_arg, self.root / "data" / "projects" / str(project_id) / "workspace")
        self.assertFalse(kwargs["shell"])
        self.assertIn("--workspace-dir", command)
        self.assertIn("--project-id", command)
        self.assertIn("--no-auto-review", command)
        self.assertFalse((self.root / "input" / "source.mp4").exists())
        self.assertFalse((self.root / "top_windows.json").exists())
        self.assertTrue((workspace_arg / "input" / "source.mp4").exists())
        self.assertTrue((workspace_arg / "top_windows.json").exists())
        log_text = "\n".join(orchestrator.read_project_log_tail(project_id, tail=50)["lines"])
        self.assertNotIn(url, log_text)
        self.assertIn("<source-url>", log_text)

    def test_stage_parser_maps_representative_manager_logs(self):
        samples = {
            "download source": "downloading",
            "Transkrypcja audio": "transcribing",
            "AI Subtitler Checker": "validating_transcript",
            "Podcast clip analysis": "generating_candidates",
            "Importing candidate clips into SQLite.": "importing_candidates",
            "Reviewing boundaries with AI": "reviewing_with_ai",
            "ready": "ready",
        }

        for line, expected in samples.items():
            self.assertEqual(parse_manager_stage(line), expected)
        self.assertEqual(progress_for_stage("reviewing_with_ai"), 85.0)

    def test_successful_pipeline_imports_clips_into_same_project(self):
        project_id = self._create_project(auto_review=False)
        orchestrator = LocalPipelineOrchestrator(
            project_root=self.root,
            popen_factory=FakeSuccessfulPopen,
            run_inline=True,
        )

        orchestrator.start_project(project_id)

        with session_scope() as session:
            projects = list(session.scalars(select(Project)).all())
            clips = list(session.scalars(select(Clip)).all())
            project = session.get(Project, project_id)
        self.assertEqual(len(projects), 1)
        self.assertEqual(len(clips), 1)
        self.assertEqual(clips[0].project_id, project_id)
        self.assertEqual(project.status, "ready")
        self.assertEqual(project.current_stage, "ready")

    def test_cpu_fallback_log_can_complete_project_flow_successfully(self):
        project_id = self._create_project(auto_review=False)
        orchestrator = LocalPipelineOrchestrator(
            project_root=self.root,
            popen_factory=FakeSuccessfulPopen,
            run_inline=True,
        )

        orchestrator.start_project(project_id)
        logs = orchestrator.read_project_log_tail(project_id, tail=50)
        status = project_service.get_project_status(project_id)

        self.assertEqual(status["status"], "ready")
        self.assertIn("CUDA transcription unavailable; falling back to CPU int8.", "\n".join(logs["lines"]))

    def test_import_retry_does_not_duplicate_clips(self):
        project_id = self._create_project(auto_review=False)
        orchestrator = LocalPipelineOrchestrator(
            project_root=self.root,
            popen_factory=FakeSuccessfulPopen,
            run_inline=True,
        )

        orchestrator.start_project(project_id)
        orchestrator.start_project(project_id)

        with session_scope() as session:
            clips = list(session.scalars(select(Clip)).all())
            jobs = list(session.scalars(select(Job)).all())
        self.assertEqual(len(clips), 1)
        self.assertEqual(len(jobs), 2)

    def test_auto_review_true_calls_batch_review_service_directly(self):
        project_id = self._create_project(auto_review=True)
        workspace = project_service.ensure_project_workspace(project_id, project_root=self.root)
        _write_candidate_workspace(workspace)
        from apps.api.services.legacy_import_service import import_candidate_file_into_project

        with session_scope() as session:
            import_candidate_file_into_project(
                session,
                project_id=project_id,
                project_root=self.root,
                workspace_root=workspace,
            )
        calls = []

        class FakeReviewService:
            def __init__(self, *, project_root):
                calls.append({"project_root": project_root})

            def review_project_clips(self, *, project_id, apply_safe_suggestions):
                calls.append({"project_id": project_id, "apply_safe_suggestions": apply_safe_suggestions})
                return {"provider": "fake", "clip_count": 1}

        with patch("apps.pipeline.stages.review_candidates.ReviewAgentService", FakeReviewService):
            result = ReviewCandidatesStage().run(self._pipeline_context(project_id, auto_review=True))

        self.assertTrue(result.success)
        self.assertEqual(calls[-1], {"project_id": project_id, "apply_safe_suggestions": True})

    def test_auto_review_uses_dotenv_configured_gemini_provider(self):
        project_id = self._create_project(auto_review=True)
        for key in ("CLIP_REVIEW_MODE", "GEMINI_API_KEY", "GEMINI_MODEL"):
            os.environ.pop(key, None)
        (self.root / ".env").write_text(
            "CLIP_REVIEW_MODE=gemini\nGEMINI_API_KEY=test-key\nGEMINI_MODEL=gemini-flow\n",
            encoding="utf-8",
        )

        class DotenvGemini:
            provider = "gemini"

            def __init__(self, *, api_key, model):
                self.model = model

            def review(self, context, corrective_message=None):
                return GeminiBoundaryDecision(
                    decision="render_ready",
                    selected_start_option_index=context["current_aligned_start_option_index"],
                    selected_end_option_index=context["current_aligned_end_option_index"],
                    reasoning_summary="Ready from Project Flow Gemini config.",
                    start_reason="Aligned start.",
                    end_reason="Aligned end.",
                    warnings=[],
                )

        workspace = project_service.ensure_project_workspace(project_id, project_root=self.root)
        _write_candidate_workspace(workspace)
        from apps.api.services.legacy_import_service import import_candidate_file_into_project

        with session_scope() as session:
            import_candidate_file_into_project(
                session,
                project_id=project_id,
                project_root=self.root,
                workspace_root=workspace,
            )
        context = self._pipeline_context(project_id, auto_review=True)
        with patch("apps.review_agent.service.GeminiBoundaryReviewer", DotenvGemini):
            ReviewCandidatesStage().run(context)
            MarkProjectReadyStage().run(context)

        self.assertEqual(project_service.get_project_status(project_id)["status"], "ready")
        with session_scope() as session:
            evaluation = session.scalars(select(ClipEvaluation)).one()
        self.assertEqual(evaluation.provider, "gemini")
        self.assertEqual(evaluation.model, "gemini-flow")

    def test_auto_review_false_does_not_call_gemini(self):
        project_id = self._create_project(auto_review=False)
        context = self._pipeline_context(project_id, auto_review=False)
        stages = project_pipeline_stages(context)
        self.assertFalse(any(isinstance(stage, ReviewCandidatesStage) for stage in stages))

    def test_gemini_configuration_failure_marks_project_failed(self):
        project_id = self._create_project(auto_review=True)

        class FailingReviewService:
            def __init__(self, *, project_root):
                pass

            def review_project_clips(self, *, project_id, apply_safe_suggestions):
                raise RuntimeError("CLIP_REVIEW_MODE=gemini requires GEMINI_API_KEY")

        context = self._pipeline_context(project_id, auto_review=True)
        with patch("apps.pipeline.stages.review_candidates.ReviewAgentService", FailingReviewService):
            PipelineRunner(
                [ReviewCandidatesStage()],
                event_sinks=(ProjectStateEventSink(context),),
            ).run(context)

        status = project_service.get_project_status(project_id)
        self.assertEqual(status["status"], "failed")
        self.assertIn("Automatic boundary review failed", status["error_message"])
        self.assertIn("GEMINI_API_KEY", status["error_message"])

    def test_nonzero_manager_exit_marks_job_and_project_failed(self):
        project_id = self._create_project(auto_review=False)

        LocalPipelineOrchestrator(
            project_root=self.root,
            popen_factory=FakeFailingPopen,
            run_inline=True,
        ).start_project(project_id)

        status = project_service.get_project_status(project_id)
        self.assertEqual(status["status"], "failed")
        self.assertIn("fixture failed", status["error_message"])
        with session_scope() as session:
            job = session.scalars(select(Job)).one()
            self.assertEqual(job.exit_code, 7)

    def test_restarting_failed_project_creates_new_job_not_new_project(self):
        project_id = self._create_project(auto_review=False)

        LocalPipelineOrchestrator(
            project_root=self.root,
            popen_factory=FakeFailingPopen,
            run_inline=True,
        ).start_project(project_id)
        LocalPipelineOrchestrator(
            project_root=self.root,
            popen_factory=FakeSuccessfulPopen,
            run_inline=True,
        ).start_project(project_id)

        with session_scope() as session:
            projects = list(session.scalars(select(Project)).all())
            jobs = list(session.scalars(select(Job).order_by(Job.id.asc())).all())
            clips = list(session.scalars(select(Clip)).all())
            project = session.get(Project, project_id)
        self.assertEqual(len(projects), 1)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0].status, "failed")
        self.assertEqual(jobs[1].status, "completed")
        self.assertEqual(len(clips), 1)
        self.assertEqual(project.status, "ready")

    def test_cancellation_updates_status(self):
        project_id = self._create_project(auto_review=False)
        orchestrator = LocalPipelineOrchestrator(project_root=self.root)

        with patch("apps.api.orchestration.local.threading.Thread.start", return_value=None):
            orchestrator.start_project(project_id)
        with session_scope() as session:
            project = session.get(Project, project_id)
            job = session.scalars(select(Job)).one()
            ProjectRepository(session).update_flow_state(
                project,
                status="running",
                current_stage="transcribing",
                progress_percent=30.0,
            )
            JobRepository(session).update_state(
                job,
                status="running",
                current_stage="transcribing",
                progress=30.0,
            )
        fake_process = SimpleNamespace(poll=lambda: None, pid=9876)
        LocalPipelineOrchestrator._workers[project_id].process = fake_process
        with patch.object(orchestrator, "_terminate_process_tree") as terminate:
            status = orchestrator.cancel_project(project_id)

        terminate.assert_called_once_with(fake_process)
        self.assertEqual(status.status, "cancelled")
        self.assertEqual(status.progress_percent, 30.0)
        with session_scope() as session:
            project = session.get(Project, project_id)
            job = session.scalars(select(Job)).one()
        self.assertEqual(project.status, "cancelled")
        self.assertEqual(job.status, "cancelled")

    def test_subprocess_launch_failure_marks_job_and_project_failed(self):
        project_id = self._create_project(auto_review=False)

        def fail_to_start(*args, **kwargs):
            raise OSError("offline launch failure")

        LocalPipelineOrchestrator(
            project_root=self.root,
            popen_factory=fail_to_start,
            run_inline=True,
        ).start_project(project_id)

        status = project_service.get_project_status(project_id)
        self.assertEqual(status["status"], "failed")
        self.assertIn("worker failed", status["error_message"])
        with session_scope() as session:
            job = session.scalars(select(Job)).one()
        self.assertEqual(job.status, "failed")

    def test_review_batch_failure_leaves_no_running_project_or_job(self):
        project_id = self._create_project(auto_review=True)
        LocalPipelineOrchestrator(
            project_root=self.root,
            popen_factory=FakeReviewFailingPopen,
            run_inline=True,
        ).start_project(project_id)

        status = project_service.get_project_status(project_id)
        self.assertEqual(status["status"], "failed")
        self.assertEqual(status["progress_percent"], 87.0)
        self.assertIn("batch timeout", status["error_message"])
        with session_scope() as session:
            job = session.scalars(select(Job)).one()
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.progress, 87.0)

    def test_restart_after_cancel_reuses_same_project_and_creates_new_job(self):
        project_id = self._create_project(auto_review=False)
        first_orchestrator = LocalPipelineOrchestrator(
            project_root=self.root,
            popen_factory=FakeCancelledReviewPopen,
            run_inline=True,
        )
        first_orchestrator.start_project(project_id)
        self.assertEqual(project_service.get_project_status(project_id)["status"], "cancelled")
        self.assertNotIn(project_id, LocalPipelineOrchestrator._workers)

        LocalPipelineOrchestrator(
            project_root=self.root,
            popen_factory=FakeSuccessfulPopen,
            run_inline=True,
        ).start_project(project_id)

        with session_scope() as session:
            projects = list(session.scalars(select(Project)).all())
            jobs = list(session.scalars(select(Job).order_by(Job.id.asc())).all())
            clips = list(session.scalars(select(Clip)).all())
        self.assertEqual([project.id for project in projects], [project_id])
        self.assertEqual([job.status for job in jobs], ["cancelled", "completed"])
        self.assertEqual(len(clips), 1)
        self.assertEqual(clips[0].project_id, project_id)

    def test_logs_endpoint_returns_requested_tail(self):
        project_id = self._create_project(auto_review=False)
        workspace = project_service.ensure_project_workspace(project_id, project_root=self.root)
        log_path = workspace / "logs" / "pipeline.log"
        log_path.write_text("one\ntwo\nthree\n", encoding="utf-8")
        with session_scope() as session:
            JobRepository(session).create(
                project_id=project_id,
                job_type="local_pipeline",
                status="completed",
                current_stage="ready",
                log_path=project_service.safe_relative_path(log_path, project_root=self.root),
            )

        with TestClient(app) as client:
            response = client.get(f"/projects/{project_id}/logs?tail=2")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["lines"], ["two", "three"])

    def test_logs_endpoint_cannot_read_arbitrary_paths(self):
        project_id = self._create_project(auto_review=False)
        secret_path = self.root / "secret.txt"
        secret_path.write_text("secret-value\n", encoding="utf-8")
        with session_scope() as session:
            JobRepository(session).create(
                project_id=project_id,
                job_type="local_pipeline",
                status="completed",
                current_stage="ready",
                log_path="secret.txt",
            )

        with TestClient(app) as client:
            response = client.get(f"/projects/{project_id}/logs?tail=10")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["lines"], [])

    def test_project_specific_clip_patch_updates_only_that_project(self):
        from apps.api.services.legacy_import_service import import_candidate_file_into_project

        first_id = self._create_project(auto_review=False, source_url="https://example.com/first")
        second_id = self._create_project(auto_review=False, source_url="https://example.com/second")
        for project_id in (first_id, second_id):
            workspace = project_service.ensure_project_workspace(project_id, project_root=self.root)
            _write_candidate_workspace(workspace)
            with session_scope() as session:
                import_candidate_file_into_project(
                    session,
                    project_id=project_id,
                    project_root=self.root,
                    workspace_root=workspace,
                )

        with TestClient(app) as client:
            response = client.patch(f"/projects/{second_id}/clips/clip_001", json={"start": 22.0, "end": 45.0})

        self.assertEqual(response.status_code, 200)
        with session_scope() as session:
            first_clip = session.scalars(select(Clip).where(Clip.project_id == first_id)).one()
            second_clip = session.scalars(select(Clip).where(Clip.project_id == second_id)).one()
        self.assertEqual(first_clip.edited_start, 20.0)
        self.assertEqual(second_clip.edited_start, 22.0)

    def test_startup_recovery_marks_orphaned_running_jobs_failed(self):
        project_id = self._create_project(auto_review=False)
        with session_scope() as session:
            project = ProjectRepository(session).get(project_id)
            ProjectRepository(session).update_flow_state(project, status="running", current_stage="transcribing")
            JobRepository(session).create(
                project_id=project_id,
                job_type="local_pipeline",
                status="running",
                current_stage="transcribing",
            )

        recovered = recover_orphaned_jobs(project_root=self.root)

        self.assertEqual(recovered, 1)
        status = project_service.get_project_status(project_id)
        self.assertEqual(status["status"], "failed")
        self.assertIn("server restarted", status["error_message"])

    def test_existing_review_endpoints_and_static_editor_remain_available(self):
        project_id = self._create_project(auto_review=False)
        workspace = project_service.ensure_project_workspace(project_id, project_root=self.root)
        _write_candidate_workspace(workspace)
        from apps.api.services.legacy_import_service import import_candidate_file_into_project

        with session_scope() as session:
            import_candidate_file_into_project(
                session,
                project_id=project_id,
                project_root=self.root,
                workspace_root=workspace,
            )

        with TestClient(app) as client:
            static_response = client.get("/")
            single_response = client.post(f"/projects/{project_id}/clips/clip_001/review")
            batch_response = client.post(f"/projects/{project_id}/review-clips")

        self.assertEqual(static_response.status_code, 200)
        self.assertIn("Project processing", static_response.text)
        self.assertEqual(single_response.status_code, 200)
        self.assertEqual(batch_response.status_code, 200)

    def test_manager_cli_defaults_remain_root_runtime_compatible(self):
        import manager

        with patch.object(sys, "argv", ["manager.py"]):
            args = manager.parse_args()
        self.assertIsNone(args.workspace_dir)
        self.assertFalse(args.analysis_only)

        workflow = manager.WorkflowManager(url=None)
        self.assertEqual(workflow.runtime_dir, workflow.script_dir)
        self.assertFalse(workflow.analysis_only)

        workspace = self.root / "workspace"
        workspace_workflow = manager.WorkflowManager(url=None, workspace_dir=str(workspace), analysis_only=True)
        self.assertEqual(workspace_workflow.runtime_dir, workspace.resolve())
        self.assertTrue(workspace_workflow.analysis_only)


if __name__ == "__main__":
    unittest.main()
