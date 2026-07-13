from __future__ import annotations

import time

import pytest

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
    controller._navigator_client_id = "navigator-client"
    controller._navigator_client_bound_monotonic = time.monotonic() - 5
    controller._find_navigator_client = lambda: {"client_id": "navigator-client"}
    requests = []
    controller.action_executor.execute = lambda request: requests.append(request) or True

    controller._handle_navigator_pending()

    assert controller.state_machine.state is GameState.NAVIGATOR_PENDING
    assert controller._navigator_requires_target_selection is False
    assert len(requests) == 1
    assert requests[0].action_type == "navigator_select_target"
    assert requests[0].metadata["target_kind"] == "monster"
    assert requests[0].metadata["search_delay_ms"] == 8000
    assert requests[0].metadata["retry_delay_ms"] == 500


def test_navigator_selection_waits_for_child_input_to_settle(
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

    assert controller._navigator_client_id == "navigator-client"
    assert controller._navigator_client_bound_monotonic is not None
    assert controller._navigator_requires_target_selection is True
    assert requests == []


@pytest.mark.parametrize("opener_tab_id", [101, None])
def test_navigator_client_accepts_unique_new_tab_when_chrome_omits_opener(
    test_config: AutomationConfig,
    monkeypatch: pytest.MonkeyPatch,
    opener_tab_id: int | None,
) -> None:
    import src.antibot_cv.automation.browser_injector as browser_injector_module

    controller = AutomationController(
        test_config,
        sink_mode="dry-run",
        logger=InMemoryEventLogger(dry_run=True),
        browser_client_id="parent-client",
    )

    class FakeInjector:
        def client_snapshot(self, client_id):
            assert client_id == "parent-client"
            return {"profile_id": "profile-1", "tab_id": 101}

        def client_snapshots(self, *, within_s):
            assert within_s == 5.0
            return [
                {
                    "client_id": "navigator-client",
                    "client_seen": True,
                    "version_ok": True,
                    "profile_id": "profile-1",
                    "opener_tab_id": opener_tab_id,
                    "href": "https://3kingdoms.ru/navigator.php",
                }
            ]

    monkeypatch.setattr(browser_injector_module, "global_browser_injector", lambda: FakeInjector())

    assert controller._find_navigator_client()["client_id"] == "navigator-client"


def test_navigator_client_rejects_ambiguous_unlinked_tabs(
    test_config: AutomationConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.antibot_cv.automation.browser_injector as browser_injector_module

    controller = AutomationController(
        test_config,
        sink_mode="dry-run",
        logger=InMemoryEventLogger(dry_run=True),
        browser_client_id="parent-client",
    )

    class FakeInjector:
        def client_snapshot(self, client_id):
            assert client_id == "parent-client"
            return {"profile_id": "profile-1", "tab_id": 101}

        def client_snapshots(self, *, within_s):
            assert within_s == 5.0
            return [
                {
                    "client_id": client_id,
                    "client_seen": True,
                    "version_ok": True,
                    "profile_id": "profile-1",
                    "opener_tab_id": None,
                    "href": "https://3kingdoms.ru/navigator.php",
                }
                for client_id in ("navigator-a", "navigator-b")
            ]

    monkeypatch.setattr(browser_injector_module, "global_browser_injector", lambda: FakeInjector())

    assert controller._find_navigator_client() is None


def test_navigator_existing_clients_snapshot_covers_unlinked_same_profile_tabs(
    test_config: AutomationConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.antibot_cv.automation.browser_injector as browser_injector_module

    controller = AutomationController(
        test_config,
        sink_mode="dry-run",
        logger=InMemoryEventLogger(dry_run=True),
        browser_client_id="parent-client",
    )

    class FakeInjector:
        def client_snapshot(self, client_id):
            assert client_id == "parent-client"
            return {"profile_id": "profile-1", "tab_id": 101}

        def client_snapshots(self, *, within_s):
            assert within_s == 5.0
            return [
                {
                    "client_id": "existing-navigator",
                    "client_seen": True,
                    "profile_id": "profile-1",
                    "opener_tab_id": None,
                    "href": "https://3kingdoms.ru/navigator.php",
                },
                {
                    "client_id": "other-profile-navigator",
                    "client_seen": True,
                    "profile_id": "profile-2",
                    "opener_tab_id": None,
                    "href": "https://3kingdoms.ru/navigator.php",
                },
            ]

    monkeypatch.setattr(browser_injector_module, "global_browser_injector", lambda: FakeInjector())

    assert controller._navigator_client_ids_for_parent() == {"existing-navigator"}
