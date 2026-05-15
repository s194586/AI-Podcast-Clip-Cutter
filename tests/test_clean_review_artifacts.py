import tempfile
import unittest
from pathlib import Path

from tools import clean_review_artifacts


class CleanReviewArtifactsTests(unittest.TestCase):
    def _build_workspace(self, base: Path) -> None:
        benchmarks = base / "benchmarks"
        benchmarks.mkdir()
        (benchmarks / "assets").mkdir()
        (benchmarks / "runs").mkdir()
        (benchmarks / "runs" / "20260516_000000").mkdir()
        (benchmarks / "review_dashboard.html").write_text("dashboard", encoding="utf-8")
        (benchmarks / "human_review_template.csv").write_text("template", encoding="utf-8")
        (benchmarks / "human_review_archive.csv").write_text("archive", encoding="utf-8")
        (benchmarks / "human_review_recovered_20260511.csv").write_text("recovered", encoding="utf-8")
        (benchmarks / "results.json").write_text("{}", encoding="utf-8")
        (benchmarks / "report.md").write_text("# report", encoding="utf-8")
        (benchmarks / "human_reviews.jsonl").write_text('{"clip_id":"a"}\n', encoding="utf-8")
        (benchmarks / "cases.json").write_text("{}", encoding="utf-8")
        (benchmarks / "README.md").write_text("# Benchmarks", encoding="utf-8")
        (benchmarks / "assets" / "clip.mp4").write_bytes(b"asset")
        outputs = base / "outputs" / "gui_runs"
        outputs.mkdir(parents=True)
        (outputs / "run_1").mkdir()

    def test_dry_run_does_not_delete_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            self._build_workspace(base)
            plan = clean_review_artifacts.build_cleanup_plan(project_root=base)
            result = clean_review_artifacts.apply_cleanup_plan(plan)

            self.assertEqual(result["mode"], "dry-run")
            self.assertTrue((base / "benchmarks" / "review_dashboard.html").exists())
            self.assertTrue((base / "benchmarks" / "runs" / "20260516_000000").exists())

    def test_apply_removes_only_generated_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            self._build_workspace(base)
            plan = clean_review_artifacts.build_cleanup_plan(project_root=base, apply=True)
            clean_review_artifacts.apply_cleanup_plan(plan)

            self.assertFalse((base / "benchmarks" / "review_dashboard.html").exists())
            self.assertFalse((base / "benchmarks" / "human_review_template.csv").exists())
            self.assertFalse((base / "benchmarks" / "runs" / "20260516_000000").exists())
            self.assertFalse((base / "outputs" / "gui_runs" / "run_1").exists())
            self.assertTrue((base / "benchmarks" / "assets" / "clip.mp4").exists())
            self.assertTrue((base / "benchmarks" / "cases.json").exists())

    def test_archive_reviews_creates_backup_and_resets_live_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            self._build_workspace(base)
            plan = clean_review_artifacts.build_cleanup_plan(
                project_root=base,
                archive_reviews=True,
                apply=True,
                timestamp="20260516_101500",
            )
            result = clean_review_artifacts.apply_cleanup_plan(plan)

            archive_path = base / "benchmarks" / "archive" / "human_reviews_20260516_101500.jsonl"
            self.assertEqual(result["archived_to"], str(archive_path))
            self.assertTrue(archive_path.exists())
            self.assertEqual((base / "benchmarks" / "human_reviews.jsonl").read_text(encoding="utf-8"), "")

    def test_keep_results_and_dashboard_preserves_requested_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            self._build_workspace(base)
            plan = clean_review_artifacts.build_cleanup_plan(
                project_root=base,
                apply=True,
                keep_results=True,
                keep_dashboard=True,
            )
            clean_review_artifacts.apply_cleanup_plan(plan)

            self.assertTrue((base / "benchmarks" / "review_dashboard.html").exists())
            self.assertTrue((base / "benchmarks" / "results.json").exists())
            self.assertTrue((base / "benchmarks" / "report.md").exists())

    def test_missing_files_do_not_crash_apply(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            (base / "benchmarks").mkdir()
            (base / "benchmarks" / "assets").mkdir()
            (base / "benchmarks" / "cases.json").write_text("{}", encoding="utf-8")
            plan = clean_review_artifacts.build_cleanup_plan(project_root=base, apply=True, archive_reviews=True)
            result = clean_review_artifacts.apply_cleanup_plan(plan)

            self.assertEqual(result["mode"], "apply")


if __name__ == "__main__":
    unittest.main()
