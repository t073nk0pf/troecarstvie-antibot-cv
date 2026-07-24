from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from src.antibot_cv.automation.browser_injector import (
    CURRENT_BRIDGE_VERSION, BrowserInjectorServer, _command_is_mutating,
)


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
    assert "inventoryDelay > 0 ? inventoryDelay + 5000 : 0" in content
    assert "Math.min(25000, Math.max(" in content


def test_content_uses_long_poll_instead_of_350ms_interval() -> None:
    content = Path("browser_injector/content.js").read_text(encoding="utf-8")
    assert 'params.set("wait", "25")' in content
    assert "window.setInterval(poll, 25000)" in content
    assert "window.setInterval(poll, 350)" not in content
    background = Path("browser_injector/background.js").read_text(encoding="utf-8")
    assert "extensionUpdateMonitor.checkNow()" not in background


def test_injector_wait_is_interruptible_with_bounded_latency() -> None:
    server = BrowserInjectorServer(port=0)
    cancelled = threading.Event()
    timer = threading.Timer(0.05, cancelled.set)
    timer.start()
    started = time.monotonic()
    result = server.execute("probe_page", timeout_s=5.0, cancellation_event=cancelled)
    elapsed = time.monotonic() - started
    timer.cancel()
    server.stop()

    assert result.ok is False
    assert result.message == "injector_cancelled"
    assert elapsed < 0.2


def test_content_transport_overwrites_caller_claimed_client_provenance() -> None:
    content = Path("browser_injector/content.js").read_text(encoding="utf-8")

    assert "const transportPayload = {" in content
    assert "...payload," in content
    assert "transport: { clientId }," in content
    assert "payload: transportPayload" in content
    assert content.index("...payload,") < content.index("transport: { clientId },")


