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


def human_reviews_path(project_root: Path) -> Path:
    return benchmarks_dir(project_root) / "human_reviews.jsonl"


def protected_paths(project_root: Path) -> tuple[Path, ...]:
    return (
        benchmarks_dir(project_root) / "assets",
        benchmarks_dir(project_root) / "cases.json",
        benchmarks_dir(project_root) / "README.md",
        project_root / "README.md",
        project_root / ".venv",
        project_root / ".git",
        project_root / "pyproject.toml",
    )


@dataclass
class CleanupPlan:
    project_root: Path
    delete_paths: list[Path]
    protected_paths: list[Path]
    archive_source: Path | None
    archive_destination: Path | None
    reset_reviews_file: bool
    keep_results: bool
    keep_dashboard: bool
    apply: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "project_root": str(self.project_root),
            "apply": self.apply,
            "keep_results": self.keep_results,
            "keep_dashboard": self.keep_dashboard,
            "delete_paths": [str(path) for path in self.delete_paths],
            "protected_paths": [str(path) for path in self.protected_paths],
            "archive_source": str(self.archive_source) if self.archive_source else None,
            "archive_destination": str(self.archive_destination) if self.archive_destination else None,
            "reset_reviews_file": self.reset_reviews_file,
        }


def _generated_paths(project_root: Path, *, keep_results: bool, keep_dashboard: bool) -> list[Path]:
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
        archive_destination = archive_dir(project_root) / f"human_reviews_{stamp}.jsonl"
        reset_reviews_file = True
    return CleanupPlan(
        project_root=project_root,
        delete_paths=_generated_paths(project_root, keep_results=keep_results, keep_dashboard=keep_dashboard),
        protected_paths=list(protected_paths(project_root)),
        archive_source=archive_source,
        archive_destination=archive_destination,
        reset_reviews_file=reset_reviews_file,
        keep_results=keep_results,
        keep_dashboard=keep_dashboard,
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

    if not plan.apply:
        return {
            "mode": "dry-run",
            "deleted": deleted,
            "archived_to": archived_to,
            "reset_reviews": reset_reviews,
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

    return {
        "mode": "apply",
        "deleted": deleted,
        "archived_to": archived_to,
        "reset_reviews": reset_reviews,
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
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    plan = build_cleanup_plan(
        keep_results=bool(args.keep_results),
        keep_dashboard=bool(args.keep_dashboard),
        archive_reviews=bool(args.archive_reviews),
        apply=bool(args.apply),
    )
    print_plan(plan)
    result = apply_cleanup_plan(plan)
    print("Result:")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
