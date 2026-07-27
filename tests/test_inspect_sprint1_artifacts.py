from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from candidate_windows import generate_candidate_windows
from heatmap_peaks import detect_heatmap_peaks
from source_media_contract import build_source_media_manifest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
INSPECTOR = REPOSITORY_ROOT / "scripts" / "inspect_sprint1_artifacts.py"


def trusted_heatmap() -> dict:
    return {
        "schema_version": 1,
        "source": "youtube_most_replayed",
        "synthetic": False,
        "video_id": "offline-demo-video",
        "extractor": "youtube",
        "extractor_version": "offline-test",
        "duration_seconds": 180.0,
        "points": [
            {"start_time": 0.0, "end_time": 30.0, "value": 0.1},
            {"start_time": 30.0, "end_time": 60.0, "value": 0.2},
            {"start_time": 60.0, "end_time": 90.0, "value": 0.9},
            {"start_time": 90.0, "end_time": 120.0, "value": 0.2},
            {"start_time": 120.0, "end_time": 150.0, "value": 0.1},
            {"start_time": 150.0, "end_time": 180.0, "value": 0.1},
        ],
    }


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_complete_workspace(root: Path, *, media_name: str = "offline.mp4") -> tuple[dict, dict]:
    media = root / "input" / media_name
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"offline fixture")
    manifest = build_source_media_manifest(
        video_id="offline-demo-video",
        source_url="https://example.invalid/watch/offline-demo-video",
        media_file=media,
        workspace_path=root,
    )
    heatmap = trusted_heatmap()
    peaks = detect_heatmap_peaks(heatmap)
    candidates = generate_candidate_windows(peaks)
    adapter = {
        "schema_version": 2,
        "source": "candidate_windows_compatibility_adapter",
        "canonical_artifact": "metadata/candidate_windows.json",
        "candidate_id_scheme": candidates["candidate_id_scheme"],
        "candidate_id_version": candidates["candidate_id_version"],
        "top_windows": [
            {
                "id": candidate["candidate_id"],
                "candidate_id": candidate["candidate_id"],
                "rank": candidate["rank"],
                "source_peak_rank": candidate["source_peak_rank"],
                "peak_time": candidate["peak_time"],
                "start": candidate["start_time"],
                "end": candidate["end_time"],
                "duration": candidate["duration_seconds"],
                "boundary_source": "replay_interest_peak",
                "selection_source": "youtube_most_replayed",
                "replay_interest": candidate["replay_interest"],
            }
            for candidate in candidates["candidates"]
        ],
    }
    write_json(root / "metadata" / "source_media.json", manifest)
    write_json(root / "metadata" / "heatmap.json", heatmap)
    write_json(root / "metadata" / "heatmap_peaks.json", peaks)
    write_json(root / "metadata" / "candidate_windows.json", candidates)
    write_json(root / "top_windows.json", adapter)
    return candidates, adapter


