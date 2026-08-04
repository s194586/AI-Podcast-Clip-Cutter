from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class PyannoteInfrastructureTests(unittest.TestCase):
    def test_compose_mounts_cache_and_exposes_token_only_to_scheduler(self):
        compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
        services = compose["services"]
        token_services = [
            name
            for name, service in services.items()
            if "HF_TOKEN" in (service.get("environment") or {})
        ]
        self.assertEqual(token_services, ["airflow-scheduler"])
        scheduler = services["airflow-scheduler"]
        self.assertEqual(scheduler["environment"]["HF_HOME"], "/opt/airflow/huggingface")
        self.assertTrue(
            any(
                volume == "huggingface-cache:/opt/airflow/huggingface"
                or (
                    isinstance(volume, dict)
                    and volume.get("source") == "huggingface-cache"
                    and volume.get("target") == "/opt/airflow/huggingface"
                )
                for volume in scheduler["volumes"]
            )
        )
        self.assertIn("huggingface-cache", compose["volumes"])

    def test_dag_import_without_token_does_not_import_pyannote_or_torch(self):
        environment = os.environ.copy()
        environment.pop("HF_TOKEN", None)
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; "
                    "import orchestration.airflow.dags.podcast_pipeline_dag; "
                    "assert 'pyannote.audio' not in sys.modules; "
                    "assert 'torch' not in sys.modules"
                ),
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_airflow_dockerfile_uses_required_install_order_and_no_model_download(self):
        dockerfile = (ROOT / "orchestration" / "airflow" / "Dockerfile").read_text(encoding="utf-8")
        airflow_with_constraints = dockerfile.index('--constraint "${AIRFLOW_CONSTRAINT_URL}"')
        torch_install = dockerfile.index('"torch==2.13.0+cpu"')
        application_install = dockerfile.index('-r /tmp/podcast-cutter-airflow-app-requirements.txt;', torch_install)
        pip_check = dockerfile.index("pip check;", application_install)
        self.assertLess(airflow_with_constraints, torch_install)
        self.assertLess(torch_install, application_install)
        self.assertLess(application_install, pip_check)
        self.assertNotIn("from_pretrained", dockerfile)


if __name__ == "__main__":
    unittest.main()
