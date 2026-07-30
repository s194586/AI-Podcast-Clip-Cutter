from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import text
from sqlalchemy.schema import CreateIndex, CreateTable

from apps.api.db.database import (
    _ensure_sqlite_clip_evaluation_legacy_fields_nullable,
    configure_database,
    init_database,
    session_scope,
)
from apps.api.db.models import ClipEvaluation
from apps.api.db.repositories import ClipRepository, ProjectRepository
from apps.review_agent.tools import save_evaluation


NULLABLE_LEGACY_FIELDS = (
    "quality_score",
    "context_score",
    "hook_score",
    "payoff_score",
    "boundary_score",
    "privacy_risk",
    "crop_advice",
    "needs_more_context",
)


class ClipEvaluationNullableMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "migration.db"
        self.db_url = f"sqlite:///{self.db_path.as_posix()}"
        self.previous_database_url = os.environ.get("PODCAST_CUTTER_DB_URL")
        os.environ["PODCAST_CUTTER_DB_URL"] = self.db_url
        self.engine = configure_database(self.db_url)
        init_database()

    def tearDown(self) -> None:
        configure_database("sqlite:///:memory:")
        if self.previous_database_url is None:
            os.environ.pop("PODCAST_CUTTER_DB_URL", None)
        else:
            os.environ["PODCAST_CUTTER_DB_URL"] = self.previous_database_url
        self.tempdir.cleanup()

    def _seed_project_and_clip(self) -> tuple[int, int]:
        with session_scope() as session:
            project = ProjectRepository(session).create(title="Migration fixture")
            clip = ClipRepository(session).create_from_dict(
                project.id,
                {
                    "id": "clip_001",
                    "index": 1,
                    "ai_start": 10.0,
                    "ai_end": 40.0,
                    "edited_start": 10.0,
                    "edited_end": 40.0,
                    "min_start": 0.0,
                    "max_start": 20.0,
                    "min_end": 30.0,
                    "max_end": 50.0,
                },
            )
            return project.id, clip.id

    def _replace_with_legacy_not_null_schema(self) -> None:
        """Recreate only the test table as the pre-2G0 schema."""
        create_table_sql = str(CreateTable(ClipEvaluation.__table__).compile(dialect=self.engine.dialect))
        legacy_lines: list[str] = []
        for line in create_table_sql.splitlines():
            field = next(
                (name for name in NULLABLE_LEGACY_FIELDS if line.lstrip().startswith(f"{name} ")),
                None,
            )
            if field is not None:
                suffix = "," if line.rstrip().endswith(",") else ""
                line = f"{line.rstrip().rstrip(',')} NOT NULL{suffix}"
            legacy_lines.append(line)
        legacy_schema_sql = "\n".join(legacy_lines)

        with self.engine.begin() as connection:
            connection.execute(text('DROP TABLE "clip_evaluations"'))
            connection.exec_driver_sql(legacy_schema_sql)
            for index in ClipEvaluation.__table__.indexes:
                connection.execute(CreateIndex(index))
            connection.exec_driver_sql(
                "CREATE INDEX ix_clip_evaluations_migration_fixture "
                "ON clip_evaluations (external_clip_id, created_at)"
            )

    def _notnull_by_column(self) -> dict[str, bool]:
        with self.engine.connect() as connection:
            return {
                str(row[1]): bool(row[3])
                for row in connection.exec_driver_sql("PRAGMA table_info(clip_evaluations)").all()
            }

    def _foreign_keys(self) -> tuple[tuple[object, ...], ...]:
        with self.engine.connect() as connection:
            return tuple(sorted(
                tuple(row)[1:]
                for row in connection.exec_driver_sql("PRAGMA foreign_key_list(clip_evaluations)").all()
            ))

    def _explicit_indexes(self) -> dict[str, str]:
        with self.engine.connect() as connection:
            return {
                str(row[0]): str(row[1])
                for row in connection.exec_driver_sql(
                    "SELECT name, sql FROM sqlite_master "
                    "WHERE type = 'index' AND tbl_name = 'clip_evaluations' AND sql IS NOT NULL "
                    "ORDER BY name"
                ).all()
            }

    def test_migrates_not_null_legacy_fields_without_losing_rows_foreign_keys_or_indexes(self) -> None:
        project_id, clip_id = self._seed_project_and_clip()
        self._replace_with_legacy_not_null_schema()
        with session_scope() as session:
            session.add(
                ClipEvaluation(
                    id=91,
                    project_id=project_id,
                    clip_id=clip_id,
                    external_clip_id="clip_001",
                    provider="local_stub",
                    model="legacy-model",
                    decision="recommended",
                    recommended_action="render_ready",
                    quality_score=0.91,
                    context_score=0.82,
                    hook_score=0.73,
                    payoff_score=0.64,
                    boundary_score=0.55,
                    privacy_risk="medium",
                    crop_advice="wider_context",
                    needs_more_context=True,
                    reasoning_summary="Historical review.",
                    start_reason="Historical start.",
                    end_reason="Historical end.",
                    reasons_json=["legacy reason"],
                    warnings_json=["legacy warning"],
                    raw_result_json={"legacy": True},
                )
            )

        self.assertTrue(all(self._notnull_by_column()[field] for field in NULLABLE_LEGACY_FIELDS))
        foreign_keys_before = self._foreign_keys()
        indexes_before = self._explicit_indexes()
        with self.engine.connect() as connection:
            historical_row_before = dict(
                connection.execute(
                    text(
                        "SELECT id, project_id, clip_id, quality_score, context_score, hook_score, "
                        "payoff_score, boundary_score, privacy_risk, crop_advice, needs_more_context, "
                        "reasoning_summary, created_at FROM clip_evaluations WHERE id = 91"
                    )
                ).mappings().one()
            )

        init_database()

        self.assertTrue(all(not self._notnull_by_column()[field] for field in NULLABLE_LEGACY_FIELDS))
        self.assertEqual(self._foreign_keys(), foreign_keys_before)
        self.assertEqual(self._explicit_indexes(), indexes_before)
        with self.engine.connect() as connection:
            self.assertEqual(connection.exec_driver_sql("PRAGMA foreign_key_check").all(), [])
            row = connection.execute(
                text(
                    "SELECT id, project_id, clip_id, quality_score, context_score, hook_score, "
                    "payoff_score, boundary_score, privacy_risk, crop_advice, needs_more_context, "
                    "reasoning_summary, created_at FROM clip_evaluations WHERE id = 91"
                )
            ).mappings().one()
        self.assertEqual(dict(row), historical_row_before)

    def test_second_initialization_is_a_schema_no_op_after_migration(self) -> None:
        self._replace_with_legacy_not_null_schema()
        init_database()
        with self.engine.connect() as connection:
            schema_version = int(connection.exec_driver_sql("PRAGMA schema_version").scalar_one())

        init_database()

        with self.engine.connect() as connection:
            self.assertEqual(int(connection.exec_driver_sql("PRAGMA schema_version").scalar_one()), schema_version)
        self.assertFalse(_ensure_sqlite_clip_evaluation_legacy_fields_nullable(self.engine))

    def test_fresh_database_creates_nullable_fields_without_rebuild(self) -> None:
        self.assertTrue(all(not self._notnull_by_column()[field] for field in NULLABLE_LEGACY_FIELDS))
        self.assertFalse(_ensure_sqlite_clip_evaluation_legacy_fields_nullable(self.engine))

    def test_gate_rechecks_the_contract_before_rebuilding_after_stale_preflight(self) -> None:
        with self.engine.connect() as connection:
            current_rows = [
                dict(row)
                for row in connection.exec_driver_sql("PRAGMA table_info(clip_evaluations)").mappings().all()
            ]
            schema_version = int(connection.exec_driver_sql("PRAGMA schema_version").scalar_one())
        stale_rows = [
            {
                **row,
                "notnull": 1 if row["name"] in NULLABLE_LEGACY_FIELDS else row["notnull"],
            }
            for row in current_rows
        ]

        with patch(
            "apps.api.db.database._sqlite_table_info",
            side_effect=[stale_rows, current_rows],
        ) as table_info:
            self.assertFalse(_ensure_sqlite_clip_evaluation_legacy_fields_nullable(self.engine))

        self.assertEqual(table_info.call_count, 2)
        with self.engine.connect() as connection:
            self.assertEqual(int(connection.exec_driver_sql("PRAGMA schema_version").scalar_one()), schema_version)
        self.assertTrue(all(not self._notnull_by_column()[field] for field in NULLABLE_LEGACY_FIELDS))

    def test_migrated_database_accepts_null_legacy_fields(self) -> None:
        project_id, clip_id = self._seed_project_and_clip()
        self._replace_with_legacy_not_null_schema()
        init_database()

        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO clip_evaluations (
                        project_id, clip_id, external_clip_id, provider, decision, recommended_action,
                        quality_score, context_score, hook_score, payoff_score, boundary_score,
                        privacy_risk, crop_advice, needs_more_context,
                        reasoning_summary, start_reason, end_reason,
                        reasons_json, warnings_json, raw_result_json, created_at
                    ) VALUES (
                        :project_id, :clip_id, 'clip_001', 'gemini', 'render_ready', 'render_ready',
                        NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                        '', '', '', '[]', '[]', '{}', '2026-07-30 12:00:00'
                    )
                    """
                ),
                {"project_id": project_id, "clip_id": clip_id},
            )
            row = connection.execute(
                text(
                    "SELECT quality_score, context_score, hook_score, payoff_score, boundary_score, "
                    "privacy_risk, crop_advice, needs_more_context "
                    "FROM clip_evaluations WHERE provider = 'gemini'"
                )
            ).one()
        self.assertEqual(tuple(row), (None, None, None, None, None, None, None, None))

    def test_save_evaluation_persists_null_for_missing_gemini_legacy_fields(self) -> None:
        project_id, _clip_id = self._seed_project_and_clip()

        saved = save_evaluation(
            {
                "project_id": project_id,
                "clip_id": "clip_001",
                "provider": "gemini",
                "model": "gemini-test",
                "decision": "manual_review",
                "recommended_action": "manual_review",
            }
        )

        self.assertIsNone(saved["needs_more_context"])
        self.assertEqual(
            saved["review_provenance"],
            {"review_kind": "manual_review", "numeric_score_provenance": "not_available"},
        )

        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT quality_score, context_score, hook_score, payoff_score, boundary_score, "
                    "privacy_risk, crop_advice, needs_more_context "
                    "FROM clip_evaluations WHERE provider = 'gemini'"
                )
            ).one()
        self.assertEqual(tuple(row), (None, None, None, None, None, None, None, None))


if __name__ == "__main__":
    unittest.main()
