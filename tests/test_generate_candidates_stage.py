from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from apps.api.services.clips import _normalize_window, extract_windows
from apps.pipeline.context import PipelineContext
from apps.pipeline.exceptions import CandidateGenerationError
from apps.pipeline.results import PipelineStageResult
from apps.pipeline.runner import PipelineRunner
from apps.pipeline.stages.generate_candidates import GenerateCandidatesStage
from candidate_windows import CandidateWindowConfig, generate_candidate_windows
from heatmap_contract import atomic_write_json


def peak_document(*, peaks: list[dict] | None = None) -> dict:
    return {
        "schema_version": 1,
        "source": "youtube_most_replayed",
        "algorithm": "offline_peak_detector",
        "algorithm_version": 1,
        "video_id": "offline-video",
        "duration_seconds": 240.0,
        "peaks": peaks
        if peaks is not None
        else [
            {
                "rank": 1,
                "peak_time": 125.0,
                "start_time": 120.0,
                "end_time": 130.0,
                "raw_value": 0.88,
                "smoothed_value": 0.81,
                "prominence": 0.22,
            }
        ],
    }


class RecordingStage:
    stage = "downstream"

    def __init__(self) -> None:
        self.called = False

    def run(self, context: PipelineContext) -> PipelineStageResult:
        self.called = True
        return PipelineStageResult(stage=self.stage, success=True, message="Unexpected run.")


class GenerateCandidatesStageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.context = PipelineContext.for_legacy_cli(
            source_url=None, repository_root=root, workspace_path=root / "workspace"
        )
        self.context.metadata_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def write_peaks(self, payload: dict | None = None) -> dict:
        payload = payload or peak_document()
        atomic_write_json(self.context.heatmap_peaks_file, payload)
        return payload

    def test_generates_canonical_document_and_minimal_compatibility_adapter(self) -> None:
        peaks = self.write_peaks()
        with patch(
            "apps.pipeline.stages.generate_candidates.generate_candidate_windows",
            wraps=generate_candidate_windows,
        ) as generator:
            result = GenerateCandidatesStage().run(self.context)

        canonical = json.loads(self.context.candidate_windows_file.read_text(encoding="utf-8"))
        adapter = json.loads(self.context.candidate_file.read_text(encoding="utf-8"))
        self.assertEqual(canonical, generate_candidate_windows(peaks))
        self.assertEqual(generator.call_args.args[1], CandidateWindowConfig())
        self.assertEqual(result.stage, "generating_candidates")
        self.assertEqual(
            result.produced_artifacts,
            ("metadata/candidate_windows.json", "top_windows.json"),
        )
        self.assertEqual(result.metadata["candidate_count"], 1)
        self.assertEqual(adapter["canonical_artifact"], "metadata/candidate_windows.json")
        window = adapter["top_windows"][0]
        candidate = canonical["candidates"][0]
        self.assertEqual(window["start"], candidate["start_time"])
        self.assertEqual(window["end"], candidate["end_time"])
        self.assertEqual(window["duration"], candidate["duration_seconds"])
        self.assertEqual(window["source_peak_rank"], candidate["source_peak_rank"])
        self.assertEqual(window["peak_time"], candidate["peak_time"])
        self.assertEqual(window["replay_interest"], candidate["replay_interest"])
        forbidden = {
            "local_score", "local_rank", "local_features", "selection_reasons", "reason",
            "summary", "text", "title", "viral_score", "semantic_score", "confidence",
            "candidate_id", "id",
        }
        self.assertTrue(forbidden.isdisjoint(window))

    def test_neutral_adapter_is_accepted_by_existing_import_parser(self) -> None:
        self.write_peaks()
        GenerateCandidatesStage().run(self.context)
        adapter = json.loads(self.context.candidate_file.read_text(encoding="utf-8"))
        clip = _normalize_window(extract_windows(adapter)[0], 1, "top_windows.json")
        self.assertEqual((clip["ai_start"], clip["ai_end"]), (95.0, 155.0))
        self.assertEqual(clip["boundary_source"], "replay_interest_peak")
        self.assertEqual(clip["selection_source"], "youtube_most_replayed")
        self.assertEqual(clip["summary"], "")
        self.assertEqual(clip["text"], "")
        self.assertIsNone(clip["local_score"])
        self.assertIsNone(clip["local_rank"])
        self.assertEqual(clip["local_features"], {})

    def test_empty_peaks_succeed_without_fallback(self) -> None:
        self.write_peaks(peak_document(peaks=[]))
        result = GenerateCandidatesStage().run(self.context)
        canonical = json.loads(self.context.candidate_windows_file.read_text(encoding="utf-8"))
        adapter = json.loads(self.context.candidate_file.read_text(encoding="utf-8"))
        self.assertTrue(result.success)
        self.assertEqual(canonical["candidates"], [])
        self.assertEqual(adapter["top_windows"], [])
        self.assertEqual(result.metadata["candidate_count"], 0)

    def test_missing_or_invalid_peak_document_preserves_original_cause_and_stops_runner(self) -> None:
        downstream = RecordingStage()
        result = PipelineRunner((GenerateCandidatesStage(), downstream)).run(self.context)
        self.assertFalse(result.success)
        self.assertEqual(result.failed_stage, "generating_candidates")
        self.assertFalse(downstream.called)

        self.context.heatmap_peaks_file.write_text("{invalid", encoding="utf-8")
        with self.assertRaises(CandidateGenerationError) as raised:
            GenerateCandidatesStage().run(self.context)
        self.assertIsInstance(raised.exception.__cause__, json.JSONDecodeError)

        self.write_peaks({"schema_version": 1})
        with self.assertRaises(CandidateGenerationError) as raised:
            GenerateCandidatesStage().run(self.context)
        self.assertIsInstance(raised.exception.__cause__, ValueError)

    def test_write_failures_preserve_existing_artifacts(self) -> None:
        self.write_peaks()
        previous_canonical = b'{"canonical": "old"}\n'
        previous_adapter = b'{"adapter": "old"}\n'
        self.context.candidate_windows_file.write_bytes(previous_canonical)
        self.context.candidate_file.write_bytes(previous_adapter)
        write_error = OSError("offline write failure")

        with patch(
            "apps.pipeline.stages.generate_candidates.atomic_write_json",
            side_effect=write_error,
        ):
            with self.assertRaises(CandidateGenerationError) as raised:
                GenerateCandidatesStage().run(self.context)
        self.assertIs(raised.exception.__cause__, write_error)
        self.assertEqual(self.context.candidate_windows_file.read_bytes(), previous_canonical)
        self.assertEqual(self.context.candidate_file.read_bytes(), previous_adapter)

        calls = 0

        def fail_adapter(path, payload):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise write_error
            atomic_write_json(path, payload)

        with patch(
            "apps.pipeline.stages.generate_candidates.atomic_write_json",
            side_effect=fail_adapter,
        ):
            with self.assertRaises(CandidateGenerationError) as raised:
                GenerateCandidatesStage().run(self.context)
        self.assertIs(raised.exception.__cause__, write_error)
        self.assertEqual(self.context.candidate_file.read_bytes(), previous_adapter)


if __name__ == "__main__":
    unittest.main()
