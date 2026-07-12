from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from src.antibot_cv.automation.browser_injector import CURRENT_BRIDGE_VERSION, BrowserInjectorServer


def test_injector_bridge_versions_match() -> None:
    page_bridge = Path("browser_injector/page_bridge.js").read_text(encoding="utf-8")
    content = Path("browser_injector/content.js").read_text(encoding="utf-8")
    background = Path("browser_injector/background.js").read_text(encoding="utf-8")
    page_version = re.search(r'BRIDGE_VERSION = "([^"]+)"', page_bridge)
    content_version = re.search(r'bridgeVersion = "([^"]+)"', content)
    background_version = re.search(r'BRIDGE_VERSION = "([^"]+)"', background)

    assert page_version is not None
    assert content_version is not None
    assert background_version is not None
    assert page_version.group(1) == content_version.group(1) == background_version.group(1) == CURRENT_BRIDGE_VERSION


def test_content_command_timeout_allows_inventory_delay() -> None:
    content = Path("browser_injector/content.js").read_text(encoding="utf-8")

    assert "timeoutForPageCommand" in content
    assert "inventoryOpenDelayMs" in content
    assert "Number.isFinite(rawInventoryDelay)" in content
    assert "inventoryDelay + 5000" in content
    assert "Math.min(15000, Math.max(" in content


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


def test_extension_routes_local_fetch_through_background() -> None:
    content = Path("browser_injector/content.js").read_text(encoding="utf-8")
    popup = Path("browser_injector/popup.js").read_text(encoding="utf-8")
    background = Path("browser_injector/background.js").read_text(encoding="utf-8")
    manifest = json.loads(Path("browser_injector/manifest.json").read_text(encoding="utf-8"))

    assert '"background.js"' == json.dumps(manifest["background"]["service_worker"])
    assert "antibot-cv-local-fetch" in content
    assert "antibot-cv-local-fetch" in popup
    assert "antibot-cv-local-fetch" in background
    assert "fetch(`${ENDPOINT}${path}`" in background
    assert "fetch(" not in content


def test_content_uses_background_tab_identity_instead_of_inherited_session_storage() -> None:
    content = Path("browser_injector/content.js").read_text(encoding="utf-8")
    background = Path("browser_injector/background.js").read_text(encoding="utf-8")

    assert "getTabClientId" in content
    assert "createDocumentNonce" in content
    assert "documentNonce" in content
    assert "antibot-cv-tab-identity" in content
    assert "window.sessionStorage" not in content
    assert "sender.tab.id" in background
    assert "tabSessionNonceById" in background
    assert "chrome.storage.session" in background
    assert "antibotCvTabSession:" in background
    assert "-session-${tabSessionNonce}" in background
    assert "chrome.tabs.onRemoved.addListener" in background
    assert "antibotCvProfileId" in background


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


def test_browser_injector_rejects_web_page_origin() -> None:
    server = BrowserInjectorServer(port=0)
    server.start()
    request = Request(
        f"http://{server.host}:{server.port}/api/status",
        headers={"origin": "https://example.com"},
    )
    try:
        with urlopen(request, timeout=2):
            raise AssertionError("web origin unexpectedly accepted")
    except HTTPError as exc:
        assert exc.code == 403
    finally:
        server.stop()


def test_browser_injector_pairs_one_exact_extension_origin() -> None:
    server = BrowserInjectorServer(port=0)
    server.start()
    base_url = f"http://{server.host}:{server.port}"
    trusted = Request(f"{base_url}/next", headers={"origin": "chrome-extension://trusted"})
    with urlopen(trusted, timeout=2) as response:
        assert response.status == 200
    untrusted = Request(f"{base_url}/health", headers={"origin": "chrome-extension://other"})
    try:
        with urlopen(untrusted, timeout=2):
            raise AssertionError("second extension origin unexpectedly accepted")
    except HTTPError as exc:
        assert exc.code == 403
    finally:
        server.stop()