class InspectSprint1ArtifactsTests(unittest.TestCase):
    def run_inspector(self, root: Path, *flags: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(INSPECTOR), "--workspace", str(root), *flags],
            cwd=REPOSITORY_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_complete_valid_artifacts_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            write_complete_workspace(root)
            result = self.run_inspector(root, "--json", "--require-complete")
            report = json.loads(result.stdout)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(report["result"], "PASS")
            self.assertEqual(report["artifacts"]["heatmap"]["point_count"], 6)
            self.assertTrue(report["artifacts"]["candidate_windows"]["candidate_ids_unique"])

    def test_missing_heatmap_is_reported_without_creating_a_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            write_complete_workspace(root)
            heatmap = root / "metadata" / "heatmap.json"
            heatmap.unlink()
            result = self.run_inspector(root, "--json")
            report = json.loads(result.stdout)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(report["artifacts"]["heatmap"]["status"], "missing")
            self.assertFalse(heatmap.exists())

    def test_malformed_heatmap_fails_when_complete_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            write_complete_workspace(root)
            (root / "metadata" / "heatmap.json").write_text("{bad", encoding="utf-8")
            result = self.run_inspector(root, "--json", "--require-complete")
            report = json.loads(result.stdout)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(report["artifacts"]["heatmap"]["status"], "invalid")

    def test_duplicate_candidate_id_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            candidates, _ = write_complete_workspace(root)
            duplicate = dict(candidates["candidates"][0])
            duplicate["rank"] = 2
            candidates["candidates"].append(duplicate)
            write_json(root / "metadata" / "candidate_windows.json", candidates)
            result = self.run_inspector(root, "--json", "--require-complete")
            report = json.loads(result.stdout)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(report["artifacts"]["candidate_windows"]["status"], "invalid")

    def test_adapter_id_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            _, adapter = write_complete_workspace(root)
            adapter["top_windows"][0]["id"] = "different-id"
            write_json(root / "top_windows.json", adapter)
            result = self.run_inspector(root, "--json", "--require-complete")
            report = json.loads(result.stdout)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(report["artifacts"]["top_windows"]["status"], "invalid")

    def test_missing_or_malformed_peak_parameters_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            write_complete_workspace(root)
            peaks_path = root / "metadata" / "heatmap_peaks.json"
            peaks = json.loads(peaks_path.read_text(encoding="utf-8"))
            for parameters in ({}, {"max_peaks": 0}):
                with self.subTest(parameters=parameters):
                    peaks["parameters"] = parameters
                    write_json(peaks_path, peaks)
                    result = self.run_inspector(root, "--json", "--require-complete")
                    report = json.loads(result.stdout)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(report["artifacts"]["heatmap_peaks"]["status"], "invalid")

    def test_malformed_candidate_parameters_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            candidates, _ = write_complete_workspace(root)
            candidates["parameters"] = {"target_duration_seconds": 10.0}
            write_json(root / "metadata" / "candidate_windows.json", candidates)
            result = self.run_inspector(root, "--json", "--require-complete")
            report = json.loads(result.stdout)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(report["artifacts"]["candidate_windows"]["status"], "invalid")

    def test_adapter_start_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            _, adapter = write_complete_workspace(root)
            adapter["top_windows"][0]["start"] += 1.0
            write_json(root / "top_windows.json", adapter)
            result = self.run_inspector(root, "--json", "--require-complete")
            report = json.loads(result.stdout)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(report["artifacts"]["top_windows"]["status"], "invalid")

    def test_adapter_replay_interest_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            _, adapter = write_complete_workspace(root)
            adapter["top_windows"][0]["replay_interest"]["prominence"] = 0.01
            write_json(root / "top_windows.json", adapter)
            result = self.run_inspector(root, "--json", "--require-complete")
            report = json.loads(result.stdout)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(report["artifacts"]["top_windows"]["status"], "invalid")

    def test_adapter_missing_required_field_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            _, adapter = write_complete_workspace(root)
            del adapter["top_windows"][0]["boundary_source"]
            write_json(root / "top_windows.json", adapter)
            result = self.run_inspector(root, "--json", "--require-complete")
            report = json.loads(result.stdout)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(report["artifacts"]["top_windows"]["status"], "invalid")

    def test_adapter_unexpected_item_fields_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            _, adapter = write_complete_workspace(root)
            for field, value in (("semantic_score", 0.95), ("legacy_metadata", "unexpected")):
                with self.subTest(field=field):
                    adapter["top_windows"][0][field] = value
                    write_json(root / "top_windows.json", adapter)
                    result = self.run_inspector(root, "--json", "--require-complete")
                    report = json.loads(result.stdout)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(report["artifacts"]["top_windows"]["status"], "invalid")
                    del adapter["top_windows"][0][field]

    def test_adapter_unexpected_root_field_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            _, adapter = write_complete_workspace(root)
            adapter["legacy_adapter_field"] = True
            write_json(root / "top_windows.json", adapter)
            result = self.run_inspector(root, "--json", "--require-complete")
            report = json.loads(result.stdout)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(report["artifacts"]["top_windows"]["status"], "invalid")

    def test_cross_artifact_duration_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            candidates, _ = write_complete_workspace(root)
            candidates["duration_seconds"] = 181.0
            write_json(root / "metadata" / "candidate_windows.json", candidates)
            result = self.run_inspector(root, "--json", "--require-complete")
            report = json.loads(result.stdout)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(report["duration_consistent"])
            self.assertEqual(report["result"], "FAIL")

    def test_cross_artifact_failure_has_priority_over_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            write_complete_workspace(root)
            heatmap_path = root / "metadata" / "heatmap.json"
            heatmap = json.loads(heatmap_path.read_text(encoding="utf-8"))
            heatmap["video_id"] = "different-video-id"
            write_json(heatmap_path, heatmap)
            (root / "top_windows.json").unlink()
            result = self.run_inspector(root, "--json", "--require-complete")
            report = json.loads(result.stdout)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(report["artifacts"]["top_windows"]["status"], "missing")
            self.assertFalse(report["identity_consistent"])
            self.assertEqual(report["result"], "FAIL")

    def test_semantic_scoring_field_in_canonical_candidate_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            candidates, _ = write_complete_workspace(root)
            candidates["candidates"][0]["semantic_score"] = 0.95
            write_json(root / "metadata" / "candidate_windows.json", candidates)
            result = self.run_inspector(root, "--json", "--require-complete")
            report = json.loads(result.stdout)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("semantic scoring", report["artifacts"]["candidate_windows"]["message"])

    def test_inspector_does_not_modify_examined_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            write_complete_workspace(root)
            before = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }
            result = self.run_inspector(root, "--require-complete")
            after = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(after, before)

    def test_json_report_redacts_source_url_and_media_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            media_name = "private_podcast_episode_2026.mp4"
            write_complete_workspace(root, media_name=media_name)
            result = self.run_inspector(root, "--json")
            report = json.loads(result.stdout)
            self.assertEqual(result.returncode, 0)
            self.assertTrue(report["read_only"])
            self.assertTrue(report["artifacts"]["source_media"]["media_present"])
            self.assertNotIn("example.invalid", result.stdout)
            self.assertNotIn("source_url", result.stdout)
            self.assertNotIn(media_name, result.stdout)
            self.assertNotIn("media_file", result.stdout)

    def test_filesystem_read_error_is_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            media_name = "private_podcast_episode_2026.mp4"
            write_complete_workspace(root, media_name=media_name)
            peaks_path = root / "metadata" / "heatmap_peaks.json"
            peaks_path.unlink()
            peaks_path.mkdir()
            result = self.run_inspector(root, "--json", "--require-complete")
            report = json.loads(result.stdout)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(report["artifacts"]["heatmap_peaks"]["status"], "invalid")
            self.assertNotIn(str(root), result.stdout)
            self.assertNotIn("example.invalid", result.stdout)
            self.assertNotIn(media_name, result.stdout)


if __name__ == "__main__":
    unittest.main()
