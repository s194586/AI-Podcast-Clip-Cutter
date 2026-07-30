import importlib.util
import json
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from apps.api.db.database import configure_database, init_database, session_scope
from apps.api.db.models import ClipEvaluation
from apps.api.db.repositories import ClipRepository, JobRepository, ProjectRepository
from apps.api.main import app
from apps.api.services import clip_service
from apps.review_agent.context import (
    build_clip_transcript_context,
    build_clip_transcript_context_from_segments,
)
from apps.review_agent.providers import (
    GeminiBoundaryReviewer,
    ReviewProviderCompatibilityError,
    ReviewProviderExtractionError,
    ReviewProviderError,
    ReviewProviderOutputError,
    build_compact_review_request,
    build_gemini_prompt,
)
from apps.review_agent.schemas import GeminiBoundaryDecision
from apps.review_agent.service import BoundaryOptionSelectionError, ReviewAgentService
from apps.review_agent.tools import check_sensitive_patterns, evaluation_to_dict, save_evaluation
from transcription.segment_identity import canonical_segment_id


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _option_index_for_segment(context, option_key, segment_id):
    for option in context[option_key]:
        if option["segment_id"] == segment_id:
            return option["option_index"]
    raise AssertionError(f"No option for segment {segment_id}")


def _option_index_for_boundary(context, option_key, boundary_key, value):
    for option in context[option_key]:
        if float(option[boundary_key]) == float(value):
            return option["option_index"]
    raise AssertionError(f"No {option_key} option with {boundary_key}={value}")


def _segment_id_for_option_index(context, option_key, option_index):
    for option in context[option_key]:
        if option["option_index"] == option_index:
            return option["segment_id"]
    raise AssertionError(f"No {option_key} option with option_index={option_index}")


def _decision_from_option_indexes(
    context,
    *,
    decision,
    selected_start_option_index,
    selected_end_option_index,
    reasoning_summary,
    start_reason,
    end_reason,
    warnings,
):
    return GeminiBoundaryDecision(
        review_response_contract_version=2,
        decision=decision,
        start_segment_id=_segment_id_for_option_index(
            context, "start_boundary_options", selected_start_option_index
        ),
        end_segment_id=_segment_id_for_option_index(
            context, "end_boundary_options", selected_end_option_index
        ),
        reasoning_summary=reasoning_summary,
        start_reason=start_reason,
        end_reason=end_reason,
        warnings=warnings,
    )


class ReviewAgentTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_url = _sqlite_url(self.root / "test.db")
        os.environ["PODCAST_CUTTER_DB_URL"] = self.db_url
        os.environ["PODCAST_CUTTER_PROJECT_ROOT"] = str(self.root)
        os.environ["CLIP_REVIEW_MODE"] = "local_stub"
        configure_database(self.db_url)
        init_database()
        self._write_transcript(
            [
                {"start": 60.0, "end": 70.0, "text": "This far away setup must not be sent."},
                {"start": 95.0, "end": 100.0, "text": "The setup matters here.", "speaker": "A"},
                {"start": 100.0, "end": 120.0, "text": "and this answer needs its setup.", "speaker": "A"},
                {"start": 120.0, "end": 140.0, "text": "The payoff lands cleanly.", "speaker": "B"},
                {"start": 140.0, "end": 150.0, "text": "Then the conversation moves on.", "speaker": "B"},
            ]
        )

    def tearDown(self):
        configure_database("sqlite:///:memory:")
        for key in (
            "PODCAST_CUTTER_DB_URL",
            "PODCAST_CUTTER_PROJECT_ROOT",
            "CLIP_REVIEW_MODE",
            "GEMINI_API_KEY",
            "GEMINI_MODEL",
            "CLIP_REVIEW_CONTEXT_SECONDS",
        ):
            os.environ.pop(key, None)
        self.tempdir.cleanup()

    def _write_transcript(self, segments):
        transcript_path = self.root / "transcripts" / "final_transcript.json"
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        transcript_path.write_text(json.dumps({"segments": segments}), encoding="utf-8")

    def _seed_project(self, clip_specs=None) -> int:
        specs = clip_specs or [{"id": "clip_001", "index": 1, "ai_start": 100.0, "ai_end": 140.0}]
        with session_scope() as session:
            project = ProjectRepository(session).create(
                source_url="https://www.youtube.com/watch?v=test",
                title="Review project",
                status="ready",
                transcript_path="transcripts/final_transcript.json",
            )
            for spec in specs:
                start = float(spec["ai_start"])
                end = float(spec["ai_end"])
                ClipRepository(session).create_from_dict(
                    project.id,
                    {
                        "id": spec["id"],
                        "index": spec["index"],
                        "ai_start": start,
                        "ai_end": end,
                        "edited_start": start,
                        "edited_end": end,
                        "min_start": max(0.0, start - 20.0),
                        "max_start": start + 20.0,
                        "min_end": max(start + 10.0, end - 20.0),
                        "max_end": end + 20.0,
                        "summary": "A clear podcast answer.",
                        "text": spec.get("text") or "and this answer needs its setup.",
                        "status": "draft",
                        "render_status": "not_rendered",
                        "local_score": spec.get("local_score", 0.82),
                        "local_rank": spec.get("local_rank", spec["index"]),
                        "selection_reasons": ["strong local score"],
                        "local_features": {"payoff": 0.8},
                    },
                )
            return project.id

    def _seed_long_boundary_project(self) -> int:
        self._write_transcript(
            [
                {"start": 80.0, "end": 100.0, "text": "Earlier setup."},
                {"start": 100.0, "end": 130.0, "text": "Candidate opening."},
                {"start": 130.0, "end": 170.0, "text": "Candidate explanation."},
                {"start": 170.0, "end": 180.0, "text": "Candidate payoff."},
                {"start": 180.0, "end": 205.0, "text": "Later topic."},
            ]
        )
        return self._seed_project(
            [{"id": "clip_001", "index": 1, "ai_start": 100.0, "ai_end": 180.0}]
        )

    def test_context_extraction_uses_configured_window_and_stable_segment_ids(self):
        context = build_clip_transcript_context(
            self.root / "transcripts" / "final_transcript.json",
            100.0,
            140.0,
            context_seconds=20.0,
            clip_id="clip_001",
        )

        self.assertEqual(context["context_seconds"], 20.0)
        self.assertEqual([segment["text"] for segment in context["context_before"]], ["The setup matters here."])
        self.assertEqual(len(context["candidate_segments"]), 2)
        self.assertEqual([segment["text"] for segment in context["context_after"]], ["Then the conversation moves on."])
        self.assertNotIn("far away", json.dumps(context))
        segment_ids = [segment["segment_id"] for segment in context["candidate_segments"]]
        self.assertTrue(all(segment_id.startswith("seg_") for segment_id in segment_ids))
        self.assertEqual(
            [option["segment_id"] for option in context["start_boundary_options"]],
            [context["context_before"][0]["segment_id"], *segment_ids],
        )
        self.assertEqual(
            [option["segment_id"] for option in context["end_boundary_options"]],
            [*segment_ids, context["context_after"][0]["segment_id"]],
        )
        self.assertEqual(context["current_aligned_start_segment_id"], context["candidate_segments"][0]["segment_id"])
        self.assertEqual(context["current_aligned_end_segment_id"], context["candidate_segments"][-1]["segment_id"])
        self.assertEqual(context["current_aligned_start_option_index"], context["start_boundary_options"][1]["option_index"])
        self.assertEqual(context["current_aligned_end_option_index"], context["end_boundary_options"][1]["option_index"])
        for option in context["start_boundary_options"] + context["end_boundary_options"]:
            self.assertIn("option_index", option)
            self.assertIn("segment_id", option)
            self.assertIn("start", option)
            self.assertIn("end", option)
            self.assertIn("text", option)
            self.assertTrue(option["text"])

    def test_context_exposes_only_range_safe_options_and_duration_safe_pairs(self):
        context = build_clip_transcript_context_from_segments(
            [
                {"start": 75.0, "end": 85.0, "text": "Crosses the start limit."},
                {"start": 80.0, "end": 100.0, "text": "Allowed early start."},
                {"start": 100.0, "end": 130.0, "text": "Original opening."},
                {"start": 130.0, "end": 170.0, "text": "Middle."},
                {"start": 170.0, "end": 180.0, "text": "Original ending."},
                {"start": 180.0, "end": 205.0, "text": "Crosses the end limit."},
            ],
            100.0,
            180.0,
            context_seconds=20.0,
            clip_id="clip_safe_contract",
            allowed_start_min=80.0,
            allowed_start_max=120.0,
            allowed_end_min=160.0,
            allowed_end_max=200.0,
            min_duration_seconds=10.0,
            max_duration_seconds=90.0,
        )

        self.assertEqual(context["context_seconds"], 20.0)
        self.assertTrue(context["allowed_boundary_pairs"])
        self.assertTrue(
            all(80.0 <= option["start"] <= 120.0 for option in context["start_boundary_options"])
        )
        self.assertTrue(
            all(160.0 <= option["end"] <= 200.0 for option in context["end_boundary_options"])
        )
        start_by_index = {
            option["option_index"]: option for option in context["start_boundary_options"]
        }
        end_by_index = {
            option["option_index"]: option for option in context["end_boundary_options"]
        }
        segment_by_id = {
            segment["segment_id"]: segment
            for key in ("context_before", "candidate_segments", "context_after")
            for segment in context[key]
        }
        for option in [*start_by_index.values(), *end_by_index.values()]:
            self.assertIn(option["segment_id"], segment_by_id)
        for pair in context["allowed_boundary_pairs"]:
            start_option = start_by_index[pair["start_option_index"]]
            end_option = end_by_index[pair["end_option_index"]]
            self.assertLess(start_option["start"], end_option["end"])
            self.assertGreaterEqual(end_option["end"] - start_option["start"], 10.0)
            self.assertLessEqual(end_option["end"] - start_option["start"], 90.0)

        original_pair = (
            _option_index_for_boundary(context, "start_boundary_options", "start", 100.0),
            _option_index_for_boundary(context, "end_boundary_options", "end", 180.0),
        )
        allowed_pairs = {
            (pair["start_option_index"], pair["end_option_index"])
            for pair in context["allowed_boundary_pairs"]
        }
        self.assertIn(original_pair, allowed_pairs)
        self.assertEqual(
            original_pair,
            (
                context["current_aligned_start_option_index"],
                context["current_aligned_end_option_index"],
            ),
        )
        overlong_pair = (
            _option_index_for_boundary(context, "start_boundary_options", "start", 80.0),
            _option_index_for_boundary(context, "end_boundary_options", "end", 180.0),
        )
        self.assertNotIn(overlong_pair, allowed_pairs)

    def test_gemini_provider_uses_structured_schema_and_compact_payload(self):
        context = build_clip_transcript_context(
            self.root / "transcripts" / "final_transcript.json",
            100.0,
            140.0,
            context_seconds=20.0,
            clip_id="clip_001",
        )
        calls = []
        client_closed = []

        class FakeInteractions:
            def create(self, **kwargs):
                calls.append(kwargs)
                output = json.dumps(
                    {
                        "review_response_contract_version": 2,
                        "decision": "render_ready",
                        "start_segment_id": context["current_aligned_start_segment_id"],
                        "end_segment_id": context["current_aligned_end_segment_id"],
                        "reasoning_summary": "The candidate is coherent.",
                        "start_reason": "It starts at a complete thought.",
                        "end_reason": "It ends at the payoff.",
                        "warnings": [],
                    }
                )
                return SimpleNamespace(
                    steps=[
                        SimpleNamespace(
                            type="model_output",
                            content=[SimpleNamespace(type="text", text=output)],
                            error=None,
                        )
                    ]
                )

        class FakeClient:
            interactions = FakeInteractions()

            def close(self):
                client_closed.append(True)

        reviewer = GeminiBoundaryReviewer(
            api_key="secret-key",
            model="gemini-test-model",
            client_factory=lambda api_key: FakeClient(),
        )
        decision = reviewer.review(context)

        self.assertEqual(decision.decision, "render_ready")
        self.assertEqual(client_closed, [True])
        self.assertEqual(calls[0]["model"], "gemini-test-model")
        self.assertIn("schema", calls[0]["response_format"])
        prompt = calls[0]["input"]
        self.assertIn("COMPACT REVIEW REQUEST", prompt)
        self.assertIn("review_request_contract_version", prompt)
        self.assertIn('"start_eligible": true', prompt)
        self.assertIn('"end_eligible": true', prompt)
        self.assertNotIn('"start_option_index"', prompt)
        self.assertNotIn('"end_option_index"', prompt)
        self.assertNotIn("ALLOWED BOUNDARY PAIRS", prompt)
        self.assertNotIn('"allowed_boundary_pairs"', prompt)
        self.assertIn("start_segment_id", calls[0]["response_format"]["schema"]["properties"])
        self.assertNotIn("selected_start_option_index", calls[0]["response_format"]["schema"]["properties"])
        self.assertIn('"text": "The setup matters here."', prompt)
        self.assertNotIn("local_score", prompt)
        self.assertNotIn("local_features", prompt)
        self.assertNotIn("candidate_features", prompt)
        self.assertNotIn(str(self.root), prompt)
        self.assertNotIn("source_video_path", prompt)
        self.assertNotIn("This far away setup must not be sent.", prompt)
        self.assertNotIn("secret-key", prompt)
        self.assertIn("You must make the editorial decision yourself.", prompt)
        self.assertIn("improving the setup, opening sentence, question, answer completeness, payoff, or ending", prompt)

    def test_compact_provider_request_keeps_pairs_backend_only(self):
        context = build_clip_transcript_context(
            self.root / "transcripts" / "final_transcript.json",
            100.0,
            140.0,
            context_seconds=20.0,
            clip_id="clip_001",
        )

        request = build_compact_review_request(context)

        self.assertNotIn("allowed_boundary_pairs", request)
        self.assertEqual(request["candidate"]["clip_id"], "clip_001")
        self.assertIsNone(request["candidate"]["candidate_id"])
        self.assertEqual(
            [segment["segment_id"] for segment in request["segments"]],
            [
                segment["segment_id"]
                for key in ("context_before", "candidate_segments", "context_after")
                for segment in context[key]
            ],
        )

    def test_interactions_steps_adapter_rejects_legacy_outputs_only(self):
        class FakeInteractions:
            def create(self, **_kwargs):
                return SimpleNamespace(outputs=[SimpleNamespace(type="text", text="legacy")])

        reviewer = GeminiBoundaryReviewer(
            api_key="offline",
            client_factory=lambda _api_key: SimpleNamespace(interactions=FakeInteractions()),
        )
        with self.assertRaises(ReviewProviderCompatibilityError):
            reviewer.review({})

    def test_interactions_steps_adapter_rejects_missing_or_non_text_model_output(self):
        responses = (
            SimpleNamespace(steps=[SimpleNamespace(type="user_input", content=[])]),
            SimpleNamespace(
                steps=[
                    SimpleNamespace(
                        type="model_output",
                        content=[SimpleNamespace(type="image", data="not-used")],
                        error=None,
                    )
                ]
            ),
        )
        for response in responses:
            with self.subTest(response=response):
                reviewer = GeminiBoundaryReviewer(
                    api_key="offline",
                    client_factory=lambda _api_key, value=response: SimpleNamespace(
                        interactions=SimpleNamespace(create=lambda **_kwargs: value)
                    ),
                )
                with self.assertRaises(ReviewProviderExtractionError):
                    reviewer.review({})

    def test_gemini_mode_without_api_key_fails_clearly_without_fallback(self):
        project_id = self._seed_project()
        os.environ["CLIP_REVIEW_MODE"] = "gemini"
        os.environ.pop("GEMINI_API_KEY", None)

        with TestClient(app) as client:
            response = client.post(f"/projects/{project_id}/review-clips")

        self.assertEqual(response.status_code, 503)
        self.assertIn("GEMINI_API_KEY", response.json()["detail"])
        with session_scope() as session:
            self.assertEqual(len(list(session.scalars(select(ClipEvaluation)).all())), 0)

    def test_local_stub_works_offline_and_records_provider_metadata(self):
        project_id = self._seed_project()

        result = ReviewAgentService(project_root=self.root).review_clip(
            project_id=project_id,
            clip_id="clip_001",
        )

        self.assertEqual(result["provider"], "local_stub")
        self.assertEqual(result["model"], "local_stub")
        self.assertIn(result["decision"], {"render_ready", "adjust_boundaries", "reject", "manual_review"})
        self.assertEqual(result["raw_result"]["review_workflow"], "langgraph_boundary_review")
        self.assertEqual(result["raw_result"]["review_workflow_version"], "2")
        self.assertEqual(result["raw_result"]["review_request_contract_version"], 3)
        self.assertEqual(result["raw_result"]["review_response_contract_version"], 2)
        self.assertEqual(result["review_workflow_version"], "2")
        self.assertEqual(result["review_request_contract_version"], 3)
        self.assertEqual(result["review_response_contract_version"], 2)
        self.assertIn(
            result["raw_result"]["review_workflow_route"],
            {"applied", "manual_review", "provider_failure"},
        )

    def test_gemini_schema_rejects_manual_review_and_schema_enum_has_three_decisions(self):
        with self.assertRaises(ValidationError):
            GeminiBoundaryDecision(
                review_response_contract_version=2,
                decision="manual_review",
                start_segment_id="seg_v1_start",
                end_segment_id="seg_v1_end",
                reasoning_summary="Do not allow model deferral.",
                start_reason="No.",
                end_reason="No.",
                warnings=[],
            )

        legacy_response = {
            "decision": "render_ready",
            "selected_start_option_index": 1,
            "selected_end_option_index": 2,
            "reasoning_summary": "Legacy.",
            "start_reason": "Legacy.",
            "end_reason": "Legacy.",
        }
        with self.assertRaises(ValidationError):
            GeminiBoundaryDecision(**legacy_response)
        with self.assertRaises(ValidationError):
            GeminiBoundaryDecision.model_validate(legacy_response)

        schema = GeminiBoundaryDecision.model_json_schema()
        decision_property = schema["properties"]["decision"]
        self.assertEqual(set(decision_property["enum"]), {"render_ready", "adjust_boundaries", "reject"})
        self.assertNotIn("manual_review", decision_property["enum"])
        self.assertIn("review_response_contract_version", schema["required"])
        self.assertIn("start_segment_id", schema["required"])
        self.assertIn("end_segment_id", schema["required"])
        self.assertNotIn("selected_start_option_index", schema["properties"])
        self.assertNotIn("selected_end_option_index", schema["properties"])
        with self.assertRaises(ValidationError):
            GeminiBoundaryDecision(
                review_response_contract_version=2,
                decision="render_ready",
                start_segment_id="",
                end_segment_id="seg_v1_end",
                reasoning_summary="Null should fail.",
                start_reason="No.",
                end_reason="No.",
                warnings=[],
            )

    def test_id_response_schema_requires_contract_version_and_nonempty_ids(self):
        valid = {
            "review_response_contract_version": 2,
            "decision": "render_ready",
            "start_segment_id": "seg_v1_start",
            "end_segment_id": "seg_v1_end",
            "reasoning_summary": "Complete.",
            "start_reason": "Complete opening.",
            "end_reason": "Complete ending.",
        }
        for field, value in (("review_response_contract_version", None), ("start_segment_id", ""), ("end_segment_id", " ")):
            with self.subTest(field=field):
                invalid = dict(valid)
                if value is None:
                    invalid.pop(field)
                else:
                    invalid[field] = value
                with self.assertRaises(ValidationError):
                    GeminiBoundaryDecision.model_validate(invalid)

    def test_selected_segment_ids_map_to_backend_indexes_and_timestamps(self):
        context = build_clip_transcript_context(
            self.root / "transcripts" / "final_transcript.json", 100.0, 140.0, clip_id="clip_001"
        )
        decision = GeminiBoundaryDecision(
            review_response_contract_version=2,
            decision="render_ready",
            start_segment_id=context["current_aligned_start_segment_id"],
            end_segment_id=context["current_aligned_end_segment_id"],
            reasoning_summary="Complete.",
            start_reason="Opening.",
            end_reason="Payoff.",
        )
        result = ReviewAgentService(project_root=self.root)._result_from_decision(
            project_id=1,
            clip={"id": "clip_001", "ai_start": 100.0, "ai_end": 140.0, "min_start": 80.0, "max_start": 120.0, "min_end": 110.0, "max_end": 160.0},
            context=context,
            decision=decision,
            provider="gemini",
            model="unit",
            apply_safe_suggestions=True,
        )
        self.assertEqual(result["selected_start_option_index"], context["current_aligned_start_option_index"])
        self.assertEqual(result["selected_end_option_index"], context["current_aligned_end_option_index"])
        self.assertEqual((result["reviewed_start"], result["reviewed_end"]), (100.0, 140.0))
        self.assertNotIn("needs_more_context", result)
        self.assertEqual(result["raw_result"]["review_response_contract_version"], 2)

    def test_id_path_rejects_unknown_ineligible_and_unlisted_boundary_pairs(self):
        context = build_clip_transcript_context(
            self.root / "transcripts" / "final_transcript.json", 100.0, 140.0, clip_id="clip_001"
        )
        clip = {"id": "clip_001", "ai_start": 100.0, "ai_end": 140.0, "min_start": 80.0, "max_start": 120.0, "min_end": 110.0, "max_end": 160.0}
        service = ReviewAgentService(project_root=self.root)

        cases = (
            ("unknown start", "seg_v1_unknown_start", context["current_aligned_end_segment_id"], "start-ineligible"),
            ("unknown end", context["current_aligned_start_segment_id"], "seg_v1_unknown_end", "end-ineligible"),
            ("start ineligible", context["context_after"][0]["segment_id"], context["current_aligned_end_segment_id"], "start-ineligible"),
            ("end ineligible", context["current_aligned_start_segment_id"], context["context_before"][0]["segment_id"], "end-ineligible"),
        )
        for label, start_id, end_id, message in cases:
            with self.subTest(label=label):
                decision = GeminiBoundaryDecision(
                    review_response_contract_version=2, decision="adjust_boundaries",
                    start_segment_id=start_id, end_segment_id=end_id,
                    reasoning_summary="Test.", start_reason="Test.", end_reason="Test.",
                )
                with self.assertRaisesRegex(BoundaryOptionSelectionError, message):
                    service._result_from_decision(
                        project_id=1, clip=clip, context=context, decision=decision,
                        provider="gemini", model="unit", apply_safe_suggestions=True,
                    )
        unlisted_context = deepcopy(context)
        unlisted_context["allowed_boundary_pairs"] = [
            pair for pair in unlisted_context["allowed_boundary_pairs"]
            if pair != {
                "start_option_index": unlisted_context["current_aligned_start_option_index"],
                "end_option_index": unlisted_context["current_aligned_end_option_index"],
            }
        ]
        unlisted = GeminiBoundaryDecision(
            review_response_contract_version=2, decision="adjust_boundaries",
            start_segment_id=unlisted_context["current_aligned_start_segment_id"],
            end_segment_id=unlisted_context["current_aligned_end_segment_id"],
            reasoning_summary="Test.", start_reason="Test.", end_reason="Test.",
        )
        with self.assertRaisesRegex(BoundaryOptionSelectionError, "allowed_boundary_pairs"):
            service._result_from_decision(
                project_id=1, clip=clip, context=unlisted_context, decision=unlisted,
                provider="gemini", model="unit", apply_safe_suggestions=True,
            )

    def test_id_path_rejects_inconsistent_boundary_option_before_using_timestamps(self):
        context = build_clip_transcript_context(
            self.root / "transcripts" / "final_transcript.json", 100.0, 140.0, clip_id="clip_001"
        )
        invalid_context = deepcopy(context)
        invalid_context["start_boundary_options"][1]["start"] = 99.0
        decision = GeminiBoundaryDecision(
            review_response_contract_version=2,
            decision="adjust_boundaries",
            start_segment_id=invalid_context["current_aligned_start_segment_id"],
            end_segment_id=invalid_context["current_aligned_end_segment_id"],
            reasoning_summary="Test.",
            start_reason="Test.",
            end_reason="Test.",
        )
        with self.assertRaisesRegex(BoundaryOptionSelectionError, "Invalid internal review context: .*does not match canonical"):
            ReviewAgentService(project_root=self.root)._result_from_decision(
                project_id=1,
                clip={"id": "clip_001", "ai_start": 100.0, "ai_end": 140.0, "min_start": 80.0, "max_start": 120.0, "min_end": 110.0, "max_end": 160.0},
                context=invalid_context,
                decision=decision,
                provider="gemini",
                model="unit",
                apply_safe_suggestions=True,
            )

    def test_reject_requires_current_aligned_segment_ids(self):
        context = build_clip_transcript_context(
            self.root / "transcripts" / "final_transcript.json", 100.0, 140.0, clip_id="clip_001"
        )
        clip = {"id": "clip_001", "ai_start": 100.0, "ai_end": 140.0, "min_start": 80.0, "max_start": 120.0, "min_end": 110.0, "max_end": 160.0}
        service = ReviewAgentService(project_root=self.root)
        non_aligned = GeminiBoundaryDecision(
            review_response_contract_version=2, decision="reject",
            start_segment_id=context["context_before"][0]["segment_id"],
            end_segment_id=context["current_aligned_end_segment_id"],
            reasoning_summary="Reject.", start_reason="Reject.", end_reason="Reject.",
        )
        with self.assertRaisesRegex(BoundaryOptionSelectionError, "current aligned segment IDs"):
            service._result_from_decision(
                project_id=1, clip=clip, context=context, decision=non_aligned,
                provider="gemini", model="unit", apply_safe_suggestions=True,
            )
        aligned = GeminiBoundaryDecision(
            review_response_contract_version=2, decision="reject",
            start_segment_id=context["current_aligned_start_segment_id"],
            end_segment_id=context["current_aligned_end_segment_id"],
            reasoning_summary="Reject.", start_reason="Reject.", end_reason="Reject.",
        )
        result = service._result_from_decision(
            project_id=1, clip=clip, context=context, decision=aligned,
            provider="gemini", model="unit", apply_safe_suggestions=True,
        )
        self.assertEqual(result["selected_start_segment_id"], context["current_aligned_start_segment_id"])
        self.assertEqual(result["selected_end_segment_id"], context["current_aligned_end_segment_id"])
        self.assertIsNone(result["reviewed_start"])

    def test_model_dump_schema_and_failed_raw_result_exclude_provider_indexes(self):
        decision = GeminiBoundaryDecision(
            review_response_contract_version=2, decision="render_ready",
            start_segment_id="seg_v1_start", end_segment_id="seg_v1_end",
            reasoning_summary="Ready.", start_reason="Start.", end_reason="End.",
        )
        for payload in (decision.model_dump(), GeminiBoundaryDecision.model_json_schema()["properties"]):
            self.assertNotIn("selected_start_option_index", payload)
            self.assertNotIn("selected_end_option_index", payload)
        failed = ReviewAgentService(project_root=self.root)._failed_result(
            project_id=1, clip={"id": "clip_001"}, context={}, provider="gemini", model="unit",
            warning="Invalid.", apply_safe_suggestions=True, failure_category="structured_output",
        )
        self.assertEqual(failed["raw_result"]["review_response_contract_version"], 2)
        self.assertNotIn("needs_more_context", failed)

    def test_visual_reference_prompt_still_requires_three_decision_choice(self):
        prompt = build_gemini_prompt(
            {
                "clip_id": "clip_001",
                "candidate_start": 100.0,
                "candidate_end": 140.0,
                "context_seconds": 20.0,
                "earliest_allowed_start": 95.0,
                "latest_allowed_end": 150.0,
                "current_aligned_start_option_index": 1,
                "current_aligned_end_option_index": 1,
                "context_before": [],
                "candidate_segments": [
                    {"segment_id": "seg_start", "start": 100.0, "end": 120.0, "text": "jak widzisz this still has meaning"},
                    {"segment_id": "seg_end", "start": 120.0, "end": 140.0, "text": "the point lands"},
                ],
                "context_after": [],
                "start_boundary_options": [
                    {
                        "option_index": 1,
                        "segment_id": "seg_start",
                        "start": 100.0,
                        "end": 120.0,
                        "text": "jak widzisz this still has meaning",
                    }
                ],
                "end_boundary_options": [
                    {"option_index": 1, "segment_id": "seg_end", "start": 120.0, "end": 140.0, "text": "the point lands"}
                ],
            }
        )

        self.assertIn("You must make the editorial decision yourself.", prompt)
        self.assertIn("You are not allowed to defer the decision to a human.", prompt)
        self.assertIn("Visual framing is not part of your task.", prompt)
        self.assertIn("Warnings may mention transcript uncertainty", prompt)
        self.assertNotIn("- manual_review", prompt)

    def test_gemini_api_error_becomes_backend_manual_review_and_does_not_apply(self):
        project_id = self._seed_project()
        os.environ["CLIP_REVIEW_MODE"] = "gemini"
        os.environ["GEMINI_API_KEY"] = "test-key"
        calls = []

        class ErrorGemini:
            provider = "gemini"

            def __init__(self, *, api_key, model):
                self.model = model

            def review(self, context, corrective_message=None):
                calls.append(corrective_message)
                raise ReviewProviderError("Gemini API error: quota exhausted")

        with patch("apps.review_agent.service.GeminiBoundaryReviewer", ErrorGemini):
            result = ReviewAgentService(project_root=self.root, mode="gemini").review_clip(
                project_id=project_id,
                clip_id="clip_001",
            )

        self.assertEqual(len(calls), 1)
        self.assertIsNone(calls[0])
        self.assertEqual(result["decision"], "manual_review")
        self.assertEqual(
            result["review_provenance"],
            {"review_kind": "manual_review", "numeric_score_provenance": "not_available"},
        )
        self.assertIsNone(result["reviewed_start"])
        self.assertIsNone(result["reviewed_end"])
        self.assertTrue(result["failed"])
        self.assertEqual(result["failure_reason"], "Gemini provider request failed.")
        clip = clip_service.load_clips(project_id=project_id, project_root=self.root)[0]
        self.assertEqual(clip["edited_start"], 100.0)
        self.assertEqual(clip["edited_end"], 140.0)
        self.assertEqual(clip["boundary_source"], "heuristic")

    def test_reject_returns_backend_derived_indexes_but_does_not_retry_or_apply(self):
        project_id = self._seed_project()
        os.environ["CLIP_REVIEW_MODE"] = "gemini"
        os.environ["GEMINI_API_KEY"] = "test-key"
        calls = []

        class RejectGemini:
            provider = "gemini"

            def __init__(self, *, api_key, model):
                self.model = model

            def review(self, context, corrective_message=None):
                calls.append(corrective_message)
                return _decision_from_option_indexes(context,
                    decision="reject",
                    selected_start_option_index=context["current_aligned_start_option_index"],
                    selected_end_option_index=context["current_aligned_end_option_index"],
                    reasoning_summary="The candidate has no standalone idea.",
                    start_reason="No hook emerges from the available context.",
                    end_reason="No payoff emerges from the available context.",
                    warnings=[],
                )

        with patch("apps.review_agent.service.GeminiBoundaryReviewer", RejectGemini):
            result = ReviewAgentService(project_root=self.root, mode="gemini").review_clip(
                project_id=project_id,
                clip_id="clip_001",
            )

        self.assertEqual(len(calls), 1)
        self.assertIsNone(calls[0])
        self.assertEqual(result["decision"], "reject")
        self.assertFalse(result["failed"])
        self.assertEqual(result["selected_start_option_index"], 2)
        self.assertEqual(result["selected_end_option_index"], 2)
        self.assertEqual(result["selected_start_segment_id"], state_start := canonical_segment_id(100.0, 120.0))
        self.assertEqual(result["selected_start_segment_id"], state_start)
        self.assertIsNone(result["reviewed_start"])
        self.assertIsNone(result["reviewed_end"])
        clip = clip_service.load_clips(project_id=project_id, project_root=self.root)[0]
        self.assertEqual(clip["status"], "rejected")
        self.assertEqual(clip["edited_start"], 100.0)
        self.assertEqual(clip["edited_end"], 140.0)
        self.assertEqual(clip["boundary_source"], "heuristic")

    def test_render_ready_with_missing_segment_id_retries_once_then_fails_safely(self):
        project_id = self._seed_project()
        os.environ["CLIP_REVIEW_MODE"] = "gemini"
        os.environ["GEMINI_API_KEY"] = "test-key"
        calls = []

        class MissingIdsGemini:
            provider = "gemini"

            def __init__(self, *, api_key, model):
                self.model = model

            def review(self, context, corrective_message=None):
                calls.append(corrective_message)
                raise ReviewProviderOutputError("start_segment_id is required")

        with patch("apps.review_agent.service.GeminiBoundaryReviewer", MissingIdsGemini):
            result = ReviewAgentService(project_root=self.root, mode="gemini").review_clip(
                project_id=project_id,
                clip_id="clip_001",
            )

        self.assertEqual(len(calls), 2)
        self.assertIsNone(calls[0])
        self.assertIn("start_segment_id", calls[1])
        self.assertTrue(result["retry_used"])
        self.assertEqual(result["provider_attempt_count"], 2)
        self.assertIn("start_segment_id", result["first_attempt_validation_error"])
        self.assertTrue(result["failed"])
        self.assertEqual(result["decision"], "manual_review")
        clip = clip_service.load_clips(project_id=project_id, project_root=self.root)[0]
        self.assertEqual(clip["edited_start"], 100.0)
        self.assertEqual(clip["boundary_source"], "heuristic")

    def test_adjust_boundaries_with_missing_segment_id_retries_once_then_fails_safely(self):
        project_id = self._seed_project()
        os.environ["CLIP_REVIEW_MODE"] = "gemini"
        os.environ["GEMINI_API_KEY"] = "test-key"
        calls = []

        class MissingIdsGemini:
            provider = "gemini"

            def __init__(self, *, api_key, model):
                self.model = model

            def review(self, context, corrective_message=None):
                calls.append(corrective_message)
                raise ReviewProviderOutputError("end_segment_id is required")

        with patch("apps.review_agent.service.GeminiBoundaryReviewer", MissingIdsGemini):
            result = ReviewAgentService(project_root=self.root, mode="gemini").review_clip(
                project_id=project_id,
                clip_id="clip_001",
            )

        self.assertEqual(len(calls), 2)
        self.assertTrue(result["retry_used"])
        self.assertEqual(result["provider_attempt_count"], 2)
        self.assertTrue(result["failed"])
        self.assertEqual(result["decision"], "manual_review")
        clip = clip_service.load_clips(project_id=project_id, project_root=self.root)[0]
        self.assertEqual(clip["edited_start"], 100.0)
        self.assertEqual(clip["edited_end"], 140.0)

    def test_corrective_retry_can_recover_safe_decision_missing_segment_ids(self):
        project_id = self._seed_project()
        os.environ["CLIP_REVIEW_MODE"] = "gemini"
        os.environ["GEMINI_API_KEY"] = "test-key"
        calls = []

        class RetryGemini:
            provider = "gemini"

            def __init__(self, *, api_key, model):
                self.model = model

            def review(self, context, corrective_message=None):
                calls.append(corrective_message)
                if len(calls) == 1:
                    raise ReviewProviderOutputError("segment IDs were missing")
                return _decision_from_option_indexes(context,
                    decision="render_ready",
                    selected_start_option_index=context["current_aligned_start_option_index"],
                    selected_end_option_index=context["current_aligned_end_option_index"],
                    reasoning_summary="Ready with aligned IDs.",
                    start_reason="Uses current aligned start.",
                    end_reason="Uses current aligned end.",
                    warnings=[],
                )

        with patch("apps.review_agent.service.GeminiBoundaryReviewer", RetryGemini):
            result = ReviewAgentService(project_root=self.root, mode="gemini").review_clip(
                project_id=project_id,
                clip_id="clip_001",
            )

        self.assertEqual(len(calls), 2)
        self.assertFalse(result["failed"])
        self.assertTrue(result["retry_used"])
        self.assertEqual(result["provider_attempt_count"], 2)
        self.assertIn("segment IDs", result["first_attempt_validation_error"])
        self.assertIsNone(result["final_validation_error"])
        self.assertEqual(result["decision"], "render_ready")
        self.assertEqual(result["reviewed_start"], 100.0)
        self.assertEqual(result["reviewed_end"], 140.0)

    def test_valid_gemini_segment_ids_map_to_exact_timestamps_and_apply(self):
        project_id = self._seed_project()
        os.environ["CLIP_REVIEW_MODE"] = "gemini"
        os.environ["GEMINI_API_KEY"] = "test-key"
        os.environ["GEMINI_MODEL"] = "gemini-unit"

        class FakeGemini:
            provider = "gemini"

            def __init__(self, *, api_key, model):
                self.model = model

            def review(self, context):
                return _decision_from_option_indexes(context,
                    decision="adjust_boundaries",
                    selected_start_option_index=_option_index_for_segment(
                        context,
                        "start_boundary_options",
                        context["context_before"][-1]["segment_id"],
                    ),
                    selected_end_option_index=_option_index_for_segment(
                        context,
                        "end_boundary_options",
                        context["candidate_segments"][-1]["segment_id"],
                    ),
                    reasoning_summary="Including setup makes the clip standalone.",
                    start_reason="The setup segment introduces the answer.",
                    end_reason="The payoff completes inside the candidate.",
                    warnings=[],
                )

        with patch("apps.review_agent.service.GeminiBoundaryReviewer", FakeGemini):
            result = ReviewAgentService(project_root=self.root, mode="gemini").review_clip(
                project_id=project_id,
                clip_id="clip_001",
            )

        self.assertEqual(result["provider"], "gemini")
        self.assertEqual(result["model"], "gemini-unit")
        self.assertEqual(result["selected_start_segment_id"], canonical_segment_id(95.0, 100.0))
        self.assertEqual(result["selected_end_segment_id"], canonical_segment_id(120.0, 140.0))
        self.assertEqual(result["reviewed_start"], 95.0)
        self.assertEqual(result["reviewed_end"], 140.0)
        self.assertEqual(result["start_delta_seconds"], -5.0)
        self.assertEqual(result["end_delta_seconds"], 0.0)
        clip = clip_service.load_clips(project_id=project_id, project_root=self.root)[0]
        self.assertEqual(clip["ai_start"], 100.0)
        self.assertEqual(clip["ai_end"], 140.0)
        self.assertEqual(clip["reviewed_start"], 95.0)
        self.assertEqual(clip["edited_start"], 95.0)
        self.assertEqual(clip["boundary_source"], "ai_review")

    def test_over_90_second_pair_triggers_one_corrective_retry_and_applies_valid_pair(self):
        project_id = self._seed_long_boundary_project()
        os.environ["CLIP_REVIEW_MODE"] = "gemini"
        os.environ["GEMINI_API_KEY"] = "test-key"
        calls = []

        class PairChoosingGemini:
            provider = "gemini"

            def __init__(self, *, api_key, model):
                self.model = model

            def review(self, context, corrective_message=None):
                calls.append(corrective_message)
                start = 80.0 if len(calls) == 1 else 100.0
                return _decision_from_option_indexes(context,
                    decision="adjust_boundaries",
                    selected_start_option_index=_option_index_for_boundary(
                        context, "start_boundary_options", "start", start
                    ),
                    selected_end_option_index=_option_index_for_boundary(
                        context, "end_boundary_options", "end", 180.0
                    ),
                    reasoning_summary="Gemini selected the semantic pair.",
                    start_reason="The selected opening carries the setup.",
                    end_reason="The selected ending lands the payoff.",
                    warnings=[],
                )

        with patch("apps.review_agent.service.GeminiBoundaryReviewer", PairChoosingGemini):
            result = ReviewAgentService(project_root=self.root, mode="gemini").review_clip(
                project_id=project_id,
                clip_id="clip_001",
            )

        self.assertEqual(len(calls), 2)
        self.assertIsNone(calls[0])
        self.assertIn("duration exceeds 90 seconds", calls[1])
        self.assertIn("Start IDs:", calls[1])
        self.assertNotIn("allowed_boundary_pairs", calls[1])
        self.assertNotIn("Candidate explanation", calls[1])
        self.assertNotIn(str(self.root), calls[1])
        self.assertNotIn("test-key", calls[1])
        self.assertFalse(result["failed"])
        self.assertTrue(result["retry_used"])
        self.assertEqual(result["provider_attempt_count"], 2)
        self.assertEqual(result["reviewed_start"], 100.0)
        self.assertEqual(result["reviewed_end"], 180.0)
        clip = clip_service.load_clips(project_id=project_id, project_root=self.root)[0]
        self.assertEqual(clip["ai_start"], 100.0)
        self.assertEqual(clip["ai_end"], 180.0)
        self.assertEqual(clip["edited_start"], 100.0)
        self.assertEqual(clip["edited_end"], 180.0)
        self.assertEqual(clip["boundary_source"], "ai_review")

    def test_out_of_range_end_triggers_corrective_retry(self):
        self._write_transcript(
            [
                {"start": 95.0, "end": 100.0, "text": "Setup."},
                {"start": 100.0, "end": 120.0, "text": "Opening."},
                {"start": 120.0, "end": 140.0, "text": "Payoff."},
                {"start": 140.0, "end": 170.0, "text": "Outside permitted end."},
            ]
        )
        project_id = self._seed_project()
        os.environ["CLIP_REVIEW_MODE"] = "gemini"
        os.environ["GEMINI_API_KEY"] = "test-key"
        calls = []

        class OutOfRangeGemini:
            provider = "gemini"

            def __init__(self, *, api_key, model):
                self.model = model

            def review(self, context, corrective_message=None):
                calls.append(corrective_message)
                if len(calls) == 1:
                    outside_segment = context["context_after"][0]
                    context["end_boundary_options"].append(
                        {
                            "option_index": 99,
                            "segment_id": outside_segment["segment_id"],
                            "start": outside_segment["start"],
                            "end": outside_segment["end"],
                            "text": outside_segment["text"],
                        }
                    )
                    end_index = 99
                else:
                    end_index = context["current_aligned_end_option_index"]
                return _decision_from_option_indexes(context,
                    decision="adjust_boundaries",
                    selected_start_option_index=context["current_aligned_start_option_index"],
                    selected_end_option_index=end_index,
                    reasoning_summary="Offline selection.",
                    start_reason="Offline start.",
                    end_reason="Offline end.",
                    warnings=[],
                )

        with patch("apps.review_agent.service.GeminiBoundaryReviewer", OutOfRangeGemini):
            result = ReviewAgentService(project_root=self.root, mode="gemini").review_clip(
                project_id=project_id,
                clip_id="clip_001",
            )

        self.assertEqual(len(calls), 2)
        self.assertIn("selected end is outside the permitted clip range", calls[1])
        self.assertFalse(result["failed"])
        self.assertTrue(result["retry_used"])
        self.assertEqual(result["reviewed_end"], 140.0)

    def test_unlisted_pair_triggers_corrective_retry(self):
        project_id = self._seed_project()
        os.environ["CLIP_REVIEW_MODE"] = "gemini"
        os.environ["GEMINI_API_KEY"] = "test-key"
        calls = []

        class UnlistedPairGemini:
            provider = "gemini"

            def __init__(self, *, api_key, model):
                self.model = model

            def review(self, context, corrective_message=None):
                calls.append(corrective_message)
                if len(calls) == 1:
                    current_pair = (
                        context["current_aligned_start_option_index"],
                        context["current_aligned_end_option_index"],
                    )
                    selected_pair = next(
                        (pair["start_option_index"], pair["end_option_index"])
                        for pair in context["allowed_boundary_pairs"]
                        if (pair["start_option_index"], pair["end_option_index"]) != current_pair
                    )
                    context["allowed_boundary_pairs"] = [
                        pair
                        for pair in context["allowed_boundary_pairs"]
                        if (pair["start_option_index"], pair["end_option_index"]) != selected_pair
                    ]
                    start_index, end_index = selected_pair
                else:
                    pair = context["allowed_boundary_pairs"][0]
                    start_index = pair["start_option_index"]
                    end_index = pair["end_option_index"]
                return _decision_from_option_indexes(context,
                    decision="adjust_boundaries",
                    selected_start_option_index=start_index,
                    selected_end_option_index=end_index,
                    reasoning_summary="Offline selection.",
                    start_reason="Offline start.",
                    end_reason="Offline end.",
                    warnings=[],
                )

        with patch("apps.review_agent.service.GeminiBoundaryReviewer", UnlistedPairGemini):
            result = ReviewAgentService(project_root=self.root, mode="gemini").review_clip(
                project_id=project_id,
                clip_id="clip_001",
            )

        self.assertEqual(len(calls), 2)
        self.assertIn("boundary pair is not allowed", calls[1])
        self.assertFalse(result["failed"])
        self.assertTrue(result["retry_used"])

    def test_two_invalid_pairs_become_manual_review_and_preserve_existing_boundaries(self):
        os.environ["CLIP_REVIEW_MODE"] = "gemini"
        os.environ["GEMINI_API_KEY"] = "test-key"

        for boundary_source, reviewed_start, reviewed_end, edited_start, edited_end in (
            ("user", None, None, 105.0, 175.0),
            ("ai_review", 100.0, 170.0, 100.0, 170.0),
        ):
            with self.subTest(boundary_source=boundary_source):
                project_id = self._seed_long_boundary_project()
                with session_scope() as session:
                    clip = ClipRepository(session).get_by_external_id(project_id, "clip_001")
                    clip.boundary_source = boundary_source
                    clip.reviewed_start = reviewed_start
                    clip.reviewed_end = reviewed_end
                    clip.edited_start = edited_start
                    clip.edited_end = edited_end
                    ClipRepository(session).touch(clip)
                calls = []

                class AlwaysInvalidGemini:
                    provider = "gemini"

                    def __init__(self, *, api_key, model):
                        self.model = model

                    def review(self, context, corrective_message=None):
                        calls.append(corrective_message)
                        return _decision_from_option_indexes(context,
                            decision="adjust_boundaries",
                            selected_start_option_index=_option_index_for_boundary(
                                context, "start_boundary_options", "start", 80.0
                            ),
                            selected_end_option_index=_option_index_for_boundary(
                                context, "end_boundary_options", "end", 180.0
                            ),
                            reasoning_summary="Offline invalid pair.",
                            start_reason="Offline start.",
                            end_reason="Offline end.",
                            warnings=[],
                        )

                with patch("apps.review_agent.service.GeminiBoundaryReviewer", AlwaysInvalidGemini):
                    result = ReviewAgentService(project_root=self.root, mode="gemini").review_clip(
                        project_id=project_id,
                        clip_id="clip_001",
                    )

                self.assertEqual(len(calls), 2)
                self.assertTrue(result["failed"])
                self.assertEqual(result["decision"], "manual_review")
                self.assertEqual(result["provider"], "gemini")
                self.assertNotEqual(result["model"], "local_stub")
                self.assertEqual(result["failure_category"], "boundary_validation")
                self.assertEqual(
                    result["reasoning_summary"],
                    "Gemini returned boundaries outside the permitted clip range. This clip requires manual review.",
                )
                clip = clip_service.load_clips(project_id=project_id, project_root=self.root)[0]
                self.assertEqual(clip["ai_start"], 100.0)
                self.assertEqual(clip["ai_end"], 180.0)
                self.assertEqual(clip["boundary_source"], boundary_source)
                self.assertEqual(clip["reviewed_start"], reviewed_start)
                self.assertEqual(clip["reviewed_end"], reviewed_end)
                self.assertEqual(clip["edited_start"], edited_start)
                self.assertEqual(clip["edited_end"], edited_end)
                self.assertEqual(clip["latest_review_failure_category"], "boundary_validation")

    def test_invalid_reversed_or_malformed_gemini_output_is_saved_without_applying(self):
        project_id = self._seed_project()
        os.environ["CLIP_REVIEW_MODE"] = "gemini"
        os.environ["GEMINI_API_KEY"] = "test-key"

        class ReversedGemini:
            provider = "gemini"
            model = "gemini-invalid"
            calls = 0

            def __init__(self, *, api_key, model):
                self.model = model

            def review(self, context, corrective_message=None):
                ReversedGemini.calls += 1
                return _decision_from_option_indexes(context,
                    decision="adjust_boundaries",
                    selected_start_option_index=_option_index_for_segment(
                        context,
                        "start_boundary_options",
                        context["candidate_segments"][-1]["segment_id"],
                    ),
                    selected_end_option_index=_option_index_for_segment(
                        context,
                        "end_boundary_options",
                        context["candidate_segments"][0]["segment_id"],
                    ),
                    reasoning_summary="Bad output.",
                    start_reason="Bad start.",
                    end_reason="Bad end.",
                    warnings=[],
                )

        with patch("apps.review_agent.service.GeminiBoundaryReviewer", ReversedGemini):
            result = ReviewAgentService(project_root=self.root, mode="gemini").review_clip(
                project_id=project_id,
                clip_id="clip_001",
            )

        self.assertTrue(result["failed"])
        self.assertEqual(result["decision"], "manual_review")
        self.assertEqual(ReversedGemini.calls, 2)
        self.assertTrue(result["retry_used"])
        self.assertEqual(result["provider_attempt_count"], 2)
        self.assertIn("reversed", result["first_attempt_validation_error"])
        clip = clip_service.load_clips(project_id=project_id, project_root=self.root)[0]
        self.assertIsNone(clip["reviewed_start"])
        self.assertEqual(clip["edited_start"], 100.0)
        self.assertEqual(clip["boundary_source"], "heuristic")

        class MalformedGemini:
            provider = "gemini"
            model = "gemini-malformed"

            def __init__(self, *, api_key, model):
                self.model = model

            def review(self, context):
                raise ReviewProviderError("structured output was malformed")

        with patch("apps.review_agent.service.GeminiBoundaryReviewer", MalformedGemini):
            malformed = ReviewAgentService(project_root=self.root, mode="gemini").review_clip(
                project_id=project_id,
                clip_id="clip_001",
            )

        self.assertTrue(malformed["failed"])
        self.assertEqual(malformed["warnings"], ["Gemini provider request failed."])

    def test_unknown_start_segment_id_triggers_one_retry_then_fails_safely(self):
        project_id = self._seed_project()
        os.environ["CLIP_REVIEW_MODE"] = "gemini"
        os.environ["GEMINI_API_KEY"] = "test-key"
        calls = []

        class UnknownIdGemini:
            provider = "gemini"

            def __init__(self, *, api_key, model):
                self.model = model

            def review(self, context, corrective_message=None):
                calls.append(corrective_message)
                return GeminiBoundaryDecision(
                    review_response_contract_version=2,
                    decision="adjust_boundaries",
                    start_segment_id="seg_v1_unknown_start",
                    end_segment_id=context["current_aligned_end_segment_id"],
                    reasoning_summary="Bad id.",
                    start_reason="Bad id.",
                    end_reason="Known end.",
                    warnings=[],
                )

        with patch("apps.review_agent.service.GeminiBoundaryReviewer", UnknownIdGemini):
            result = ReviewAgentService(project_root=self.root, mode="gemini").review_clip(
                project_id=project_id,
                clip_id="clip_001",
            )

        self.assertEqual(len(calls), 2)
        self.assertIsNone(calls[0])
        self.assertIn("Start IDs:", calls[1])
        self.assertTrue(result["failed"])
        self.assertTrue(result["retry_used"])
        self.assertEqual(result["provider_attempt_count"], 2)
        self.assertIn("unknown or start-ineligible segment_id", result["first_attempt_validation_error"])
        clip = clip_service.load_clips(project_id=project_id, project_root=self.root)[0]
        self.assertEqual(clip["edited_start"], 100.0)
        self.assertEqual(clip["edited_end"], 140.0)

    def test_unknown_end_segment_id_triggers_one_retry_then_fails_safely(self):
        project_id = self._seed_project()
        os.environ["CLIP_REVIEW_MODE"] = "gemini"
        os.environ["GEMINI_API_KEY"] = "test-key"
        calls = []

        class UnknownEndGemini:
            provider = "gemini"

            def __init__(self, *, api_key, model):
                self.model = model

            def review(self, context, corrective_message=None):
                calls.append(corrective_message)
                return GeminiBoundaryDecision(
                    review_response_contract_version=2,
                    decision="adjust_boundaries",
                    start_segment_id=context["current_aligned_start_segment_id"],
                    end_segment_id="seg_v1_unknown_end",
                    reasoning_summary="Bad end index.",
                    start_reason="Known start.",
                    end_reason="Bad end.",
                    warnings=[],
                )

        with patch("apps.review_agent.service.GeminiBoundaryReviewer", UnknownEndGemini):
            result = ReviewAgentService(project_root=self.root, mode="gemini").review_clip(
                project_id=project_id,
                clip_id="clip_001",
            )

        self.assertEqual(len(calls), 2)
        self.assertIn("End IDs:", calls[1])
        self.assertTrue(result["failed"])
        self.assertIn("unknown or end-ineligible segment_id", result["first_attempt_validation_error"])

    def test_manual_patch_changes_only_edited_boundaries_after_review(self):
        project_id = self._seed_project()
        ReviewAgentService(project_root=self.root).review_clip(
            project_id=project_id,
            clip_id="clip_001",
        )

        updated = clip_service.update_bounds("clip_001", 99.0, 139.0, project_id=project_id, project_root=self.root)

        self.assertEqual(updated["ai_start"], 100.0)
        self.assertEqual(updated["ai_end"], 140.0)
        self.assertIsNotNone(updated["reviewed_start"])
        self.assertEqual(updated["edited_start"], 99.0)
        self.assertEqual(updated["edited_end"], 139.0)
        self.assertEqual(updated["boundary_source"], "user")

    def test_unknown_provider_manual_review_is_not_presented_as_heuristic(self):
        project_id = self._seed_project()

        saved = save_evaluation(
            {
                "project_id": project_id,
                "clip_id": "clip_001",
                "decision": "manual_review",
                "recommended_action": "manual_review",
            }
        )

        self.assertEqual(saved["provider"], "unknown")
        self.assertEqual(saved["model"], "unknown")
        self.assertEqual(
            saved["review_provenance"],
            {"review_kind": "manual_review", "numeric_score_provenance": "not_available"},
        )
        with session_scope() as session:
            evaluation = session.scalars(select(ClipEvaluation)).one()
        self.assertEqual(evaluation.provider, "unknown")

    def test_unknown_review_without_legacy_scores_has_unknown_provenance(self):
        project_id = self._seed_project()
        with session_scope() as session:
            clip = ClipRepository(session).get_by_external_id(project_id, "clip_001")
            evaluation = ClipEvaluation(
                project_id=project_id,
                clip_id=clip.id,
                external_clip_id="clip_001",
                provider="unknown",
                decision="reviewed",
                recommended_action="manual_review",
            )
            session.add(evaluation)
            session.flush()
            serialized = evaluation_to_dict(evaluation)

        self.assertEqual(
            serialized["review_provenance"],
            {"review_kind": "unknown", "numeric_score_provenance": "not_available"},
        )

    def test_legacy_heuristic_evaluation_serializes_scores_without_changes(self):
        project_id = self._seed_project()
        with session_scope() as session:
            clip = ClipRepository(session).get_by_external_id(project_id, "clip_001")
            evaluation = ClipEvaluation(
                project_id=project_id,
                clip_id=clip.id,
                external_clip_id="clip_001",
                provider="local_stub",
                model="legacy-local",
                decision="render_ready",
                recommended_action="render_ready",
                quality_score=0.91,
                context_score=0.82,
                hook_score=0.73,
                payoff_score=0.64,
                boundary_score=0.55,
                privacy_risk="medium",
                crop_advice="wider_context",
                needs_more_context=True,
                raw_result_json={"provider": "local_stub", "model": "legacy-local"},
            )
            session.add(evaluation)
            session.flush()
            serialized = evaluation_to_dict(evaluation)

        self.assertEqual(
            {field: serialized[field] for field in (
                "quality_score", "context_score", "hook_score", "payoff_score", "boundary_score",
                "privacy_risk", "crop_advice", "needs_more_context",
            )},
            {
                "quality_score": 0.91,
                "context_score": 0.82,
                "hook_score": 0.73,
                "payoff_score": 0.64,
                "boundary_score": 0.55,
                "privacy_risk": "medium",
                "crop_advice": "wider_context",
                "needs_more_context": True,
            },
        )
        self.assertEqual(
            serialized["review_provenance"],
            {"review_kind": "legacy_heuristic", "numeric_score_provenance": "legacy_heuristic"},
        )

    def test_historical_gemini_zero_fillers_are_not_presented_as_numeric_scores(self):
        project_id = self._seed_project()
        with session_scope() as session:
            clip = ClipRepository(session).get_by_external_id(project_id, "clip_001")
            evaluation = ClipEvaluation(
                project_id=project_id,
                clip_id=clip.id,
                external_clip_id="clip_001",
                provider="gemini",
                model="historical-gemini",
                decision="render_ready",
                recommended_action="render_ready",
                quality_score=0.0,
                context_score=0.0,
                hook_score=0.0,
                payoff_score=0.0,
                boundary_score=0.0,
                privacy_risk="low",
                crop_advice="",
                needs_more_context=False,
                raw_result_json={"provider": "gemini", "model": "historical-gemini"},
            )
            session.add(evaluation)
            session.flush()
            serialized = evaluation_to_dict(evaluation)

        self.assertNotIn("quality_score", serialized)
        self.assertNotIn("crop_advice", serialized)
        self.assertEqual(
            serialized["review_provenance"],
            {"review_kind": "gemini_boundary_decision", "numeric_score_provenance": "not_available"},
        )

    def test_gemini_persistence_uses_decision_and_preserves_explicit_legacy_values(self):
        project_id = self._seed_project()

        save_evaluation(
            {
                "project_id": project_id,
                "clip_id": "clip_001",
                "provider": "gemini",
                "model": "gemini-unit",
                "decision": "reject",
                "recommended_action": "render_ready",
                "quality_score": 0.91,
                "context_score": 0.82,
                "hook_score": 0.73,
                "payoff_score": 0.64,
                "boundary_score": 0.55,
                "privacy_risk": "medium",
                "crop_advice": "wider_context",
                "needs_more_context": "false",
            }
        )

        with session_scope() as session:
            evaluation = session.scalars(select(ClipEvaluation)).one()
        self.assertEqual(evaluation.recommended_action, "reject")
        self.assertEqual(
            (
                evaluation.quality_score,
                evaluation.context_score,
                evaluation.hook_score,
                evaluation.payoff_score,
                evaluation.boundary_score,
                evaluation.privacy_risk,
                evaluation.crop_advice,
                evaluation.needs_more_context,
            ),
            (0.91, 0.82, 0.73, 0.64, 0.55, "medium", "wider_context", False),
        )
        clip = clip_service.load_clips(project_id=project_id, project_root=self.root)[0]
        self.assertEqual(clip["status"], "rejected")

    def test_missing_or_invalid_gemini_decision_never_persists_reviewed(self):
        project_id = self._seed_project()

        saved = save_evaluation(
            {
                "project_id": project_id,
                "clip_id": "clip_001",
                "provider": "gemini",
                "model": "gemini-unit",
            }
        )
        self.assertEqual(saved["decision"], "manual_review")
        self.assertEqual(saved["recommended_action"], "manual_review")

        with self.assertRaisesRegex(ValueError, "Invalid Gemini decision"):
            save_evaluation(
                {
                    "project_id": project_id,
                    "clip_id": "clip_001",
                    "provider": "gemini",
                    "model": "gemini-unit",
                    "decision": "reviewed",
                }
            )

        with session_scope() as session:
            decisions = list(session.scalars(select(ClipEvaluation.decision)).all())
        self.assertEqual(decisions, ["manual_review"])

    def test_reject_and_manual_review_do_not_auto_apply_boundaries(self):
        project_id = self._seed_project()

        save_evaluation(
            {
                "project_id": project_id,
                "clip_id": "clip_001",
                "provider": "gemini",
                "model": "gemini-unit",
                "decision": "reject",
                "recommended_action": "reject",
                "reasoning_summary": "Weak clip.",
                "start_reason": "No usable standalone start.",
                "end_reason": "No payoff.",
                "warnings": [],
                "reviewed_start": None,
                "reviewed_end": None,
            }
        )

        rejected_clip = clip_service.load_clips(project_id=project_id, project_root=self.root)[0]
        self.assertEqual(rejected_clip["status"], "rejected")
        self.assertIsNone(rejected_clip["reviewed_start"])
        self.assertEqual(rejected_clip["edited_start"], 100.0)
        self.assertEqual(rejected_clip["boundary_source"], "heuristic")

        with session_scope() as session:
            ClipRepository(session).create_from_dict(
                project_id,
                {
                    "id": "clip_002",
                    "index": 2,
                    "ai_start": 200.0,
                    "ai_end": 240.0,
                    "edited_start": 200.0,
                    "edited_end": 240.0,
                    "min_start": 180.0,
                    "max_start": 220.0,
                    "min_end": 220.0,
                    "max_end": 260.0,
                    "summary": "Manual review clip.",
                    "text": "This needs a person.",
                    "status": "draft",
                    "render_status": "not_rendered",
                },
            )

        save_evaluation(
            {
                "project_id": project_id,
                "clip_id": "clip_002",
                "provider": "gemini",
                "model": "gemini-unit",
                "decision": "manual_review",
                "recommended_action": "manual_review",
                "reasoning_summary": "Ambiguous transcript.",
                "start_reason": "Needs a human.",
                "end_reason": "Needs a human.",
                "warnings": ["Manual review required."],
                "reviewed_start": None,
                "reviewed_end": None,
            }
        )

        manual_clip = [
            clip for clip in clip_service.load_clips(project_id=project_id, project_root=self.root) if clip["id"] == "clip_002"
        ][0]
        self.assertIsNone(manual_clip["reviewed_start"])
        self.assertEqual(manual_clip["edited_start"], 200.0)
        self.assertEqual(manual_clip["boundary_source"], "heuristic")

    def test_batch_endpoint_reviews_all_clips_with_gemini_and_returns_summary(self):
        self._write_transcript(
            [
                {"start": 95.0, "end": 100.0, "text": "Setup one."},
                {"start": 100.0, "end": 120.0, "text": "Candidate one starts."},
                {"start": 120.0, "end": 140.0, "text": "Candidate one ends."},
                {"start": 195.0, "end": 200.0, "text": "Setup two."},
                {"start": 200.0, "end": 220.0, "text": "Candidate two starts."},
                {"start": 220.0, "end": 240.0, "text": "Candidate two ends."},
                {"start": 300.0, "end": 320.0, "text": "Candidate three starts."},
                {"start": 320.0, "end": 340.0, "text": "Candidate three ends."},
            ]
        )
        project_id = self._seed_project(
            [
                {"id": "clip_001", "index": 1, "ai_start": 100.0, "ai_end": 140.0},
                {"id": "clip_002", "index": 2, "ai_start": 200.0, "ai_end": 240.0},
                {"id": "clip_003", "index": 3, "ai_start": 300.0, "ai_end": 340.0},
            ]
        )
        os.environ["CLIP_REVIEW_MODE"] = "gemini"
        os.environ["GEMINI_API_KEY"] = "test-key"
        os.environ["GEMINI_MODEL"] = "gemini-batch"

        class BatchGemini:
            provider = "gemini"

            def __init__(self, *, api_key, model):
                self.model = model

            def review(self, context):
                clip_id = context["clip_id"]
                if clip_id == "clip_001":
                    return _decision_from_option_indexes(context,
                        decision="render_ready",
                        selected_start_option_index=context["current_aligned_start_option_index"],
                        selected_end_option_index=context["current_aligned_end_option_index"],
                        reasoning_summary="Ready.",
                        start_reason="Good start.",
                        end_reason="Good end.",
                        warnings=[],
                    )
                if clip_id == "clip_002":
                    return _decision_from_option_indexes(context,
                        decision="adjust_boundaries",
                        selected_start_option_index=_option_index_for_segment(
                            context,
                            "start_boundary_options",
                            context["context_before"][-1]["segment_id"],
                        ),
                        selected_end_option_index=_option_index_for_segment(
                            context,
                            "end_boundary_options",
                            context["candidate_segments"][-1]["segment_id"],
                        ),
                        reasoning_summary="Needs setup.",
                        start_reason="Include setup.",
                        end_reason="Candidate ending works.",
                        warnings=[],
                    )
                return _decision_from_option_indexes(context,
                    decision="reject",
                    selected_start_option_index=context["current_aligned_start_option_index"],
                    selected_end_option_index=context["current_aligned_end_option_index"],
                    reasoning_summary="No standalone idea.",
                    start_reason="Weak start.",
                    end_reason="Weak end.",
                    warnings=[],
                )

        with patch("apps.review_agent.service.GeminiBoundaryReviewer", BatchGemini):
            with TestClient(app) as client:
                response = client.post(f"/projects/{project_id}/review-clips")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["provider"], "gemini")
        self.assertEqual(payload["model"], "gemini-batch")
        self.assertEqual(payload["clip_count"], 3)
        self.assertEqual(payload["success_count"], 3)
        self.assertEqual(payload["render_ready_count"], 1)
        self.assertEqual(payload["adjust_boundaries_count"], 1)
        self.assertEqual(payload["reject_count"], 1)
        self.assertEqual(payload["manual_review_count"], 0)
        self.assertEqual(payload["failed_count"], 0)
        with session_scope() as session:
            evaluations = list(session.scalars(select(ClipEvaluation)).all())
        self.assertEqual(len(evaluations), 3)
        self.assertEqual({evaluation.provider for evaluation in evaluations}, {"gemini"})

    def test_batch_endpoint_uses_dotenv_gemini_config_when_process_env_missing(self):
        project_id = self._seed_project()
        for key in ("CLIP_REVIEW_MODE", "GEMINI_API_KEY", "GEMINI_MODEL"):
            os.environ.pop(key, None)
        (self.root / ".env").write_text(
            "CLIP_REVIEW_MODE=gemini\nGEMINI_API_KEY=test-key\nGEMINI_MODEL=gemini-dotenv-batch\n",
            encoding="utf-8",
        )

        class DotenvGemini:
            provider = "gemini"

            def __init__(self, *, api_key, model):
                self.model = model

            def review(self, context, corrective_message=None):
                return _decision_from_option_indexes(context,
                    decision="render_ready",
                    selected_start_option_index=context["current_aligned_start_option_index"],
                    selected_end_option_index=context["current_aligned_end_option_index"],
                    reasoning_summary="Ready from dotenv Gemini config.",
                    start_reason="Aligned start.",
                    end_reason="Aligned end.",
                    warnings=[],
                )

        with patch("apps.review_agent.service.GeminiBoundaryReviewer", DotenvGemini):
            with TestClient(app) as client:
                response = client.post(f"/projects/{project_id}/review-clips")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["provider"], "gemini")
        self.assertEqual(payload["model"], "gemini-dotenv-batch")
        self.assertEqual(payload["clip_count"], 1)
        self.assertEqual(payload["failed_count"], 0)
        with session_scope() as session:
            evaluation = session.scalars(select(ClipEvaluation)).one()
        self.assertEqual(evaluation.provider, "gemini")
        self.assertEqual(evaluation.model, "gemini-dotenv-batch")

    def test_batch_endpoint_counts_backend_manual_review_failures_separately(self):
        self._write_transcript(
            [
                {"start": 100.0, "end": 120.0, "text": "Candidate one starts."},
                {"start": 120.0, "end": 140.0, "text": "Candidate one ends."},
                {"start": 200.0, "end": 220.0, "text": "Candidate two starts."},
                {"start": 220.0, "end": 240.0, "text": "Candidate two ends."},
            ]
        )
        project_id = self._seed_project(
            [
                {"id": "clip_001", "index": 1, "ai_start": 100.0, "ai_end": 140.0},
                {"id": "clip_002", "index": 2, "ai_start": 200.0, "ai_end": 240.0},
            ]
        )
        os.environ["CLIP_REVIEW_MODE"] = "gemini"
        os.environ["GEMINI_API_KEY"] = "test-key"

        class MixedGemini:
            provider = "gemini"

            def __init__(self, *, api_key, model):
                self.model = model

            def review(self, context, corrective_message=None):
                if context["clip_id"] == "clip_002":
                    raise ReviewProviderError("Gemini API error for one clip")
                return _decision_from_option_indexes(context,
                    decision="render_ready",
                    selected_start_option_index=context["current_aligned_start_option_index"],
                    selected_end_option_index=context["current_aligned_end_option_index"],
                    reasoning_summary="Ready.",
                    start_reason="Good start.",
                    end_reason="Good end.",
                    warnings=[],
                )

        with patch("apps.review_agent.service.GeminiBoundaryReviewer", MixedGemini):
            with TestClient(app) as client:
                response = client.post(f"/projects/{project_id}/review-clips")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["render_ready_count"], 1)
        self.assertEqual(payload["reject_count"], 0)
        self.assertEqual(payload["manual_review_count"], 1)
        self.assertEqual(payload["failed_count"], 1)
        self.assertEqual(payload["success_count"], 1)
        self.assertEqual(payload["applied_count"], 1)
        self.assertEqual(payload["requires_attention_count"], 1)
        self.assertEqual(
            payload["summary_message"],
            "Gemini review finished: 1 applied, 1 requires attention.",
        )
        failed_review = [review for review in payload["reviews"] if review["failed"]][0]
        self.assertEqual(failed_review["decision"], "manual_review")
        self.assertEqual(failed_review["failure_reason"], "Gemini provider request failed.")

    def test_all_failed_batch_summary_reports_attention_not_success(self):
        project_id = self._seed_project()
        os.environ["CLIP_REVIEW_MODE"] = "gemini"
        os.environ["GEMINI_API_KEY"] = "test-key"

        class IncompatibleGemini:
            provider = "gemini"
            model = "gemini-test"

            def __init__(self, **_kwargs):
                pass

            def review(self, _context, **_kwargs):
                raise ReviewProviderCompatibilityError(
                    "Gemini provider compatibility error (HTTP 400)."
                )

        with patch("apps.review_agent.service.GeminiBoundaryReviewer", IncompatibleGemini):
            with TestClient(app) as client:
                response = client.post(f"/projects/{project_id}/review-clips")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["success_count"], 0)
        self.assertEqual(payload["applied_count"], 0)
        self.assertEqual(payload["failed_count"], 1)
        self.assertEqual(payload["requires_attention_count"], 1)
        self.assertEqual(
            payload["summary_message"],
            "Gemini review finished: 0 applied, 1 requires attention.",
        )
        self.assertNotIn("completed with Gemini", str(payload))

    def test_clip_response_exposes_frontend_review_contract(self):
        project_id = self._seed_project()
        ReviewAgentService(project_root=self.root).review_clip(
            project_id=project_id,
            clip_id="clip_001",
        )

        with TestClient(app) as client:
            response = client.get("/clips")

        self.assertEqual(response.status_code, 200)
        clip = response.json()["clips"][0]
        for key in (
            "ai_start",
            "ai_end",
            "reviewed_start",
            "reviewed_end",
            "edited_start",
            "edited_end",
            "boundary_source",
            "latest_review_provider",
            "latest_review_model",
            "latest_review_decision",
            "latest_review_reasoning_summary",
            "latest_review_start_reason",
            "latest_review_end_reason",
            "latest_review_warnings",
            "latest_review_changed_boundaries",
        ):
            self.assertIn(key, clip)
        index_html = (Path(__file__).resolve().parents[1] / "apps" / "api" / "static" / "index.html").read_text(
            encoding="utf-8"
        )
        app_js = (Path(__file__).resolve().parents[1] / "apps" / "api" / "static" / "app.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("Review all with AI", index_html)
        self.assertIn("/review-clips", app_js)
        self.assertIn("configuredReviewProvider", index_html + app_js)
        self.assertIn("lastReviewProvider", index_html + app_js)
        self.assertIn('fetch("/health")', app_js)
        self.assertIn("updateHistoricalReviewProvider(payload.provider || state.configuredReviewProvider)", app_js)
        self.assertNotIn("Apply suggestion", index_html + app_js)

    def test_airflow_helper_imports_without_airflow_and_calls_batch_service(self):
        project_id = self._seed_project()
        dag_path = Path(__file__).resolve().parents[1] / "orchestration" / "airflow" / "dags" / "podcast_pipeline_dag.py"
        spec = importlib.util.spec_from_file_location("podcast_pipeline_dag_test", dag_path)
        module = importlib.util.module_from_spec(spec)
        self.assertIsNotNone(spec.loader)
        spec.loader.exec_module(module)

        self.assertTrue(hasattr(module, "AIRFLOW_AVAILABLE"))
        if not module.AIRFLOW_AVAILABLE:
            self.assertIsNone(module.podcast_pipeline)

        from orchestration.airflow import pipeline_tasks

        calls = []

        class FakeService:
            def __init__(self, *, project_root, mode):
                calls.append({"project_root": project_root, "mode": mode})

            def review_project_clips(self, *, project_id, apply_safe_suggestions):
                return {"project_id": project_id, "provider": "gemini", "clip_count": 1}

        with patch("apps.pipeline.stages.review_candidates.ReviewAgentService", FakeService):
            config = pipeline_tasks.review_candidates_with_gemini(
                {"project_id": project_id, "clip_review_mode": "gemini"}
            )

        self.assertEqual(calls[0]["mode"], "gemini")
        self.assertEqual(config["review_summary"]["provider"], "gemini")

    def test_sensitive_pattern_checker_remains_available_for_legacy_tools(self):
        result = check_sensitive_patterns("Reach me at person@example.com or +48 123 456 789 about the invoice.")

        self.assertEqual(result["privacy_risk"], "medium")
        self.assertIn("email", {match["type"] for match in result["matches"]})
        self.assertIn("phone", {match["type"] for match in result["matches"]})

    def test_project_status_endpoint_works(self):
        project_id = self._seed_project()
        with session_scope() as session:
            JobRepository(session).create(
                project_id=project_id,
                job_type="airflow_pipeline",
                status="failed",
                stage="transcribing",
                error_message="boom",
            )

        with TestClient(app) as client:
            response = client.get(f"/projects/{project_id}/status")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["project_id"], project_id)
        self.assertEqual(payload["clip_count"], 1)
        self.assertEqual(payload["last_error"], "boom")


if __name__ == "__main__":
    unittest.main()
