import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select

from apps.api.db.database import configure_database, init_database, session_scope
from apps.api.db.models import Clip, Project
from apps.api.db.repositories import ClipRepository, ProjectRepository
from apps.api.tools.import_local_project import main as import_local_project_main


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


class ImportLocalProjectTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_url = _sqlite_url(self.root / "test.db")
        os.environ["PODCAST_CUTTER_DB_URL"] = self.db_url
        os.environ["PODCAST_CUTTER_PROJECT_ROOT"] = str(self.root)
        configure_database(self.db_url)
        init_database()

    def tearDown(self):
        configure_database("sqlite:///:memory:")
        os.environ.pop("PODCAST_CUTTER_DB_URL", None)
        os.environ.pop("PODCAST_CUTTER_PROJECT_ROOT", None)
        self.tempdir.cleanup()

    def _seed_demo_project(self):
        with session_scope() as session:
            project = ProjectRepository(session).create(
                source_url="",
                title="Demo project",
                status="ready",
                candidate_source_path="examples/top_windows.example.json",
            )
            ClipRepository(session).create_from_dict(
                project.id,
                {
                    "id": "clip_001",
                    "index": 1,
                    "ai_start": 100.0,
                    "ai_end": 140.0,
                    "edited_start": 100.0,
                    "edited_end": 140.0,
                    "min_start": 80.0,
                    "max_start": 120.0,
                    "min_end": 120.0,
                    "max_end": 160.0,
                    "summary": "A clear podcast answer.",
                    "text": "Demo text",
                    "status": "draft",
                    "render_status": "not_rendered",
                },
            )

    def _write_project_state(self):
        state_path = self.root / "data" / "projects" / "local" / "project_state.json"
        state_path.parent.mkdir(parents=True)
        state_path.write_text(
            json.dumps(
                {
                    "project_id": "local",
                    "title": "Real pipeline project",
                    "status": "ready",
                    "source": {"video_path": "input/source.mp4"},
                    "artifacts": {"transcript_path": "transcripts/final_transcript.json"},
                    "clips": [
                        {
                            "id": "clip_001",
                            "index": 1,
                            "ai_start": 762.32,
                            "ai_end": 813.62,
                            "edited_start": 742.32,
                            "edited_end": 832.32,
                            "summary": "Real generated clip",
                            "text": "Real project text",
                            "raw_outputs": ["cuts/raw/clip_001.mp4"],
                            "subtitled_outputs": ["cuts/subtitles/clip_001.mp4"],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return state_path

    def _write_top_windows(self, relative_path="top_windows.json", *, start=762.32, end=813.62, summary="Real top window"):
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "top_windows": [
                        {
                            "start": start,
                            "end": end,
                            "summary": summary,
                            "text": "Generated candidate text",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return path

    def _run_cli(self, *args):
        output = io.StringIO()
        exit_code = import_local_project_main(["--project-root", str(self.root), *args], stdout=output)
        return exit_code, output.getvalue()

    def test_reset_replaces_stale_demo_db_with_real_project_state_times(self):
        self._seed_demo_project()
        self._write_project_state()

        exit_code, output = self._run_cli("--reset")

        self.assertEqual(exit_code, 0)
        with session_scope() as session:
            clips = list(session.scalars(select(Clip)).all())
            self.assertEqual(len(clips), 1)
            self.assertEqual(clips[0].ai_start, 762.32)
            self.assertEqual(clips[0].ai_end, 813.62)
            self.assertEqual(clips[0].edited_start, 742.32)
            self.assertEqual(clips[0].edited_end, 832.32)
        self.assertIn("Imported clips: 1", output)
        self.assertIn("Candidate source used: data/projects/local/project_state.json", output)
        self.assertIn("First imported clip: clip_001 ai_start=762.32 ai_end=813.62", output)

    def test_top_windows_has_priority_over_examples_without_project_state(self):
        self._write_top_windows()
        self._write_top_windows("examples/top_windows.example.json", start=100.0, end=140.0, summary="Demo example")

        exit_code, output = self._run_cli("--reset")

        self.assertEqual(exit_code, 0)
        with session_scope() as session:
            clip = session.scalars(select(Clip)).one()
            project = session.scalars(select(Project)).one()
            self.assertEqual(clip.ai_start, 762.32)
            self.assertEqual(project.candidate_source_path, "top_windows.json")
        self.assertIn("Candidate source used: top_windows.json", output)

    def test_examples_are_not_used_when_real_top_windows_exists(self):
        self._write_top_windows(summary="Real generated candidate")
        self._write_top_windows("examples/top_windows.example.json", start=100.0, end=140.0, summary="Demo example")

        exit_code, _output = self._run_cli("--reset")

        self.assertEqual(exit_code, 0)
        with session_scope() as session:
            clip = session.scalars(select(Clip)).one()
            self.assertEqual(clip.summary, "Real generated candidate")
            self.assertNotEqual(clip.ai_start, 100.0)

    def test_reset_does_not_touch_generated_media_files(self):
        self._seed_demo_project()
        self._write_project_state()
        media_paths = [
            self.root / "input" / "source.mp4",
            self.root / "cuts" / "raw" / "clip_001.mp4",
            self.root / "cuts" / "subtitles" / "clip_001.mp4",
            self.root / "transcripts" / "final_transcript.json",
        ]
        for path in media_paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"contents for {path.name}".encode("utf-8"))
        before = {path: path.read_bytes() for path in media_paths}

        exit_code, _output = self._run_cli("--reset")

        self.assertEqual(exit_code, 0)
        self.assertEqual(before, {path: path.read_bytes() for path in media_paths})

    def test_non_destructive_mode_prints_current_summary_when_db_exists(self):
        self._seed_demo_project()
        self._write_project_state()

        exit_code, output = self._run_cli()

        self.assertEqual(exit_code, 0)
        self.assertIn("SQLite project summary: projects=1", output)
        self.assertIn("SQLite already has project data. Run with --reset", output)
        self.assertIn("SQLite contains demo data while real candidate files exist", output)


if __name__ == "__main__":
    unittest.main()
