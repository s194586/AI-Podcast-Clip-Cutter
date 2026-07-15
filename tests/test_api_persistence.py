import json
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import inspect, select

from apps.api.db.database import configure_database, init_database, session_scope
from apps.api.db.models import Artifact, Clip, Project
from apps.api.db.repositories import ClipRepository, ProjectRepository
from apps.api.main import app
from apps.api.services import clip_service, project_service
from apps.api.services.legacy_import_service import bootstrap_legacy_state_if_needed


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


class ApiPersistenceTests(unittest.TestCase):
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

    def _seed_clip(self, *, external_id: str = "clip_001") -> tuple[int, str]:
        with session_scope() as session:
            project = ProjectRepository(session).create(
                source_url="https://www.youtube.com/watch?v=test",
                title="Seed project",
                status="ready",
            )
            ClipRepository(session).create_from_dict(
                project.id,
                {
                    "id": external_id,
                    "index": 1,
                    "ai_start": 100.0,
                    "ai_end": 140.0,
                    "edited_start": 100.0,
                    "edited_end": 140.0,
                    "min_start": 80.0,
                    "max_start": 120.0,
                    "min_end": 120.0,
                    "max_end": 160.0,
                    "summary": "Useful podcast moment",
                    "text": "Question followed by answer.",
                    "status": "draft",
                    "render_status": "not_rendered",
                    "local_score": 0.91,
                    "local_rank": 1,
                    "selection_reasons": ["strong payoff"],
                    "local_features": {"payoff": 0.8},
                },
            )
            return project.id, external_id

    def test_database_initialization_creates_domain_tables(self):
        table_names = set(inspect(configure_database(self.db_url)).get_table_names())

        self.assertTrue({"projects", "clips", "jobs", "artifacts"}.issubset(table_names))

    def test_project_creation(self):
        project = project_service.create_project(source_url="https://www.youtube.com/watch?v=new", title="New podcast")
        self.assertEqual(project["source_url"], "https://www.youtube.com/watch?v=new")
        self.assertEqual(project["title"], "New podcast")

        with session_scope() as session:
            stored = session.get(Project, project["id"])
            self.assertIsNotNone(stored)

    def test_project_listing_newest_first_with_counts(self):
        first = project_service.create_project(source_url="https://example.com/1", title="First")
        second = project_service.create_project(source_url="https://example.com/2", title="Second")

        projects = project_service.list_projects()

        self.assertEqual([item["id"] for item in projects[:2]], [second["id"], first["id"]])
        self.assertEqual(projects[0]["clip_count"], 0)
        self.assertEqual(projects[0]["accepted_clip_count"], 0)

    def test_clip_persistence_loads_from_sqlite(self):
        _project_id, clip_id = self._seed_clip()

        clips = clip_service.load_clips()

        self.assertEqual(clips[0]["id"], clip_id)
        self.assertEqual(clips[0]["selection_reasons"], ["strong payoff"])

    def test_edited_bounds_survive_new_database_session(self):
        self._seed_clip()

        updated = clip_service.update_bounds("clip_001", 105.0, 145.0)
        self.assertEqual(updated["edited_start"], 105.0)

        with session_scope() as session:
            stored = session.scalars(select(Clip).where(Clip.external_id == "clip_001")).one()
            self.assertEqual(stored.edited_start, 105.0)
            self.assertEqual(stored.edited_end, 145.0)

    def test_accept_status_persists(self):
        self._seed_clip()

        clip_service.set_status("clip_001", "accepted")

        self.assertEqual(clip_service.load_clips()[0]["status"], "accepted")

    def test_reject_status_persists(self):
        self._seed_clip()

        clip_service.set_status("clip_001", "rejected")

        self.assertEqual(clip_service.load_clips()[0]["status"], "rejected")

    def test_imports_old_project_state_json(self):
        state_path = self.root / "data" / "projects" / "local" / "project_state.json"
        state_path.parent.mkdir(parents=True)
        state_path.write_text(
            json.dumps(
                {
                    "project_id": "local",
                    "title": "Legacy title",
                    "source": {"url": "https://legacy.example/video", "video_path": "input/source.mp4"},
                    "artifacts": {
                        "transcript_path": "transcripts/final_transcript.json",
                        "candidate_source_path": "metadata/top_windows.json",
                    },
                    "clips": [
                        {
                            "id": "clip_001",
                            "index": 1,
                            "ai_start": 10,
                            "ai_end": 50,
                            "edited_start": 12,
                            "edited_end": 48,
                            "status": "accepted",
                            "render_status": "completed",
                            "raw_outputs": ["outputs/raw/segment_001.mp4"],
                            "subtitled_outputs": ["outputs/subs/segment_001.mp4"],
                            "selection_reasons": ["legacy reason"],
                            "local_features": {"legacy": True},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        with session_scope() as session:
            bootstrap_legacy_state_if_needed(session, project_root=self.root)

        with session_scope() as session:
            project = session.scalars(select(Project)).one()
            clip = session.scalars(select(Clip)).one()
            artifact_types = {artifact.artifact_type for artifact in session.scalars(select(Artifact)).all()}
            self.assertEqual(project.title, "Legacy title")
            self.assertEqual(clip.status, "accepted")
            self.assertEqual(clip.raw_outputs, ["outputs/raw/segment_001.mp4"])
            self.assertIn("raw_clip", artifact_types)
            self.assertIn("subtitled_clip", artifact_types)

    def test_imports_candidate_windows_when_database_is_empty(self):
        windows_path = self.root / "top_windows.json"
        windows_path.write_text(
            json.dumps(
                {
                    "top_windows": [
                        {
                            "start": 20,
                            "end": 65,
                            "summary": "Candidate summary",
                            "text": "Candidate text",
                            "local_score": 0.7,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        with session_scope() as session:
            bootstrap_legacy_state_if_needed(session, project_root=self.root)

        clips = clip_service.load_clips(project_root=self.root)
        self.assertEqual(clips[0]["id"], "clip_001")
        self.assertEqual(clips[0]["source"], "top_windows.json")

    def test_does_not_reimport_legacy_state_when_database_has_project(self):
        self._seed_clip()
        state_path = self.root / "data" / "projects" / "local" / "project_state.json"
        state_path.parent.mkdir(parents=True)
        state_path.write_text(
            json.dumps(
                {
                    "project_id": "local",
                    "clips": [{"id": "clip_999", "ai_start": 1, "ai_end": 20}],
                }
            ),
            encoding="utf-8",
        )

        with session_scope() as session:
            bootstrap_legacy_state_if_needed(session, project_root=self.root)
            self.assertEqual(len(session.scalars(select(Project)).all()), 1)

        clips = clip_service.load_clips(project_root=self.root)
        self.assertEqual([clip["id"] for clip in clips], ["clip_001"])

    def test_artifact_creation_after_mocked_render_result(self):
        self._seed_clip()

        updated = clip_service.record_render_result(
            "clip_001",
            {
                "status": "completed_with_warnings",
                "output_dir": "outputs/editor_renders/render1",
                "raw_outputs": ["outputs/editor_renders/render1/raw/segment_001.mp4"],
                "subtitled_outputs": ["outputs/editor_renders/render1/subtitles/segment_001.mp4"],
                "warnings": ["subtitle warning"],
            },
        )

        self.assertEqual(updated["render_status"], "completed_with_warnings")
        with session_scope() as session:
            artifact_types = [artifact.artifact_type for artifact in session.scalars(select(Artifact)).all()]
            self.assertEqual(artifact_types.count("raw_clip"), 1)
            self.assertEqual(artifact_types.count("subtitled_clip"), 1)

    def test_compatibility_get_clips(self):
        _project_id, clip_id = self._seed_clip()

        with TestClient(app) as client:
            response = client.get("/clips")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["clips"][0]["id"], clip_id)
        self.assertFalse(payload["source_video_available"])

    def test_project_specific_get_project_clips(self):
        project_id, clip_id = self._seed_clip()

        with TestClient(app) as client:
            response = client.get(f"/projects/{project_id}/clips")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["clips"][0]["id"], clip_id)

    def test_project_api_create_and_list(self):
        self._seed_clip()

        with TestClient(app) as client:
            create_response = client.post(
                "/projects",
                json={"source_url": "https://www.youtube.com/watch?v=api", "title": "API project"},
            )
            list_response = client.get("/projects")

        self.assertEqual(create_response.status_code, 200)
        self.assertEqual(create_response.json()["project"]["title"], "API project")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["projects"][0]["title"], "API project")


if __name__ == "__main__":
    unittest.main()
