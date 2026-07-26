from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from apps.api.orchestration.local import LocalPipelineOrchestrator
from apps.api.services.project_service import STAGE_MESSAGES as PROJECT_STAGE_MESSAGES
from apps.pipeline.context import PipelineContext
from apps.pipeline.events import PipelineEvent, STAGE_MESSAGES, STAGE_PROGRESS
from apps.pipeline.exceptions import HeatmapPeakDetectionError
from apps.pipeline.profiles import legacy_cli_stages, project_pipeline_stages
from apps.pipeline.registry import DEFAULT_STAGE_REGISTRY
from apps.pipeline.results import PipelineStageResult
from apps.pipeline.runner import PipelineRunner
from apps.pipeline.stages.detect_heatmap_peaks import DetectHeatmapPeaksStage
from heatmap_contract import HeatmapUnavailableError, atomic_write_json
from heatmap_peaks import PeakDetectorConfig, detect_heatmap_peaks


def trusted_heatmap(values: list[float]) -> dict:
    points = [
        {
            "start_time": index * 10.0,
            "end_time": (index + 1) * 10.0,
            "value": value,
        }
        for index, value in enumerate(values)
    ]
    return {
        "schema_version": 1,
        "source": "youtube_most_replayed",
        "synthetic": False,
        "video_id": "offline-video",
        "extractor": "youtube",
        "extractor_version": "offline-test",
        "duration_seconds": len(points) * 10.0,
        "points": points,
    }


class RecordingStage:
    stage = "transcribing"

    def __init__(self) -> None:
        self.called = False

    def run(self, context: PipelineContext) -> PipelineStageResult:
        self.called = True
        return PipelineStageResult(stage=self.stage, success=True, message="Unexpected run.")


class DetectHeatmapPeaksStageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.context = PipelineContext.for_legacy_cli(
            source_url="https://example.invalid/watch",
            repository_root=self.root,
            workspace_path=self.root / "workspace",
        )
        self.context.metadata_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def write_heatmap(self, values: list[float]) -> None:
        atomic_write_json(self.context.heatmap_file, trusted_heatmap(values))

    def test_writes_versioned_detector_document_with_default_configuration(self) -> None:
        self.write_heatmap([0.1, 0.1, 0.1, 0.9, 0.1, 0.1, 0.1])
        with patch(
            "apps.pipeline.stages.detect_heatmap_peaks.detect_heatmap_peaks",
            wraps=detect_heatmap_peaks,
        ) as detector:
            result = DetectHeatmapPeaksStage().run(self.context)

        self.assertTrue(result.success)
        self.assertEqual(result.stage, "detecting_heatmap_peaks")
        self.assertEqual(result.produced_artifacts, ("metadata/heatmap_peaks.json",))
        self.assertEqual(detector.call_args.args[1], PeakDetectorConfig())
        stored = json.loads(self.context.heatmap_peaks_file.read_text(encoding="utf-8"))
        self.assertEqual(stored, detect_heatmap_peaks(trusted_heatmap([0.1, 0.1, 0.1, 0.9, 0.1, 0.1, 0.1])))
        self.assertEqual(result.metadata["peak_count"], len(stored["peaks"]))
        self.assertEqual(result.metadata["algorithm"], stored["algorithm"])
        self.assertEqual(result.metadata["algorithm_version"], stored["algorithm_version"])

    def test_flat_heatmap_is_a_successful_empty_result(self) -> None:
        self.write_heatmap([0.5, 0.5, 0.5])

        result = DetectHeatmapPeaksStage().run(self.context)

        self.assertTrue(result.success)
        self.assertTrue(self.context.heatmap_peaks_file.exists())
        self.assertEqual(json.loads(self.context.heatmap_peaks_file.read_text(encoding="utf-8"))["peaks"], [])
        self.assertEqual(result.metadata["peak_count"], 0)

    def test_missing_malformed_or_provenance_free_heatmap_is_unavailable(self) -> None:
        cases = (None, "{malformed", json.dumps([{ "start_time": 0, "value": 1 }]))
        for payload in cases:
            with self.subTest(payload=payload):
                self.context.heatmap_file.unlink(missing_ok=True)
                self.context.heatmap_peaks_file.unlink(missing_ok=True)
                if payload is not None:
                    self.context.heatmap_file.write_text(payload, encoding="utf-8")

                with self.assertRaises(HeatmapUnavailableError):
                    DetectHeatmapPeaksStage().run(self.context)
                self.assertFalse(self.context.heatmap_peaks_file.exists())

    def test_write_failure_preserves_cause_and_existing_artifact(self) -> None:
        self.write_heatmap([0.1, 0.1, 0.1])
        previous = b'{"previous": true}\n'
        self.context.heatmap_peaks_file.write_bytes(previous)
        write_error = OSError("offline write failure")

        with patch(
            "apps.pipeline.stages.detect_heatmap_peaks.atomic_write_json",
            side_effect=write_error,
        ):
            with self.assertRaises(HeatmapPeakDetectionError) as raised:
                DetectHeatmapPeaksStage().run(self.context)

        self.assertIs(raised.exception.__cause__, write_error)
        self.assertNotIsInstance(raised.exception, HeatmapUnavailableError)
        self.assertEqual(self.context.heatmap_peaks_file.read_bytes(), previous)

    def test_missing_heatmap_stops_runner_before_transcription(self) -> None:
        downstream = RecordingStage()

        result = PipelineRunner((DetectHeatmapPeaksStage(), downstream)).run(self.context)

        self.assertFalse(result.success)
        self.assertEqual(result.failed_stage, "detecting_heatmap_peaks")
        self.assertEqual(result.error_code, "heatmap_unavailable")
        self.assertFalse(downstream.called)


class DetectHeatmapPeaksRegistrationTests(unittest.TestCase):
    def test_registry_profiles_and_project_order_include_stage_once_in_position(self) -> None:
        context = PipelineContext.for_legacy_cli(
            source_url=None,
            repository_root=Path.cwd(),
            workspace_path=Path.cwd() / "offline-workspace",
        )
        self.assertEqual(DEFAULT_STAGE_REGISTRY.create("detect_heatmap_peaks").stage, "detecting_heatmap_peaks")
        for stages in (legacy_cli_stages(context), project_pipeline_stages(context)):
            names = [stage.stage for stage in stages]
            self.assertEqual(names.count("detecting_heatmap_peaks"), 1)
            self.assertEqual(names.index("detecting_heatmap_peaks"), names.index("downloading") + 1)
            self.assertEqual(names.index("transcribing"), names.index("detecting_heatmap_peaks") + 1)
    def test_status_and_local_orchestrator_accept_running_peak_stage(self) -> None:
        self.assertEqual(STAGE_PROGRESS["detecting_heatmap_peaks"], 20.0)
        self.assertEqual(STAGE_MESSAGES["detecting_heatmap_peaks"], "Detecting replay-interest peaks")
        self.assertEqual(PROJECT_STAGE_MESSAGES["detecting_heatmap_peaks"], "Detecting replay-interest peaks")
        orchestrator = LocalPipelineOrchestrator()
        event = PipelineEvent(
            event="stage_started",
            stage="detecting_heatmap_peaks",
            message="Detecting replay-interest peaks",
            progress_percent=20.0,
        )
        with patch.object(orchestrator, "_mark_running_stage") as mark_running:
            orchestrator._apply_pipeline_event(1, 2, event)
        mark_running.assert_called_once_with(1, 2, "detecting_heatmap_peaks", progress_percent=20.0)


if __name__ == "__main__":
    unittest.main()
