import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from apps.api.db.database import configure_database, init_database, session_scope
from apps.api.db.models import ClipEvaluation
from apps.api.db.repositories import ClipRepository, JobRepository, ProjectRepository
from apps.api.main import app
from apps.review_agent.service import ReviewAgentService
from apps.review_agent.tools import check_sensitive_patterns, suggest_boundaries


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


class ReviewAgentTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_url = _sqlite_url(self.root / "test.db")
        os.environ["PODCAST_CUTTER_DB_URL"] = self.db_url
        os.environ["PODCAST_CUTTER_PROJECT_ROOT"] = str(self.root)
        os.environ["CLIP_REVIEW_MODE"] = "local_only"
        configure_database(self.db_url)
        init_database()
        self._write_transcript(
            [
                {"start": 80.0, "end": 98.0, "text": "Here is the setup before the answer.", "speaker": "A"},
                {
                    "start": 100.0,
                    "end": 120.0,
                    "text": "What changed the project? The team made the flow easier because people needed clarity.",
                    "speaker": "A",
                },
                {"start": 120.0, "end": 140.0, "text": "That was the useful payoff.", "speaker": "B"},
            ]
        )

    def tearDown(self):
        configure_database("sqlite:///:memory:")
        os.environ.pop("PODCAST_CUTTER_DB_URL", None)
        os.environ.pop("PODCAST_CUTTER_PROJECT_ROOT", None)
        os.environ.pop("CLIP_REVIEW_MODE", None)
        self.tempdir.cleanup()

    def _write_transcript(self, segments):
        transcript_path = self.root / "transcripts" / "final_transcript.json"
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        transcript_path.write_text(json.dumps({"segments": segments}), encoding="utf-8")

    def _seed_clip(self, *, text=None, local_score=0.82):
        with session_scope() as session:
            project = ProjectRepository(session).create(
                source_url="https://www.youtube.com/watch?v=test",
                title="Review project",
                status="ready",
                transcript_path="transcripts/final_transcript.json",
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
                    "text": text or "What changed the project? The team made the flow easier because people needed clarity.",
                    "status": "draft",
                    "render_status": "not_rendered",
                    "local_score": local_score,
                    "local_rank": 1,
                    "selection_reasons": ["strong local score"],
                    "local_features": {"payoff": 0.8},
                },
            )
            return project.id

    def test_local_clip_review_agent_returns_structured_evaluation(self):
        project_id = self._seed_clip()

        result = ReviewAgentService(project_root=self.root, use_langgraph=False).review_clip(
            project_id=project_id,
            clip_id="clip_001",
        )

        self.assertEqual(result["clip_id"], "clip_001")
        self.assertIn(result["recommended_action"], {"keep", "adjust_boundaries", "render_ready"})
        self.assertGreater(result["quality_score"], 0.0)
        self.assertEqual(result["privacy_risk"], "low")

    def test_review_agent_can_request_more_context_once(self):
        self._write_transcript(
            [
                {"start": 50.0, "end": 80.0, "text": "The missing setup explains what the speaker means."},
                {"start": 100.0, "end": 130.0, "text": "and that is why this clip depends on the previous setup."},
                {"start": 130.0, "end": 140.0, "text": "The thought ends cleanly."},
            ]
        )
        project_id = self._seed_clip(text="and that is why this clip depends on the previous setup.")

        result = ReviewAgentService(project_root=self.root, use_langgraph=False).review_clip(
            project_id=project_id,
            clip_id="clip_001",
        )

        self.assertEqual(result["context_expansions"], 1)
        self.assertFalse(result["needs_more_context"])

    def test_sensitive_pattern_checker_detects_email_and_phone_like_data(self):
        result = check_sensitive_patterns("Reach me at person@example.com or +48 123 456 789 about the invoice.")

        self.assertEqual(result["privacy_risk"], "medium")
        self.assertIn("email", {match["type"] for match in result["matches"]})
        self.assertIn("phone", {match["type"] for match in result["matches"]})

    def test_boundary_suggestion_detects_context_dependent_start(self):
        result = suggest_boundaries(
            {"clip_text": "and then the useful answer finally lands."},
            {
                "edited_start": 100.0,
                "edited_end": 140.0,
                "min_start": 80.0,
                "max_start": 120.0,
                "min_end": 120.0,
                "max_end": 160.0,
            },
        )

        self.assertLess(result["suggested_start"], 100.0)
        self.assertIn("earlier", result["start_advice"])

    def test_evaluation_result_persists_in_sqlite(self):
        project_id = self._seed_clip()

        ReviewAgentService(project_root=self.root, use_langgraph=False).review_clip(
            project_id=project_id,
            clip_id="clip_001",
        )

        with session_scope() as session:
            evaluations = list(session.scalars(select(ClipEvaluation)).all())
            self.assertEqual(len(evaluations), 1)
            self.assertEqual(evaluations[0].external_clip_id, "clip_001")

    def test_get_review_endpoint_returns_latest_evaluation(self):
        project_id = self._seed_clip()
        ReviewAgentService(project_root=self.root, use_langgraph=False).review_clip(
            project_id=project_id,
            clip_id="clip_001",
        )

        with TestClient(app) as client:
            response = client.get("/clips/clip_001/review")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["clip_id"], "clip_001")

    def test_fastapi_app_starts_without_airflow_installed(self):
        self._seed_clip()

        with TestClient(app) as client:
            response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_airflow_dag_file_imports_or_safely_skips_without_airflow(self):
        dag_path = Path(__file__).resolve().parents[1] / "orchestration" / "airflow" / "dags" / "podcast_pipeline_dag.py"
        spec = importlib.util.spec_from_file_location("podcast_pipeline_dag_test", dag_path)
        module = importlib.util.module_from_spec(spec)
        self.assertIsNotNone(spec.loader)
        spec.loader.exec_module(module)

        self.assertTrue(hasattr(module, "AIRFLOW_AVAILABLE"))
        if not module.AIRFLOW_AVAILABLE:
            self.assertIsNone(module.podcast_pipeline)

    def test_project_status_endpoint_works(self):
        project_id = self._seed_clip()
        with session_scope() as session:
            JobRepository(session).create(
                project_id=project_id,
                job_type="airflow_pipeline",
                status="failed",
                stage="transcribing",
                error_message="boom",
            )

        with TestClient(app) as client:
            response = client.get(f"/projects/{project_id}/status")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["project_id"], project_id)
        self.assertEqual(payload["clip_count"], 1)
        self.assertEqual(payload["last_error"], "boom")


if __name__ == "__main__":
    unittest.main()
