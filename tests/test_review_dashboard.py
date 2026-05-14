import json
from argparse import Namespace
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import review_dashboard


class ReviewDashboardTests(unittest.TestCase):
    def _sample_results(self, path: Path) -> None:
        payload = {
            "cases": [
                {
                    "case_id": "case_a",
                    "expected_content_type": "gameplay",
                    "scenarios": [
                        {
                            "scenario_id": "auto",
                            "status": "completed",
                            "content_type_arg": "auto",
                            "classification": {"detected_content_type": "gameplay"},
                            "artifacts": {"subtitle_dir": "benchmarks/runs/test/case_a/auto/cuts_subtitles"},
                            "selection": {
                                "clips": [
                                    {
                                        "index": 1,
                                        "start": 10.0,
                                        "end": 40.0,
                                        "start_label": "00:10.00",
                                        "end_label": "00:40.00",
                                        "duration": 30.0,
                                        "local_score": 91.2,
                                        "selection_reasons": ["strong heatmap support"],
                                        "local_features": {"low_payoff_penalty": 0.2},
                                        "summary": "short transcript excerpt",
                                    }
                                ]
                            },
                        }
                    ],
                }
            ]
        }
        path.write_text(json.dumps(payload), encoding="utf-8")

    def _sample_template(self, path: Path) -> None:
        path.write_text(
            "\n".join(
                [
                    "case_id,scenario_id,clip_start,clip_end,clip_file,human_relevance_score,human_boundary_score,human_crop_score,notes",
                    r"case_a,auto,00:10.00,00:40.00,benchmarks\runs\test\case_a\auto\cuts_subtitles\segment_1.mp4,,,,",
                ]
            ),
            encoding="utf-8",
        )

    def test_stable_clip_id_is_deterministic(self):
        left = review_dashboard.stable_clip_id("case_a", "auto", "00:10.00", "00:40.00")
        right = review_dashboard.stable_clip_id("case_a", "auto", 10.0, 40.0)
        self.assertEqual(left, right)
        self.assertIn("case_a-auto", left)

    def test_make_video_src_for_benchmark_html_uses_runs_relative_path(self):
        video_path = Path(review_dashboard.PROJECT_ROOT) / "benchmarks" / "runs" / "test_review_dashboard" / "segment_1.mp4"
        video_path.parent.mkdir(parents=True, exist_ok=True)
        video_path.write_bytes(b"fake mp4")
        try:
            src = review_dashboard.make_video_src(
                "benchmarks/runs/test_review_dashboard/segment_1.mp4",
                Path(review_dashboard.PROJECT_ROOT) / "benchmarks" / "review_dashboard.html",
            )
        finally:
            video_path.unlink(missing_ok=True)

        self.assertEqual(src, "runs/test_review_dashboard/segment_1.mp4")

    def test_make_video_src_for_external_file_uses_relative_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            html_path = base / "dashboards" / "review.html"
            html_path.parent.mkdir()
            video_path = base / "videos" / "clip.mp4"
            video_path.parent.mkdir()
            video_path.write_bytes(b"fake mp4")

            src = review_dashboard.make_video_src(str(video_path), html_path)

        self.assertEqual(src, "../videos/clip.mp4")

    def test_collect_clips_handles_missing_optional_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            results = base / "results.json"
            template = base / "template.csv"
            reviews = base / "reviews.jsonl"
            self._sample_results(results)
            self._sample_template(template)

            clips = review_dashboard.collect_clips(results, template, reviews)

        self.assertEqual(len(clips), 1)
        self.assertEqual(clips[0].case_id, "case_a")
        self.assertEqual(clips[0].video_path, "benchmarks/runs/test/case_a/auto/cuts_subtitles/segment_1.mp4")
        self.assertEqual(clips[0].local_features["low_payoff_penalty"], 0.2)

    def test_template_scores_mark_clip_as_reviewed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            results = base / "results.json"
            template = base / "template.csv"
            reviews = base / "reviews.jsonl"
            self._sample_results(results)
            template.write_text(
                "\n".join(
                    [
                        "case_id,scenario_id,clip_start,clip_end,clip_file,human_relevance_score,human_boundary_score,human_crop_score,notes",
                        "case_a,auto,00:10.00,00:40.00,,4,3,5,clean clip",
                    ]
                ),
                encoding="utf-8",
            )

            clips = review_dashboard.collect_clips(results, template, reviews)

        self.assertEqual(clips[0].review_count, 1)
        self.assertEqual(clips[0].latest_review["source"], "human_review_template.csv")
        self.assertEqual(clips[0].latest_review["human_relevance_score"], "4")
        self.assertEqual(clips[0].latest_review["notes"], "clean clip")

    def test_write_and_read_jsonl_reviews(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "reviews.jsonl"
            review_dashboard.append_review(
                path,
                {
                    "clip_id": "clip_a",
                    "rating": 4,
                    "good_clip": True,
                    "boundary_issue": False,
                    "boring_setup": False,
                    "no_payoff": False,
                    "too_context_dependent": False,
                    "notes": "good",
                },
            )
            reviews = review_dashboard.load_jsonl_reviews(path)

        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0]["clip_id"], "clip_a")
        self.assertEqual(reviews[0]["rating"], 4)

    def test_add_review_payload_keeps_all_fields(self):
        payload = review_dashboard.build_review_payload(
            Namespace(
                clip_id="clip_a",
                rating=5,
                good_clip=True,
                boundary_issue=True,
                boring_setup=False,
                no_payoff=True,
                too_context_dependent=True,
                notes="strong hook, ending too late",
            )
        )

        self.assertEqual(payload["rating"], 5)
        self.assertTrue(payload["good_clip"])
        self.assertTrue(payload["boundary_issue"])
        self.assertFalse(payload["boring_setup"])
        self.assertTrue(payload["no_payoff"])
        self.assertTrue(payload["too_context_dependent"])
        self.assertEqual(payload["notes"], "strong hook, ending too late")

    def test_cli_add_review_writes_new_fields_to_jsonl(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            reviews_path = Path(temp_dir) / "human_reviews.jsonl"
            subprocess.run(
                [
                    sys.executable,
                    "review_dashboard.py",
                    "add-review",
                    "--reviews",
                    str(reviews_path),
                    "--clip-id",
                    "clip_a",
                    "--rating",
                    "4",
                    "--good-clip",
                    "true",
                    "--boundary-issue",
                    "true",
                    "--boring-setup",
                    "false",
                    "--no-payoff",
                    "true",
                    "--too-context-dependent",
                    "false",
                    "--notes",
                    "good action, weak ending",
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=True,
            )
            reviews = review_dashboard.load_jsonl_reviews(reviews_path)

        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0]["clip_id"], "clip_a")
        self.assertEqual(reviews[0]["rating"], 4)
        self.assertTrue(reviews[0]["good_clip"])
        self.assertTrue(reviews[0]["boundary_issue"])
        self.assertFalse(reviews[0]["boring_setup"])
        self.assertTrue(reviews[0]["no_payoff"])
        self.assertFalse(reviews[0]["too_context_dependent"])
        self.assertEqual(reviews[0]["notes"], "good action, weak ending")

    def test_html_contains_video_source_for_existing_mp4(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            output = base / "dashboard.html"
            video = base / "clips" / "clip.mp4"
            video.parent.mkdir()
            video.write_bytes(b"fake mp4")
            clip = review_dashboard.ReviewClip(
                clip_id="clip_a",
                case_id="case_a",
                scenario_id="auto",
                content_type="gameplay",
                start_label="00:10.00",
                end_label="00:40.00",
                duration=30.0,
                final_score=91.2,
                video_path=str(video),
            )

            page = review_dashboard.render_html([clip], output)

        self.assertIn('<source src="clips/clip.mp4" type="video/mp4">', page)

    def test_html_marks_missing_video_file(self):
        clip = review_dashboard.ReviewClip(
            clip_id="clip_a",
            case_id="case_a",
            scenario_id="auto",
            video_path="benchmarks/runs/missing/segment_1.mp4",
        )
        page = review_dashboard.render_html([clip], Path("benchmarks/review_dashboard.html"))

        self.assertIn("missing video file", page)
        self.assertIn("benchmarks/runs/missing/segment_1.mp4", page)

    def test_export_html_contains_review_and_filter_controls(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            results = base / "results.json"
            template = base / "template.csv"
            reviews = base / "reviews.jsonl"
            output = base / "dashboard.html"
            self._sample_results(results)
            self._sample_template(template)

            clips = review_dashboard.collect_clips(results, template, reviews)
            output.write_text(review_dashboard.render_html(clips, output), encoding="utf-8")

            html = output.read_text(encoding="utf-8")
        self.assertIn("AI Virtual Cutter Review Dashboard", html)
        self.assertIn("case_a", html)
        self.assertIn("auto / gameplay", html)
        self.assertIn("id=\"searchInput\"", html)
        self.assertIn("id=\"statusFilter\"", html)
        self.assertIn("id=\"caseFilter\"", html)
        self.assertIn("id=\"scenarioFilter\"", html)
        self.assertIn("id=\"sortControl\"", html)
        self.assertIn("review-rating", html)
        self.assertIn("review-good-clip", html)
        self.assertIn("review-boundary-issue", html)
        self.assertIn("review-boring-setup", html)
        self.assertIn("review-no-payoff", html)
        self.assertIn("review-too-context-dependent", html)
        self.assertIn("review-notes", html)
        self.assertIn("Copy review command", html)

    def test_cli_help(self):
        completed = subprocess.run(
            [sys.executable, "review_dashboard.py", "--help"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("Local human-review dashboard", completed.stdout)


if __name__ == "__main__":
    unittest.main()