def test_browser_injector_can_pair_extension_origin_on_first_ack_post() -> None:
    server = BrowserInjectorServer(port=0)
    server.start()
    base_url = f"http://{server.host}:{server.port}"
    request = Request(
        f"{base_url}/ack",
        data=json.dumps({"id": "missing", "ok": False, "message": "test"}).encode("utf-8"),
        headers={"content-type": "application/json", "origin": "chrome-extension://trusted"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=2):
            raise AssertionError("unknown acknowledgement unexpectedly accepted")
    except HTTPError as exc:
        assert exc.code == 404
        assert json.loads(exc.read().decode("utf-8"))["error"] == "ack_unknown_command"
    trusted = Request(f"{base_url}/health", headers={"origin": "chrome-extension://trusted"})
    with urlopen(trusted, timeout=2) as response:
        assert response.status == 200
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


def test_browser_injector_rejects_ack_from_wrong_client() -> None:
    server = BrowserInjectorServer(port=0)
    server.start()
    base_url = f"http://{server.host}:{server.port}"
    _read_json(f"{base_url}/next?client=client-a&version={CURRENT_BRIDGE_VERSION}")
    _read_json(f"{base_url}/next?client=client-b&version={CURRENT_BRIDGE_VERSION}")
    result_box: dict[str, object] = {}

    def execute_command() -> None:
        result_box["result"] = server.execute("probe_page", timeout_s=2, client_id="client-b")

    thread = threading.Thread(target=execute_command)
    thread.start()
    command = None
    for _ in range(30):
        payload = _read_json(f"{base_url}/next?client=client-b&version={CURRENT_BRIDGE_VERSION}")
        command = payload.get("command")
        if command:
            break
        time.sleep(0.01)
    assert command is not None
    wrong_ack = Request(
        f"{base_url}/ack",
        data=json.dumps({"id": command["id"], "ok": True, "message": "wrong", "client_id": "client-a"}).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(wrong_ack, timeout=2):
            raise AssertionError("wrong client acknowledgement unexpectedly accepted")
    except HTTPError as exc:
        assert exc.code == 409
        assert json.loads(exc.read().decode("utf-8"))["error"] == "ack_client_mismatch"
    assert thread.is_alive()
    _post_json(
        f"{base_url}/ack",
        {"id": command["id"], "ok": True, "message": "correct", "client_id": "client-b"},
    )
    thread.join(timeout=2)
    server.stop()

    result = result_box["result"]
    assert result.ok is True
    assert result.message == "correct"
    assert result.client_id == "client-b"


def test_browser_injector_rebinds_stale_document_client_to_same_profile_tab() -> None:
    server = BrowserInjectorServer(port=0)
    server.start()
    base_url = f"http://{server.host}:{server.port}"
    _read_json(
        f"{base_url}/next?client=old-client&version={CURRENT_BRIDGE_VERSION}&profile=profile-a&tab=42"
    )
    with server._lock:  # noqa: SLF001 - focused client-lineage regression test.
        server._clients["old-client"]["last_seen"] -= 10  # noqa: SLF001
    _read_json(
        f"{base_url}/next?client=new-client&version={CURRENT_BRIDGE_VERSION}&profile=profile-a&tab=42"
    )
    result_box: dict[str, object] = {}

    def execute_command() -> None:
        result_box["result"] = server.execute("probe_page", timeout_s=2, client_id="old-client")

    thread = threading.Thread(target=execute_command)
    thread.start()
    command = None
    for _ in range(30):
        payload = _read_json(
            f"{base_url}/next?client=new-client&version={CURRENT_BRIDGE_VERSION}&profile=profile-a&tab=42"
        )
        command = payload.get("command")
        if command:
            break
        time.sleep(0.01)
    assert command is not None
    _post_json(
        f"{base_url}/ack",
        {"id": command["id"], "ok": True, "message": "rebound", "client_id": "new-client"},
    )
    thread.join(timeout=2)
    server.stop()

    result = result_box["result"]
    assert result.ok is True
    assert result.message == "rebound"
    assert result.client_id == "new-client"


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
    assert result.message == "injector_delivery_timeout"


def test_browser_injector_distinguishes_missing_ack_from_missing_delivery() -> None:
    server = BrowserInjectorServer(port=0)
    server.start()
    base_url = f"http://{server.host}:{server.port}"
    _read_json(f"{base_url}/next?client=client-a&version={CURRENT_BRIDGE_VERSION}")
    results = []

    def execute() -> None:
        results.append(server.execute("probe_page", timeout_s=0.15, client_id="client-a"))

    thread = threading.Thread(target=execute)
    thread.start()
    deadline = time.time() + 0.1
    while time.time() < deadline:
        payload = _read_json(f"{base_url}/next?client=client-a&version={CURRENT_BRIDGE_VERSION}")
        if payload.get("command"):
            break
        time.sleep(0.005)
    thread.join(timeout=1)
    server.stop()

    assert len(results) == 1
    assert results[0].ok is False
    assert results[0].message == "injector_ack_timeout"
