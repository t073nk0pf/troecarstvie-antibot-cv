from __future__ import annotations

from pathlib import Path

from src.antibot_cv.automation.controller import AutomationController
from src.antibot_cv.telemetry.event_logger import InMemoryEventLogger


def test_shared_test_config_creates_controller_runs_only_under_tmp_path(test_config, tmp_path) -> None:
    controller = AutomationController(test_config, logger=InMemoryEventLogger())

    assert controller.run_dir.is_relative_to(tmp_path / "runs")
    assert not (Path.cwd() / "runs" / controller.session.session_id).exists()
