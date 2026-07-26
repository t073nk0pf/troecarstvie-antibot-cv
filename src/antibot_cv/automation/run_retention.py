"""Bounded retention for completed run directories."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import time


@dataclass(frozen=True)
class RunRetentionPolicy:
    max_age_days: int = 30
    max_runs: int = 100
    max_total_bytes: int = 2_000_000_000
    min_keep: int = 3


@dataclass(frozen=True)
class RunRetentionReport:
    removed: tuple[str, ...]
    retained: int
    retained_bytes: int


@dataclass(frozen=True)
class RunPruningPlan:
    """A fail-closed deletion plan for direct children of a runs directory."""

    root: Path
    removable: tuple[Path, ...]
    retained: tuple[Path, ...]
    excluded_active: tuple[Path, ...]
    excluded_unmarked: tuple[Path, ...]
    excluded_quest_chains: tuple[Path, ...]
    retained_bytes: int


def plan_run_pruning(
    root: str | Path,
    policy: RunRetentionPolicy,
    *,
    active_session_ids: frozenset[str] = frozenset(),
    now: float | None = None,
) -> RunPruningPlan:
    """Plan pruning of completed runs without modifying the filesystem.

    A directory is removable only when it is a direct, non-symlink child with
    a ``.complete`` file and no active marker.  Unknown directories and quest
    persistence are deliberately outside the retention boundary.
    """

    runs_root = Path(root)
    empty = RunPruningPlan(runs_root, (), (), (), (), (), 0)
    if not runs_root.is_dir():
        return empty

    active: list[Path] = []
    unmarked: list[Path] = []
    quest_chains: list[Path] = []
    completed: list[Path] = []
    for path in runs_root.iterdir():
        if not path.is_dir() or path.is_symlink():
            continue
        if path.name == "quest_chains":
            quest_chains.append(path)
        elif path.name in active_session_ids or (path / ".active").exists():
            active.append(path)
        elif (path / ".complete").is_file():
            completed.append(path)
        else:
            unmarked.append(path)

    current_time = time.time() if now is None else float(now)
    completed.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    sizes = {path: _directory_size(path) for path in completed}
    protected = set(completed[:max(0, policy.min_keep)])
    retained: list[Path] = []
    removable: list[Path] = []
    retained_bytes = 0
    max_age_s = max(0, policy.max_age_days) * 86400
    for path in completed:
        size = sizes[path]
        age_exceeded = current_time - path.stat().st_mtime > max_age_s
        count_exceeded = len(retained) >= max(0, policy.max_runs)
        bytes_exceeded = retained_bytes + size > max(0, policy.max_total_bytes)
        if path not in protected and (age_exceeded or count_exceeded or bytes_exceeded):
            removable.append(path)
            continue
        retained.append(path)
        retained_bytes += size
    return RunPruningPlan(
        runs_root,
        tuple(removable),
        tuple(retained),
        tuple(active),
        tuple(unmarked),
        tuple(quest_chains),
        retained_bytes,
    )


def apply_run_pruning(plan: RunPruningPlan) -> RunRetentionReport:
    """Apply a plan, rechecking each target to keep stale plans fail-closed."""

    removed: list[str] = []
    for path in plan.removable:
        if (
            path.parent == plan.root
            and path.is_dir()
            and not path.is_symlink()
            and (path / ".complete").is_file()
            and not (path / ".active").exists()
            and path.name != "quest_chains"
        ):
            shutil.rmtree(path)
            removed.append(path.name)
    return RunRetentionReport(tuple(removed), len(plan.retained), plan.retained_bytes)


def prune_run_directories(
    root: str | Path,
    policy: RunRetentionPolicy,
    *,
    active_session_ids: frozenset[str] = frozenset(),
    now: float | None = None,
) -> RunRetentionReport:
    """Prune only completed child directories; active markers always win."""

    return apply_run_pruning(
        plan_run_pruning(root, policy, active_session_ids=active_session_ids, now=now)
    )


def _directory_size(path: Path) -> int:
    return sum(
        item.stat().st_size for item in path.rglob("*")
        if item.is_file() and not item.is_symlink()
    )