def test_content_transport_behavior_rejects_nested_and_primitive_client_spoofs() -> None:
    script = r'''
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("browser_injector/content.js", "utf8");
const listeners = {};
const pageCommands = [];
let intervalCallback = null;
let nextCount = 0;
const commands = [
  {id:"nested", type:"procurement_observation_snapshot", payload:{
    transport:{clientId:"client-tab-a"}, metadata:{clientId:"client-tab-a"}, primitive:"preserved",
  }},
  {id:"primitive", type:"procurement_observation_snapshot", payload:"client-tab-a"},
];
const document = {
  title:"Test", documentElement:{
    dataset:{},
    appendChild(script) { queueMicrotask(() => script.onload()); },
  },
  createElement() { return {setAttribute(){}, remove(){}, onload:null, onerror:null}; },
};
const root = {
  location:{href:"https://3kingdoms.ru/auction.php"}, document,
  addEventListener(type, callback) { listeners[type] = callback; },
  removeEventListener() {},
  postMessage(message) {
    if (!message || !message.command) return;
    pageCommands.push(message.command);
    queueMicrotask(() => listeners.message({source:root, data:{
      source:message.source.replace("content", "injector"), token:message.token, ok:true, message:"{}",
    }}));
  },
  setTimeout, clearTimeout,
  setInterval(callback) { intervalCallback = callback; return 1; },
};
root.top = root; root.window = root;
const chrome = {
  runtime:{
    lastError:null,
    getURL(path) { return `chrome-extension://test/${path}`; },
    onMessage:{addListener(){}},
    sendMessage(message, callback) {
      if (message.type === "antibot-cv-tab-identity") {
        callback({ok:true, clientId:"client-tab-b", profileId:"profile-b", tabId:22});
        return;
      }
      const request = message.request || {};
      if (request.path && request.path.startsWith("/next")) {
        callback({ok:true, data:{command:commands[nextCount++] || null}});
        return;
      }
      if (request.path === "/ack") {
        callback({ok:true, data:{ok:true}});
        if (nextCount < commands.length) setTimeout(() => intervalCallback(), 0);
        return;
      }
      callback({ok:false, error:"unexpected"});
    },
  },
};
vm.runInNewContext(source, {
  window:root, document, chrome, crypto:{getRandomValues(values){ values.fill(7); return values; }},
  Uint32Array, URLSearchParams, Promise, setTimeout, clearTimeout, queueMicrotask,
});
(async () => {
  const deadline = Date.now() + 1000;
  while (pageCommands.length < 2 && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 5));
  assert.strictEqual(pageCommands.length, 2);
  assert.strictEqual(pageCommands[0].payload.transport.clientId, "client-tab-b");
  assert.strictEqual(pageCommands[0].payload.primitive, "preserved");
  assert.strictEqual(pageCommands[0].payload.metadata.clientId, "client-tab-a");
  assert.deepStrictEqual(Object.keys(pageCommands[1].payload), ["transport"]);
  assert.strictEqual(pageCommands[1].payload.transport.clientId, "client-tab-b");
})().then(
  () => process.exit(0),
  (error) => { console.error(error); process.exit(1); },
);
'''
    completed = subprocess.run(
        ["node", "-e", script],
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


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


def test_popup_exposes_damage_boost_chance_control() -> None:
    popup_html = Path("browser_injector/popup.html").read_text(encoding="utf-8")
    popup_js = Path("browser_injector/popup.js").read_text(encoding="utf-8")

    assert 'id="battleDamageBoostChancePercent"' in popup_html
    assert 'type="range"' in popup_html
    assert 'id="battleDamageBoostChanceValue"' in popup_html
    assert '"battleDamageBoostChancePercent"' in popup_js
    assert "renderDamageBoostChance" in popup_js


def test_popup_exposes_autonomous_quest_cycle_button() -> None:
    popup_html = Path("browser_injector/popup.html").read_text(encoding="utf-8")
    popup_js = Path("browser_injector/popup.js").read_text(encoding="utf-8")

    assert 'id="questRunButton"' in popup_html
    assert "Выполнять квесты" in popup_html
    assert "async function startQuestBot()" in popup_js
    assert "Для реального выполнения квестов включи live" in popup_js
    assert "settings.autonomousQuestDirector = true" in popup_js
    assert "settings.autoNavigateQuestTargets = true" in popup_js
    assert "settings.openHuntOnStart = false" in popup_js
    assert 'settings.requiredCharacterName = ""' in popup_js


def test_popup_plain_start_forces_non_quest_farm_mode() -> None:
    popup_js = Path("browser_injector/popup.js").read_text(encoding="utf-8")
    plain_start = popup_js.split("async function startBot()", 1)[1].split(
        "async function startQuestBot()", 1
    )[0]

    assert "settings.autonomousQuestDirector = false" in plain_start
    assert "settings.autoNavigateQuestTargets = false" in plain_start
    assert "settings.openHuntOnStart = true" in plain_start


def test_content_injects_page_bridge_as_utf8_for_legacy_game_document() -> None:
    content = Path("browser_injector/content.js").read_text(encoding="utf-8")

    charset_assignment = 'script.charset = "UTF-8"'
    charset_attribute = 'script.setAttribute("charset", "UTF-8")'
    source_assignment = 'script.src = chrome.runtime.getURL("page_bridge.js")'

    assert charset_assignment in content
    assert charset_attribute in content
    assert content.index(charset_assignment) < content.index(source_assignment)
    assert content.index(charset_attribute) < content.index(source_assignment)


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


def test_mutation_is_not_redelivered_after_reload_before_ack() -> None:
    server = BrowserInjectorServer(port=0)
    server.start()
    base_url = f"http://{server.host}:{server.port}"
    results = []
    payload = {
        "mutationFence": {
            "profile_id": "profile-a", "tab_id": 17,
            "actor_generation": 1, "fencing_token": 1,
        },
    }

    thread = threading.Thread(
        target=lambda: results.append(server.execute("open_area", payload, timeout_s=0.35))
    )
    thread.start()
    command = None
    for _ in range(20):
        first = _read_json(
            f"{base_url}/next?client=stable-tab-session&version={CURRENT_BRIDGE_VERSION}"
            "&profile=profile-a&tab=17"
        )
        command = first.get("command")
        if command:
            break
        time.sleep(0.01)
    assert command is not None

    # A reloaded content document keeps the same tab-session client identity.
    # With no ACK, the physical mutation claim must still be one-shot.
    second = _read_json(
        f"{base_url}/next?client=stable-tab-session&version={CURRENT_BRIDGE_VERSION}"
        "&profile=profile-a&tab=17"
    )
    assert second.get("command") is None
    thread.join(timeout=1)
    server.stop()

    assert len(results) == 1
    assert results[0].ok is False
    assert results[0].message == "injector_ack_timeout"


def test_inventory_default_open_is_not_redelivered_after_reload_before_ack() -> None:
    server = BrowserInjectorServer(port=0)
    server.start()
    base_url = f"http://{server.host}:{server.port}"
    results = []
    payload = {"mutationFence": {
        "profile_id": "profile-a", "tab_id": 17,
        "actor_generation": 1, "fencing_token": 1,
    }}
    thread = threading.Thread(
        target=lambda: results.append(server.execute("inventory_snapshot", payload, timeout_s=0.35))
    )
    thread.start()
    command = None
    for _ in range(20):
        first = _read_json(
            f"{base_url}/next?client=stable-tab-session&version={CURRENT_BRIDGE_VERSION}"
            "&profile=profile-a&tab=17"
        )
        command = first.get("command")
        if command:
            break
        time.sleep(0.01)
    assert command is not None and command["payload"].get("open") is None
    second = _read_json(
        f"{base_url}/next?client=stable-tab-session&version={CURRENT_BRIDGE_VERSION}"
        "&profile=profile-a&tab=17"
    )
    assert second.get("command") is None
    thread.join(timeout=1)
    server.stop()
    assert len(results) == 1 and results[0].message == "injector_ack_timeout"


def test_inventory_quest_category_click_is_not_redelivered_after_reload_before_ack() -> None:
    server = BrowserInjectorServer(port=0)
    server.start()
    base_url = f"http://{server.host}:{server.port}"
    results = []
    payload = {
        "open": False,
        "category": "quest",
        "mutationFence": {
            "profile_id": "profile-a", "tab_id": 17,
            "actor_generation": 1, "fencing_token": 1,
        },
    }
    thread = threading.Thread(
        target=lambda: results.append(server.execute("inventory_snapshot", payload, timeout_s=0.35))
    )
    thread.start()
    command = None
    for _ in range(20):
        first = _read_json(
            f"{base_url}/next?client=stable-tab-session&version={CURRENT_BRIDGE_VERSION}"
            "&profile=profile-a&tab=17"
        )
        command = first.get("command")
        if command:
            break
        time.sleep(0.01)
    assert command is not None and command["payload"]["category"] == "quest"
    second = _read_json(
        f"{base_url}/next?client=stable-tab-session&version={CURRENT_BRIDGE_VERSION}"
        "&profile=profile-a&tab=17"
    )
    assert second.get("command") is None
    thread.join(timeout=1)
    server.stop()
    assert len(results) == 1 and results[0].message == "injector_ack_timeout"


def test_inventory_container_category_is_fail_closed_across_reload_before_ack() -> None:
    server = BrowserInjectorServer(port=0)
    server.start()
    base_url = f"http://{server.host}:{server.port}"
    results = []
    payload = {
        "open": False,
        "category": ["quest"],
        "mutationFence": {
            "profile_id": "profile-a", "tab_id": 17,
            "actor_generation": 1, "fencing_token": 1,
        },
    }
    thread = threading.Thread(
        target=lambda: results.append(server.execute("inventory_snapshot", payload, timeout_s=0.35))
    )
    thread.start()
    command = None
    for _ in range(20):
        first = _read_json(
            f"{base_url}/next?client=stable-tab-session&version={CURRENT_BRIDGE_VERSION}"
            "&profile=profile-a&tab=17"
        )
        command = first.get("command")
        if command:
            break
        time.sleep(0.01)
    assert command is not None and command["payload"]["category"] == ["quest"]
    second = _read_json(
        f"{base_url}/next?client=stable-tab-session&version={CURRENT_BRIDGE_VERSION}"
        "&profile=profile-a&tab=17"
    )
    assert second.get("command") is None
    thread.join(timeout=1)
    server.stop()
    assert len(results) == 1 and results[0].message == "injector_ack_timeout"


def test_execute_snapshots_mutating_payload_and_nested_fence_before_enqueue() -> None:
    server = BrowserInjectorServer(port=0)
    server.start()
    base_url = f"http://{server.host}:{server.port}"
    results = []
    caller_payload = {
        "open": True,
        "category": "quest",
        "mutationFence": {
            "profile_id": "profile-a", "tab_id": 17,
            "actor_generation": 1, "fencing_token": 1,
        },
    }
    thread = threading.Thread(target=lambda: results.append(
        server.execute("inventory_snapshot", caller_payload, timeout_s=0.35)
    ))
    thread.start()
    for _ in range(50):
        with server._lock:
            if server._pending:
                break
        time.sleep(0.005)
    caller_payload["open"] = False
    caller_payload["category"] = None
    caller_payload["mutationFence"]["fencing_token"] = 99

    first = _read_json(
        f"{base_url}/next?client=stable-tab-session&version={CURRENT_BRIDGE_VERSION}"
        "&profile=profile-a&tab=17"
    )["command"]
    assert first["payload"]["open"] is True
    assert first["payload"]["category"] == "quest"
    assert first["payload"]["mutationFence"]["fencing_token"] == 1
    assert _read_json(
        f"{base_url}/next?client=stable-tab-session&version={CURRENT_BRIDGE_VERSION}"
        "&profile=profile-a&tab=17"
    )["command"] is None
    thread.join(timeout=1)
    server.stop()
    assert len(results) == 1 and results[0].message == "injector_ack_timeout"


def test_execute_snapshot_prevents_readonly_payload_alias_becoming_mutating() -> None:
    server = BrowserInjectorServer(port=0)
    server.start()
    base_url = f"http://{server.host}:{server.port}"
    results = []
    caller_payload = {"open": False}
    thread = threading.Thread(target=lambda: results.append(
        server.execute("inventory_snapshot", caller_payload, timeout_s=0.35)
    ))
    thread.start()
    for _ in range(50):
        with server._lock:
            if server._pending:
                break
        time.sleep(0.005)
    caller_payload["open"] = True
    caller_payload["category"] = "quest"

    first = _read_json(
        f"{base_url}/next?client=stable-tab-session&version={CURRENT_BRIDGE_VERSION}"
    )["command"]
    second = _read_json(
        f"{base_url}/next?client=stable-tab-session&version={CURRENT_BRIDGE_VERSION}"
    )["command"]
    assert first["id"] == second["id"]
    assert first["payload"] == {"open": False}
    thread.join(timeout=1)
    server.stop()
    assert len(results) == 1 and results[0].message == "injector_ack_timeout"


def test_two_concurrent_next_claims_deliver_one_mutating_command() -> None:
    server = BrowserInjectorServer(port=0)
    server._pending["command-1"] = {
        "command": {"id": "command-1", "type": "open_area", "payload": {}},
        "required_version": CURRENT_BRIDGE_VERSION,
        "target_client_id": "stable-tab-session",
        "delivered_count": 0,
        "mutating": True,
    }
    barrier = threading.Barrier(3)
    delivered = []

    def poll() -> None:
        barrier.wait()
        with server._lock:
            delivered.append(server._next_command_for_client_locked(
                "stable-tab-session", CURRENT_BRIDGE_VERSION,
            ))

    threads = [threading.Thread(target=poll) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=1)

    assert sum(item is not None for item in delivered) == 1
    assert server._pending["command-1"]["delivered_count"] == 1


def test_inventory_snapshot_mutability_depends_on_open_payload() -> None:
    assert _command_is_mutating("inventory_snapshot", {"open": True}) is True
    assert _command_is_mutating("inventory_snapshot", {"open": False}) is False
    assert _command_is_mutating(
        "inventory_snapshot", {"open": False, "category": "quest"},
    ) is True
    for category in (["quest"], [" quest "], ["QUEST"], {}, 1, False):
        assert _command_is_mutating(
            "inventory_snapshot", {"open": False, "category": category},
        ) is True
    assert _command_is_mutating("inventory_snapshot", {"open": 0}) is False
    assert _command_is_mutating("inventory_snapshot", {"open": ""}) is False
    assert _command_is_mutating("inventory_snapshot", {"open": []}) is True
    assert _command_is_mutating("inventory_snapshot", {"open": {}}) is True
    assert _command_is_mutating("inventory_snapshot", {"open": None}) is True
    assert _command_is_mutating("inventory_snapshot", {}) is True


def test_delivery_revalidates_pending_fence_after_concurrent_newer_token() -> None:
    server = BrowserInjectorServer(port=0)
    server._mutation_owner_fences[("profile-a", 17)] = ("profile-a", 17, 1, 2)
    server._mutation_target_fences[("profile-a", 17)] = ("profile-a", 17, 1, 2)
    server._clients["stable-tab-session"] = {
        "profile_id": "profile-a", "tab_id": 17, "opener_tab_id": None,
        "version": CURRENT_BRIDGE_VERSION, "last_seen": time.monotonic(),
    }

    def pending(command_id: str, token: int):
        return {
            "command": {
                "id": command_id,
                "type": "open_area",
                "payload": {"mutationFence": {
                    "profile_id": "profile-a", "tab_id": 17,
                    "actor_generation": 1, "fencing_token": token,
                }},
            },
            "required_version": CURRENT_BRIDGE_VERSION,
            "target_client_id": "stable-tab-session",
            "delivered_count": 0,
            "mutating": True,
        }

    # Reproduce the enqueue order from the TOCTOU: newer accepted first,
    # previously validated stale command enqueued afterwards.
    server._pending["new"] = pending("new", 2)
    server._pending["old"] = pending("old", 1)
    with server._lock:
        first = server._next_command_for_client_locked(
            "stable-tab-session", CURRENT_BRIDGE_VERSION,
        )
        second = server._next_command_for_client_locked(
            "stable-tab-session", CURRENT_BRIDGE_VERSION,
        )

    assert first is not None and first["id"] == "new"
    assert second is None
    assert server._pending["old"]["delivered_count"] == 0

    inventory = pending("inventory-old", 1)
    inventory["command"]["type"] = "inventory_snapshot"
    inventory["command"]["payload"].pop("open", None)
    server._pending.clear()
    server._pending["inventory-old"] = inventory
    with server._lock:
        assert server._next_command_for_client_locked(
            "stable-tab-session", CURRENT_BRIDGE_VERSION,
        ) is None
    assert server._pending["inventory-old"]["delivered_count"] == 0

    container_category = pending("inventory-container-old", 1)
    container_category["command"]["type"] = "inventory_snapshot"
    container_category["command"]["payload"]["open"] = False
    container_category["command"]["payload"]["category"] = ["quest"]
    container_category["mutating"] = _command_is_mutating(
        "inventory_snapshot", container_category["command"]["payload"],
    )
    server._pending.clear()
    server._pending["inventory-container-old"] = container_category
    with server._lock:
        assert server._next_command_for_client_locked(
            "stable-tab-session", CURRENT_BRIDGE_VERSION,
        ) is None
    assert server._pending["inventory-container-old"]["delivered_count"] == 0


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


def test_browser_client_registry_prunes_stale_entries_and_caps_size() -> None:
    server = BrowserInjectorServer(port=0)
    with server._lock:  # noqa: SLF001 - focused retention-policy test.
        server._record_client_locked("stale", CURRENT_BRIDGE_VERSION)  # noqa: SLF001
        server._clients["stale"]["last_seen"] -= server.CLIENT_TTL_S + 1  # noqa: SLF001
        for index in range(server.MAX_CLIENTS + 5):
            server._record_client_locked(f"client-{index}", CURRENT_BRIDGE_VERSION)  # noqa: SLF001
        assert "stale" not in server._clients  # noqa: SLF001
        assert len(server._clients) == server.MAX_CLIENTS  # noqa: SLF001
        assert "client-0" not in server._clients  # noqa: SLF001


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
def test_actor_fence_allows_distinct_same_name_commands_and_rejects_stale_generation() -> None:
    server = BrowserInjectorServer(port=0)
    current = {
        "mutationFence": {
            "profile_id": "profile-a", "tab_id": 17,
            "actor_generation": 2, "fencing_token": 9,
        },
    }
    assert server._validate_mutation_fence("open_area", current) is None
    # The fence owns the actor; idempotency belongs to each server command ID.
    for command in (
        "open_area", "attack_visible_bot", "attack_visible_bot",
        "npc_quest_action", "npc_quest_action",
        "open_recovery_item", "open_recovery_item",
        "navigator_select_target", "navigator_select_target",
    ):
        assert server._validate_mutation_fence(command, current) is None
    stale = {
        "mutationFence": {
            "profile_id": "profile-a", "tab_id": 17,
            "actor_generation": 2, "fencing_token": 8,
        },
    }
    assert server._validate_mutation_fence("open_hunt", stale) == "mutation_fence_stale"
    # Observation polling may reuse the same intent fence without mutation.
    assert server._validate_mutation_fence("state_snapshot", current) is None


def test_child_mutation_authority_binds_resolved_target_and_opener() -> None:
    server = BrowserInjectorServer(port=0)
    now = time.monotonic()
    server._clients["child"] = {
        "profile_id": "profile-a", "tab_id": 23, "opener_tab_id": 17,
        "version": CURRENT_BRIDGE_VERSION, "last_seen": now,
    }
    payload = {
        "mutationFence": {
            "profile_id": "profile-a", "tab_id": 17,
            "actor_generation": 1, "fencing_token": 7,
        },
        "mutationTarget": {
            "kind": "opener_child", "profile_id": "profile-a",
            "tab_id": 23, "opener_tab_id": 17,
        },
    }
    accepted = server.execute(
        "navigator_select_target", payload, timeout_s=0, client_id="child",
    )
    assert accepted.message == "injector_delivery_timeout"

    wrong_tab = json.loads(json.dumps(payload))
    wrong_tab["mutationTarget"]["tab_id"] = 24
    rejected = server.execute(
        "navigator_select_target", wrong_tab, timeout_s=0, client_id="child",
    )
    assert rejected.message == "mutation_target_identity_mismatch"

    missing = {"mutationFence": payload["mutationFence"]}
    rejected = server.execute(
        "navigator_select_target", missing, timeout_s=0, client_id="child",
    )
    assert rejected.message == "mutation_child_authority_missing"


def test_owner_fence_is_monotonic_across_parent_and_child_targets() -> None:
    server = BrowserInjectorServer(port=0)
    parent = {"profile_id": "profile-a", "tab_id": 17}
    child = {
        "profile_id": "profile-a", "tab_id": 23, "opener_tab_id": 17,
    }

    def payload(generation: int, token: int, *, child_target: bool) -> dict[str, object]:
        result: dict[str, object] = {"mutationFence": {
            "profile_id": "profile-a", "tab_id": 17,
            "actor_generation": generation, "fencing_token": token,
        }}
        if child_target:
            result["mutationTarget"] = {
                "kind": "opener_child", "profile_id": "profile-a",
                "tab_id": 23, "opener_tab_id": 17,
            }
        return result

    assert server._validate_mutation_fence(
        "navigator_go", payload(1, 8, child_target=False), target_snapshot=parent,
    ) is None
    assert server._validate_mutation_fence(
        "navigator_go", payload(1, 7, child_target=True), target_snapshot=child,
    ) == "mutation_fence_stale"
    assert server._validate_mutation_fence(
        "navigator_go", payload(1, 8, child_target=True), target_snapshot=child,
    ) is None

    rollover = BrowserInjectorServer(port=0)
    assert rollover._validate_mutation_fence(
        "navigator_go", payload(2, 1, child_target=True), target_snapshot=child,
    ) is None
    assert rollover._validate_mutation_fence(
        "navigator_go", payload(1, 99, child_target=False), target_snapshot=parent,
    ) == "mutation_fence_stale"
    assert rollover._validate_mutation_fence(
        "navigator_go", payload(2, 1, child_target=False), target_snapshot=parent,
    ) is None


def test_new_owner_fence_on_parent_invalidates_queued_child_delivery() -> None:
    server = BrowserInjectorServer(port=0)
    server.start()
    base_url = f"http://{server.host}:{server.port}"
    with server._lock:
        server._record_client_locked(
            "parent", CURRENT_BRIDGE_VERSION,
            profile_id="profile-a", tab_id=17, opener_tab_id=None,
        )
        server._record_client_locked(
            "child", CURRENT_BRIDGE_VERSION,
            profile_id="profile-a", tab_id=23, opener_tab_id=17,
        )
    child_payload = {
        "mutationFence": {
            "profile_id": "profile-a", "tab_id": 17,
            "actor_generation": 1, "fencing_token": 7,
        },
        "mutationTarget": {
            "kind": "opener_child", "profile_id": "profile-a",
            "tab_id": 23, "opener_tab_id": 17,
        },
    }
    results = []
    thread = threading.Thread(target=lambda: results.append(
        server.execute("navigator_go", child_payload, timeout_s=0.35, client_id="child")
    ))
    thread.start()
    for _ in range(50):
        with server._lock:
            if server._pending:
                break
        time.sleep(0.005)
    parent_payload = {"mutationFence": {
        "profile_id": "profile-a", "tab_id": 17,
        "actor_generation": 1, "fencing_token": 8,
    }}
    assert server._validate_mutation_fence(
        "open_area", parent_payload,
        target_snapshot={"profile_id": "profile-a", "tab_id": 17},
    ) is None
    delivered = _read_json(
        f"{base_url}/next?client=child&version={CURRENT_BRIDGE_VERSION}"
        "&profile=profile-a&tab=23&opener=17"
    )
    assert delivered.get("command") is None
    thread.join(timeout=1)
    server.stop()
    assert len(results) == 1 and results[0].message == "injector_delivery_timeout"


def test_child_authority_rejects_foreign_opener_and_parallel_child() -> None:
    server = BrowserInjectorServer(port=0)
    now = time.monotonic()
    server._clients["child-a"] = {
        "profile_id": "profile-a", "tab_id": 23, "opener_tab_id": 17,
        "version": CURRENT_BRIDGE_VERSION, "last_seen": now,
    }
    server._clients["child-b"] = {
        "profile_id": "profile-a", "tab_id": 24, "opener_tab_id": 18,
        "version": CURRENT_BRIDGE_VERSION, "last_seen": now,
    }
    authority_a = {
        "mutationFence": {
            "profile_id": "profile-a", "tab_id": 17,
            "actor_generation": 1, "fencing_token": 7,
        },
        "mutationTarget": {
            "kind": "opener_child", "profile_id": "profile-a",
            "tab_id": 23, "opener_tab_id": 17,
        },
    }
    assert server.execute(
        "navigator_go", authority_a, timeout_s=0, client_id="child-a",
    ).message == "injector_delivery_timeout"
    assert server.execute(
        "navigator_go", authority_a, timeout_s=0, client_id="child-b",
    ).message == "mutation_target_identity_mismatch"


def test_child_authority_is_checked_after_client_alias_resolution() -> None:
    server = BrowserInjectorServer(port=0)
    now = time.monotonic()
    server._clients["old-child"] = {
        "profile_id": "profile-a", "tab_id": 23, "opener_tab_id": 17,
        "version": CURRENT_BRIDGE_VERSION, "last_seen": now - 10,
    }
    server._clients["new-child"] = {
        "profile_id": "profile-a", "tab_id": 23, "opener_tab_id": 17,
        "version": CURRENT_BRIDGE_VERSION, "last_seen": now,
    }
    payload = {
        "mutationFence": {
            "profile_id": "profile-a", "tab_id": 17,
            "actor_generation": 1, "fencing_token": 7,
        },
        "mutationTarget": {
            "kind": "opener_child", "profile_id": "profile-a",
            "tab_id": 23, "opener_tab_id": 17,
        },
    }
    result = server.execute(
        "navigator_go", payload, timeout_s=0, client_id="old-child",
    )
    assert result.client_id == "new-child"
    assert result.message == "injector_delivery_timeout"


def test_child_identity_is_revalidated_at_delivery_after_enqueue() -> None:
    server = BrowserInjectorServer(port=0)
    server.start()
    base_url = f"http://{server.host}:{server.port}"
    with server._lock:
        server._record_client_locked(
            "child", CURRENT_BRIDGE_VERSION,
            profile_id="profile-a", tab_id=23, opener_tab_id=17,
        )
    payload = {
        "mutationFence": {
            "profile_id": "profile-a", "tab_id": 17,
            "actor_generation": 1, "fencing_token": 7,
        },
        "mutationTarget": {
            "kind": "opener_child", "profile_id": "profile-a",
            "tab_id": 23, "opener_tab_id": 17,
        },
    }
    results = []
    thread = threading.Thread(target=lambda: results.append(
        server.execute("navigator_go", payload, timeout_s=0.35, client_id="child")
    ))
    thread.start()
    for _ in range(50):
        with server._lock:
            if server._pending:
                break
        time.sleep(0.005)
    delivered = _read_json(
        f"{base_url}/next?client=child&version={CURRENT_BRIDGE_VERSION}"
        "&profile=profile-a&tab=23&opener=99"
    )
    assert delivered.get("command") is None
    thread.join(timeout=1)
    server.stop()
    assert len(results) == 1 and results[0].message == "injector_delivery_timeout"


def test_direct_fence_waiting_for_client_rejects_foreign_first_poller() -> None:
    server = BrowserInjectorServer(port=0)
    server.start()
    base_url = f"http://{server.host}:{server.port}"
    payload = {"mutationFence": {
        "profile_id": "profile-a", "tab_id": 17,
        "actor_generation": 1, "fencing_token": 7,
    }}
    results = []
    thread = threading.Thread(target=lambda: results.append(
        server.execute("open_area", payload, timeout_s=0.35)
    ))
    thread.start()
    for _ in range(50):
        with server._lock:
            if server._pending:
                break
        time.sleep(0.005)
    delivered = _read_json(
        f"{base_url}/next?client=foreign&version={CURRENT_BRIDGE_VERSION}"
        "&profile=profile-b&tab=99"
    )
    assert delivered.get("command") is None
    thread.join(timeout=1)
    server.stop()
    assert len(results) == 1 and results[0].message == "injector_delivery_timeout"
