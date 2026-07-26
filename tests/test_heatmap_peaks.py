from __future__ import annotations

import copy
import math
import unittest

from heatmap_contract import build_youtube_heatmap
from heatmap_peaks import PeakDetectorConfig, detect_heatmap_peaks


def document(values: list[float], *, width: float = 10.0) -> dict:
    points = [
        {
            "start_time": index * width,
            "end_time": (index + 1) * width,
            "value": value,
        }
        for index, value in enumerate(values)
    ]
    return build_youtube_heatmap(
        {
            "id": "video-123",
            "extractor": "youtube",
            "duration": len(points) * width,
            "heatmap": points,
        },
        extractor_version="test",
    )


def config(**overrides: object) -> PeakDetectorConfig:
    defaults: dict[str, object] = {
        "smoothing_radius_seconds": 0.0,
        "prominence_window_seconds": 1000.0,
        "min_prominence": 0.0,
        "min_distance_seconds": 0.0,
        "max_peaks": 20,
    }
    defaults.update(overrides)
    return PeakDetectorConfig(**defaults)


class HeatmapPeakTests(unittest.TestCase):
    def peaks(self, values: list[float], **overrides: object) -> list[dict]:
        return detect_heatmap_peaks(document(values), config(**overrides))["peaks"]

    def test_single_clear_peak(self) -> None:
        peaks = self.peaks([0.1, 0.8, 0.2])
        self.assertEqual(len(peaks), 1)
        self.assertEqual(peaks[0]["peak_time"], 15.0)
        self.assertAlmostEqual(peaks[0]["prominence"], 0.6)

    def test_flat_and_monotonic_heatmaps_have_no_peaks(self) -> None:
        for values in ([0.5, 0.5, 0.5], [0.1, 0.2, 0.3], [0.3, 0.2, 0.1]):
            with self.subTest(values=values):
                self.assertEqual(self.peaks(values), [])

    def test_plateau_returns_one_deterministic_peak(self) -> None:
        peaks = self.peaks([0.0, 0.9, 0.9, 0.0])
        self.assertEqual(len(peaks), 1)
        self.assertEqual(peaks[0]["peak_time"], 15.0)

    def test_temporal_nms_keeps_stronger_close_peak(self) -> None:
        peaks = self.peaks(
            [0.0, 0.9, 0.0, 0.8, 0.0], min_distance_seconds=30.0
        )
        self.assertEqual([peak["raw_value"] for peak in peaks], [0.9])

    def test_temporal_nms_keeps_distant_peaks(self) -> None:
        peaks = self.peaks(
            [0.0, 0.9, 0.0, 0.0, 0.0, 0.0, 0.8, 0.0],
            min_distance_seconds=30.0,
        )
        self.assertEqual([peak["raw_value"] for peak in peaks], [0.9, 0.8])

    def test_min_prominence_filters_candidates(self) -> None:
        peaks = self.peaks(
            [0.0, 0.5, 0.4, 0.6, 0.4, 0.5, 0.0],
            min_prominence=0.15,
            prominence_window_seconds=10.1,
        )
        self.assertEqual([peak["raw_value"] for peak in peaks], [0.6])

    def test_max_peaks_and_tie_break_are_deterministic(self) -> None:
        peaks = self.peaks(
            [0.0, 0.8, 0.0, 0.8, 0.0, 0.8, 0.0], max_peaks=2
        )
        self.assertEqual([peak["peak_time"] for peak in peaks], [15.0, 35.0])
        self.assertEqual([peak["rank"] for peak in peaks], [1, 2])

    def test_smoothing_uses_interval_coverage_not_point_count(self) -> None:
        trusted = build_youtube_heatmap(
            {
                "id": "video-123",
                "extractor": "youtube",
                "duration": 12.0,
                "heatmap": [
                    {"start_time": 0.0, "end_time": 1.0, "value": 0.0},
                    {"start_time": 1.0, "end_time": 11.0, "value": 1.0},
                    {"start_time": 11.0, "end_time": 12.0, "value": 0.0},
                ],
            },
            extractor_version="test",
        )
        peak = detect_heatmap_peaks(
            trusted,
            config(smoothing_radius_seconds=5.1),
        )["peaks"][0]
        self.assertAlmostEqual(peak["smoothed_value"], 10.0 / 10.2)

    def test_input_is_not_modified_and_result_preserves_metadata_and_parameters(self) -> None:
        trusted = document([0.0, 0.9, 0.0])
        original = copy.deepcopy(trusted)
        result = detect_heatmap_peaks(trusted, config(min_distance_seconds=12.0))
        self.assertEqual(trusted, original)
        self.assertEqual(result["video_id"], "video-123")
        self.assertEqual(result["duration_seconds"], 30.0)
        self.assertEqual(result["parameters"]["min_distance_seconds"], 12.0)
        self.assertEqual(result["algorithm"], "time_weighted_local_prominence")

    def test_invalid_configuration_is_rejected(self) -> None:
        invalid = (
            {"smoothing_radius_seconds": -1.0},
            {"prominence_window_seconds": math.inf},
            {"min_prominence": 1.1},
            {"min_distance_seconds": -0.1},
            {"max_peaks": 0},
        )
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    PeakDetectorConfig(**kwargs)

    def test_repeated_runs_are_identical(self) -> None:
        trusted = document([0.0, 0.8, 0.1, 0.7, 0.0])
        detector_config = config(min_distance_seconds=30.0)
        self.assertEqual(
            detect_heatmap_peaks(trusted, detector_config),
            detect_heatmap_peaks(trusted, detector_config),
        )


if __name__ == "__main__":
    unittest.main()
