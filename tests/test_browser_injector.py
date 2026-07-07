from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen

from src.antibot_cv.automation.browser_injector import CURRENT_BRIDGE_VERSION, BrowserInjectorServer


def test_injector_bridge_versions_match() -> None:
    page_bridge = Path("browser_injector/page_bridge.js").read_text(encoding="utf-8")
    content = Path("browser_injector/content.js").read_text(encoding="utf-8")
    page_version = re.search(r'BRIDGE_VERSION = "([^"]+)"', page_bridge)
    content_version = re.search(r'bridgeVersion = "([^"]+)"', content)

    assert page_version is not None
    assert content_version is not None
    assert page_version.group(1) == content_version.group(1) == CURRENT_BRIDGE_VERSION


def test_content_command_timeout_allows_inventory_delay() -> None:
    content = Path("browser_injector/content.js").read_text(encoding="utf-8")

    assert "timeoutForPageCommand" in content
    assert "inventoryOpenDelayMs" in content
    assert "Number.isFinite(rawInventoryDelay)" in content
    assert "inventoryDelay + 5000" in content
    assert "Math.max(2000, Math.min(15000" in content


def test_content_exposes_current_client_to_popup() -> None:
    content = Path("browser_injector/content.js").read_text(encoding="utf-8")
    popup = Path("browser_injector/popup.js").read_text(encoding="utf-8")
    manifest = json.loads(Path("browser_injector/manifest.json").read_text(encoding="utf-8"))

    assert "antibot-cv-current-client" in content
    assert "chrome.runtime.onMessage.addListener" in content
    assert "clientId" in content
    assert "chrome.tabs.query({ active: true, currentWindow: true })" in popup
    assert "chrome.tabs.sendMessage" in popup
    assert "tabs" in manifest["permissions"]


def test_content_keeps_client_id_stable_per_tab_session() -> None:
    content = Path("browser_injector/content.js").read_text(encoding="utf-8")

    assert "getStableClientId" in content
    assert "window.sessionStorage.getItem" in content
    assert "window.sessionStorage.setItem" in content
    assert "antibotCvClientId" in content


def _read_json(url: str) -> dict:
    with urlopen(url, timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    request = Request(url, data=data, headers={"content-type": "application/json"}, method="POST")
    with urlopen(request, timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))


def test_browser_injector_allows_private_network_preflight() -> None:
    server = BrowserInjectorServer(port=0)
    server.start()
    request = Request(
        f"http://{server.host}:{server.port}/next",
        method="OPTIONS",
        headers={"access-control-request-private-network": "true"},
    )
    with urlopen(request, timeout=2) as response:
        assert response.headers["access-control-allow-private-network"] == "true"
    server.stop()


def test_browser_injector_api_handler_routes_get_and_post() -> None:
    server = BrowserInjectorServer(port=0)
    calls: list[tuple[str, str, dict | None]] = []

    def api_handler(method: str, path: str, query: dict[str, list[str]], payload: dict | None) -> tuple[int, dict]:
        calls.append((method, path, payload))
        return 200, {"ok": True, "method": method, "path": path, "query": query, "payload": payload}

    server.set_api_handler(api_handler)
    server.start()
    base_url = f"http://{server.host}:{server.port}"
    get_payload = _read_json(f"{base_url}/api/status?x=1")
    post_payload = _post_json(f"{base_url}/api/start", {"live": True})
    server.stop()

    assert get_payload["ok"] is True
    assert get_payload["path"] == "/api/status"
    assert get_payload["query"] == {"x": ["1"]}
    assert post_payload["payload"] == {"live": True}
    assert calls == [("GET", "/api/status", None), ("POST", "/api/start", {"live": True})]


