"""Fail-closed maintenance CLI for completed automation run directories."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from src.antibot_cv.automation.run_retention import (
    RunRetentionPolicy,
    apply_run_pruning,
    plan_run_pruning,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prune-runs",
        description="Plan or apply retention to completed run directories.",
    )
    parser.add_argument("--runs-dir", default="runs", help="Runs directory (default: runs)")
    parser.add_argument("--max-age-days", type=_non_negative, default=30)
    parser.add_argument("--max-runs", type=_non_negative, default=100)
    parser.add_argument("--max-total-bytes", type=_non_negative, default=2_000_000_000)
    parser.add_argument("--min-keep", type=_non_negative, default=3)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete the completed runs in the printed plan; otherwise dry-run only.",
    )
    return parser


def _non_negative(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    policy = RunRetentionPolicy(
        max_age_days=args.max_age_days,
        max_runs=args.max_runs,
        max_total_bytes=args.max_total_bytes,
        min_keep=args.min_keep,
    )
    plan = plan_run_pruning(args.runs_dir, policy)
    report = apply_run_pruning(plan) if args.apply else None
    payload = {
        "ok": True,
        "dry_run": not args.apply,
        "runs_dir": str(Path(args.runs_dir)),
        "policy": {
            "max_age_days": policy.max_age_days,
            "max_runs": policy.max_runs,
            "max_total_bytes": policy.max_total_bytes,
            "min_keep": policy.min_keep,
        },
        "would_remove": [path.name for path in plan.removable],
        "retained": [path.name for path in plan.retained],
        "retained_bytes": plan.retained_bytes,
        "excluded": {
            "active": [path.name for path in plan.excluded_active],
            "unmarked": [path.name for path in plan.excluded_unmarked],
            "quest_chains": [path.name for path in plan.excluded_quest_chains],
        },
        "removed": [] if report is None else list(report.removed),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
