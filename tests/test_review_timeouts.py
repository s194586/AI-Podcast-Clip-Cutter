import json
import os
import ssl
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
    ReviewProviderCredentialError,
    ReviewProviderError,
    ReviewProviderOutputError,
    ReviewProviderQuotaError,
    ReviewProviderRequestCancelledError,
    ReviewProviderTimeoutError,
    _provider_error_from_exception,
    _gemini_request_worker,
    _run_gemini_request_in_process,
    _run_bounded_process,
    _create_genai_client,
    preflight_gemini_credentials,
)
from apps.review_agent.schemas import GeminiBoundaryDecision
from apps.review_agent.service import (
    ClipReviewCancelledError,
    ClipReviewConfigurationError,
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
        review_response_contract_version=2,
        decision="render_ready",
        start_segment_id=context["current_aligned_start_segment_id"],
        end_segment_id=context["current_aligned_end_segment_id"],
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
            _create_genai_client("offline-placeholder", timeout_seconds=300)

        http_options = captured["http_options"]
        self.assertEqual(http_options.timeout, 300000)
        self.assertEqual(http_options.retry_options.attempts, 1)
        self.assertIsInstance(http_options.client_args["verify"], ssl.SSLContext)

    def test_spawned_worker_uses_the_shared_client_factory(self):
        class CapturingConnection:
            def __init__(self):
                self.messages = []

            def send(self, payload):
                self.messages.append(payload)

            def close(self):
                pass

        decision = GeminiBoundaryDecision(
            review_response_contract_version=2,
            decision="render_ready",
            start_segment_id="seg_v1_start",
            end_segment_id="seg_v1_end",
            reasoning_summary="Offline decision.",
            start_reason="Offline start.",
            end_reason="Offline end.",
            warnings=[],
        )
        connection = CapturingConnection()
        with patch(
            "apps.review_agent.providers._create_genai_client", return_value=object()
        ) as create_client, patch(
            "apps.review_agent.providers._request_structured_response", return_value=object()
        ), patch("apps.review_agent.providers._parse_boundary_decision", return_value=decision):
            _gemini_request_worker(connection, "offline-placeholder", "gemini-test", "prompt", 300)

        create_client.assert_called_once_with("offline-placeholder", timeout_seconds=300)
        self.assertTrue(connection.messages[0]["ok"])

    def test_worker_serializes_only_safe_provider_failure_diagnostics(self):
        class CapturingConnection:
            def __init__(self):
                self.messages = []

            def send(self, payload):
                self.messages.append(payload)

            def close(self):
                pass

        class FakeTransportError(RuntimeError):
            status_code = 503

            def __str__(self):
                return (
                    "upstream failed; Bearer secret-bearer; api_key=secret-api-key; "
                    "https://example.invalid/v1?key=secret-url-key; response body=private payload"
                )

        connection = CapturingConnection()
        with patch(
            "apps.review_agent.providers._create_genai_client",
            side_effect=FakeTransportError(),
        ):
            _gemini_request_worker(connection, "offline-placeholder", "gemini-test", "prompt", 300)

        payload = connection.messages[0]
        diagnostics = payload["diagnostics"]
        serialized = json.dumps(payload)
        self.assertEqual(set(payload), {"ok", "diagnostics"})
        self.assertFalse(payload["ok"])
        self.assertTrue(diagnostics["original_error_type"].endswith(".FakeTransportError"))
        self.assertEqual(diagnostics["mapped_error_type"], "ReviewProviderError")
        self.assertEqual(diagnostics["http_status"], 503)
        self.assertNotIn("secret-bearer", serialized)
        self.assertNotIn("secret-api-key", serialized)
        self.assertNotIn("secret-url-key", serialized)
        self.assertNotIn("private payload", serialized)

    def test_parent_restores_provider_error_category_and_diagnostics(self):
        diagnostics = {
            "category": "ReviewProviderTimeoutError",
            "mapped_error_type": "ReviewProviderTimeoutError",
            "original_error_type": "ReadTimeout",
            "cause_type": "ConnectError",
            "safe_message": "timed out while connecting to <redacted-url>",
        }
        with patch(
            "apps.review_agent.providers._run_bounded_process",
            return_value={"ok": False, "diagnostics": diagnostics},
        ):
            with self.assertRaises(ReviewProviderTimeoutError) as raised:
                _run_gemini_request_in_process(
                    api_key="offline-placeholder",
                    model="gemini-test",
                    prompt="prompt",
                    timeout_seconds=300,
                    cancellation_check=None,
                )

        self.assertEqual(str(raised.exception), diagnostics["safe_message"])
        self.assertEqual(raised.exception.diagnostics, diagnostics)

    def test_final_manual_review_keeps_safe_provider_detail(self):
        diagnostics = {
            "category": "ReviewProviderError",
            "mapped_error_type": "ReviewProviderError",
            "original_error_type": "ReadTimeout",
            "safe_message": (
                "ReadTimeout: Bearer secret-bearer; api_key=secret-api-key; "
                "https://example.invalid/v1?key=secret-url-key; response body=private payload"
            ),
        }
        result = ReviewAgentService(project_root=Path.cwd())._failed_result(
            project_id=1,
            clip={"id": "clip_001"},
            context={},
            provider="gemini",
            model="unit",
            warning="Gemini provider request failed.",
            apply_safe_suggestions=True,
            failure_category="provider",
            debug_metadata={"provider_failure_diagnostics": diagnostics},
        )

        serialized = json.dumps(result)
        self.assertEqual(result["decision"], "manual_review")
        self.assertIn("ReadTimeout", result["failure_reason"])
        self.assertEqual(result["warnings"], [result["failure_reason"]])
        self.assertEqual(
            result["raw_result"]["provider_failure_diagnostics"]["original_error_type"],
            "ReadTimeout",
        )
        self.assertNotIn("secret", serialized.casefold())
        self.assertNotIn("private payload", serialized)

    def test_credential_preflight_uses_one_short_non_generating_request(self):
        captured = {}

        class FakeModels:
            def list(self):
                return iter(())

        class FakeClient:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                self.models = FakeModels()

            def close(self):
                pass

        with patch("google.genai.Client", FakeClient):
            self.assertTrue(preflight_gemini_credentials(api_key="offline-placeholder"))

        http_options = captured["http_options"]
        self.assertEqual(http_options.timeout, 10000)
        self.assertEqual(http_options.retry_options.attempts, 1)

    def test_child_process_receives_default_request_deadline(self):
        decision = {
            "review_response_contract_version": 2,
            "decision": "render_ready",
            "start_segment_id": "seg_v1_start",
            "end_segment_id": "seg_v1_end",
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

    def test_http_401_is_a_sanitized_credential_configuration_failure(self):
        error = RuntimeError("request failed with HTTP 401; api_key=should-not-survive")
        controlled = _provider_error_from_exception(error)
        self.assertIsInstance(controlled, ReviewProviderCredentialError)
        self.assertIn("GEMINI_API_KEY", str(controlled))
        self.assertNotIn("should-not-survive", str(controlled))

    def test_http_403_and_explicit_invalid_key_400_are_credential_failures(self):
        errors = (
            RuntimeError("request failed with HTTP 403; api_key=should-not-survive"),
            RuntimeError("HTTP 400: API key not valid; api_key=should-not-survive"),
        )
        for error in errors:
            with self.subTest(error=str(error)):
                controlled = _provider_error_from_exception(error)
                self.assertIsInstance(controlled, ReviewProviderCredentialError)
                self.assertNotIn("should-not-survive", str(controlled))

    def test_preflight_allows_transient_failures_without_creating_local_stub(self):
        failures = (
            ReviewProviderTimeoutError("offline timeout"),
            ReviewProviderQuotaError("offline HTTP 429"),
            ReviewProviderError("offline HTTP 503"),
        )

        for failure in failures:
            class FakeModels:
                def list(self):
                    raise failure

            class FakeClient:
                models = FakeModels()

                def close(self):
                    pass

            with self.subTest(failure=str(failure)), patch(
                "apps.review_agent.providers.LocalStubBoundaryReviewer"
            ) as local_stub:
                self.assertFalse(
                    preflight_gemini_credentials(
                        api_key="offline-placeholder",
                        client_factory=lambda _api_key: FakeClient(),
                    )
                )
                local_stub.assert_not_called()

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

    def test_rejected_gemini_credentials_raise_configuration_error_without_local_stub(self):
        os.environ["CLIP_REVIEW_MODE"] = "gemini"
        os.environ["GEMINI_API_KEY"] = "invalid-placeholder"
        calls = []

        class CredentialReviewer:
            provider = "gemini"

            def __init__(self, *, api_key, model, request_timeout_seconds):
                self.model = model

            def review(self, context, **kwargs):
                calls.append(context["clip_id"])
                raise ReviewProviderCredentialError("Gemini rejected credentials")

        with patch("apps.review_agent.service.GeminiBoundaryReviewer", CredentialReviewer), patch(
            "apps.review_agent.service.LocalStubBoundaryReviewer"
        ) as local_stub:
            with self.assertRaisesRegex(ClipReviewConfigurationError, "GEMINI_API_KEY"):
                ReviewAgentService(project_root=self.root, mode="gemini").review_clip(
                    project_id=self.project_id,
                    clip_id="clip_001",
                )

        self.assertEqual(calls, ["clip_001"])
        local_stub.assert_not_called()

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

    def test_cancelled_project_retry_regenerates_candidates_from_peaks(self):
        context = self.context()
        context.subtitle_report_file.write_text(json.dumps({"summary": {"status": "pass"}}), encoding="utf-8")
        context.heatmap_peaks_file.write_text(
            json.dumps({
                "schema_version": 1,
                "source": "youtube_most_replayed",
                "algorithm": "offline_peak_detector",
                "algorithm_version": 1,
                "video_id": "offline-video",
                "duration_seconds": 120.0,
                "peaks": [],
            }),
            encoding="utf-8",
        )

        with patch("apps.pipeline.stages.prepare.shutil.which", return_value="offline-tool"):
            PrepareWorkspaceStage().run(context)
        result = GenerateCandidatesStage().run(context)

        self.assertTrue(context.subtitle_report_file.exists())
        self.assertTrue(context.candidate_file.exists())
        self.assertTrue(context.candidate_windows_file.exists())
        self.assertEqual(result.metadata["candidate_count"], 0)

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