def test_browser_injector_round_trip_command_ack() -> None:
    server = BrowserInjectorServer(port=0)
    server.start()
    base_url = f"http://{server.host}:{server.port}"

    def client() -> None:
        command = None
        for _ in range(20):
            payload = _read_json(f"{base_url}/next?client=test-client&version={CURRENT_BRIDGE_VERSION}")
            command = payload.get("command")
            if command:
                break
            time.sleep(0.02)
        assert command is not None
        assert command["type"] == "probe_page"
        _post_json(
            f"{base_url}/ack",
            {"id": command["id"], "ok": True, "message": "processMenu_b07", "client_id": "test-client"},
        )

    thread = threading.Thread(target=client)
    thread.start()
    result = server.execute("probe_page", timeout_s=2)
    thread.join(timeout=2)
    server.stop()

    assert result.ok is True
    assert result.message == "processMenu_b07"
    assert result.client_id == "test-client"
    assert server.last_client_id == "test-client"
    assert server.last_client_version == CURRENT_BRIDGE_VERSION


def test_browser_injector_ignores_stale_client_version() -> None:
    server = BrowserInjectorServer(port=0)
    server.start()
    base_url = f"http://{server.host}:{server.port}"

    stale_payload = _read_json(f"{base_url}/next?client=stale&version=old")
    assert stale_payload.get("command") is None

    def client() -> None:
        command = None
        for _ in range(20):
            _read_json(f"{base_url}/next?client=stale&version=old")
            payload = _read_json(f"{base_url}/next?client=fresh&version={CURRENT_BRIDGE_VERSION}")
            command = payload.get("command")
            if command:
                break
            time.sleep(0.02)
        assert command is not None
        assert command["type"] == "visible_hunt_targets"
        _post_json(
            f"{base_url}/ack",
            {"id": command["id"], "ok": True, "message": "{}", "client_id": "fresh"},
        )

    thread = threading.Thread(target=client)
    thread.start()
    result = server.execute("visible_hunt_targets", timeout_s=2)
    thread.join(timeout=2)
    server.stop()

    assert result.ok is True
    assert result.client_id == "fresh"


def test_browser_injector_routes_command_to_target_client() -> None:
    server = BrowserInjectorServer(port=0)
    server.start()
    base_url = f"http://{server.host}:{server.port}"
    _read_json(f"{base_url}/next?client=client-a&version={CURRENT_BRIDGE_VERSION}&title=A")
    _read_json(f"{base_url}/next?client=client-b&version={CURRENT_BRIDGE_VERSION}&title=B")
    result_box: dict[str, object] = {}

    def execute_command() -> None:
        result_box["result"] = server.execute("probe_page", timeout_s=2, client_id="client-b")

    thread = threading.Thread(target=execute_command)
    thread.start()
    command_b = None
    commands_a = []
    for _ in range(30):
        payload_a = _read_json(f"{base_url}/next?client=client-a&version={CURRENT_BRIDGE_VERSION}&title=A")
        if payload_a.get("command"):
            commands_a.append(payload_a["command"])
        payload_b = _read_json(f"{base_url}/next?client=client-b&version={CURRENT_BRIDGE_VERSION}&title=B")
        command_b = payload_b.get("command")
        if command_b:
            break
        time.sleep(0.02)
    assert commands_a == []
    assert command_b is not None
    assert command_b["type"] == "probe_page"
    _post_json(
        f"{base_url}/ack",
        {"id": command_b["id"], "ok": True, "message": "target_ok", "client_id": "client-b"},
    )
    thread.join(timeout=2)
    server.stop()

    result = result_box["result"]
    assert result.ok is True
    assert result.message == "target_ok"
    assert result.client_id == "client-b"


def test_browser_injector_requires_explicit_client_when_multiple_recent_clients() -> None:
    server = BrowserInjectorServer(port=0)
    server.start()
    base_url = f"http://{server.host}:{server.port}"
    _read_json(f"{base_url}/next?client=client-a&version={CURRENT_BRIDGE_VERSION}")
    _read_json(f"{base_url}/next?client=client-b&version={CURRENT_BRIDGE_VERSION}")

    result = server.execute("probe_page", timeout_s=0.05)
    server.stop()

    assert result.ok is False
    assert result.message.startswith("injector_ambiguous_clients:")
    assert "client-a" in result.message
    assert "client-b" in result.message


def test_browser_injector_times_out_without_client() -> None:
    server = BrowserInjectorServer(port=0)
    result = server.execute("open_hunt", timeout_s=0.01)
    server.stop()

    assert result.ok is False
    assert result.message == "injector_timeout"
