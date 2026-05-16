#!/usr/bin/env python3

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def build_benchmark_command(*, review_batch: str = "", extra_args: list[str] | None = None) -> list[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "benchmark.py"),
        "--ai-mode",
        "local_only",
        "--subtitle-checker-mode",
        "local_only",
    ]
    if str(review_batch or "").strip():
        command.extend(["--review-batch", str(review_batch).strip()])
    if extra_args:
        command.extend(extra_args)
    return command


def build_dashboard_command() -> list[str]:
    return [
        sys.executable,
        str(PROJECT_ROOT / "review_dashboard.py"),
        "export-html",
        "--results",
        str(PROJECT_ROOT / "benchmarks" / "results.json"),
        "--output",
        str(PROJECT_ROOT / "benchmarks" / "review_dashboard.html"),
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a clean local-only benchmark and export the review dashboard.")
    parser.add_argument("--review-batch", default="")
    parser.add_argument("--top", type=int, default=0, help="Optional override for benchmark top clips per case.")
    parser.add_argument("--case", action="append", default=[], help="Optional case filter passed to benchmark.py")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    extra_args: list[str] = []
    if args.top > 0:
        extra_args.extend(["--top", str(args.top)])
    for case_id in args.case:
        extra_args.extend(["--case", case_id])

    benchmark_cmd = build_benchmark_command(review_batch=args.review_batch, extra_args=extra_args)
    dashboard_cmd = build_dashboard_command()

    subprocess.run(benchmark_cmd, cwd=str(PROJECT_ROOT), check=True)
    subprocess.run(dashboard_cmd, cwd=str(PROJECT_ROOT), check=True)
    print("Open dashboard with:")
    print(r"start benchmarks\review_dashboard.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
