from __future__ import annotations

import json
import os
import time

from src.antibot_cv.automation.prune_runs_cli import main


def _complete_run(root, name: str, *, mtime: int, size: int = 10) -> None:
    run = root / name
    run.mkdir()
    (run / ".complete").touch()
    (run / "evidence.bin").write_bytes(b"x" * size)
    os.utime(run, (mtime, mtime))


def test_prune_runs_cli_is_dry_run_by_default_and_covers_all_limits(tmp_path, capsys) -> None:
    current = int(time.time())
    _complete_run(tmp_path, "newest", mtime=current, size=10)
    _complete_run(tmp_path, "count", mtime=current - 1, size=10)
    _complete_run(tmp_path, "old", mtime=1, size=10)

    assert main([
        "--runs-dir", str(tmp_path), "--max-age-days", "1", "--max-runs", "1",
        "--max-total-bytes", "15", "--min-keep", "0",
    ]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["would_remove"] == ["count", "old"]
    assert payload["removed"] == []
    assert (tmp_path / "count").is_dir()
    assert (tmp_path / "old").is_dir()


def test_prune_runs_cli_apply_preserves_active_unmarked_and_quest_chains(tmp_path, capsys) -> None:
    _complete_run(tmp_path, "removable", mtime=1)
    stale_active = tmp_path / "stale-active"
    stale_active.mkdir()
    (stale_active / ".complete").touch()
    (stale_active / ".active").touch()
    (tmp_path / "unknown").mkdir()
    quest_chains = tmp_path / "quest_chains"
    quest_chains.mkdir()
    (quest_chains / "character.json").write_text("{}", encoding="utf-8")

    assert main([
        "--runs-dir", str(tmp_path), "--max-age-days", "0", "--max-runs", "0",
        "--max-total-bytes", "0", "--min-keep", "0", "--apply",
    ]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is False
    assert payload["removed"] == ["removable"]
    assert payload["excluded"] == {
        "active": ["stale-active"],
        "quest_chains": ["quest_chains"],
        "unmarked": ["unknown"],
    }
    assert not (tmp_path / "removable").exists()
    assert (stale_active / ".complete").exists()
    assert (tmp_path / "unknown").is_dir()
    assert (quest_chains / "character.json").exists()
