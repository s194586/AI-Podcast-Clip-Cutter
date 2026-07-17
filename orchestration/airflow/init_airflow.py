from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


PASSWORD_FILE = Path("/opt/airflow/secrets/simple_auth_manager_passwords.json.generated")
PLACEHOLDER_PREFIXES = ("change-me", "replace-me")


def _required_secret(name: str, *, minimum_length: int = 12) -> str:
    value = str(os.environ.get(name) or "").strip()
    if len(value) < minimum_length or value.lower().startswith(PLACEHOLDER_PREFIXES):
        raise SystemExit(f"{name} must be replaced with a secret of at least {minimum_length} characters.")
    return value


def main() -> None:
    username = str(os.environ.get("AIRFLOW_API_USERNAME") or "").strip()
    if not username or any(character in username for character in ":, \t\r\n"):
        raise SystemExit("AIRFLOW_API_USERNAME must be a non-empty simple username.")

    password = _required_secret("AIRFLOW_API_PASSWORD")
    _required_secret("AIRFLOW_DB_PASSWORD")
    _required_secret("AIRFLOW_JWT_SECRET", minimum_length=32)

    PASSWORD_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = PASSWORD_FILE.with_suffix(".tmp")
    temporary_file.write_text(json.dumps({username: password}), encoding="utf-8")
    temporary_file.chmod(0o600)
    temporary_file.replace(PASSWORD_FILE)
    PASSWORD_FILE.chmod(0o600)

    subprocess.run(["airflow", "db", "migrate"], check=True)


if __name__ == "__main__":
    main()
