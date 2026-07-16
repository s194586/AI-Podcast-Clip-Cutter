import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from apps.api.db.database import configure_database, init_database, session_scope
from apps.api.db.models import utc_now
from apps.api.db.repositories import ClipRepository, ProjectRepository
from apps.api.main import app
from apps.api.services import project_service
from apps.api.services.render import locate_input_video


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _clip_payload(clip_id: str, *, start: float = 10.0, end: float = 40.0) -> dict:
    return {
        "id": clip_id,
        "index": 1,
        "ai_start": start,
        "ai_end": end,
        "edited_start": start,
        "edited_end": end,
        "min_start": max(0.0, start - 20.0),
        "max_start": start + 20.0,
        "min_end": max(start + 10.0, end - 20.0),
        "max_end": end + 20.0,
        "summary": f"Summary {clip_id}",
        "text": f"Transcript {clip_id}",
        "status": "draft",
        "render_status": "not_rendered",
    }


class ProjectRestorationTests(unittest.TestCase):
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

    def _seed_project(self, *, title: str, status: str = "ready", stage: str = "ready", progress: float = 100.0) -> int:
        with session_scope() as session:
            project = ProjectRepository(session).create(
                source_url=f"https://example.com/{title}",
                title=title,
                status=status,
                current_stage=stage,
                progress_percent=progress,
            )
            ClipRepository(session).create_from_dict(project.id, _clip_payload(f"{title}_clip"))
            return project.id

    def test_ready_legacy_project_serializes_ready_stage_and_100_progress(self):
        with session_scope() as session:
            project = ProjectRepository(session).create(
                source_url="https://example.com/legacy",
                title="Legacy",
                status="ready",
                current_stage="waiting",
                progress_percent=0.0,
            )
            ClipRepository(session).create_from_dict(project.id, _clip_payload("legacy_clip"))
            project_id = project.id

        status = project_service.get_project_status(project_id)
        listed = project_service.list_projects()[0]

        self.assertEqual(status["status"], "ready")
        self.assertEqual(status["stage"], "ready")
        self.assertEqual(status["progress_percent"], 100.0)
        self.assertEqual(listed["current_stage"], "ready")
        self.assertEqual(listed["progress_percent"], 100.0)

    def test_projects_are_ordered_by_most_recently_updated(self):
        first_id = self._seed_project(title="first")
        second_id = self._seed_project(title="second")
        now = utc_now()
        with session_scope() as session:
            first = ProjectRepository(session).get(first_id)
            second = ProjectRepository(session).get(second_id)
            first.updated_at = now
            second.updated_at = now - timedelta(days=1)

        projects = project_service.list_projects()

        self.assertEqual(projects[0]["id"], first_id)

    def test_project_specific_clip_loading_has_no_stale_global_clips(self):
        first_id = self._seed_project(title="first")
        second_id = self._seed_project(title="second")

        with TestClient(app) as client:
            first_response = client.get(f"/projects/{first_id}/clips")
            second_response = client.get(f"/projects/{second_id}/clips")

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(first_response.json()["clips"][0]["id"], "first_clip")
        self.assertEqual(second_response.json()["clips"][0]["id"], "second_clip")

    def test_static_frontend_project_restore_contract(self):
        repo_root = Path(__file__).resolve().parents[1]
        index_html = (repo_root / "apps" / "api" / "static" / "index.html").read_text(encoding="utf-8")
        app_js = (repo_root / "apps" / "api" / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="projectSelector"', index_html)
        self.assertIn('id="configuredReviewProvider"', index_html)
        self.assertIn('id="lastReviewProvider"', index_html)
        self.assertIn("podcast_cutter_selected_project_id", app_js)
        self.assertIn('fetch("/projects")', app_js)
        self.assertIn('fetch("/health")', app_js)
        self.assertIn("selectedProjectIdFromStorage", app_js)
        self.assertIn("const restoredProject = state.projects.find", app_js)
        self.assertIn("const selectedProject = restoredProject || state.projects[0]", app_js)
        self.assertIn("initializeProjectFlow();", app_js)
        self.assertIn("/projects/${encodeURIComponent(state.activeProjectId)}/clips", app_js)
        self.assertIn("projectSelectionRequestId", app_js)
        self.assertIn("clipLoadRequestId", app_js)
        self.assertIn("loadedmetadata", app_js)
        self.assertIn("markSourceVideoFailed", app_js)
        self.assertIn("preserveVideo: true", app_js)
        self.assertIn("state.activeProjectId || state.activeFlowProjectId", app_js)
        self.assertIn("updateHistoricalReviewProvider(payload.provider || state.configuredReviewProvider)", app_js)
        self.assertNotIn('fetch("/clips")', app_js)

    def test_unicode_source_video_path_resolves_and_source_endpoint_opens(self):
        unicode_name = "To jest BARDZO potężne – Słuchaj przez 30 minut ｜ Napoleon Hill.mp4"
        with session_scope() as session:
            project = ProjectRepository(session).create(
                source_url="https://example.com/unicode",
                title="Unicode",
                status="ready",
                current_stage="ready",
                progress_percent=100.0,
                workspace_path="data/projects/7/workspace",
                source_video_path=f"data/projects/7/workspace/input/{unicode_name}",
            )
            ClipRepository(session).create_from_dict(project.id, _clip_payload("unicode_clip"))
            project_id = project.id

        workspace = self.root / "data" / "projects" / "7" / "workspace"
        input_dir = workspace / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        source_path = input_dir / unicode_name
        source_path.write_bytes(b"fake video bytes")

        resolved = project_service.get_project_source_video_path(project_id, project_root=self.root)

        self.assertEqual(resolved, source_path)
        self.assertEqual(locate_input_video(workspace), source_path)
        with TestClient(app) as client:
            response = client.get(f"/projects/{project_id}/source-video")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"fake video bytes")

    def test_source_video_prefers_merged_media_over_format_variant(self):
        base_name = "Podcast Unicode title"
        with session_scope() as session:
            project = ProjectRepository(session).create(
                source_url="https://example.com/muxed",
                title="Muxed",
                status="ready",
                current_stage="ready",
                progress_percent=100.0,
                workspace_path="data/projects/8/workspace",
                source_video_path=f"data/projects/8/workspace/input/{base_name}.f399.mp4",
            )
            project_id = project.id

        workspace = self.root / "data" / "projects" / "8" / "workspace"
        input_dir = workspace / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        (input_dir / f"{base_name}.f251.webm").write_bytes(b"a" * 3)
        (input_dir / f"{base_name}.f399.mp4").write_bytes(b"v" * 5)
        merged_path = input_dir / f"{base_name}.mp4"
        merged_path.write_bytes(b"merged media")

        self.assertEqual(project_service.get_project_source_video_path(project_id, project_root=self.root), merged_path)
        self.assertEqual(locate_input_video(workspace), merged_path)


if __name__ == "__main__":
    unittest.main()
