import csv
import json
import tempfile
import unittest
from pathlib import Path

from benchmark import (
    BenchmarkCase,
    count_overlapping_windows,
    load_cases,
    summarize_selection_metrics,
    validate_case_inputs,
    write_human_review_template,
    write_json,
)


class BenchmarkHelpersTests(unittest.TestCase):
    def test_overlap_counts_near_identical_windows(self):
        left = [
            {"start": 10.0, "end": 40.0},
            {"start": 100.0, "end": 130.0},
        ]
        right = [
            {"start": 10.4, "end": 40.1},
            {"start": 99.5, "end": 129.8},
        ]
        self.assertEqual(count_overlapping_windows(left, right), 2)

    def test_summarize_selection_metrics_collects_scores_and_reasons(self):
        windows = [
            {
                "start": 10.0,
                "end": 40.0,
                "duration": 30.0,
                "local_score": 91.2,
                "selection_reasons": ["strong heatmap support", "good speech density for a short clip"],
                "summary": "Clip one",
            },
            {
                "start": 100.0,
                "end": 140.0,
                "duration": 40.0,
                "local_score": 88.0,
                "selection_reasons": ["strong heatmap support"],
                "summary": "Clip two",
            },
        ]
        summary = summarize_selection_metrics(windows, material_duration=200.0)
        self.assertEqual(summary["clip_count"], 2)
        self.assertEqual(summary["score_distribution"]["max"], 91.2)
        self.assertEqual(summary["selection_reason_counts"]["strong heatmap support"], 2)
        self.assertGreater(summary["temporal_metrics"]["temporal_coverage_ratio"], 0.0)

    def test_write_outputs_create_files(self):
        rows = [
            {
                "case_id": "case_a",
                "case_label": "Case A",
                "expected_content_type": "gameplay",
                "scenario_id": "auto",
                "scenario_label": "auto classification",
                "clip_index": 1,
                "clip_start": "00:10.00",
                "clip_end": "00:40.00",
                "local_score": 91.2,
                "clip_file": "benchmarks/runs/test/case_a/auto/clip.mp4",
                "human_relevance_score": "",
                "human_boundary_score": "",
                "human_crop_score": "",
                "notes": "",
            }
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            json_path = base / "results.json"
            csv_path = base / "review.csv"
            write_json(json_path, {"ok": True})
            write_human_review_template(csv_path, rows)

            self.assertTrue(json_path.exists())
            self.assertTrue(csv_path.exists())
            with open(csv_path, "r", encoding="utf-8", newline="") as file_handle:
                records = list(csv.DictReader(file_handle))
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["case_id"], "case_a")

    def test_validate_case_inputs_reports_missing_materials(self):
        missing = Path(tempfile.gettempdir()) / "definitely_missing_benchmark_asset.mp4"
        case = BenchmarkCase(
            case_id="missing_case",
            label="Missing case",
            expected_content_type="podcast",
            source_url="",
            description="",
            video=missing,
            audio=missing.with_suffix(".mp3"),
            heatmap=missing.with_suffix(".json"),
            info_json=None,
            transcript_source=None,
            expected_speaker_mode="multi",
            comparison_content_types=[],
            include_generic_baseline=True,
            notes="",
        )
        issues = validate_case_inputs(case)
        self.assertGreaterEqual(len(issues), 3)
        self.assertTrue(any("Missing video file" in issue for issue in issues))

    def test_validate_case_inputs_allows_missing_transcript_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            video = base / "clip.mp4"
            heatmap = base / "heatmap.json"
            video.write_bytes(b"video")
            heatmap.write_text("[]", encoding="utf-8")
            case = BenchmarkCase(
                case_id="cache_case",
                label="Cache case",
                expected_content_type="generic",
                source_url="",
                description="",
                video=video,
                audio=None,
                heatmap=heatmap,
                info_json=None,
                transcript_source=base / "transcripts" / "final_transcript.json",
                expected_speaker_mode="single",
                comparison_content_types=[],
                include_generic_baseline=True,
                notes="",
            )
            self.assertEqual(validate_case_inputs(case), [])

    def test_load_cases_accepts_single_speaker_alias(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            config_path = base / "cases.json"
            config_path.write_text(
                json.dumps(
                    {
                        "cases": [
                            {
                                "id": "alias_case",
                                "expected_content_type": "generic",
                                "expected_speaker_mode": "single_speaker",
                                "video": "clip.mp4",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            cases = load_cases(config_path)
            self.assertEqual(len(cases), 1)
            self.assertEqual(cases[0].expected_speaker_mode, "single")


if __name__ == "__main__":
    unittest.main()
