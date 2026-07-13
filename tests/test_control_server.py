from __future__ import annotations

import json
import threading

from src.antibot_cv.automation.browser_injector import CURRENT_BRIDGE_VERSION, BrowserInjectorServer, InjectorResult
from src.antibot_cv.automation.control_server import AutomationControlApi, ControlRun


class FakeThread:
    def __init__(self, alive: bool) -> None:
        self._alive = alive

    def is_alive(self) -> bool:
        return self._alive


def test_status_with_client_id_is_not_global_running() -> None:
    api = AutomationControlApi(BrowserInjectorServer(port=0))
    api._runs["client-a"] = ControlRun(  # noqa: SLF001 - focused state-regression test.
        client_id="client-a",
        thread=FakeThread(True),  # type: ignore[arg-type]
        stop_event=threading.Event(),
        started_at=1.0,
        last_status={"state": "BATTLE_ACTIVE", "completed_cycles": 1, "requested_cycles": 1000},
    )

    assert api.status()["running"] is True
    assert api.status("client-a")["running"] is True
    assert api.status("client-b")["running"] is False
    assert api.status("client-b")["last_status"] == {}


def test_control_api_live_requires_explicit_true() -> None:
    api = AutomationControlApi(BrowserInjectorServer(port=0))

    default_options = api._options_from_payload({}, browser_client_id="client-a")  # noqa: SLF001
    live_options = api._options_from_payload({"live": True}, browser_client_id="client-a")  # noqa: SLF001

    assert default_options.live is False
    assert live_options.live is True


def test_control_api_forwards_autonomous_quest_director_override() -> None:
    api = AutomationControlApi(BrowserInjectorServer(port=0))

    options = api._options_from_payload(  # noqa: SLF001
        {"autonomousQuestDirector": True, "pinnedQuestId": "246"},
        browser_client_id="client-a",
    )

    assert options.runtime_overrides["autonomousQuestDirector"] is True
    assert options.runtime_overrides["pinnedQuestId"] == "246"


def test_control_api_rejects_string_live_flag() -> None:
    api = AutomationControlApi(BrowserInjectorServer(port=0))

    status, payload = api.start({"live": "false"})

    assert status == 400
    assert payload["error"] == "invalid_live_flag"


def test_control_api_rejects_live_run_without_server_live_capability() -> None:
    api = AutomationControlApi(BrowserInjectorServer(port=0), allow_live=False)

    status, payload = api.start({"live": True})

    assert status == 403
    assert payload["error"] == "live_server_not_authorized"


def test_control_api_state_snapshot_uses_existing_injector_client() -> None:
    calls: list[tuple[str, dict, float, str]] = []

    class FakeInjector:
        def client_snapshot(self, client_id):
            return {"client_id": client_id, "client_seen": True, "version_ok": True}

        def execute(self, command, payload, *, timeout_s, client_id):
            calls.append((command, payload, timeout_s, client_id))
            return InjectorResult(
                True,
                json.dumps({"schemaVersion": 1, "sections": {"player": {"status": "available"}}}),
                client_id,
            )

    api = AutomationControlApi(FakeInjector())  # type: ignore[arg-type]
    status, payload = api.handle(
        "GET",
        "/api/state-snapshot",
        {"clientId": ["client-a"], "include": ["player,location"]},
        None,
    )

    assert status == 200
    assert payload["ok"] is True
    assert payload["message"] == "state_snapshot"
    assert payload["snapshot"]["schemaVersion"] == 1
    assert calls == [
        ("state_snapshot", {"include": ["player", "location"]}, 5.0, "client-a"),
    ]


def test_control_api_treats_new_document_client_as_same_running_tab() -> None:
    injector = BrowserInjectorServer(port=0)
    with injector._lock:  # noqa: SLF001 - focused logical-tab regression test.
        injector._record_client_locked(  # noqa: SLF001
            "old-client",
            CURRENT_BRIDGE_VERSION,
            profile_id="profile-a",
            tab_id=42,
        )
        injector._record_client_locked(  # noqa: SLF001
            "new-client",
            CURRENT_BRIDGE_VERSION,
            profile_id="profile-a",
            tab_id=42,
        )
    api = AutomationControlApi(injector)
    stop_event = threading.Event()
    api._runs["old-client"] = ControlRun(  # noqa: SLF001
        client_id="old-client",
        thread=FakeThread(True),  # type: ignore[arg-type]
        stop_event=stop_event,
        started_at=1.0,
    )

    assert api.status("new-client")["running"] is True
    status, payload = api.start({"clientId": "new-client", "live": False})
    assert status == 409
    assert payload["error"] == "tab_already_running"
    stop_status, _ = api.stop({"clientId": "new-client"})
    assert stop_status == 200
    assert stop_event.is_set()
