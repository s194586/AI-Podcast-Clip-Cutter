from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


class AirflowApiError(RuntimeError):
    pass


class AirflowApiUnavailableError(AirflowApiError):
    pass


class AirflowApiAuthenticationError(AirflowApiError):
    pass


@dataclass(frozen=True, repr=False)
class AirflowSettings:
    base_url: str
    ui_base_url: str | None
    dag_id: str
    username: str
    password: str
    request_timeout_seconds: float
    container_project_root: str

    @classmethod
    def from_environment(cls, environ: dict[str, str] | None = None) -> "AirflowSettings":
        import os

        values = os.environ if environ is None else environ
        base_url = _safe_base_url(
            values.get("AIRFLOW_API_BASE_URL"),
            "AIRFLOW_API_BASE_URL",
            required=True,
        )
        ui_base_url = _safe_base_url(
            values.get("AIRFLOW_UI_BASE_URL"),
            "AIRFLOW_UI_BASE_URL",
            required=False,
        )
        dag_id = str(values.get("AIRFLOW_DAG_ID") or "podcast_clip_pipeline").strip()
        if not dag_id or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for character in dag_id):
            raise ValueError("AIRFLOW_DAG_ID contains unsupported characters.")
        username = str(values.get("AIRFLOW_API_USERNAME") or "").strip()
        password = str(values.get("AIRFLOW_API_PASSWORD") or "")
        if not username or not password:
            raise ValueError(
                "AIRFLOW_API_USERNAME and AIRFLOW_API_PASSWORD are required in Airflow mode."
            )
        try:
            timeout = float(values.get("AIRFLOW_API_TIMEOUT_SECONDS") or "10")
        except ValueError as exc:
            raise ValueError("AIRFLOW_API_TIMEOUT_SECONDS must be a number.") from exc
        if timeout <= 0 or timeout > 120:
            raise ValueError("AIRFLOW_API_TIMEOUT_SECONDS must be between 0 and 120 seconds.")
        container_root = str(values.get("AIRFLOW_CONTAINER_ROOT") or "/opt/ai-cutter").strip()
        if not container_root.startswith("/") or ".." in container_root.split("/"):
            raise ValueError("AIRFLOW_CONTAINER_ROOT must be a safe absolute container path.")
        return cls(
            base_url=base_url or "",
            ui_base_url=ui_base_url,
            dag_id=dag_id,
            username=username,
            password=password,
            request_timeout_seconds=timeout,
            container_project_root=container_root,
        )


class AirflowApiClient:
    def __init__(self, settings: AirflowSettings) -> None:
        self.settings = settings
        self._token: str | None = None

    def __enter__(self) -> "AirflowApiClient":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def close(self) -> None:
        self._token = None

    def trigger_dag_run(self, *, dag_id: str, dag_run_id: str, conf: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/v2/dags/{quote(dag_id, safe='')}/dagRuns",
            {"dag_run_id": dag_run_id, "logical_date": None, "conf": conf},
        )

    def get_dag_run(self, *, dag_id: str, dag_run_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/v2/dags/{quote(dag_id, safe='')}/dagRuns/{quote(dag_run_id, safe='')}",
        )

    def list_task_instances(self, *, dag_id: str, dag_run_id: str) -> list[dict[str, Any]]:
        payload = self._request(
            "GET",
            f"/api/v2/dags/{quote(dag_id, safe='')}/dagRuns/{quote(dag_run_id, safe='')}/taskInstances",
        )
        values = payload.get("task_instances") or []
        return [dict(value) for value in values if isinstance(value, dict)]

    def set_dag_run_failed(self, *, dag_id: str, dag_run_id: str) -> dict[str, Any]:
        return self._request(
            "PATCH",
            f"/api/v2/dags/{quote(dag_id, safe='')}/dagRuns/{quote(dag_run_id, safe='')}",
            {"state": "failed", "note": "Cancelled by the application."},
        )

    def fail_task_instance(self, *, dag_id: str, dag_run_id: str, task_id: str) -> dict[str, Any]:
        return self._request(
            "PATCH",
            (
                f"/api/v2/dags/{quote(dag_id, safe='')}/dagRuns/{quote(dag_run_id, safe='')}"
                f"/taskInstances/{quote(task_id, safe='')}"
            ),
            {
                "new_state": "failed",
                "note": "Cancelled by the application.",
                "include_upstream": False,
                "include_downstream": True,
                "include_future": False,
                "include_past": False,
            },
        )

    def _access_token(self) -> str:
        if self._token:
            return self._token
        payload = self._request_raw(
            "POST",
            "/auth/token",
            {"username": self.settings.username, "password": self.settings.password},
            authenticated=False,
        )
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise AirflowApiAuthenticationError("Airflow authentication did not return an access token.")
        self._token = token
        return token

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._request_raw(method, path, payload, authenticated=True)

    def _request_raw(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        *,
        authenticated: bool,
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if authenticated:
            headers["Authorization"] = f"Bearer {self._access_token()}"
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            f"{self.settings.base_url.rstrip('/')}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=self.settings.request_timeout_seconds) as response:
                raw = response.read()
        except HTTPError as exc:
            exc.close()
            if exc.code in {401, 403}:
                raise AirflowApiAuthenticationError("Airflow authentication was rejected.") from exc
            raise AirflowApiError(f"Airflow API request failed with HTTP {exc.code}.") from exc
        except (TimeoutError, socket.timeout, URLError, OSError) as exc:
            raise AirflowApiUnavailableError("Airflow API is unavailable or timed out.") from exc
        if not raw:
            return {}
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AirflowApiError("Airflow API returned an invalid JSON response.") from exc
        if not isinstance(parsed, dict):
            raise AirflowApiError("Airflow API returned an unexpected response shape.")
        return parsed


def _safe_base_url(value: str | None, name: str, *, required: bool) -> str | None:
    text = str(value or "").strip().rstrip("/")
    if not text:
        if required:
            raise ValueError(f"{name} is required in Airflow mode.")
        return None
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{name} must be an HTTP or HTTPS URL.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(f"{name} must not contain credentials, query parameters, or fragments.")
    return text
