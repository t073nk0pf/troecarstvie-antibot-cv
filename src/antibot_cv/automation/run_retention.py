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


def prune_run_directories(
    root: str | Path,
    policy: RunRetentionPolicy,
    *,
    active_session_ids: frozenset[str] = frozenset(),
    now: float | None = None,
) -> RunRetentionReport:
    """Prune only completed child directories; active markers always win."""

    runs_root = Path(root)
    if not runs_root.is_dir():
        return RunRetentionReport((), 0, 0)
    current_time = time.time() if now is None else float(now)
    entries = [
        path for path in runs_root.iterdir()
        if path.is_dir()
        and not path.is_symlink()
        and (path / ".complete").is_file()
        and not (path / ".active").exists()
    ]
    entries.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    sizes = {path: _directory_size(path) for path in entries}
    protected = {
        path for index, path in enumerate(entries)
        if index < max(0, policy.min_keep)
        or path.name in active_session_ids
        or (path / ".active").exists()
    }
    retained_count = 0
    retained_bytes = 0
    removed: list[str] = []
    max_age_s = max(0, policy.max_age_days) * 86400
    for path in entries:
        size = sizes[path]
        age_exceeded = current_time - path.stat().st_mtime > max_age_s
        count_exceeded = retained_count >= max(0, policy.max_runs)
        bytes_exceeded = retained_bytes + size > max(0, policy.max_total_bytes)
        if path not in protected and (age_exceeded or count_exceeded or bytes_exceeded):
            shutil.rmtree(path)
            removed.append(path.name)
            continue
        retained_count += 1
        retained_bytes += size
    return RunRetentionReport(tuple(removed), retained_count, retained_bytes)


def _directory_size(path: Path) -> int:
    return sum(
        item.stat().st_size for item in path.rglob("*")
        if item.is_file() and not item.is_symlink()
    )
