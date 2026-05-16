#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def benchmarks_dir(project_root: Path) -> Path:
    return project_root / "benchmarks"


def outputs_dir(project_root: Path) -> Path:
    return project_root / "outputs"


def archive_dir(project_root: Path) -> Path:
    return benchmarks_dir(project_root) / "archive"


def backup_dir(project_root: Path) -> Path:
    return project_root / "backups" / "review_reset"


def human_reviews_path(project_root: Path) -> Path:
    return benchmarks_dir(project_root) / "human_reviews.jsonl"


def local_media_dirs(project_root: Path) -> tuple[Path, ...]:
    return (
        project_root / "input",
        project_root / "cuts",
        project_root / "ready_to_post",
        project_root / "cache",
        project_root / "metadata",
        project_root / "transcripts",
        project_root / "tmp",
        project_root / "temp",
    )


def protected_paths(project_root: Path, *, destructive_assets: bool = False) -> tuple[Path, ...]:
    protected = [
        benchmarks_dir(project_root) / "cases.json",
        benchmarks_dir(project_root) / "README.md",
        benchmarks_dir(project_root) / "REVIEW_RESET.md",
        benchmarks_dir(project_root) / "cases.example.json",
        benchmarks_dir(project_root) / "old_cases.json.bak",
        project_root / "README.md",
        project_root / ".venv",
        project_root / ".git",
        project_root / "pyproject.toml",
        project_root / "requirements.txt",
    ]
    if not destructive_assets:
        protected.append(benchmarks_dir(project_root) / "assets")
        protected.extend(local_media_dirs(project_root))
    return tuple(protected)


@dataclass
class CleanupPlan:
    project_root: Path
    delete_paths: list[Path]
    protected_paths: list[Path]
    archive_source: Path | None
    archive_destination: Path | None
    reset_reviews_file: bool
    reset_cases_file: bool
    cases_path: Path | None
    keep_results: bool
    keep_dashboard: bool
    destructive_assets: bool
    purge_archives: bool
    apply: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "project_root": str(self.project_root),
            "apply": self.apply,
            "keep_results": self.keep_results,
            "keep_dashboard": self.keep_dashboard,
            "destructive_assets": self.destructive_assets,
            "purge_archives": self.purge_archives,
            "delete_paths": [str(path) for path in self.delete_paths],
            "protected_paths": [str(path) for path in self.protected_paths],
            "archive_source": str(self.archive_source) if self.archive_source else None,
            "archive_destination": str(self.archive_destination) if self.archive_destination else None,
            "reset_reviews_file": self.reset_reviews_file,
            "reset_cases_file": self.reset_cases_file,
            "cases_path": str(self.cases_path) if self.cases_path else None,
        }


def _add_existing_children(target_dir: Path, candidates: list[Path]) -> None:
    if not target_dir.exists():
        return
    candidates.extend(sorted(target_dir.iterdir()))


def _generated_paths(
    project_root: Path,
    *,
    keep_results: bool,
    keep_dashboard: bool,
    destructive_assets: bool,
    purge_archives: bool,
) -> list[Path]:
    benchmarks = benchmarks_dir(project_root)
    outputs = outputs_dir(project_root)
    candidates: list[Path] = []
    if not keep_dashboard:
        candidates.append(benchmarks / "review_dashboard.html")
    candidates.extend(
        [
            benchmarks / "human_review_template.csv",
            benchmarks / "human_review_archive.csv",
        ]
    )
    candidates.extend(sorted(benchmarks.glob("human_review_recovered_*.csv")))
    if not keep_results:
        candidates.extend(
            [
                benchmarks / "results.json",
                benchmarks / "report.md",
            ]
        )
    runs_dir = benchmarks / "runs"
    if runs_dir.exists():
        candidates.extend(sorted(path for path in runs_dir.iterdir()))
    if purge_archives:
        _add_existing_children(archive_dir(project_root), candidates)
    if destructive_assets:
        _add_existing_children(benchmarks / "assets", candidates)
        for local_dir in local_media_dirs(project_root):
            _add_existing_children(local_dir, candidates)
    gui_runs_dir = outputs / "gui_runs"
    if gui_runs_dir.exists():
        candidates.extend(sorted(path for path in gui_runs_dir.iterdir()))
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    return unique


