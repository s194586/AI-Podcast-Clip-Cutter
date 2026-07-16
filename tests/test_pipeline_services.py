import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import select

import manager
from apps.api.db.database import configure_database, init_database, session_scope
from apps.api.db.models import Clip, Project
from apps.api.services import project_service
from apps.pipeline.config import PipelineConfig
from apps.pipeline.context import PipelineContext
from apps.pipeline.entrypoint import run_project_pipeline
from apps.pipeline.events import PipelineEvent, parse_pipeline_event, progress_for_stage
from apps.pipeline.exceptions import ReviewStageError, TranscriptionStageError
from apps.pipeline.profiles import legacy_cli_stages, project_pipeline_stages
from apps.pipeline.results import PipelineStageResult
from apps.pipeline.runner import PipelineRunner
from apps.pipeline.stages.import_candidates import ImportCandidatesStage
from apps.pipeline.stages.review_candidates import ReviewCandidatesStage


class RecordingStage:
    def __init__(self, stage, calls, *, failure=None):
        self.stage = stage
        self.calls = calls
        self.failure = failure

    def run(self, context):
        self.calls.append(self.stage)
        if self.failure is not None:
            raise self.failure
        return PipelineStageResult(
            stage=self.stage,
            success=True,
            message=f"{self.stage} complete",
        )


class PipelineRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.workspace = self.root / "workspace"
        self.context = PipelineContext.for_legacy_cli(
            source_url="https://example.com/watch",
            repository_root=self.root,
            workspace_path=self.workspace,
            analysis_only=True,
            config=PipelineConfig(ai_mode="local_only", subtitle_checker_mode="local_only"),
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_pipeline_context_accepts_explicit_legacy_workspace(self):
        self.assertEqual(self.context.workspace_path, self.workspace.resolve())
        self.assertEqual(self.context.transcript_file, self.workspace.resolve() / "transcripts" / "final_transcript.json")

    def test_project_context_rejects_nonisolated_workspace(self):
        with self.assertRaisesRegex(ValueError, "isolated workspace"):
            PipelineContext(
                project_id=7,
                source_url="https://example.com/watch",
                workspace_path=self.root / "wrong",
                repository_root=self.root,
            )

    def test_pipeline_context_repr_does_not_include_source_url(self):
        context = PipelineContext.for_legacy_cli(
            source_url="https://example.com/watch?token=source-secret",
            repository_root=self.root,
            workspace_path=self.workspace,
        )
        self.assertNotIn("source-secret", repr(context))
        self.assertIn("source_url_configured=True", repr(context))

    def test_runner_executes_stages_in_order(self):
        calls = []
        stages = [RecordingStage(name, calls) for name in ("downloading", "transcribing", "ready")]
        result = PipelineRunner(stages).run(self.context)
        self.assertTrue(result.success)
        self.assertEqual(calls, ["downloading", "transcribing", "ready"])

    def test_failed_stage_prevents_dependent_stages(self):
        calls = []
        stages = [
            RecordingStage("downloading", calls),
            RecordingStage("transcribing", calls, failure=TranscriptionStageError("offline failure")),
            RecordingStage("generating_candidates", calls),
        ]
        result = PipelineRunner(stages).run(self.context)
        self.assertFalse(result.success)
        self.assertEqual(result.failed_stage, "transcribing")
        self.assertEqual(calls, ["downloading", "transcribing"])

    def test_structured_events_are_emitted_in_lifecycle_order(self):
        events = []
        PipelineRunner([RecordingStage("downloading", [])], event_sinks=(events.append,)).run(self.context)
        self.assertEqual(
            [event.event for event in events],
            ["stage_started", "stage_progress", "stage_completed", "pipeline_completed"],
        )

    def test_failure_events_include_stage_then_pipeline_completion(self):
        events = []
        PipelineRunner(
            [RecordingStage("transcribing", [], failure=TranscriptionStageError("offline failure"))],
            event_sinks=(events.append,),
        ).run(self.context)
        self.assertEqual(events[-2].event, "stage_failed")
        self.assertEqual(events[-1].event, "pipeline_completed")
        self.assertFalse(events[-1].success)

    def test_stage_progress_is_coarse_and_stable(self):
        self.assertEqual(progress_for_stage("downloading"), 10.0)
        self.assertEqual(progress_for_stage("transcribing"), 30.0)
        self.assertEqual(progress_for_stage("reviewing_with_ai"), 85.0)
        self.assertIsNone(progress_for_stage("rendering"))

    def test_structured_event_round_trip_redacts_secrets(self):
        event = PipelineEvent(
            event="stage_failed",
            stage="reviewing_with_ai",
            message="GEMINI_API_KEY=super-secret",
            success=False,
            produced_artifacts=("token=artifact-secret",),
            metadata={"api_key": "super-secret", "nested": {"password": "hidden"}},
        )
        marker = event.to_marker()
        self.assertNotIn("super-secret", marker)
        self.assertNotIn("artifact-secret", marker)
        parsed = parse_pipeline_event(marker)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.metadata["api_key"], "<redacted>")

    def test_event_object_redacts_url_query_secrets_before_persistence(self):
        event = PipelineEvent(
            event="stage_failed",
            stage="downloading",
            message="Download failed for https://example.com/watch?token=url-secret&v=1",
            success=False,
        )
        self.assertNotIn("url-secret", event.message)

    def test_offline_success_smoke_has_all_project_stages(self):
        calls = []
        names = (
            "downloading",
            "transcribing",
            "validating_transcript",
            "generating_candidates",
            "importing_candidates",
            "reviewing_with_ai",
            "ready",
        )
        result = PipelineRunner([RecordingStage(name, calls) for name in names]).run(self.context)
        self.assertTrue(result.success)
        self.assertEqual(tuple(calls), names)

    def test_offline_failure_smoke_skips_later_project_stages(self):
        calls = []
        result = PipelineRunner(
            [
                RecordingStage("downloading", calls),
                RecordingStage("transcribing", calls, failure=TranscriptionStageError("mock whisper failure")),
                RecordingStage("validating_transcript", calls),
                RecordingStage("generating_candidates", calls),
            ]
        ).run(self.context)
        self.assertFalse(result.success)
        self.assertEqual(calls, ["downloading", "transcribing"])

    def test_offline_no_review_smoke_reaches_ready(self):
        calls = []
        names = (
            "downloading",
            "transcribing",
            "validating_transcript",
            "generating_candidates",
            "importing_candidates",
            "ready",
        )
        result = PipelineRunner([RecordingStage(name, calls) for name in names]).run(self.context)
        self.assertTrue(result.success)
        self.assertNotIn("reviewing_with_ai", calls)
        self.assertEqual(calls[-1], "ready")


class PipelineServiceDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_url = f"sqlite:///{(self.root / 'pipeline.db').as_posix()}"
        os.environ["PODCAST_CUTTER_DB_URL"] = self.db_url
        os.environ["PODCAST_CUTTER_PROJECT_ROOT"] = str(self.root)
        configure_database(self.db_url)
        init_database()
        project = project_service.create_project(
            source_url="https://example.com/watch",
            title="Pipeline service test",
            auto_review=True,
            project_root=self.root,
        )
        self.project_id = int(project["id"])
        self.workspace = self.root / "data" / "projects" / str(self.project_id) / "workspace"
        (self.workspace / "input").mkdir(parents=True, exist_ok=True)
        (self.workspace / "transcripts").mkdir(parents=True, exist_ok=True)
        (self.workspace / "input" / "source.mp4").write_bytes(b"offline media fixture")
        (self.workspace / "transcripts" / "final_transcript.json").write_text(
            json.dumps({"segments": [{"start": 10, "end": 50, "text": "Offline transcript."}]}),
            encoding="utf-8",
        )
        (self.workspace / "top_windows.json").write_text(
            json.dumps(
                {
                    "top_windows": [
                        {
                            "id": "clip_001",
                            "start": 12.0,
                            "end": 42.0,
                            "summary": "Offline candidate",
                            "text": "Offline transcript.",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        configure_database("sqlite:///:memory:")
        os.environ.pop("PODCAST_CUTTER_DB_URL", None)
        os.environ.pop("PODCAST_CUTTER_PROJECT_ROOT", None)
        self.tempdir.cleanup()

    def context(self, *, auto_review=True):
        return PipelineContext(
            project_id=self.project_id,
            source_url="https://example.com/watch",
            workspace_path=self.workspace,
            repository_root=self.root,
            auto_review=auto_review,
            analysis_only=True,
            config=PipelineConfig(ai_mode="local_only", subtitle_checker_mode="local_only"),
        )

    def test_candidate_import_targets_existing_project_and_initializes_boundaries(self):
        result = ImportCandidatesStage().run(self.context())
        self.assertTrue(result.success)
        with session_scope() as session:
            projects = list(session.scalars(select(Project)).all())
            clip = session.scalars(select(Clip)).one()
        self.assertEqual(len(projects), 1)
        self.assertEqual(clip.project_id, self.project_id)
        self.assertEqual(clip.ai_start, 12.0)
        self.assertEqual(clip.edited_start, 12.0)
        self.assertIsNone(clip.reviewed_start)

    def test_candidate_import_retry_is_idempotent(self):
        stage = ImportCandidatesStage()
        stage.run(self.context())
        stage.run(self.context())
        with session_scope() as session:
            self.assertEqual(len(list(session.scalars(select(Project)).all())), 1)
            self.assertEqual(len(list(session.scalars(select(Clip)).all())), 1)

    def test_candidate_import_retry_preserves_reviewed_boundaries(self):
        stage = ImportCandidatesStage()
        stage.run(self.context())
        with session_scope() as session:
            clip = session.scalars(select(Clip)).one()
            clip.reviewed_start = 13.0
            clip.reviewed_end = 41.0
            clip.edited_start = 13.0
            clip.edited_end = 41.0
            clip.boundary_source = "gemini"
        stage.run(self.context())
        with session_scope() as session:
            clip = session.scalars(select(Clip)).one()
        self.assertEqual((clip.reviewed_start, clip.reviewed_end), (13.0, 41.0))
        self.assertEqual((clip.edited_start, clip.edited_end), (13.0, 41.0))

    def test_auto_review_true_calls_review_agent_service_directly(self):
        ImportCandidatesStage().run(self.context())
        calls = []

        class FakeReviewService:
            def __init__(self, *, project_root):
                calls.append(("init", project_root))

            def review_project_clips(self, *, project_id, apply_safe_suggestions):
                calls.append(("review", project_id, apply_safe_suggestions))
                return {"provider": "gemini", "clip_count": 1, "failed_count": 0}

        with patch("apps.pipeline.stages.review_candidates.ReviewAgentService", FakeReviewService):
            result = ReviewCandidatesStage().run(self.context(auto_review=True))
        self.assertTrue(result.success)
        self.assertEqual(calls[-1], ("review", self.project_id, True))

    def test_auto_review_false_is_omitted_from_project_profile(self):
        context = self.context(auto_review=False)
        names = [stage.stage for stage in project_pipeline_stages(context)]
        self.assertNotIn("reviewing_with_ai", names)
        self.assertEqual(names[-1], "ready")

    def test_review_service_exception_is_controlled(self):
        class FailingReviewService:
            def __init__(self, *, project_root):
                pass

            def review_project_clips(self, **kwargs):
                raise RuntimeError("offline review failure")

        with patch("apps.pipeline.stages.review_candidates.ReviewAgentService", FailingReviewService):
            with self.assertRaises(ReviewStageError):
                ReviewCandidatesStage().run(self.context(auto_review=True))


class PipelineEntrypointCompatibilityTests(unittest.TestCase):
    def test_manager_default_cli_contract(self):
        args = manager.parse_args([])
        self.assertIsNone(args.url)
        self.assertIsNone(args.workspace_dir)
        self.assertFalse(args.analysis_only)
        self.assertEqual(args.transcription_backend, "faster_whisper")

    def test_manager_workspace_and_analysis_only_contract(self):
        args = manager.parse_args(["--workspace-dir", "work", "--analysis-only"])
        self.assertEqual(args.workspace_dir, "work")
        self.assertTrue(args.analysis_only)

    def test_legacy_analysis_only_profile_skips_rendering(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            workflow = manager.WorkflowManager(
                url=None,
                workspace_dir=str(root / "work"),
                analysis_only=True,
                ai_mode="local_only",
            )
            names = [stage.stage for stage in legacy_cli_stages(workflow.context)]
        self.assertNotIn("rendering", names)
        self.assertIn("generating_candidates", names)

    def test_dedicated_entrypoint_uses_pipeline_runner(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            context = PipelineContext(
                project_id=4,
                source_url="https://example.com/watch",
                workspace_path=root / "data" / "projects" / "4" / "workspace",
                repository_root=root,
                auto_review=False,
                analysis_only=True,
                config=PipelineConfig(ai_mode="local_only", subtitle_checker_mode="local_only"),
            )
            with patch("apps.pipeline.entrypoint.PipelineRunner") as runner_class:
                runner_class.return_value.run.return_value = SimpleNamespace(exit_code=0)
                exit_code = run_project_pipeline(context)
        self.assertEqual(exit_code, 0)
        runner_class.return_value.run.assert_called_once_with(context)

    def test_root_manager_help_is_operational_offline(self):
        result = subprocess.run(
            [sys.executable, "manager.py", "--help"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--workspace-dir", result.stdout)
        self.assertIn("--analysis-only", result.stdout)


if __name__ == "__main__":
    unittest.main()
