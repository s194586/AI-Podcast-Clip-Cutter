from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select

from apps.api.db.database import configure_database, init_database, session_scope
from apps.api.db.models import ClipEvaluation, Project
from apps.api.db.repositories import ClipRepository, ProjectRepository
from apps.api.main import app
from apps.api.services.clip_service import load_clips
from apps.review_agent.providers import (
    ReviewProviderCompatibilityError,
    ReviewProviderExtractionError,
    ReviewProviderQuotaError,
)
from apps.review_agent.schemas import GeminiBoundaryDecision
from apps.review_agent.service import ClipReviewCancelledError, ReviewAgentService


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


class LangGraphReleaseSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        requested_root = os.environ.get("LANGGRAPH_SMOKE_ROOT")
        if requested_root:
            self.root = Path(requested_root) / self._testMethodName
            self.root.mkdir(parents=True, exist_ok=False)
            self._temporary_directory = None
        else:
            self._temporary_directory = tempfile.TemporaryDirectory()
            self.root = Path(self._temporary_directory.name)
        self.db_path = self.root / "application.db"
        self.db_url = _sqlite_url(self.db_path)
        os.environ["PODCAST_CUTTER_DB_URL"] = self.db_url
        os.environ["PODCAST_CUTTER_PROJECT_ROOT"] = str(self.root)
        os.environ["CLIP_REVIEW_MODE"] = "gemini"
        os.environ["GEMINI_API_KEY"] = "mock-only-key"
        configure_database(self.db_url)
        init_database()
        self._write_transcript()

    def tearDown(self) -> None:
        configure_database("sqlite:///:memory:")
        for key in (
            "PODCAST_CUTTER_DB_URL",
            "PODCAST_CUTTER_PROJECT_ROOT",
            "CLIP_REVIEW_MODE",
            "GEMINI_API_KEY",
        ):
            os.environ.pop(key, None)
        if self._temporary_directory is not None:
            self._temporary_directory.cleanup()

    def _write_transcript(self) -> None:
        path = self.root / "transcripts" / "final_transcript.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "segments": [
                        {"start": 80.0, "end": 100.0, "text": "Nearby setup."},
                        {"start": 100.0, "end": 120.0, "text": "Candidate opening."},
                        {"start": 120.0, "end": 140.0, "text": "Candidate payoff."},
                        {"start": 140.0, "end": 155.0, "text": "Nearby continuation."},
                    ]
                }
            ),
            encoding="utf-8",
        )

    def _seed_project(self, clip_count: int = 1) -> int:
        with session_scope() as session:
            project = ProjectRepository(session).create(
                source_url="https://example.invalid/mock-source",
                title="Disposable LangGraph smoke",
                status="ready",
                transcript_path="transcripts/final_transcript.json",
            )
            for index in range(1, clip_count + 1):
                ClipRepository(session).create_from_dict(
                    project.id,
                    {
                        "id": f"clip_{index:03d}",
                        "index": index,
                        "ai_start": 100.0,
                        "ai_end": 140.0,
                        "edited_start": 101.0,
                        "edited_end": 139.0,
                        "min_start": 80.0,
                        "max_start": 120.0,
                        "min_end": 120.0,
                        "max_end": 160.0,
                        "summary": "Disposable candidate.",
                        "text": "Candidate opening and payoff.",
                        "status": "draft",
                        "render_status": "not_rendered",
                    },
                )
            return project.id

    @staticmethod
    def _valid_decision(context: dict) -> GeminiBoundaryDecision:
        pair = context["allowed_boundary_pairs"][0]
        return GeminiBoundaryDecision(
            decision="adjust_boundaries",
            selected_start_option_index=pair["start_option_index"],
            selected_end_option_index=pair["end_option_index"],
            reasoning_summary="Mocked semantic decision.",
            start_reason="Mocked complete start.",
            end_reason="Mocked complete end.",
        )

    def _counts(self, project_id: int) -> tuple[int, int, int]:
        with session_scope() as session:
            projects = len(list(session.scalars(select(Project)).all()))
            evaluations = len(list(session.scalars(select(ClipEvaluation)).all()))
        clips = len(load_clips(project_id=project_id, project_root=self.root))
        return projects, clips, evaluations

    def test_valid_first_response_persists_once_through_api(self):
        project_id = self._seed_project()
        self.assertEqual(self._counts(project_id), (1, 1, 0))
        calls = 0

        class Provider:
            provider = "gemini"
            model = "mock-gemini"

            def __init__(self, **_kwargs):
                pass

            def review(inner_self, context, **_kwargs):
                nonlocal calls
                calls += 1
                return self._valid_decision(context)

        with patch("apps.review_agent.service.GeminiBoundaryReviewer", Provider):
            with TestClient(app) as client:
                response = client.post(f"/projects/{project_id}/clips/clip_001/review")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(calls, 1)
        self.assertEqual(payload["raw_result"]["review_workflow"], "langgraph_boundary_review")
        self.assertEqual(payload["raw_result"]["review_workflow_route"], "applied")
        self.assertNotIn("transcript", json.dumps(payload).casefold())
        self.assertNotIn("prompt", json.dumps(payload).casefold())
        self.assertNotIn(str(self.root).casefold(), json.dumps(payload).casefold())
        clip = load_clips(project_id=project_id, project_root=self.root)[0]
        self.assertEqual((clip["ai_start"], clip["ai_end"]), (100.0, 140.0))
        self.assertIsNotNone(clip["reviewed_start"])
        self.assertIsNotNone(clip["reviewed_end"])
        self.assertEqual(self._counts(project_id), (1, 1, 1))

    def test_corrective_retry_persists_valid_second_response_once(self):
        project_id = self._seed_project()
        feedback: list[str | None] = []

        class Provider:
            provider = "gemini"
            model = "mock-gemini"

            def __init__(self, **_kwargs):
                pass

            def review(inner_self, context, corrective_message=None, **_kwargs):
                feedback.append(corrective_message)
                if len(feedback) == 1:
                    return GeminiBoundaryDecision(
                        decision="adjust_boundaries",
                        selected_start_option_index=999,
                        selected_end_option_index=999,
                        reasoning_summary="Invalid mocked pair.",
                        start_reason="Invalid.",
                        end_reason="Invalid.",
                    )
                return self._valid_decision(context)

        with patch("apps.review_agent.service.GeminiBoundaryReviewer", Provider):
            result = ReviewAgentService(project_root=self.root, mode="gemini").review_clip(
                project_id=project_id,
                clip_id="clip_001",
            )

        self.assertEqual(len(feedback), 2)
        self.assertIsNone(feedback[0])
        self.assertLess(len(feedback[1] or ""), 700)
        self.assertNotIn("Candidate opening", feedback[1] or "")
        self.assertNotIn(str(self.root), feedback[1] or "")
        self.assertTrue(result["retry_used"])
        self.assertEqual(result["provider_attempt_count"], 2)
        self.assertEqual(result["raw_result"]["review_workflow_route"], "applied")
        self.assertEqual(self._counts(project_id), (1, 1, 1))

    def test_two_invalid_responses_end_manual_and_preserve_boundaries(self):
        project_id = self._seed_project()
        calls = 0

        class Provider:
            provider = "gemini"
            model = "mock-gemini"

            def __init__(self, **_kwargs):
                pass

            def review(inner_self, _context, **_kwargs):
                nonlocal calls
                calls += 1
                return GeminiBoundaryDecision(
                    decision="adjust_boundaries",
                    selected_start_option_index=999,
                    selected_end_option_index=999,
                    reasoning_summary="Invalid mocked pair.",
                    start_reason="Invalid.",
                    end_reason="Invalid.",
                )

        with patch("apps.review_agent.service.GeminiBoundaryReviewer", Provider):
            result = ReviewAgentService(project_root=self.root, mode="gemini").review_clip(
                project_id=project_id,
                clip_id="clip_001",
            )

        clip = load_clips(project_id=project_id, project_root=self.root)[0]
        self.assertEqual(calls, 2)
        self.assertEqual(result["decision"], "manual_review")
        self.assertEqual(result["raw_result"]["review_workflow_route"], "manual_review")
        self.assertEqual((clip["ai_start"], clip["ai_end"]), (100.0, 140.0))
        self.assertEqual((clip["edited_start"], clip["edited_end"]), (101.0, 139.0))
        self.assertIsNone(clip["reviewed_start"])
        self.assertIsNone(clip["reviewed_end"])
        self.assertEqual(result["provider"], "gemini")

    def test_quota_failure_does_not_retry_or_change_boundaries(self):
        project_id = self._seed_project()
        calls = 0

        class Provider:
            provider = "gemini"
            model = "mock-gemini"

            def __init__(self, **_kwargs):
                pass

            def review(inner_self, _context, **_kwargs):
                nonlocal calls
                calls += 1
                raise ReviewProviderQuotaError("Mocked HTTP 429 quota failure.")

        with patch("apps.review_agent.service.GeminiBoundaryReviewer", Provider):
            result = ReviewAgentService(project_root=self.root, mode="gemini").review_clip(
                project_id=project_id,
                clip_id="clip_001",
            )

        clip = load_clips(project_id=project_id, project_root=self.root)[0]
        self.assertEqual(calls, 1)
        self.assertFalse(result["retry_used"])
        self.assertTrue(result["failed"])
        self.assertEqual(result["failure_category"], "quota")
        self.assertEqual(
            result["reasoning_summary"],
            "Gemini quota is temporarily unavailable. Retry this review later.",
        )
        self.assertEqual(result["raw_result"]["review_workflow_route"], "provider_failure")
        self.assertEqual((clip["edited_start"], clip["edited_end"]), (101.0, 139.0))

    def test_provider_compatibility_failure_uses_one_call_and_preserves_boundaries(self):
        project_id = self._seed_project()
        calls = 0

        class Provider:
            provider = "gemini"
            model = "mock-gemini"

            def __init__(self, **_kwargs):
                pass

            def review(inner_self, _context, **_kwargs):
                nonlocal calls
                calls += 1
                raise ReviewProviderCompatibilityError(
                    "Gemini provider compatibility error (HTTP 400)."
                )

        with patch("apps.review_agent.service.GeminiBoundaryReviewer", Provider):
            result = ReviewAgentService(project_root=self.root, mode="gemini").review_clip(
                project_id=project_id,
                clip_id="clip_001",
            )

        clip = load_clips(project_id=project_id, project_root=self.root)[0]
        self.assertEqual(calls, 1)
        self.assertFalse(result["retry_used"])
        self.assertEqual(result["provider_attempt_count"], 1)
        self.assertEqual(result["failure_category"], "provider_compatibility")
        self.assertEqual(
            result["reasoning_summary"],
            "Gemini could not complete the review because the provider integration requires "
            "attention. The existing clip boundaries were preserved.",
        )
        self.assertNotIn("legacy Interactions", str(result))
        self.assertEqual((clip["edited_start"], clip["edited_end"]), (101.0, 139.0))
        self.assertIsNone(clip["reviewed_start"])
        self.assertIsNone(clip["reviewed_end"])

    def test_missing_supported_model_output_does_not_trigger_corrective_retry(self):
        project_id = self._seed_project()
        calls = 0

        class Provider:
            provider = "gemini"
            model = "mock-gemini"

            def __init__(self, **_kwargs):
                pass

            def review(inner_self, _context, **_kwargs):
                nonlocal calls
                calls += 1
                raise ReviewProviderExtractionError(
                    "Gemini interaction did not contain supported model output."
                )

        with patch("apps.review_agent.service.GeminiBoundaryReviewer", Provider):
            result = ReviewAgentService(project_root=self.root, mode="gemini").review_clip(
                project_id=project_id,
                clip_id="clip_001",
            )

        clip = load_clips(project_id=project_id, project_root=self.root)[0]
        self.assertEqual(calls, 1)
        self.assertFalse(result["retry_used"])
        self.assertEqual(result["provider_attempt_count"], 1)
        self.assertEqual(result["failure_category"], "provider_output")
        self.assertEqual((clip["edited_start"], clip["edited_end"]), (101.0, 139.0))

    def test_cancellation_after_provider_prevents_persistence(self):
        project_id = self._seed_project()
        cancelled = False
        calls = 0

        class Provider:
            provider = "gemini"
            model = "mock-gemini"

            def __init__(self, **_kwargs):
                pass

            def review(inner_self, context, **_kwargs):
                nonlocal cancelled, calls
                calls += 1
                cancelled = True
                return self._valid_decision(context)

        with patch("apps.review_agent.service.GeminiBoundaryReviewer", Provider):
            with self.assertRaises(ClipReviewCancelledError):
                ReviewAgentService(project_root=self.root, mode="gemini").review_clip(
                    project_id=project_id,
                    clip_id="clip_001",
                    cancellation_check=lambda: cancelled,
                )

        self.assertEqual(calls, 1)
        self.assertEqual(self._counts(project_id), (1, 1, 0))
        clip = load_clips(project_id=project_id, project_root=self.root)[0]
        self.assertEqual((clip["edited_start"], clip["edited_end"]), (101.0, 139.0))

    def test_batch_isolates_three_graph_invocations_and_counts_outcomes(self):
        project_id = self._seed_project(clip_count=3)
        calls = {"clip_001": 0, "clip_002": 0, "clip_003": 0}
        progress: list[tuple[str, dict]] = []

        class Provider:
            provider = "gemini"
            model = "mock-gemini"

            def __init__(self, **_kwargs):
                pass

            def review(inner_self, context, **_kwargs):
                clip_id = context["clip_id"]
                calls[clip_id] += 1
                if clip_id == "clip_002":
                    return GeminiBoundaryDecision(
                        decision="adjust_boundaries",
                        selected_start_option_index=999,
                        selected_end_option_index=999,
                        reasoning_summary="Invalid mocked pair.",
                        start_reason="Invalid.",
                        end_reason="Invalid.",
                    )
                if clip_id == "clip_003":
                    raise ReviewProviderQuotaError("Mocked HTTP 429 quota failure.")
                return self._valid_decision(context)

        with patch("apps.review_agent.service.GeminiBoundaryReviewer", Provider):
            summary = ReviewAgentService(
                project_root=self.root,
                mode="gemini",
            ).review_project_clips(
                project_id=project_id,
                progress_callback=lambda event, metadata: progress.append((event, metadata)),
            )

        self.assertEqual(calls, {"clip_001": 1, "clip_002": 2, "clip_003": 1})
        self.assertEqual(summary["clip_count"], 3)
        self.assertEqual(summary["adjust_boundaries_count"], 1)
        self.assertEqual(summary["manual_review_count"], 2)
        self.assertEqual(summary["failed_count"], 2)
        self.assertEqual(len(summary["reviews"]), 3)
        self.assertEqual(self._counts(project_id), (1, 3, 3))
        self.assertEqual(len(progress), 6)
        self.assertEqual([event for event, _metadata in progress][::2], ["review_clip_started"] * 3)
        terminal_events = [event for event, _metadata in progress][1::2]
        self.assertEqual(
            terminal_events,
            ["review_clip_completed", "review_clip_failed", "review_clip_failed"],
        )


if __name__ == "__main__":
    unittest.main()
