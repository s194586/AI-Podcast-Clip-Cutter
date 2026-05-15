import csv
import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmark import (
    BenchmarkCase,
    annotate_case_duplicates,
    build_case_scenarios,
    build_next_human_review_targets,
    count_overlapping_windows,
    extract_human_review_rows_from_results,
    load_cases,
    merge_human_review_rows,
    summarize_human_review,
    summarize_rendering_metrics,
    summarize_transcript_metrics,
    summarize_selection_metrics,
    summarize_subtitle_styles,
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

    def test_annotate_case_duplicates_prefers_auto_when_scores_are_close(self):
        case_payload = {
            "case_id": "case_a",
            "scenarios": [
                {
                    "scenario_id": "auto",
                    "status": "completed",
                    "selection": {
                        "clips": [
                            {
                                "index": 1,
                                "start": 10.0,
                                "end": 40.0,
                                "start_label": "00:10.00",
                                "end_label": "00:40.00",
                                "duration": 30.0,
                                "local_score": 91.0,
                            }
                        ]
                    },
                },
                {
                    "scenario_id": "manual_gameplay",
                    "status": "completed",
                    "selection": {
                        "clips": [
                            {
                                "index": 1,
                                "start": 10.5,
                                "end": 39.8,
                                "start_label": "00:10.50",
                                "end_label": "00:39.80",
                                "duration": 29.3,
                                "local_score": 92.4,
                            }
                        ]
                    },
                },
            ],
        }
        summary = annotate_case_duplicates(case_payload)
        auto_clip = case_payload["scenarios"][0]["selection"]["clips"][0]
        manual_clip = case_payload["scenarios"][1]["selection"]["clips"][0]

        self.assertEqual(summary["duplicates_removed"], 1)
        self.assertFalse(auto_clip["deduped"])
        self.assertTrue(manual_clip["deduped"])
        self.assertIn("case_a:auto:1", manual_clip["duplicate_of"])
        self.assertGreaterEqual(manual_clip["overlap_ratio"], 0.67)

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

    def test_build_case_scenarios_defaults_to_auto_only(self):
        case = BenchmarkCase(
            case_id="case_a",
            label="Case A",
            expected_content_type="gameplay",
            source_url="",
            description="",
            video=Path("clip.mp4"),
            audio=None,
            heatmap=None,
            info_json=None,
            transcript_source=None,
            expected_speaker_mode="single",
            comparison_content_types=["podcast"],
            include_generic_baseline=True,
            notes="",
        )
        scenarios = build_case_scenarios(case)
        self.assertEqual([item["id"] for item in scenarios], ["auto"])

    def test_build_case_scenarios_can_include_compare_variants(self):
        case = BenchmarkCase(
            case_id="case_a",
            label="Case A",
            expected_content_type="gameplay",
            source_url="",
            description="",
            video=Path("clip.mp4"),
            audio=None,
            heatmap=None,
            info_json=None,
            transcript_source=None,
            expected_speaker_mode="single",
            comparison_content_types=["podcast"],
            include_generic_baseline=True,
            notes="",
        )
        scenarios = build_case_scenarios(case, include_compare_strategies=True)
        self.assertEqual(
            [item["id"] for item in scenarios],
            ["auto", "manual_gameplay", "compare_podcast", "compare_generic"],
        )

    def test_summarize_transcript_metrics_keeps_diarization_diagnostics(self):
        segments = [
            {"start": 0.0, "end": 2.0, "text": "one", "speaker": "Speaker 0"},
            {"start": 2.0, "end": 4.0, "text": "two", "speaker": "Speaker 0"},
            {"start": 4.0, "end": 6.0, "text": "three", "speaker": "Speaker 1"},
        ]
        metadata = {
            "speaker_count": 2,
            "diarization_status": "applied_multi_speaker",
            "raw_cluster_count": 4,
            "final_speaker_count": 2,
            "single_speaker_likelihood": 0.32,
            "multi_speaker_evidence": 0.71,
            "clusters_merged": 2,
            "tiny_clusters_removed": 2,
            "decision_reason": "kept_multi_speaker_clusters",
        }
        summary = summarize_transcript_metrics(
            segments,
            metadata,
            expected_speaker_mode="multi",
        )
        self.assertEqual(summary["raw_cluster_count"], 4)
        self.assertEqual(summary["final_speaker_count"], 2)
        self.assertEqual(summary["decision_reason"], "kept_multi_speaker_clusters")

    def test_summarize_subtitle_styles_reports_smoothing_metadata(self):
        transcript_payload = {
            "segments": [
                {"start": 0.0, "end": 2.0, "text": "part one", "speaker": "Speaker 0"},
                {"start": 2.0, "end": 2.6, "text": "short flip", "speaker": "Speaker 1"},
                {"start": 2.6, "end": 5.0, "text": "speaker zero again", "speaker": "Speaker 0"},
            ]
        }
        windows = [{"start": 0.0, "duration": 5.0}]
        with tempfile.TemporaryDirectory() as temp_dir:
            transcript_path = Path(temp_dir) / "transcript.json"
            transcript_path.write_text(json.dumps(transcript_payload), encoding="utf-8")
            summary = summarize_subtitle_styles(transcript_path, windows)

        self.assertEqual(summary["speaker_flips_smoothed"], 1)
        self.assertTrue(summary["speaker_smoothing_enabled"])
        self.assertGreaterEqual(summary["effective_speaker_count"], 1)
        self.assertIn("Speaker 0", summary["speaker_color_map"])

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

    def test_load_cases_accepts_commentary_content_type(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            config_path = base / "cases.json"
            config_path.write_text(
                json.dumps(
                    {
                        "cases": [
                            {
                                "id": "commentary_case",
                                "expected_content_type": "commentary",
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
            self.assertEqual(cases[0].expected_content_type, "commentary")

    def test_merge_human_review_rows_preserves_existing_scores(self):
        generated = [
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
                "clip_file": "benchmarks/runs/new/case_a/auto/clip.mp4",
                "human_relevance_score": "",
                "human_boundary_score": "",
                "human_crop_score": "",
                "notes": "",
            }
        ]
        existing = [
            {
                "case_id": "case_a",
                "case_label": "Case A",
                "expected_content_type": "gameplay",
                "scenario_id": "auto",
                "scenario_label": "auto classification",
                "clip_index": "1",
                "clip_start": "00:10.00",
                "clip_end": "00:40.00",
                "local_score": "89.0",
                "clip_file": "benchmarks/runs/old/case_a/auto/clip.mp4",
                "human_relevance_score": "4",
                "human_boundary_score": "3",
                "human_crop_score": "2",
                "notes": "manual review",
            }
        ]
        merged, archived = merge_human_review_rows(generated, existing)
        self.assertEqual(len(merged), 1)
        self.assertEqual(archived, [])
        self.assertEqual(merged[0]["human_relevance_score"], "4")
        self.assertEqual(merged[0]["notes"], "manual review")

    def test_merge_human_review_rows_prefers_scored_duplicate_for_same_key(self):
        generated = [
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
                "clip_file": "benchmarks/runs/new/case_a/auto/clip.mp4",
                "human_relevance_score": "",
                "human_boundary_score": "",
                "human_crop_score": "",
                "notes": "",
            }
        ]
        existing = [
            {
                "case_id": "case_a",
                "case_label": "Case A",
                "expected_content_type": "gameplay",
                "scenario_id": "auto",
                "scenario_label": "auto classification",
                "clip_index": "1",
                "clip_start": "00:10.00",
                "clip_end": "00:40.00",
                "local_score": "90.0",
                "clip_file": "benchmarks/runs/blank/case_a/auto/clip.mp4",
                "human_relevance_score": "",
                "human_boundary_score": "",
                "human_crop_score": "",
                "notes": "",
            },
            {
                "case_id": "case_a",
                "case_label": "Case A",
                "expected_content_type": "podcast",
                "scenario_id": "auto",
                "scenario_label": "auto classification",
                "clip_index": "1",
                "clip_start": "00:10.00",
                "clip_end": "00:40.00",
                "local_score": "89.0",
                "clip_file": "benchmarks/runs/scored/case_a/auto/clip.mp4",
                "human_relevance_score": "5",
                "human_boundary_score": "4",
                "human_crop_score": "3",
                "notes": "kept score",
            },
        ]
        merged, archived = merge_human_review_rows(generated, existing)
        self.assertEqual(len(merged), 1)
        self.assertEqual(archived, [])
        self.assertEqual(merged[0]["human_relevance_score"], "5")
        self.assertEqual(merged[0]["human_boundary_score"], "4")
        self.assertEqual(merged[0]["human_crop_score"], "3")
        self.assertEqual(merged[0]["notes"], "kept score")

    def test_merge_human_review_rows_moves_unmatched_review_to_archive(self):
        generated = [
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
                "clip_file": "benchmarks/runs/new/case_a/auto/clip.mp4",
                "human_relevance_score": "",
                "human_boundary_score": "",
                "human_crop_score": "",
                "notes": "",
            }
        ]
        existing = [
            {
                "case_id": "case_b",
                "case_label": "Case B",
                "expected_content_type": "commentary",
                "scenario_id": "auto",
                "scenario_label": "auto classification",
                "clip_index": "7",
                "clip_start": "01:00.00",
                "clip_end": "01:30.00",
                "local_score": "75.0",
                "clip_file": "benchmarks/runs/old/case_b/auto/clip.mp4",
                "human_relevance_score": "4",
                "human_boundary_score": "4",
                "human_crop_score": "5",
                "notes": "historical review",
            }
        ]
        merged, archived = merge_human_review_rows(generated, existing)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["human_relevance_score"], "")
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0]["case_id"], "case_b")
        self.assertEqual(archived[0]["human_crop_score"], "5")

    def test_extract_human_review_rows_from_results_recovers_scored_rows(self):
        payload = {
            "human_review": {
                "scored_rows": [
                    {
                        "case_id": "case_old",
                        "case_label": "Old Case",
                        "expected_content_type": "gameplay",
                        "scenario_id": "auto",
                        "scenario_label": "auto classification",
                        "clip_index": 1,
                        "clip_start": "00:10.00",
                        "clip_end": "00:40.00",
                        "local_score": 91.2,
                        "clip_file": "benchmarks/runs/old/case_old/auto/clip.mp4",
                        "human_relevance_score": 4,
                        "human_boundary_score": 3,
                        "human_crop_score": 5,
                        "notes": "historical score",
                        "local_features": {"ignored": True},
                    }
                ]
            }
        }
        rows = extract_human_review_rows_from_results(payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["case_id"], "case_old")
        self.assertEqual(rows[0]["human_relevance_score"], 4)
        self.assertNotIn("local_features", rows[0])

    def test_next_human_review_targets_prioritize_auto_core_cases(self):
        rows = [
            {
                "case_id": "roman_giertych_commentary",
                "expected_content_type": "commentary",
                "scenario_id": "auto",
                "clip_index": "1",
                "clip_start": "00:10.00",
                "clip_end": "00:40.00",
                "local_score": "90",
                "human_relevance_score": "",
                "human_boundary_score": "",
                "human_crop_score": "",
            },
            {
                "case_id": "emeritos_gameplay",
                "expected_content_type": "gameplay",
                "scenario_id": "auto",
                "clip_index": "1",
                "clip_start": "00:10.00",
                "clip_end": "00:40.00",
                "local_score": "88",
                "human_relevance_score": "",
                "human_boundary_score": "",
                "human_crop_score": "",
            },
            {
                "case_id": "canva_presentation_tutorial",
                "expected_content_type": "tutorial",
                "scenario_id": "manual_tutorial",
                "clip_index": "1",
                "clip_start": "00:10.00",
                "clip_end": "00:40.00",
                "local_score": "95",
                "human_relevance_score": "",
                "human_boundary_score": "",
                "human_crop_score": "",
            },
        ]
        targets = build_next_human_review_targets(rows)
        self.assertEqual(targets[0]["case_id"], "emeritos_gameplay")
        self.assertEqual(targets[0]["scenario_id"], "auto")

    def test_summarize_human_review_ignores_blank_rows_and_counts_auto(self):
        review_rows = [
            {
                "case_id": "case_a",
                "case_label": "Case A",
                "expected_content_type": "gameplay",
                "scenario_id": "auto",
                "scenario_label": "auto classification",
                "clip_index": "1",
                "clip_start": "00:10.00",
                "clip_end": "00:40.00",
                "local_score": "91.2",
                "clip_file": r"benchmarks\runs\20260511_152536\case_a\auto\clip.mp4",
                "human_relevance_score": "2",
                "human_boundary_score": "3",
                "human_crop_score": "4",
                "notes": "buy menu i setup bez payoffu",
            },
            {
                "case_id": "case_a",
                "case_label": "Case A",
                "expected_content_type": "gameplay",
                "scenario_id": "manual_gameplay",
                "scenario_label": "manual gameplay",
                "clip_index": "2",
                "clip_start": "00:50.00",
                "clip_end": "01:20.00",
                "local_score": "88.0",
                "clip_file": "",
                "human_relevance_score": "",
                "human_boundary_score": "",
                "human_crop_score": "",
                "notes": "",
            },
        ]
        current_cases = [
            {
                "case_id": "case_a",
                "label": "Case A",
                "expected_content_type": "gameplay",
                "status": "completed",
                "scenarios": [
                    {
                        "scenario_id": "auto",
                        "scenario_label": "auto classification",
                        "status": "completed",
                        "classification": {"strategy_render_hints": {"crop_mode": "gameplay_balanced"}},
                        "artifacts": {"subtitle_dir": r"benchmarks\runs\20260511_152536\case_a\auto\cuts_subtitles"},
                        "selection": {
                            "clips": [
                                {
                                    "index": 1,
                                    "start_label": "00:10.00",
                                    "end_label": "00:40.00",
                                    "local_score": 91.2,
                                    "selection_strategy": "gameplay",
                                    "selection_source": "local_ranking",
                                    "selection_reasons": ["strong heatmap support"],
                                    "local_features": {"gameplay_setup_penalty": 0.5},
                                    "boundary_metadata": {"sentence_boundary_used": True},
                                }
                            ]
                        },
                    }
                ],
            }
        ]
        summary = summarize_human_review(review_rows, current_cases)
        self.assertEqual(summary["scored_record_count"], 1)
        self.assertEqual(summary["auto_scored_record_count"], 1)
        self.assertAlmostEqual(summary["averages"]["overall"]["relevance"], 2.0)
        self.assertEqual(summary["note_issue_counts"]["buy menu"], 1)
        self.assertEqual(summary["largest_remaining_issue"]["key"], "scoring")

    def test_summarize_rendering_metrics_tracks_layout_and_vertical_outputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            raw_dir = base / "raw"
            subtitle_dir = base / "subs"
            raw_dir.mkdir()
            subtitle_dir.mkdir()
            (raw_dir / "segment_1_test.mp4").write_bytes(b"raw")
            (subtitle_dir / "segment_1_test.mp4").write_bytes(b"subs")
            cutting_log = {
                "cutter_adjustments": [
                    {
                        "segment_index": 1,
                        "framing_mode": "full_frame_blur_background",
                        "layout_mode": "full_frame_blur_background",
                        "face_tracking": {
                            "layout_mode_used": "full_frame_blur_background",
                            "crop_mode": "content_preserving",
                            "crop_priority": "screen",
                            "tracking_mode": "full_frame_blur_background",
                            "sampled_detections": 0,
                            "fallback_samples": 0,
                            "reaction_samples": 0,
                            "zoom_samples": 0,
                            "ignored_faces_count": 0,
                            "face_tracking_used": False,
                            "full_frame_preserved": True,
                            "crop_stabilized": True,
                            "fallback_reason": "",
                            "center_x_mean_norm": 0.5,
                            "center_y_mean_norm": 0.5,
                        },
                    }
                ]
            }
            with patch(
                "benchmark.probe_output_video",
                return_value={"width": 1080, "height": 1920, "aspect_ratio": "9:16", "is_vertical_9_16": True},
            ):
                summary = summarize_rendering_metrics(cutting_log, raw_dir, subtitle_dir, expected_clips=1)
            self.assertEqual(summary["layout_modes"]["full_frame_blur_background"], 1)
            self.assertEqual(summary["layout_modes_used"]["full_frame_blur_background"], 1)
            self.assertEqual(summary["output_aspect_ratio"], "9:16")
            self.assertTrue(summary["is_vertical_9_16"])
            self.assertEqual(summary["output_width"], "1080")
            self.assertEqual(summary["output_height"], "1920")


if __name__ == "__main__":
    unittest.main()
