from __future__ import annotations

import importlib.metadata
import unittest
from enum import Enum
from pathlib import Path
from types import SimpleNamespace

from google.genai import interactions

from apps.review_agent.providers import (
    ReviewProviderCompatibilityError,
    ReviewProviderExtractionError,
    ReviewProviderOutputError,
    _interaction_structured_text,
    _parse_boundary_decision,
)


VALID_DECISION = """{
  "decision": "render_ready",
  "selected_start_option_index": 0,
  "selected_end_option_index": 1,
  "reasoning_summary": "The current boundaries are complete.",
  "start_reason": "The opening is complete.",
  "end_reason": "The ending is complete.",
  "warnings": []
}"""


class GeminiSdkContractTests(unittest.TestCase):
    def test_supported_sdk_is_exactly_pinned_without_legacy_package(self) -> None:
        root = Path(__file__).resolve().parents[1]
        requirements = (root / "requirements.txt").read_text(encoding="utf-8")
        pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
        lockfile = (root / "uv.lock").read_text(encoding="utf-8")

        self.assertEqual(importlib.metadata.version("google-genai"), "2.11.0")
        self.assertIn("google-genai==2.11.0", requirements)
        self.assertIn('"google-genai==2.11.0"', pyproject)
        self.assertIn('name = "google-genai"', lockfile)
        self.assertIn('version = "2.11.0"', lockfile)
        self.assertNotIn("google-generativeai", requirements)
        self.assertNotIn("google-generativeai", pyproject)
        self.assertNotIn('name = "google-generativeai"', lockfile)

    def test_airflow_build_installs_supported_sdk_outside_airflow_constraints(self) -> None:
        root = Path(__file__).resolve().parents[1]
        dockerfile = (root / "orchestration/airflow/Dockerfile").read_text(encoding="utf-8")

        self.assertIn('pip install --no-cache-dir "google-genai==2.11.0"', dockerfile)
        self.assertIn(
            "pip uninstall --yes apache-airflow-providers-google google-cloud-aiplatform",
            dockerfile,
        )
        self.assertIn('grep -Ev "^google-(generativeai|genai)', dockerfile)
        self.assertIn("pip check", dockerfile)

    def test_real_sdk_interaction_uses_output_text_contract(self) -> None:
        interaction = interactions.Interaction.model_validate(
            {
                "status": "completed",
                "steps": [
                    {"type": "user_input", "content": [{"type": "text", "text": "prompt"}]},
                    {
                        "type": "model_output",
                        "content": [{"type": "text", "text": VALID_DECISION}],
                    },
                ],
            }
        )

        self.assertEqual(interaction.output_text, VALID_DECISION)
        self.assertEqual(_interaction_structured_text(interaction), VALID_DECISION.strip())
        self.assertEqual(_parse_boundary_decision(interaction).selected_end_option_index, 1)

    def test_real_sdk_steps_fallback_concatenates_text_blocks(self) -> None:
        midpoint = len(VALID_DECISION) // 2
        interaction = interactions.Interaction.model_validate(
            {
                "status": "completed",
                "steps": [
                    {"type": "user_input", "content": [{"type": "text", "text": "prompt"}]},
                    {
                        "type": "model_output",
                        "content": [
                            {"type": "text", "text": VALID_DECISION[:midpoint]},
                            {"type": "text", "text": VALID_DECISION[midpoint:]},
                        ],
                    },
                ],
            }
        )

        fallback_response = SimpleNamespace(
            status=interaction.status,
            output_text=None,
            steps=interaction.steps,
        )
        self.assertEqual(_interaction_structured_text(fallback_response), VALID_DECISION.strip())

    def test_adapter_handles_enum_like_discriminators_and_ignores_non_text(self) -> None:
        class Kind(Enum):
            MODEL_OUTPUT = "model_output"
            TEXT = "text"
            IMAGE = "image"

        response = SimpleNamespace(
            status="completed",
            output_text=None,
            steps=[
                SimpleNamespace(
                    type=Kind.MODEL_OUTPUT,
                    error=None,
                    content=[
                        SimpleNamespace(type=Kind.IMAGE, data="ignored"),
                        SimpleNamespace(type=Kind.TEXT, text=VALID_DECISION),
                    ],
                )
            ],
        )

        self.assertEqual(_interaction_structured_text(response), VALID_DECISION.strip())

    def test_missing_or_incomplete_model_output_is_non_retryable_extraction_failure(self) -> None:
        responses = (
            interactions.Interaction.model_validate({"status": "completed", "steps": []}),
            interactions.Interaction.model_validate(
                {
                    "status": "incomplete",
                    "output_text": VALID_DECISION,
                    "steps": [],
                }
            ),
        )

        for response in responses:
            with self.subTest(status=response.status):
                with self.assertRaises(ReviewProviderExtractionError):
                    _interaction_structured_text(response)

    def test_legacy_outputs_shape_remains_a_compatibility_failure(self) -> None:
        with self.assertRaises(ReviewProviderCompatibilityError):
            _interaction_structured_text({"outputs": [{"type": "text", "text": VALID_DECISION}]})

    def test_extracted_malformed_json_remains_retryable_output_failure(self) -> None:
        response = interactions.Interaction.model_validate(
            {
                "status": "completed",
                "steps": [
                    {
                        "type": "model_output",
                        "content": [{"type": "text", "text": "not-json"}],
                    }
                ],
            }
        )

        with self.assertRaises(ReviewProviderOutputError):
            _parse_boundary_decision(response)


if __name__ == "__main__":
    unittest.main()
