import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import select

from apps.api.db.database import configure_database, init_database, session_scope
from apps.api.db.models import ClipEvaluation, Project
from apps.api.db.repositories import ClipRepository, ProjectRepository
from apps.api.services import project_service
from apps.pipeline.config import PipelineConfig
from apps.pipeline.context import PipelineContext
from apps.pipeline.events import PipelineEvent
from apps.pipeline.exceptions import PipelineCancelled
from apps.pipeline.persistence import ProjectStateEventSink
from apps.pipeline.runner import PipelineRunner
from apps.pipeline.stages.ready import MarkProjectReadyStage
from apps.pipeline.stages.generate_candidates import GenerateCandidatesStage
from apps.pipeline.stages.prepare import PrepareWorkspaceStage
from apps.pipeline.stages.review_candidates import ReviewCandidatesStage
from apps.review_agent.config import ReviewConfigError, load_review_config
from apps.review_agent.providers import (
    GeminiBoundaryReviewer,
    ReviewProviderCancelledError,
    ReviewProviderCompatibilityError,
    ReviewProviderError,
    ReviewProviderOutputError,
    ReviewProviderQuotaError,
    ReviewProviderRequestCancelledError,
    ReviewProviderTimeoutError,
    _provider_error_from_exception,
    _run_bounded_process,
)
from apps.review_agent.schemas import GeminiBoundaryDecision
from apps.review_agent.service import (
    ClipReviewCancelledError,
    ReviewAgentService,
    ReviewBatchTimeoutError,
)


def _hanging_worker(send_connection, sleep_seconds):
    try:
        time.sleep(sleep_seconds)
    finally:
        send_connection.close()


def _aligned_decision(context):
    return GeminiBoundaryDecision(
        decision="render_ready",
        selected_start_option_index=context["current_aligned_start_option_index"],
        selected_end_option_index=context["current_aligned_end_option_index"],
        reasoning_summary="Offline aligned decision.",
        start_reason="Aligned start.",
        end_reason="Aligned end.",
        warnings=[],
    )


class GeminiProviderTimeoutTests(unittest.TestCase):
    def test_sdk_request_timeout_and_retry_limit_are_configured(self):
        captured = {}

        class FakeClient:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        with patch("google.genai.Client", FakeClient):
            from apps.review_agent.providers import _create_genai_client

            _create_genai_client("offline-placeholder", timeout_seconds=300)

        http_options = captured["http_options"]
        self.assertEqual(http_options.timeout, 300000)
        self.assertEqual(http_options.retry_options.attempts, 1)

    def test_child_process_receives_default_request_deadline(self):
        decision = {
            "decision": "render_ready",
            "selected_start_option_index": 1,
            "selected_end_option_index": 1,
            "reasoning_summary": "Offline decision.",
            "start_reason": "Offline start.",
            "end_reason": "Offline end.",
            "warnings": [],
        }
        with patch(
            "apps.review_agent.providers._run_bounded_process",
            return_value={"ok": True, "decision": decision},
        ) as bounded_process:
            reviewer = GeminiBoundaryReviewer(api_key="offline-placeholder")
            reviewer.review({})

        self.assertEqual(bounded_process.call_args.kwargs["timeout_seconds"], 300)

    def test_hanging_process_is_terminated_at_application_deadline(self):
        started = time.monotonic()
        with self.assertRaises(ReviewProviderTimeoutError):
            _run_bounded_process(
                _hanging_worker,
                (5.0,),
                timeout_seconds=0.2,
            )
        self.assertLess(time.monotonic() - started, 3.0)

    def test_http_499_is_a_controlled_upstream_cancellation(self):
        error = RuntimeError("request failed with HTTP 499; key=should-not-survive")
        controlled = _provider_error_from_exception(error)
        self.assertIsInstance(controlled, ReviewProviderRequestCancelledError)
        self.assertNotIn("should-not-survive", str(controlled))

    def test_http_429_is_a_controlled_quota_failure(self):
        error = RuntimeError("request failed with HTTP 429; key=should-not-survive")
        controlled = _provider_error_from_exception(error)
        self.assertIsInstance(controlled, ReviewProviderQuotaError)
        self.assertIn("Retry review later", str(controlled))
        self.assertNotIn("should-not-survive", str(controlled))

    def test_http_400_schema_incompatibility_is_sanitized_and_non_output_failure(self):
        error = RuntimeError(
            "HTTP 400 invalid_request: legacy Interactions API schema; "
            "prompt=private transcript; key=should-not-survive"
        )
        controlled = _provider_error_from_exception(error)
        self.assertIsInstance(controlled, ReviewProviderCompatibilityError)
        self.assertEqual(str(controlled), "Gemini provider compatibility error (HTTP 400).")


class ReviewTimeoutFlowTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_url = f"sqlite:///{(self.root / 'review-timeouts.db').as_posix()}"
        os.environ["PODCAST_CUTTER_DB_URL"] = self.db_url
        os.environ["PODCAST_CUTTER_PROJECT_ROOT"] = str(self.root)
        os.environ["CLIP_REVIEW_MODE"] = "local_stub"
        os.environ["GEMINI_REQUEST_TIMEOUT_SECONDS"] = "300"
        os.environ["GEMINI_BATCH_TIMEOUT_SECONDS"] = "1800"
        configure_database(self.db_url)
        init_database()
        project = project_service.create_project(
            source_url="https://example.com/offline",
            title="Five clip timeout fixture",
            auto_review=True,
            project_root=self.root,
        )
        self.project_id = int(project["id"])
        self.workspace = self.root / "data" / "projects" / str(self.project_id) / "workspace"
        self._seed_transcript_and_clips()

    def tearDown(self):
        configure_database("sqlite:///:memory:")
        for key in (
            "PODCAST_CUTTER_DB_URL",
            "PODCAST_CUTTER_PROJECT_ROOT",
            "CLIP_REVIEW_MODE",
            "GEMINI_API_KEY",
            "GEMINI_REQUEST_TIMEOUT_SECONDS",
            "GEMINI_BATCH_TIMEOUT_SECONDS",
        ):
            os.environ.pop(key, None)
        self.tempdir.cleanup()

    def _seed_transcript_and_clips(self):
        transcript_path = self.workspace / "transcripts" / "final_transcript.json"
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        segments = [
            {
                "start": float(index * 10),
                "end": float((index + 1) * 10),
                "text": f"Offline segment {index} forms a complete thought.",
            }
            for index in range(30)
        ]
        transcript_path.write_text(json.dumps({"segments": segments}), encoding="utf-8")
        with session_scope() as session:
            project = ProjectRepository(session).get(self.project_id)
            project.transcript_path = str(transcript_path.relative_to(self.root)).replace("\\", "/")
            ProjectRepository(session).touch(project)
            repository = ClipRepository(session)
            for index in range(1, 6):
                start = float(20 + (index - 1) * 50)
                end = start + 30.0
                repository.create_from_dict(
                    self.project_id,
                    {
                        "id": f"clip_{index:03d}",
                        "index": index,
                        "ai_start": start,
                        "ai_end": end,
                        "reviewed_start": None,
                        "reviewed_end": None,
                        "edited_start": start,
                        "edited_end": end,
                        "boundary_source": "heuristic",
                        "min_start": max(0.0, start - 10.0),
                        "max_start": start + 10.0,
                        "min_end": end - 10.0,
                        "max_end": end + 10.0,
                        "summary": f"Candidate {index}",
                        "text": f"Offline candidate {index}",
                        "status": "draft",
                    },
                )

    def context(self):
        return PipelineContext(
            project_id=self.project_id,
            source_url="https://example.com/offline",
            workspace_path=self.workspace,
            repository_root=self.root,
            auto_review=True,
            analysis_only=True,
            config=PipelineConfig(ai_mode="local_only", subtitle_checker_mode="local_only"),
        )

    def test_timeout_configuration_defaults_are_five_and_thirty_minutes(self):
        os.environ.pop("GEMINI_REQUEST_TIMEOUT_SECONDS", None)
        os.environ.pop("GEMINI_BATCH_TIMEOUT_SECONDS", None)
        config = load_review_config(project_root=self.root, require_api_key=False)
        self.assertEqual(config.request_timeout_seconds, 300)
        self.assertEqual(config.batch_timeout_seconds, 1800)
        self.assertIs(type(config.request_timeout_seconds), int)
        self.assertIs(type(config.batch_timeout_seconds), int)

    def test_timeout_configuration_accepts_explicit_integer_overrides(self):
        os.environ["GEMINI_REQUEST_TIMEOUT_SECONDS"] = "11"
        os.environ["GEMINI_BATCH_TIMEOUT_SECONDS"] = "44"
        config = load_review_config(project_root=self.root, require_api_key=False)
        self.assertEqual(config.request_timeout_seconds, 11)
        self.assertEqual(config.batch_timeout_seconds, 44)
        self.assertNotIn("api_key='", repr(config))

    def test_timeout_configuration_rejects_non_positive_or_non_integer_values(self):
        invalid_values = ("0", "-1", "1.5", "not-a-number")
        for name in ("GEMINI_REQUEST_TIMEOUT_SECONDS", "GEMINI_BATCH_TIMEOUT_SECONDS"):
            for value in invalid_values:
                with self.subTest(name=name, value=value):
                    os.environ["GEMINI_REQUEST_TIMEOUT_SECONDS"] = "300"
                    os.environ["GEMINI_BATCH_TIMEOUT_SECONDS"] = "1800"
                    os.environ[name] = value
                    with self.assertRaisesRegex(ReviewConfigError, name):
                        load_review_config(project_root=self.root, require_api_key=False)

    def test_batch_timeout_cannot_be_shorter_than_request_timeout(self):
        os.environ["GEMINI_REQUEST_TIMEOUT_SECONDS"] = "301"
        os.environ["GEMINI_BATCH_TIMEOUT_SECONDS"] = "300"
        with self.assertRaisesRegex(
            ReviewConfigError,
            "GEMINI_BATCH_TIMEOUT_SECONDS must be greater than or equal to GEMINI_REQUEST_TIMEOUT_SECONDS",
        ):
            load_review_config(project_root=self.root, require_api_key=False)

    def test_corrective_retry_uses_a_bounded_timeout_on_both_attempts(self):
        os.environ["CLIP_REVIEW_MODE"] = "gemini"
        os.environ["GEMINI_API_KEY"] = "offline-placeholder"
        calls = []

        class RetryReviewer:
            provider = "gemini"

            def __init__(self, *, api_key, model, request_timeout_seconds):
                self.model = model

            def review(self, context, corrective_message=None, *, timeout_seconds=None, cancellation_check=None):
                calls.append(timeout_seconds)
                if len(calls) == 1:
                    raise ReviewProviderOutputError("offline invalid structured output")
                return _aligned_decision(context)

        with patch("apps.review_agent.service.GeminiBoundaryReviewer", RetryReviewer):
            result = ReviewAgentService(project_root=self.root, mode="gemini").review_clip(
                project_id=self.project_id,
                clip_id="clip_001",
            )

        self.assertFalse(result["failed"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls, [300, 300])

    def test_per_clip_timeout_is_saved_as_manual_review(self):
        os.environ["CLIP_REVIEW_MODE"] = "gemini"
        os.environ["GEMINI_API_KEY"] = "offline-placeholder"
        calls = []

        class TimeoutReviewer:
            provider = "gemini"

            def __init__(self, *, api_key, model, request_timeout_seconds):
                self.model = model

            def review(self, context, **kwargs):
                calls.append(context["clip_id"])
                raise ReviewProviderTimeoutError("offline provider timeout")

        with patch("apps.review_agent.service.GeminiBoundaryReviewer", TimeoutReviewer):
            result = ReviewAgentService(project_root=self.root, mode="gemini").review_clip(
                project_id=self.project_id,
                clip_id="clip_001",
            )

        self.assertTrue(result["failed"])
        self.assertEqual(result["decision"], "manual_review")
        self.assertEqual(result["provider"], "gemini")
        self.assertNotEqual(result["model"], "local_stub")
        self.assertFalse(result["retry_used"])
        self.assertEqual(result["provider_attempt_count"], 1)
        self.assertEqual(calls, ["clip_001"])
        with session_scope() as session:
            clip = ClipRepository(session).get_by_external_id(self.project_id, "clip_001")
        self.assertIsNone(clip.reviewed_start)
        self.assertEqual(clip.edited_start, clip.ai_start)

    def test_transport_auth_and_quota_failures_do_not_use_corrective_retry(self):
        os.environ["CLIP_REVIEW_MODE"] = "gemini"
        os.environ["GEMINI_API_KEY"] = "offline-placeholder"
        failures = (
            ReviewProviderTimeoutError("offline provider timeout"),
            ReviewProviderRequestCancelledError("offline HTTP 499"),
            ReviewProviderError("offline HTTP 504"),
            ReviewProviderError("offline invalid credentials"),
            ReviewProviderQuotaError("offline HTTP 429 quota exceeded"),
        )

        for index, failure in enumerate(failures, start=1):
            calls = []

            class FailingReviewer:
                provider = "gemini"

                def __init__(self, *, api_key, model, request_timeout_seconds):
                    self.model = model

                def review(self, context, **kwargs):
                    calls.append(context["clip_id"])
                    raise failure

            clip_id = f"clip_{index:03d}"
            with self.subTest(failure=str(failure)):
                with patch("apps.review_agent.service.GeminiBoundaryReviewer", FailingReviewer):
                    result = ReviewAgentService(project_root=self.root, mode="gemini").review_clip(
                        project_id=self.project_id,
                        clip_id=clip_id,
                    )
                self.assertTrue(result["failed"])
                self.assertEqual(result["decision"], "manual_review")
                self.assertEqual(result["provider"], "gemini")
                self.assertNotEqual(result["model"], "local_stub")
                self.assertFalse(result["retry_used"])
                self.assertEqual(result["provider_attempt_count"], 1)
                self.assertEqual(calls, [clip_id])

        cancellation_calls = []

        class CancelledReviewer:
            provider = "gemini"

            def __init__(self, *, api_key, model, request_timeout_seconds):
                self.model = model

            def review(self, context, **kwargs):
                cancellation_calls.append(context["clip_id"])
                raise ReviewProviderCancelledError("offline cancellation")

        with patch("apps.review_agent.service.GeminiBoundaryReviewer", CancelledReviewer):
            with self.assertRaises(ClipReviewCancelledError):
                ReviewAgentService(project_root=self.root, mode="gemini").review_clip(
                    project_id=self.project_id,
                    clip_id="clip_001",
                )
        self.assertEqual(cancellation_calls, ["clip_001"])

    def test_one_timeout_does_not_block_later_clips(self):
        os.environ["CLIP_REVIEW_MODE"] = "gemini"
        os.environ["GEMINI_API_KEY"] = "offline-placeholder"
        calls = []

        class PartialTimeoutReviewer:
            provider = "gemini"

            def __init__(self, *, api_key, model, request_timeout_seconds):
                self.model = model

            def review(self, context, **kwargs):
                calls.append(context["clip_id"])
                if context["clip_id"] == "clip_002":
                    raise ReviewProviderTimeoutError("offline provider timeout")
                return _aligned_decision(context)

        with patch("apps.review_agent.service.GeminiBoundaryReviewer", PartialTimeoutReviewer):
            summary = ReviewAgentService(project_root=self.root, mode="gemini").review_project_clips(
                project_id=self.project_id,
            )

        self.assertEqual(calls, [f"clip_{index:03d}" for index in range(1, 6)])
        self.assertEqual(summary["failed_count"], 1)
        self.assertEqual(summary["success_count"], 4)

    def test_partial_timeout_stage_completes_and_project_becomes_ready(self):
        os.environ["CLIP_REVIEW_MODE"] = "gemini"
        os.environ["GEMINI_API_KEY"] = "offline-placeholder"

        class PartialTimeoutReviewer:
            provider = "gemini"

            def __init__(self, *, api_key, model, request_timeout_seconds):
                self.model = model

            def review(self, context, **kwargs):
                if context["clip_id"] == "clip_002":
                    raise ReviewProviderTimeoutError("offline provider timeout")
                return _aligned_decision(context)

        context = self.context()
        events = []
        with patch("apps.review_agent.service.GeminiBoundaryReviewer", PartialTimeoutReviewer):
            result = PipelineRunner(
                [ReviewCandidatesStage(review_mode="gemini"), MarkProjectReadyStage()],
                event_sinks=(events.append, ProjectStateEventSink(context)),
            ).run(context)

        self.assertTrue(result.success)
        self.assertIn("review_clip_failed", [event.event for event in events])
        self.assertEqual(events[-1].event, "pipeline_completed")
        with session_scope() as session:
            project = session.get(Project, self.project_id)
            evaluations = list(session.scalars(select(ClipEvaluation)).all())
        self.assertEqual(project.status, "ready")
        self.assertEqual(len(evaluations), 5)
        self.assertEqual(sum(bool((item.raw_result_json or {}).get("failed")) for item in evaluations), 1)

    def test_every_clip_timeout_fails_the_review_stage(self):
        os.environ["CLIP_REVIEW_MODE"] = "gemini"
        os.environ["GEMINI_API_KEY"] = "offline-placeholder"

        class TimeoutReviewer:
            provider = "gemini"

            def __init__(self, *, api_key, model, request_timeout_seconds):
                self.model = model

            def review(self, context, **kwargs):
                raise ReviewProviderTimeoutError("offline provider timeout")

        events = []
        with patch("apps.review_agent.service.GeminiBoundaryReviewer", TimeoutReviewer):
            result = PipelineRunner(
                [ReviewCandidatesStage(review_mode="gemini")],
                event_sinks=(events.append,),
            ).run(self.context())

        self.assertFalse(result.success)
        self.assertEqual(events[-2].event, "stage_failed")
        self.assertIn("every clip", events[-2].message)

    def test_batch_timeout_terminates_review(self):
        os.environ["CLIP_REVIEW_MODE"] = "gemini"
        os.environ["GEMINI_API_KEY"] = "offline-placeholder"
        os.environ["GEMINI_REQUEST_TIMEOUT_SECONDS"] = "1"
        os.environ["GEMINI_BATCH_TIMEOUT_SECONDS"] = "1"
        clock = iter((0.0, 0.0, 2.0))

        with patch(
            "apps.review_agent.service.time",
            SimpleNamespace(monotonic=lambda: next(clock)),
        ):
            with self.assertRaises(ReviewBatchTimeoutError):
                ReviewAgentService(project_root=self.root, mode="gemini").review_project_clips(
                    project_id=self.project_id,
                )

    def test_batch_timeout_emits_failure_terminal_and_persists_failed_project(self):
        os.environ["CLIP_REVIEW_MODE"] = "gemini"
        os.environ["GEMINI_API_KEY"] = "offline-placeholder"
        os.environ["GEMINI_REQUEST_TIMEOUT_SECONDS"] = "1"
        os.environ["GEMINI_BATCH_TIMEOUT_SECONDS"] = "1"
        clock = iter((0.0, 0.0, 2.0))

        events = []
        context = self.context()
        with patch(
            "apps.review_agent.service.time",
            SimpleNamespace(monotonic=lambda: next(clock)),
        ):
            result = PipelineRunner(
                [ReviewCandidatesStage(review_mode="gemini")],
                event_sinks=(events.append, ProjectStateEventSink(context)),
            ).run(context)

        self.assertFalse(result.success)
        self.assertEqual([event.event for event in events[-2:]], ["stage_failed", "pipeline_completed"])
        with session_scope() as session:
            project = session.get(Project, self.project_id)
        self.assertEqual(project.status, "failed")

    def test_review_progress_and_five_clip_percentages_are_monotonic(self):
        events = []
        context = self.context()
        result = PipelineRunner(
            [ReviewCandidatesStage(review_mode="local_stub"), MarkProjectReadyStage()],
            event_sinks=(events.append, ProjectStateEventSink(context)),
        ).run(context)

        self.assertTrue(result.success)
        terminal = [
            event.progress_percent
            for event in events
            if event.event in {"review_clip_completed", "review_clip_manual", "review_clip_failed"}
        ]
        self.assertEqual(terminal, [87.0, 89.0, 91.0, 93.0, 95.0])
        all_progress = [
            event.progress_percent
            for event in events
            if event.progress_percent is not None and event.stage in {"reviewing_with_ai", "ready"}
        ]
        self.assertEqual(all_progress, sorted(all_progress))
        self.assertEqual(
            sorted(set(all_progress)),
            [85.0, 87.0, 89.0, 91.0, 93.0, 95.0, 100.0],
        )
        self.assertEqual(events[-1].event, "pipeline_completed")

    def test_explicit_cancellation_stops_before_next_clip_and_emits_terminal_event(self):
        events = []
        context = self.context()

        def observe(event: PipelineEvent):
            events.append(event)
            if event.event == "review_clip_completed" and event.metadata.get("index") == 1:
                context.cancellation.cancel()

        result = PipelineRunner(
            [ReviewCandidatesStage(review_mode="local_stub"), MarkProjectReadyStage()],
            event_sinks=(observe, ProjectStateEventSink(context)),
        ).run(context)

        self.assertFalse(result.success)
        self.assertEqual(result.exit_code, 130)
        self.assertEqual(events[-1].event, "pipeline_cancelled")
        with session_scope() as session:
            evaluations = list(session.scalars(select(ClipEvaluation)).all())
            project = session.get(Project, self.project_id)
        self.assertEqual(len(evaluations), 1)
        self.assertEqual(project.status, "cancelled")

    def test_late_completion_cannot_mark_cancelled_project_ready(self):
        context = self.context()
        with session_scope() as session:
            project = ProjectRepository(session).get(self.project_id)
            ProjectRepository(session).update_flow_state(
                project,
                status="cancelled",
                current_stage="cancelled",
                progress_percent=89.0,
            )

        with self.assertRaises(PipelineCancelled):
            MarkProjectReadyStage().run(context)
        ProjectStateEventSink(context)(
            PipelineEvent(
                event="stage_completed",
                stage="ready",
                message="Late ready response",
                progress_percent=100.0,
                success=True,
            )
        )
        with session_scope() as session:
            project = session.get(Project, self.project_id)
        self.assertEqual(project.status, "cancelled")
        self.assertEqual(project.progress_percent, 89.0)

    def test_cancelled_project_retry_preserves_and_reuses_completed_artifacts(self):
        context = self.context()
        context.heatmap_file.write_text(json.dumps([{"start_time": 0, "value": 1}]), encoding="utf-8")
        context.subtitle_report_file.write_text(json.dumps({"summary": {"status": "pass"}}), encoding="utf-8")
        context.content_profile_file.write_text(json.dumps({"content_type": "podcast"}), encoding="utf-8")
        context.cutting_log_file.write_text(json.dumps({"ai_mode": "local_only"}), encoding="utf-8")
        context.candidate_file.write_text(
            json.dumps([{"id": "clip_001", "start": 20, "end": 50}]),
            encoding="utf-8",
        )
        future = time.time() + 1
        os.utime(context.candidate_file, (future, future))

        with patch("apps.pipeline.stages.prepare.shutil.which", return_value="offline-tool"):
            PrepareWorkspaceStage().run(context)
        result = GenerateCandidatesStage().run(context)

        self.assertTrue(context.subtitle_report_file.exists())
        self.assertTrue(context.candidate_file.exists())
        self.assertTrue(result.metadata["reused"])

    def test_retry_skips_completed_reviews_and_preserves_user_boundaries(self):
        service = ReviewAgentService(project_root=self.root, mode="local_stub")
        first = service.review_project_clips(project_id=self.project_id)
        self.assertEqual(first["success_count"], 5)
        with session_scope() as session:
            clip = ClipRepository(session).get_by_external_id(self.project_id, "clip_001")
            clip.edited_start += 1.0
            clip.boundary_source = "user"
            ClipRepository(session).touch(clip)
        with session_scope() as session:
            evaluation_count = len(list(session.scalars(select(ClipEvaluation)).all()))

        retried = service.review_project_clips(project_id=self.project_id, skip_completed=True)

        with session_scope() as session:
            clip = ClipRepository(session).get_by_external_id(self.project_id, "clip_001")
            retry_evaluation_count = len(list(session.scalars(select(ClipEvaluation)).all()))
        self.assertEqual(retried["success_count"], 5)
        self.assertEqual(retry_evaluation_count, evaluation_count)
        self.assertEqual(clip.boundary_source, "user")

    def test_project_status_reports_persisted_review_clip_progress(self):
        with session_scope() as session:
            project = ProjectRepository(session).get(self.project_id)
            ProjectRepository(session).update_flow_state(
                project,
                status="running",
                current_stage="reviewing_with_ai",
                progress_percent=91.0,
            )
        status = project_service.get_project_status(self.project_id)
        self.assertEqual(status["message"], "Reviewing clip boundaries (3 of 5 complete)")

    def test_review_events_do_not_contain_prompt_or_credentials(self):
        events = []
        context = self.context()
        PipelineRunner(
            [ReviewCandidatesStage(review_mode="local_stub")],
            event_sinks=(events.append,),
        ).run(context)
        serialized = "\n".join(event.to_marker() for event in events)
        self.assertNotIn("Offline segment", serialized)
        self.assertNotIn("offline-placeholder", serialized)
        self.assertNotIn("context_before", serialized)


if __name__ == "__main__":
    unittest.main()