def build_cleanup_plan(
    *,
    project_root: Path = PROJECT_ROOT,
    keep_results: bool = False,
    keep_dashboard: bool = False,
    archive_reviews: bool = False,
    destructive_assets: bool = False,
    purge_archives: bool = False,
    reset_cases: bool = False,
    apply: bool = False,
    timestamp: str | None = None,
) -> CleanupPlan:
    archive_source: Path | None = None
    archive_destination: Path | None = None
    reset_reviews_file = False
    reviews_path = human_reviews_path(project_root)
    if archive_reviews and reviews_path.exists():
        archive_source = reviews_path
        stamp = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_root = backup_dir(project_root) if purge_archives else archive_dir(project_root)
        archive_destination = archive_root / f"human_reviews_{stamp}.jsonl"
        reset_reviews_file = True
    cases_path = benchmarks_dir(project_root) / "cases.json"
    return CleanupPlan(
        project_root=project_root,
        delete_paths=_generated_paths(
            project_root,
            keep_results=keep_results,
            keep_dashboard=keep_dashboard,
            destructive_assets=destructive_assets,
            purge_archives=purge_archives,
        ),
        protected_paths=list(protected_paths(project_root, destructive_assets=destructive_assets)),
        archive_source=archive_source,
        archive_destination=archive_destination,
        reset_reviews_file=reset_reviews_file,
        reset_cases_file=bool(reset_cases),
        cases_path=cases_path,
        keep_results=keep_results,
        keep_dashboard=keep_dashboard,
        destructive_assets=destructive_assets,
        purge_archives=purge_archives,
        apply=apply,
    )


def _safe_to_delete(path: Path) -> bool:
    resolved = path.resolve()
    for protected in protected_paths(PROJECT_ROOT):
        try:
            if resolved == protected.resolve():
                return False
            resolved.relative_to(protected.resolve())
            return False
        except ValueError:
            continue
    return True


def _safe_to_delete_for_plan(path: Path, plan: CleanupPlan) -> bool:
    resolved = path.resolve()
    for protected in plan.protected_paths:
        try:
            protected_resolved = protected.resolve()
            if resolved == protected_resolved:
                return False
            resolved.relative_to(protected_resolved)
            return False
        except ValueError:
            continue
    return True


def apply_cleanup_plan(plan: CleanupPlan) -> dict[str, object]:
    deleted: list[str] = []
    archived_to: str | None = None
    reset_reviews = False
    cases_reset = False

    if not plan.apply:
        return {
            "mode": "dry-run",
            "deleted": deleted,
            "archived_to": archived_to,
            "reset_reviews": reset_reviews,
            "cases_reset": cases_reset,
        }

    if plan.archive_source and plan.archive_destination and plan.archive_source.exists():
        plan.archive_destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(plan.archive_source, plan.archive_destination)
        archived_to = str(plan.archive_destination)
        if plan.reset_reviews_file:
            plan.archive_source.write_text("", encoding="utf-8")
            reset_reviews = True

    for path in plan.delete_paths:
        if not path.exists():
            continue
        if not _safe_to_delete_for_plan(path, plan):
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        deleted.append(str(path))

    if plan.reset_cases_file and plan.cases_path:
        plan.cases_path.parent.mkdir(parents=True, exist_ok=True)
        plan.cases_path.write_text('{\n  "cases": []\n}\n', encoding="utf-8")
        cases_reset = True

    return {
        "mode": "apply",
        "deleted": deleted,
        "archived_to": archived_to,
        "reset_reviews": reset_reviews,
        "cases_reset": cases_reset,
    }


def print_plan(plan: CleanupPlan) -> None:
    payload = plan.to_dict()
    print("Cleanup plan:")
    print(json.dumps(payload, indent=2))
    if plan.apply:
        print("Mode: apply")
    else:
        print("Mode: dry-run")
    if plan.archive_destination:
        print(f"Reviews archive target: {plan.archive_destination}")
        print("Archived reviews are historical and should not be mixed with the next review pass.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dry-run by default cleanup for generated benchmark/review artifacts."
    )
    parser.add_argument("--apply", action="store_true", help="Actually delete generated artifacts.")
    parser.add_argument("--archive-reviews", action="store_true", help="Archive benchmarks/human_reviews.jsonl before cleanup.")
    parser.add_argument("--keep-results", action="store_true", help="Keep benchmarks/results.json and benchmarks/report.md.")
    parser.add_argument("--keep-dashboard", action="store_true", help="Keep benchmarks/review_dashboard.html.")
    parser.add_argument("--destructive-assets", action="store_true", help="Also delete benchmark assets and local media/input artifact directories.")
    parser.add_argument("--purge-archives", action="store_true", help="Delete benchmarks/archive contents after optionally backing up reviews to backups/review_reset.")
    parser.add_argument("--reset-cases", action="store_true", help="Reset benchmarks/cases.json to an empty cases list after cleanup.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    plan = build_cleanup_plan(
        keep_results=bool(args.keep_results),
        keep_dashboard=bool(args.keep_dashboard),
        archive_reviews=bool(args.archive_reviews),
        destructive_assets=bool(args.destructive_assets),
        purge_archives=bool(args.purge_archives),
        reset_cases=bool(args.reset_cases),
        apply=bool(args.apply),
    )
    print_plan(plan)
    result = apply_cleanup_plan(plan)
    print("Result:")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
