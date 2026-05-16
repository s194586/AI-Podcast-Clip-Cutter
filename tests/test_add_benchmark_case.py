import json
import tempfile
import unittest
from pathlib import Path

from tools import add_benchmark_case
from tools import run_local_benchmark


class AddBenchmarkCaseTests(unittest.TestCase):
    def _workspace(self) -> tuple[Path, Path, Path]:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        base = Path(temp_dir.name)
        benchmarks = base / "benchmarks"
        benchmarks.mkdir()
        cases_path = benchmarks / "cases.json"
        cases_path.write_text('{\n  "cases": []\n}\n', encoding="utf-8")
        assets_root = benchmarks / "assets"
        assets_root.mkdir()
        return base, cases_path, assets_root

    def test_creates_new_case_and_copies_video(self):
        base, cases_path, assets_root = self._workspace()
        source = base / "sample.mp4"
        source.write_bytes(b"video")

        result = add_benchmark_case.add_case(
            case_id="my_gameplay_01",
            video_path=str(source),
            content_type="gameplay",
            review_batch="local_v1",
            notes="new local test",
            cases_path=cases_path,
            assets_root=assets_root,
        )

        copied = assets_root / "my_gameplay_01" / "input" / "source.mp4"
        self.assertTrue(copied.exists())
        payload = json.loads(cases_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["cases"][0]["id"], "my_gameplay_01")
        self.assertEqual(payload["cases"][0]["expected_content_type"], "gameplay")
        self.assertEqual(payload["cases"][0]["review_batch"], "local_v1")
        self.assertTrue(result["video_copied"])

    def test_does_not_overwrite_existing_case_without_force(self):
        _base, cases_path, assets_root = self._workspace()
        payload = {
            "cases": [
                {
                    "id": "my_case",
                    "expected_content_type": "commentary",
                }
            ]
        }
        cases_path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaises(ValueError):
            add_benchmark_case.add_case(
                case_id="my_case",
                content_type="commentary",
                review_batch="local_v1",
                cases_path=cases_path,
                assets_root=assets_root,
            )

    def test_force_overwrites_existing_case(self):
        base, cases_path, assets_root = self._workspace()
        source = base / "sample.mp4"
        source.write_bytes(b"video")
        cases_path.write_text(
            json.dumps({"cases": [{"id": "my_case", "expected_content_type": "generic"}]}),
            encoding="utf-8",
        )

        add_benchmark_case.add_case(
            case_id="my_case",
            video_path=str(source),
            content_type="tutorial",
            review_batch="local_v2",
            force=True,
            cases_path=cases_path,
            assets_root=assets_root,
        )

        payload = json.loads(cases_path.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["cases"]), 1)
        self.assertEqual(payload["cases"][0]["expected_content_type"], "tutorial")
        self.assertEqual(payload["cases"][0]["review_batch"], "local_v2")

    def test_validates_content_type(self):
        _base, cases_path, assets_root = self._workspace()

        with self.assertRaises(ValueError):
            add_benchmark_case.add_case(
                case_id="bad_case",
                content_type="unknown_type",
                review_batch="local_v1",
                cases_path=cases_path,
                assets_root=assets_root,
            )

    def test_supports_source_url_without_local_video(self):
        _base, cases_path, assets_root = self._workspace()

        add_benchmark_case.add_case(
            case_id="url_case",
            source_url="https://example.com/video",
            content_type="commentary",
            review_batch="local_v1",
            notes="url only",
            cases_path=cases_path,
            assets_root=assets_root,
        )

        payload = json.loads(cases_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["cases"][0]["source_url"], "https://example.com/video")
        self.assertEqual(payload["cases"][0]["video"], "")

    def test_cases_file_stays_valid_json(self):
        base, cases_path, assets_root = self._workspace()
        source = base / "sample.mp4"
        source.write_bytes(b"video")

        add_benchmark_case.add_case(
            case_id="case_one",
            video_path=str(source),
            content_type="gameplay",
            review_batch="local_v1",
            cases_path=cases_path,
            assets_root=assets_root,
        )
        add_benchmark_case.add_case(
            case_id="case_two",
            content_type="podcast",
            review_batch="local_v1",
            cases_path=cases_path,
            assets_root=assets_root,
        )

        payload = json.loads(cases_path.read_text(encoding="utf-8"))
        self.assertEqual([item["id"] for item in payload["cases"]], ["case_one", "case_two"])


class RunLocalBenchmarkTests(unittest.TestCase):
    def test_builds_local_only_benchmark_command(self):
        command = run_local_benchmark.build_benchmark_command(review_batch="local_v1", extra_args=["--top", "3"])

        self.assertIn("--ai-mode", command)
        self.assertIn("local_only", command)
        self.assertIn("--subtitle-checker-mode", command)
        self.assertIn("--review-batch", command)
        self.assertIn("local_v1", command)

    def test_helper_does_not_require_api_key(self):
        command = run_local_benchmark.build_benchmark_command(review_batch="")

        joined = " ".join(command)
        self.assertIn("local_only", joined)
        self.assertNotIn("GEMINI_API_KEY", joined)


if __name__ == "__main__":
    unittest.main()
