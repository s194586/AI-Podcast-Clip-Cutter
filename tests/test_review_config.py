import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from apps.api.db.database import configure_database, init_database
from apps.api.main import app
from apps.review_agent.config import ReviewConfigError, load_review_config
from apps.review_agent.service import ClipReviewConfigurationError, ReviewAgentService


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


class ReviewConfigTests(unittest.TestCase):
    def test_default_mode_is_gemini_and_missing_key_never_selects_local_stub(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            with patch.dict(os.environ, {}, clear=True):
                config = load_review_config(project_root=root, require_api_key=False)
                service = ReviewAgentService(project_root=root)
                with self.assertRaisesRegex(ReviewConfigError, "GEMINI_API_KEY"):
                    load_review_config(project_root=root)
                with self.assertRaisesRegex(ClipReviewConfigurationError, "GEMINI_API_KEY"):
                    service._create_provider()

        self.assertEqual(config.provider, "gemini")
        self.assertEqual(config.mode_source, "default")
        self.assertFalse(config.api_key_configured)

    def test_local_stub_remains_available_only_when_explicitly_selected(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            with patch.dict(os.environ, {"CLIP_REVIEW_MODE": "local_stub"}, clear=True):
                config = load_review_config(project_root=root)
                provider = ReviewAgentService(project_root=root)._create_provider()

        self.assertEqual(config.provider, "local_stub")
        self.assertEqual(config.mode_source, "environment")
        self.assertEqual(provider.provider, "local_stub")

    def test_dotenv_gemini_selects_gemini_provider_without_process_env(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / ".env").write_text(
                "CLIP_REVIEW_MODE= Gemini \nGEMINI_API_KEY=fake-test-key\nGEMINI_MODEL=gemini-dotenv\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                config = load_review_config(project_root=root)
                service = ReviewAgentService(project_root=root)
                provider = service._create_provider()

        self.assertEqual(config.provider, "gemini")
        self.assertEqual(config.mode_source, ".env")
        self.assertEqual(config.model, "gemini-dotenv")
        self.assertTrue(config.api_key_configured)
        self.assertEqual(provider.provider, "gemini")
        self.assertEqual(provider.model, "gemini-dotenv")
        self.assertNotIn("fake-test-key", repr(config))

    def test_process_environment_overrides_dotenv_mode_with_warning(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / ".env").write_text(
                "CLIP_REVIEW_MODE=gemini\nGEMINI_API_KEY=fake-test-key\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"CLIP_REVIEW_MODE": "local_stub"}, clear=True):
                config = load_review_config(project_root=root, require_api_key=False)

        self.assertEqual(config.provider, "local_stub")
        self.assertEqual(config.mode_source, "environment")
        self.assertTrue(any("CLIP_REVIEW_MODE" in warning for warning in config.warnings))

    def test_unsupported_review_mode_fails_clearly(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / ".env").write_text("CLIP_REVIEW_MODE=bogus\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(ReviewConfigError) as raised:
                    load_review_config(project_root=root, require_api_key=False)

        self.assertIn("Unsupported CLIP_REVIEW_MODE", str(raised.exception))

    def test_gemini_mode_requires_non_empty_api_key_when_provider_is_used(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / ".env").write_text("CLIP_REVIEW_MODE=gemini\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(ReviewConfigError) as raised:
                    load_review_config(project_root=root)
                config = load_review_config(project_root=root, require_api_key=False)

        self.assertIn("GEMINI_API_KEY", str(raised.exception))
        self.assertEqual(config.provider, "gemini")
        self.assertFalse(config.api_key_configured)

    def test_health_exposes_safe_review_config_without_secret(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            db_url = _sqlite_url(root / "test.db")
            (root / ".env").write_text(
                "CLIP_REVIEW_MODE=gemini\nGEMINI_API_KEY=fake-health-key\nGEMINI_MODEL=gemini-health\n",
                encoding="utf-8",
            )

            with patch.dict(
                os.environ,
                {
                    "PODCAST_CUTTER_DB_URL": db_url,
                    "PODCAST_CUTTER_PROJECT_ROOT": str(root),
                },
                clear=True,
            ):
                try:
                    configure_database(db_url)
                    init_database()
                    with TestClient(app) as client:
                        response = client.get("/health")
                finally:
                    configure_database("sqlite:///:memory:")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["clip_review_provider"], "gemini")
        self.assertEqual(payload["clip_review_model"], "gemini-health")
        self.assertTrue(payload["gemini_api_key_configured"])
        self.assertNotIn("fake-health-key", json.dumps(payload))

    def test_health_and_project_start_expose_missing_gemini_key_as_configuration_error(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            db_url = _sqlite_url(root / "test.db")
            with patch.dict(
                os.environ,
                {
                    "PODCAST_CUTTER_DB_URL": db_url,
                    "PODCAST_CUTTER_PROJECT_ROOT": str(root),
                    "PIPELINE_ORCHESTRATOR": "local",
                },
                clear=True,
            ):
                try:
                    configure_database(db_url)
                    init_database()
                    from apps.api.services.project_service import create_project

                    project = create_project(
                        source_url="https://www.youtube.com/watch?v=test",
                        auto_review=True,
                        project_root=root,
                    )
                    with TestClient(app) as client:
                        health_response = client.get("/health")
                        start_response = client.post(f"/projects/{project['id']}/start")
                finally:
                    configure_database("sqlite:///:memory:")

        self.assertEqual(health_response.status_code, 200)
        self.assertEqual(health_response.json()["clip_review_provider"], "gemini")
        self.assertIn("GEMINI_API_KEY", health_response.json()["review_config"]["configuration_error"])
        self.assertEqual(start_response.status_code, 503)
        self.assertIn("GEMINI_API_KEY", start_response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
