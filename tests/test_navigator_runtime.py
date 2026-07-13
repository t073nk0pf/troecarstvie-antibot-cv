from __future__ import annotations

import time

from src.antibot_cv.automation.config import AutomationConfig
from src.antibot_cv.automation.controller import AutomationController
from src.antibot_cv.automation.runtime_helpers import navigator_target_kind
from src.antibot_cv.automation.state_machine import GameState
from src.antibot_cv.telemetry.event_logger import InMemoryEventLogger


def test_navigator_target_kind_distinguishes_monster_from_location() -> None:
    assert navigator_target_kind("Белая Рысь [6]") == "monster"
    assert navigator_target_kind("  Белая   Рысь [ 6 ]  ") == "monster"
    assert navigator_target_kind("Порт безбрежного моря") == "location"


def test_navigator_selection_uses_full_autocomplete_window(
    test_config: AutomationConfig,
) -> None:
    controller = AutomationController(
        test_config,
        sink_mode="dry-run",
        logger=InMemoryEventLogger(dry_run=True),
    )
    controller.state_machine.state = GameState.NAVIGATOR_PENDING
    controller._navigator_opened_monotonic = time.monotonic()
    controller._navigator_target_name = "Белая Рысь [6]"
    controller._navigator_target_kind = navigator_target_kind(controller._navigator_target_name)
    controller._navigator_requires_target_selection = True
    controller._find_navigator_client = lambda: {"client_id": "navigator-client"}
    requests = []
    controller.action_executor.execute = lambda request: requests.append(request) or True

    controller._handle_navigator_pending()

    assert controller.state_machine.state is GameState.NAVIGATOR_PENDING
    assert controller._navigator_requires_target_selection is False
    assert len(requests) == 1
    assert requests[0].action_type == "navigator_select_target"
    assert requests[0].metadata["target_kind"] == "monster"
    assert requests[0].metadata["search_delay_ms"] == 7000
