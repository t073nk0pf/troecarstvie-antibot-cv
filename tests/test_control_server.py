from __future__ import annotations

import threading

from src.antibot_cv.automation.browser_injector import BrowserInjectorServer
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
