from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import yaml
from sqlalchemy import inspect

from apps.api.db.database import configure_database, get_engine, init_database, session_scope
from apps.api.db.repositories import JobRepository, ProjectRepository
from apps.api.orchestration.airflow import AirflowOrchestrator
from apps.api.orchestration.airflow_client import (
    AirflowApiClient,
    AirflowApiUnavailableError,
    AirflowSettings,
)
from apps.api.orchestration.base import (
    AIRFLOW_PIPELINE_JOB_TYPE,
    LOCAL_PIPELINE_JOB_TYPE,
    ProjectAlreadyRunningError,
    ProjectOrchestratorConfigurationError,
)
from apps.api.orchestration.local import LocalPipelineOrchestrator
from apps.api.orchestration.service import get_pipeline_orchestrator
from apps.api.services import project_service
from apps.pipeline.airflow_config import AirflowRunConfig
from apps.pipeline.registry import PROJECT_STAGE_ORDER
from apps.pipeline.results import PipelineStageResult
from orchestration.airflow.dags.podcast_pipeline_dag import (
    AIRFLOW_AVAILABLE,
    DAG_ID,
    TASK_ORDER,
    TASK_RETRIES,
    podcast_clip_pipeline,
)
from orchestration.airflow.pipeline_tasks import execute_airflow_stage


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _valid_payload(*, project_id: int = 7, job_id: int = 11, auto_review: bool = True):
    return {
        "schema_version": 1,
        "project_id": project_id,
        "job_id": job_id,
        "source_url": "https://www.youtube.com/watch?v=offline",
        "workspace_relative_path": f"data/projects/{project_id}/workspace",
        "auto_review": auto_review,
        "subtitle_checker_mode": "local_only",
    }


