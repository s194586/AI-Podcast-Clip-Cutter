from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import TextIO

from sqlalchemy import select

from apps.api.db.database import init_database, session_scope
from apps.api.db.models import Clip, Project
from apps.api.db.repositories import ClipRepository, ProjectRepository
from apps.api.services.legacy_import_service import (
    clear_sqlite_project_rows,
    find_local_import_source,
    import_selected_local_source,
    stale_demo_warning,
)
from apps.api.services.project_state import DEFAULT_PROJECT_ID, PROJECT_ROOT


def resolve_project_root(value: str | None = None) -> Path:
    return Path(value or os.environ.get("PODCAST_CUTTER_PROJECT_ROOT") or PROJECT_ROOT).resolve()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import the current local pipeline project into SQLite.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Clear SQLite project/clip/artifact/evaluation/job rows before importing current local files.",
    )
    parser.add_argument(
        "--allow-demo",
        action="store_true",
        help="Allow examples/top_windows.example.json to be imported even when no real local outputs exist.",
    )
    parser.add_argument(
        "--project-root",
        default=None,
        help="Project root to inspect. Defaults to PODCAST_CUTTER_PROJECT_ROOT or the repository root.",
    )
    parser.add_argument(
        "--project-id",
        default=DEFAULT_PROJECT_ID,
        help="Legacy project_state.json id to inspect. Defaults to local.",
    )
    return parser


def main(argv: list[str] | None = None, *, stdout: TextIO | None = None) -> int:
    args = build_parser().parse_args(argv)
    output = stdout or sys.stdout
    project_root = resolve_project_root(args.project_root)

    init_database()
    with session_scope() as session:
        project_repo = ProjectRepository(session)
        project_count = project_repo.count()
        source = find_local_import_source(
            project_root=project_root,
            project_id=args.project_id,
            allow_demo=bool(args.allow_demo),
        )

        print(f"Project root: {project_root}", file=output)
        if source is not None:
            print(
                f"Import source selected: {source.source_path} "
                f"({source.source_type}, clips={source.clip_count}, demo={source.is_demo})",
                file=output,
            )
        else:
            print("Import source selected: none", file=output)

        if project_count > 0 and not args.reset:
            print_current_summary(session, output)
            warning = stale_demo_warning(session, project_root=project_root)
            if warning:
                print(warning, file=output)
            print("SQLite already has project data. Run with --reset to replace stale local data.", file=output)
            return 0

        if args.reset and project_count > 0:
            clear_sqlite_project_rows(session)
            session.flush()
            print(f"Cleared SQLite rows for {project_count} existing project(s).", file=output)

        project = import_selected_local_source(
            session,
            project_root=project_root,
            project_id=args.project_id,
            allow_demo=bool(args.allow_demo),
        )
        if project is None:
            print("No importable local project state or candidate windows were found.", file=output)
            return 1

        session.flush()
        print_import_summary(session, project.id, source_path=source.source_path if source else None, output=output)
        return 0


def print_current_summary(session, output: TextIO) -> None:
    project = ProjectRepository(session).get_default()
    if project is None:
        print("SQLite project summary: 0 projects, 0 clips.", file=output)
        return
    project_count = ProjectRepository(session).count()
    clips = ClipRepository(session).list_for_project(project.id)
    print(
        f"SQLite project summary: projects={project_count}, "
        f"default_project_id={project.id}, clips={len(clips)}, status={project.status}.",
        file=output,
    )
    if clips:
        first = clips[0]
        print(
            f"First SQLite clip: {first.external_id} "
            f"ai_start={round(float(first.ai_start), 2)} ai_end={round(float(first.ai_end), 2)} "
            f"edited_start={round(float(first.edited_start), 2)} edited_end={round(float(first.edited_end), 2)}",
            file=output,
        )


def print_import_summary(session, project_id: int, *, source_path: str | None, output: TextIO) -> None:
    projects = list(session.scalars(select(Project).order_by(Project.id.asc())).all())
    clips = list(
        session.scalars(
            select(Clip).where(Clip.project_id == project_id).order_by(Clip.clip_index.asc(), Clip.id.asc())
        ).all()
    )
    print(f"Imported projects: {len(projects)}", file=output)
    print(f"Imported clips: {len(clips)}", file=output)
    print(f"Candidate source used: {source_path or 'unknown'}", file=output)
    if clips:
        first = clips[0]
        print(
            f"First imported clip: {first.external_id} "
            f"ai_start={round(float(first.ai_start), 2)} ai_end={round(float(first.ai_end), 2)} "
            f"edited_start={round(float(first.edited_start), 2)} edited_end={round(float(first.edited_end), 2)}",
            file=output,
        )


if __name__ == "__main__":
    raise SystemExit(main())
