from __future__ import annotations

import os

from src.antibot_cv.automation.run_retention import RunRetentionPolicy, prune_run_directories


def test_run_retention_preserves_active_and_newest_recovery_evidence(tmp_path) -> None:
    for index in range(6):
        run = tmp_path / f"run-{index}"
        run.mkdir()
        (run / "evidence.bin").write_bytes(b"x" * 10)
        (run / ".complete").touch()
        os.utime(run, (1000 + index, 1000 + index))
    (tmp_path / "run-0" / ".active").touch()
    os.utime(tmp_path / "run-0", (1000, 1000))

    report = prune_run_directories(
        tmp_path,
        RunRetentionPolicy(max_age_days=0, max_runs=2, max_total_bytes=25, min_keep=2),
        active_session_ids=frozenset({"run-1"}),
        now=10_000,
    )

    assert {"run-5", "run-4", "run-1", "run-0"}.issubset(
        {path.name for path in tmp_path.iterdir()}
    )
    assert set(report.removed) == {"run-2", "run-3"}


def test_run_retention_ignores_files_and_symlink_targets(tmp_path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    (tmp_path / "linked").symlink_to(outside, target_is_directory=True)
    (tmp_path / "plain.log").write_text("keep", encoding="utf-8")

    prune_run_directories(
        tmp_path,
        RunRetentionPolicy(max_age_days=0, max_runs=0, max_total_bytes=0, min_keep=0),
        now=10_000,
    )

    assert (outside / "keep.txt").exists()
    assert (tmp_path / "linked").is_symlink()
    assert (tmp_path / "plain.log").exists()


def test_run_retention_ignores_unmarked_unknown_directories(tmp_path) -> None:
    unknown = tmp_path / "unknown"
    unknown.mkdir()
    (unknown / "important.bin").write_bytes(b"keep")

    report = prune_run_directories(
        tmp_path,
        RunRetentionPolicy(max_age_days=0, max_runs=0, max_total_bytes=0, min_keep=0),
        now=10_000,
    )

    assert report.removed == ()
    assert (unknown / "important.bin").exists()


def test_controller_retention_marker_lifecycle_is_fail_closed(tmp_path) -> None:
    from src.antibot_cv.automation.config import AutomationConfig
    from src.antibot_cv.automation.controller import AutomationController

    config = AutomationConfig.from_dict(
        {
            "runs_dir": str(tmp_path),
            "templates_path": "tests/fixtures/no-templates.json",
            "run_retention": {"enabled": True},
        }
    )
    controller = AutomationController(config)

    assert (controller.run_dir / ".active").is_file()
    assert not (controller.run_dir / ".complete").exists()
    controller.finish()
    assert not (controller.run_dir / ".active").exists()
    assert (controller.run_dir / ".complete").is_file()