class AirflowRunConfigTests(unittest.TestCase):
    def test_valid_config_round_trips_exact_allowlist(self):
        config = AirflowRunConfig.from_dict(_valid_payload())
        self.assertEqual(config.to_dict(), _valid_payload())

    def test_unknown_field_is_rejected(self):
        payload = {**_valid_payload(), "password": "not-allowed"}
        with self.assertRaisesRegex(ValueError, "Unknown"):
            AirflowRunConfig.from_dict(payload)

    def test_missing_field_is_rejected(self):
        payload = _valid_payload()
        del payload["job_id"]
        with self.assertRaisesRegex(ValueError, "Missing"):
            AirflowRunConfig.from_dict(payload)

    def test_unknown_schema_version_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "schema_version"):
            AirflowRunConfig.from_dict({**_valid_payload(), "schema_version": 2})

    def test_boolean_project_id_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "project_id"):
            AirflowRunConfig.from_dict({**_valid_payload(), "project_id": True})

    def test_nonpositive_job_id_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "job_id"):
            AirflowRunConfig.from_dict({**_valid_payload(), "job_id": 0})

    def test_empty_source_url_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "source_url"):
            AirflowRunConfig.from_dict({**_valid_payload(), "source_url": " "})

    def test_long_source_url_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "length"):
            AirflowRunConfig.from_dict({**_valid_payload(), "source_url": "x" * 2049})

    def test_nonboolean_auto_review_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "auto_review"):
            AirflowRunConfig.from_dict({**_valid_payload(), "auto_review": 1})

    def test_unknown_subtitle_mode_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "subtitle_checker_mode"):
            AirflowRunConfig.from_dict({**_valid_payload(), "subtitle_checker_mode": "remote"})

    def test_absolute_workspace_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "relative"):
            AirflowRunConfig.from_dict({**_valid_payload(), "workspace_relative_path": "/data/projects/7/workspace"})

    def test_windows_workspace_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "POSIX"):
            AirflowRunConfig.from_dict({**_valid_payload(), "workspace_relative_path": r"C:\data\projects\7\workspace"})

    def test_workspace_traversal_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unsafe"):
            AirflowRunConfig.from_dict({**_valid_payload(), "workspace_relative_path": "data/projects/7/../workspace"})

    def test_workspace_for_another_project_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Project 7"):
            AirflowRunConfig.from_dict({**_valid_payload(), "workspace_relative_path": "data/projects/8/workspace"})

    def test_context_is_reconstructed_under_container_root(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            context = AirflowRunConfig.from_dict(_valid_payload()).build_context(
                container_project_root=root
            )
            self.assertEqual(context.workspace_path, (root / "data/projects/7/workspace").resolve())
            self.assertEqual(context.repository_root, root.resolve())


class AirflowSettingsAndInfrastructureTests(unittest.TestCase):
    def test_settings_load_documented_environment_contract(self):
        settings = AirflowSettings.from_environment(
            {
                "AIRFLOW_API_BASE_URL": "http://airflow-api-server:8080",
                "AIRFLOW_UI_BASE_URL": "http://localhost:8080",
                "AIRFLOW_API_USERNAME": "admin",
                "AIRFLOW_API_PASSWORD": "secret-value",
                "AIRFLOW_API_TIMEOUT_SECONDS": "12",
                "AIRFLOW_CONTAINER_ROOT": "/opt/ai-cutter",
            }
        )
        self.assertEqual(settings.request_timeout_seconds, 12.0)
        self.assertNotIn("secret-value", repr(settings))

    def test_settings_require_api_credentials(self):
        with self.assertRaisesRegex(ValueError, "AIRFLOW_API_USERNAME"):
            AirflowSettings.from_environment({"AIRFLOW_API_BASE_URL": "http://localhost:8080"})

    def test_settings_reject_credentials_in_url(self):
        with self.assertRaisesRegex(ValueError, "must not contain credentials"):
            AirflowSettings.from_environment(
                {
                    "AIRFLOW_API_BASE_URL": "http://user:pass@localhost:8080",
                    "AIRFLOW_API_USERNAME": "admin",
                    "AIRFLOW_API_PASSWORD": "secret",
                }
            )

    def test_settings_reject_unbounded_timeout(self):
        with self.assertRaisesRegex(ValueError, "between 0 and 120"):
            AirflowSettings.from_environment(
                {
                    "AIRFLOW_API_BASE_URL": "http://localhost:8080",
                    "AIRFLOW_API_USERNAME": "admin",
                    "AIRFLOW_API_PASSWORD": "secret",
                    "AIRFLOW_API_TIMEOUT_SECONDS": "121",
                }
            )

    def test_settings_reject_unsafe_container_root(self):
        with self.assertRaisesRegex(ValueError, "safe absolute"):
            AirflowSettings.from_environment(
                {
                    "AIRFLOW_API_BASE_URL": "http://localhost:8080",
                    "AIRFLOW_API_USERNAME": "admin",
                    "AIRFLOW_API_PASSWORD": "secret",
                    "AIRFLOW_CONTAINER_ROOT": "../repo",
                }
            )

    def test_client_close_discards_bearer_token(self):
        settings = AirflowSettings(
            base_url="http://localhost:8080",
            ui_base_url=None,
            dag_id=DAG_ID,
            username="admin",
            password="secret",
            request_timeout_seconds=10,
            container_project_root="/opt/ai-cutter",
        )
        client = AirflowApiClient(settings)
        client._token = "temporary-token"
        client.close()
        self.assertIsNone(client._token)

    def test_trigger_payload_includes_required_nullable_logical_date(self):
        settings = AirflowSettings(
            base_url="http://localhost:8080",
            ui_base_url=None,
            dag_id=DAG_ID,
            username="admin",
            password="secret",
            request_timeout_seconds=10,
            container_project_root="/opt/ai-cutter",
        )
        client = AirflowApiClient(settings)
        client._request = Mock(return_value={"state": "queued"})

        client.trigger_dag_run(
            dag_id=DAG_ID,
            dag_run_id="project-1-job-1",
            conf={"schema_version": 1},
        )

        client._request.assert_called_once_with(
            "POST",
            f"/api/v2/dags/{DAG_ID}/dagRuns",
            {
                "dag_run_id": "project-1-job-1",
                "logical_date": None,
                "conf": {"schema_version": 1},
            },
        )

    def test_registry_and_dag_share_exact_stage_order(self):
        self.assertEqual(TASK_ORDER, PROJECT_STAGE_ORDER)

    def test_review_has_no_airflow_retry(self):
        self.assertEqual(TASK_RETRIES["review_boundaries"], 0)
        self.assertTrue(all(0 <= value <= 2 for value in TASK_RETRIES.values()))

    def test_dag_module_has_an_explicit_parse_state(self):
        self.assertIsInstance(AIRFLOW_AVAILABLE, bool)
        self.assertEqual(DAG_ID, "podcast_clip_pipeline")
        self.assertEqual(podcast_clip_pipeline is not None, AIRFLOW_AVAILABLE)

    def test_dag_and_task_adapter_do_not_invoke_legacy_manager_or_full_runner(self):
        root = Path(__file__).resolve().parents[1]
        source = "\n".join(
            (root / path).read_text(encoding="utf-8")
            for path in (
                "orchestration/airflow/dags/podcast_pipeline_dag.py",
                "orchestration/airflow/pipeline_tasks.py",
            )
        )
        self.assertNotIn("manager.py", source)
        self.assertNotIn("PipelineRunner", source)
        self.assertIn("execute_airflow_stage", source)

    def test_compose_uses_local_executor_postgres_and_no_celery_stack(self):
        root = Path(__file__).resolve().parents[1]
        compose = yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))
        services = compose["services"]
        self.assertEqual(
            set(services),
            {"postgres", "airflow-init", "airflow-api-server", "airflow-scheduler", "airflow-dag-processor", "app-api"},
        )
        self.assertEqual(compose["x-airflow-environment"]["AIRFLOW__CORE__EXECUTOR"], "LocalExecutor")
        self.assertNotIn("redis", services)
        self.assertNotIn("airflow-worker", services)

    def test_compose_pins_runtime_images_and_has_healthchecks(self):
        root = Path(__file__).resolve().parents[1]
        compose_text = (root / "docker-compose.yml").read_text(encoding="utf-8")
        compose = yaml.safe_load(compose_text)
        self.assertIn("postgres:16.14-bookworm", compose_text)
        self.assertIn("apache/airflow:3.3.0-python3.12", (root / "orchestration/airflow/Dockerfile").read_text(encoding="utf-8"))
        for service in ("postgres", "airflow-api-server", "airflow-scheduler", "airflow-dag-processor", "app-api"):
            self.assertIn("healthcheck", compose["services"][service])

    def test_docker_build_context_excludes_runtime_secrets_and_data(self):
        root = Path(__file__).resolve().parents[1]
        ignored = (root / ".dockerignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".env", ignored)
        self.assertIn(".env.*", ignored)
        self.assertIn("data", ignored)
        self.assertIn("orchestration/airflow/.env.airflow", ignored)


class FakeAirflowClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.run: dict = {"state": "running"}
        self.tasks: list[dict] = [{"task_id": "download_source", "state": "running", "try_number": 1, "max_tries": 1}]
        self.trigger_error: Exception | None = None
        self.read_error: Exception | None = None
        self.cancel_error: Exception | None = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def trigger_dag_run(self, **kwargs):
        self.calls.append(("trigger", kwargs))
        if self.trigger_error:
            raise self.trigger_error
        return {"state": "queued"}

    def get_dag_run(self, **kwargs):
        self.calls.append(("get_run", kwargs))
        if self.read_error:
            raise self.read_error
        return dict(self.run)

    def list_task_instances(self, **kwargs):
        self.calls.append(("list_tasks", kwargs))
        if self.cancel_error:
            raise self.cancel_error
        if self.read_error:
            raise self.read_error
        return [dict(task) for task in self.tasks]

    def fail_task_instance(self, **kwargs):
        self.calls.append(("fail_task", kwargs))
        if self.cancel_error:
            raise self.cancel_error
        return {}

    def set_dag_run_failed(self, **kwargs):
        self.calls.append(("fail_run", kwargs))
        if self.cancel_error:
            raise self.cancel_error
        return {"state": "failed"}


class AirflowOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_url = _sqlite_url(self.root / "airflow-tests.db")
        self.environment = patch.dict(
            os.environ,
            {
                "PODCAST_CUTTER_DB_URL": self.db_url,
                "PODCAST_CUTTER_PROJECT_ROOT": str(self.root),
                "AIRFLOW_API_BASE_URL": "http://airflow-api-server:8080",
                "AIRFLOW_UI_BASE_URL": "http://localhost:8080",
                "AIRFLOW_API_USERNAME": "admin",
                "AIRFLOW_API_PASSWORD": "offline-secret",
                "AIRFLOW_API_TIMEOUT_SECONDS": "10",
                "AIRFLOW_CONTAINER_ROOT": "/opt/ai-cutter",
            },
            clear=False,
        )
        self.environment.start()
        configure_database(self.db_url)
        init_database()
        project = project_service.create_project(
            source_url="https://www.youtube.com/watch?v=offline",
            title="Airflow offline test",
            auto_review=True,
            project_root=self.root,
        )
        self.project_id = int(project["id"])
        self.client = FakeAirflowClient()
        self.settings = AirflowSettings.from_environment()
        self.orchestrator = AirflowOrchestrator(
            project_root=self.root,
            settings=self.settings,
            client_factory=lambda _settings: self.client,
        )

    def tearDown(self):
        configure_database("sqlite:///:memory:")
        self.environment.stop()
        self.tempdir.cleanup()

    def _start(self):
        return self.orchestrator.start_project(self.project_id)

    def _latest_job(self):
        with session_scope() as session:
            job = JobRepository(session).latest_for_project(self.project_id, AIRFLOW_PIPELINE_JOB_TYPE)
            self.assertIsNotNone(job)
            session.expunge(job)
            return job

    def test_default_backend_is_local(self):
        with patch.dict(os.environ, {"PIPELINE_ORCHESTRATOR": ""}):
            self.assertIsInstance(get_pipeline_orchestrator(project_root=self.root), LocalPipelineOrchestrator)

    def test_explicit_local_backend_is_local(self):
        with patch.dict(os.environ, {"PIPELINE_ORCHESTRATOR": "local"}):
            self.assertIsInstance(get_pipeline_orchestrator(project_root=self.root), LocalPipelineOrchestrator)

    def test_airflow_backend_is_selected(self):
        with patch.dict(os.environ, {"PIPELINE_ORCHESTRATOR": "airflow"}):
            self.assertIsInstance(get_pipeline_orchestrator(project_root=self.root), AirflowOrchestrator)

    def test_unknown_backend_fails_clearly(self):
        with patch.dict(os.environ, {"PIPELINE_ORCHESTRATOR": "unknown"}):
            with self.assertRaisesRegex(ProjectOrchestratorConfigurationError, "Unsupported"):
                get_pipeline_orchestrator(project_root=self.root)

    def test_start_submits_strict_relative_payload_and_persists_mapping(self):
        result = self._start()
        call = self.client.calls[0]
        self.assertEqual(call[0], "trigger")
        self.assertEqual(set(call[1]["conf"]), set(_valid_payload()))
        self.assertEqual(call[1]["conf"]["workspace_relative_path"], f"data/projects/{self.project_id}/workspace")
        self.assertNotIn("password", str(call[1]["conf"]).lower())
        self.assertNotIn("\\", call[1]["conf"]["workspace_relative_path"])
        self.assertEqual(result.airflow_dag_run_id, self._latest_job().airflow_dag_run_id)

    def test_run_id_is_deterministic_and_safe(self):
        result = self._start()
        self.assertRegex(result.airflow_dag_run_id or "", rf"^project-{self.project_id}-job-\d+-\d{{8}}T\d{{6}}Z$")

    def test_duplicate_guard_covers_local_jobs(self):
        with session_scope() as session:
            JobRepository(session).create(
                project_id=self.project_id,
                job_type=LOCAL_PIPELINE_JOB_TYPE,
                status="running",
                stage="transcribing",
            )
        with self.assertRaises(ProjectAlreadyRunningError):
            self._start()

    def test_trigger_failure_is_persisted_without_local_fallback(self):
        self.client.trigger_error = AirflowApiUnavailableError("offline")
        with self.assertRaisesRegex(ProjectOrchestratorConfigurationError, "not started"):
            self._start()
        job = self._latest_job()
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.airflow_state, "unavailable")
        self.assertEqual(project_service.get_project_status(self.project_id)["status"], "failed")

    def test_running_remote_task_is_reconciled(self):
        self._start()
        status = self.orchestrator.get_status(self.project_id)
        self.assertEqual(status.status, "running")
        self.assertEqual(status.stage, "downloading")
        self.assertEqual(status.airflow_task_id, "download_source")

    def test_retry_state_remains_nonterminal(self):
        self._start()
        self.client.tasks = [{"task_id": "transcribe", "state": "up_for_retry", "try_number": 1, "max_tries": 1}]
        status = self.orchestrator.get_status(self.project_id)
        self.assertEqual(status.status, "running")
        self.assertEqual(status.airflow_state, "up_for_retry")
        self.assertEqual((status.retry_attempt, status.retry_max_attempts), (1, 2))

    def test_successful_run_marks_project_ready(self):
        self._start()
        self.client.run = {"state": "success"}
        self.client.tasks = [{"task_id": "mark_ready", "state": "success", "try_number": 1, "max_tries": 1}]
        status = self.orchestrator.get_status(self.project_id)
        self.assertEqual((status.status, status.stage, status.progress_percent), ("ready", "ready", 100.0))

    def test_failed_run_marks_project_failed(self):
        self._start()
        self.client.run = {"state": "failed"}
        self.client.tasks = [{"task_id": "transcribe", "state": "failed", "try_number": 2, "max_tries": 1}]
        status = self.orchestrator.get_status(self.project_id)
        self.assertEqual(status.status, "failed")
        self.assertIn("transcribe", status.error_message or "")

    def test_exhausted_task_failure_is_terminal_before_run_state_catches_up(self):
        self._start()
        self.client.tasks = [{"task_id": "transcribe", "state": "failed", "try_number": 2, "max_tries": 1}]
        status = self.orchestrator.get_status(self.project_id)
        self.assertEqual(status.status, "failed")

    def test_unavailable_status_is_honest_and_nonterminal(self):
        self._start()
        self.client.read_error = AirflowApiUnavailableError("offline")
        status = self.orchestrator.get_status(self.project_id)
        self.assertEqual(status.status, "queued")
        self.assertEqual(status.airflow_state, "unavailable")
        self.assertIn("temporarily unavailable", status.error_message or "")

    def test_cancel_persists_first_and_fails_active_remote_work(self):
        self._start()
        self.client.calls.clear()
        status = self.orchestrator.cancel_project(self.project_id)
        self.assertEqual(status.status, "cancelled")
        self.assertEqual([name for name, _ in self.client.calls], ["list_tasks", "fail_task", "fail_run"])
        self.assertTrue(self._latest_job().cancel_requested)

    def test_cancel_remains_terminal_when_airflow_is_unavailable(self):
        self._start()
        self.client.cancel_error = AirflowApiUnavailableError("offline")
        status = self.orchestrator.cancel_project(self.project_id)
        self.assertEqual(status.status, "cancelled")
        self.assertEqual(self._latest_job().status, "cancelled")

    def test_completed_run_ignores_late_cancel(self):
        self._start()
        self.client.run = {"state": "success"}
        self.client.tasks = [{"task_id": "mark_ready", "state": "success", "try_number": 1, "max_tries": 1}]
        self.orchestrator.get_status(self.project_id)
        self.client.calls.clear()
        status = self.orchestrator.cancel_project(self.project_id)
        self.assertEqual(status.status, "ready")
        self.assertEqual(self.client.calls, [])

    def test_startup_reconciliation_checks_active_jobs_without_restarting(self):
        self._start()
        trigger_count = len([call for call in self.client.calls if call[0] == "trigger"])
        self.assertEqual(self.orchestrator.reconcile_active_jobs(), 1)
        self.assertEqual(len([call for call in self.client.calls if call[0] == "trigger"]), trigger_count)

    def test_unknown_remote_task_id_is_not_exposed(self):
        self._start()
        self.client.tasks = [{"task_id": "../../secret", "state": "running", "try_number": 1, "max_tries": 0}]
        status = self.orchestrator.get_status(self.project_id)
        self.assertIsNone(status.airflow_task_id)
        self.assertNotIn("secret", str(status.to_dict()))

    def test_airflow_ui_url_encodes_run_identifier(self):
        self.assertIn("run%20id%2Funsafe", self.orchestrator._ui_url("run id/unsafe") or "")

    def test_safe_log_tail_does_not_return_remote_payloads(self):
        self._start()
        payload = self.orchestrator.read_project_log_tail(self.project_id, tail=2)
        self.assertEqual(len(payload["lines"]), 2)
        self.assertNotIn("offline-secret", str(payload))
        self.assertNotIn("source_url", str(payload))

    def test_explicit_retry_creates_new_job_and_run(self):
        first = self._start()
        with session_scope() as session:
            jobs = JobRepository(session)
            projects = ProjectRepository(session)
            job = jobs.get(first.job_id)
            project = projects.get(self.project_id)
            jobs.update_state(job, status="failed", airflow_state="failed")
            projects.update_flow_state(project, status="failed", current_stage="failed")
        second = self._start()
        self.assertNotEqual(first.job_id, second.job_id)
        self.assertNotEqual(first.airflow_dag_run_id, second.airflow_dag_run_id)

    def test_sqlite_runtime_pragmas_and_airflow_indexes_are_enabled(self):
        engine = get_engine()
        with engine.connect() as connection:
            self.assertEqual(connection.exec_driver_sql("PRAGMA foreign_keys").scalar(), 1)
            self.assertEqual(connection.exec_driver_sql("PRAGMA busy_timeout").scalar(), 30000)
            self.assertEqual(str(connection.exec_driver_sql("PRAGMA journal_mode").scalar()).lower(), "wal")
        indexes = {item["name"]: item for item in inspect(engine).get_indexes("jobs")}
        self.assertIn("ix_jobs_orchestrator_type", indexes)
        self.assertTrue(indexes["ix_jobs_airflow_dag_run_id"]["unique"])

    def test_no_review_stage_skips_without_constructing_provider(self):
        started = self._start()
        config = _valid_payload(project_id=self.project_id, job_id=started.job_id, auto_review=False)
        with patch("apps.pipeline.stages.review_candidates.ReviewAgentService") as provider:
            result = execute_airflow_stage(
                config,
                "review_boundaries",
                container_project_root=self.root,
            )
        self.assertTrue(result["skipped"])
        provider.assert_not_called()

    def test_review_stage_requests_gemini_without_fallback(self):
        started = self._start()
        stage = Mock()
        stage.stage = "reviewing_with_ai"
        stage.run.return_value = PipelineStageResult(
            stage="reviewing_with_ai",
            success=True,
            message="mocked Gemini review",
            metadata={"provider": "gemini"},
        )
        with patch("orchestration.airflow.pipeline_tasks.DEFAULT_STAGE_REGISTRY.create", return_value=stage) as create:
            result = execute_airflow_stage(
                _valid_payload(project_id=self.project_id, job_id=started.job_id),
                "review_boundaries",
                container_project_root=self.root,
            )
        create.assert_called_once_with("review_boundaries", review_mode="gemini")
        self.assertEqual(result["stage"], "reviewing_with_ai")

    def test_stage_failure_before_last_attempt_is_retryable(self):
        started = self._start()
        stage = Mock()
        stage.stage = "transcribing"
        stage.run.side_effect = RuntimeError("mock transient failure")
        with patch("orchestration.airflow.pipeline_tasks.DEFAULT_STAGE_REGISTRY.create", return_value=stage):
            with self.assertRaisesRegex(RuntimeError, "transient"):
                execute_airflow_stage(
                    _valid_payload(project_id=self.project_id, job_id=started.job_id),
                    "transcribe",
                    try_number=1,
                    max_attempts=2,
                    container_project_root=self.root,
                )
        job = self._latest_job()
        self.assertEqual((job.status, job.airflow_state), ("running", "up_for_retry"))

    def test_stage_failure_on_last_attempt_is_terminal(self):
        started = self._start()
        stage = Mock()
        stage.stage = "transcribing"
        stage.run.side_effect = RuntimeError("mock final failure")
        with patch("orchestration.airflow.pipeline_tasks.DEFAULT_STAGE_REGISTRY.create", return_value=stage):
            with self.assertRaisesRegex(RuntimeError, "final"):
                execute_airflow_stage(
                    _valid_payload(project_id=self.project_id, job_id=started.job_id),
                    "transcribe",
                    try_number=2,
                    max_attempts=2,
                    container_project_root=self.root,
                )
        self.assertEqual(self._latest_job().status, "failed")
        self.assertEqual(project_service.get_project_status(self.project_id)["status"], "failed")

    def test_cancelled_job_prevents_stage_execution_and_readiness(self):
        started = self._start()
        with session_scope() as session:
            job = JobRepository(session).get(started.job_id)
            JobRepository(session).update_state(job, cancel_requested=True)
        stage = Mock()
        stage.stage = "ready"
        with patch("orchestration.airflow.pipeline_tasks.DEFAULT_STAGE_REGISTRY.create", return_value=stage):
            with self.assertRaises(Exception):
                execute_airflow_stage(
                    _valid_payload(project_id=self.project_id, job_id=started.job_id),
                    "mark_ready",
                    container_project_root=self.root,
                )
        stage.run.assert_not_called()
        self.assertEqual(self._latest_job().status, "cancelled")
        self.assertNotEqual(project_service.get_project_status(self.project_id)["status"], "ready")


if __name__ == "__main__":
    unittest.main()
