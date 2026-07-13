from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_extension_update_runtime_is_wired_without_new_permissions() -> None:
    manifest = json.loads(Path("browser_injector/manifest.json").read_text(encoding="utf-8"))
    background = Path("browser_injector/background.js").read_text(encoding="utf-8")
    popup = Path("browser_injector/popup.js").read_text(encoding="utf-8")
    popup_html = Path("browser_injector/popup.html").read_text(encoding="utf-8")

    assert set(manifest["permissions"]) == {"storage", "tabs"}
    assert 'importScripts("update_runtime.js")' in background
    assert "registerExtensionUpdateLifecycle" in background
    assert "startExtensionUpdateMonitor" in background
    assert "const extensionUpdateMonitor" in background
    assert "void extensionUpdateMonitor.checkNow()" in background
    assert 'const status = await api("/status")' in popup
    assert "currentClient?.version" in popup
    assert "unknown-pre-updater" in popup
    assert "requestExtensionUpdate" in popup
    assert 'id="updateExtensionButton"' in popup_html
    assert popup_html.index('src="update_runtime.js"') < popup_html.index('src="popup.js"')


def test_extension_update_runtime_guards_and_consumes_marker_once() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync("browser_injector/update_runtime.js", "utf8");
const context = { URL, console };
vm.runInNewContext(source, context);
const update = context.AntibotCvUpdateRuntime;
const key = update.UPDATE_MARKER_KEY;

function makeChrome({ url = "https://3kingdoms.ru/main.php", marker = null } = {}) {
  const data = marker ? { [key]: marker } : {};
  const events = [];
  const state = { data, events, runtimeReloads: 0, tabReloads: 0, listener: null };
  const chromeApi = {
    runtime: {
      lastError: null,
      reload() { events.push("runtime.reload"); state.runtimeReloads += 1; },
      onInstalled: {
        addListener(listener) { events.push("onInstalled.addListener"); state.listener = listener; },
      },
    },
    storage: {
      local: {
        get(storageKey, callback) {
          events.push("storage.get");
          callback({ [storageKey]: data[storageKey] });
        },
        set(values, callback) {
          events.push("storage.set");
          Object.assign(data, values);
          callback();
        },
        remove(storageKey, callback) {
          events.push("storage.remove");
          delete data[storageKey];
          callback();
        },
      },
    },
    tabs: {
      query(query, callback) {
        events.push("tabs.query");
        callback([{ id: 17, url }]);
      },
      get(tabId, callback) {
        events.push("tabs.get");
        callback({ id: tabId, url });
      },
      reload(tabId, options, callback) {
        events.push(`tabs.reload:${tabId}:${options.bypassCache}`);
        state.tabReloads += 1;
        callback();
      },
    },
  };
  return { chromeApi, state };
}

async function expectCode(promise, code) {
  let actual = null;
  try {
    await promise;
  } catch (error) {
    actual = error.code;
  }
  assert.strictEqual(actual, code);
}

;(async () => {
  const running = makeChrome();
  await expectCode(
    update.requestExtensionUpdate({
      chromeApi: running.chromeApi,
      status: { ok: true, any_running: true, required_version: "v31" },
      currentVersion: "v30",
      now: () => 1000,
    }),
    "automation_running"
  );
  assert.deepStrictEqual(running.state.events, []);

  const wrongOrigin = makeChrome({ url: "https://evil.example/main.php" });
  await expectCode(
    update.requestExtensionUpdate({
      chromeApi: wrongOrigin.chromeApi,
      status: { ok: true, any_running: false, required_version: "v31" },
      currentVersion: "v30",
      now: () => 1000,
    }),
    "active_tab_not_game"
  );
  assert.strictEqual(wrongOrigin.state.runtimeReloads, 0);
  assert.strictEqual(wrongOrigin.state.data[key], undefined);

  const navigatorPopup = makeChrome({ url: "https://3kingdoms.ru/navigator.php" });
  await expectCode(
    update.requestExtensionUpdate({
      chromeApi: navigatorPopup.chromeApi,
      status: { ok: true, any_running: false, required_version: "v31" },
      currentVersion: "v30",
      now: () => 1000,
    }),
    "active_tab_is_navigator"
  );
  assert.strictEqual(navigatorPopup.state.runtimeReloads, 0);
  assert.strictEqual(navigatorPopup.state.data[key], undefined);

  const requested = makeChrome();
  const requestResult = await update.requestExtensionUpdate({
    chromeApi: requested.chromeApi,
    status: { ok: true, any_running: false, required_version: "v31" },
    currentVersion: "v30",
    now: () => 1000,
  });
  assert.strictEqual(requestResult.ok, true);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(requested.state.data[key])), {
    tabId: 17,
    expectedVersion: "v31",
    createdAt: 1000,
    attempts: 0,
  });
  assert.strictEqual(requested.state.runtimeReloads, 1);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(requested.state.data[update.UPDATE_ATTEMPT_KEY])), {
    currentVersion: "v30",
    expectedVersion: "v31",
    attemptedAt: 1000,
  });
  assert.deepStrictEqual(requested.state.events, ["tabs.query", "storage.set", "runtime.reload"]);

  const validMarker = { tabId: 17, expectedVersion: "v31", createdAt: 1000, attempts: 0 };
  const consumed = makeChrome({ marker: validMarker });
  const registration = update.registerExtensionUpdateLifecycle({
    chromeApi: consumed.chromeApi,
    bridgeVersion: "v31",
    now: () => 1050,
    onError(error) { throw error; },
  });
  const startupResult = await registration.startup;
  assert.strictEqual(startupResult.handled, true);
  assert.strictEqual(consumed.state.data[key], undefined);
  assert.deepStrictEqual(consumed.state.events, [
    "onInstalled.addListener",
    "storage.get",
    "tabs.get",
    "storage.remove",
    "tabs.reload:17:true",
  ]);
  await registration.listener({ reason: "update" });
  assert.strictEqual(consumed.state.tabReloads, 1);

  for (const [marker, currentTime] of [
    [{ tabId: 17, expectedVersion: "other", createdAt: 1000, attempts: 0 }, 1050],
    [{ tabId: 17, expectedVersion: "v31", createdAt: 0, attempts: 0 }, 200000],
    [{ tabId: 17, expectedVersion: "v31", createdAt: 1000, attempts: 1 }, 1050],
  ]) {
    const rejected = makeChrome({ marker });
    const result = await update.consumePendingUpdate({
      chromeApi: rejected.chromeApi,
      bridgeVersion: "v31",
      now: () => currentTime,
      markerTtlMs: 1000,
    });
    assert.strictEqual(result.handled, false);
    assert.strictEqual(rejected.state.data[key], undefined);
    assert.strictEqual(rejected.state.tabReloads, 0);
  }

  const navigated = makeChrome({
    url: "https://example.com/after-marker",
    marker: { tabId: 17, expectedVersion: "v31", createdAt: 1000, attempts: 0 },
  });
  const navigatedResult = await update.consumePendingUpdate({
    chromeApi: navigated.chromeApi,
    bridgeVersion: "v31",
    now: () => 1050,
  });
  assert.strictEqual(navigatedResult.reason, "tab_not_game");
  assert.strictEqual(navigated.state.data[key], undefined);
  assert.strictEqual(navigated.state.tabReloads, 0);

  const unavailable = makeChrome();
  const unavailableResult = await update.checkForExtensionUpdate({
    chromeApi: unavailable.chromeApi,
    bridgeVersion: "v30",
    endpoint: "http://127.0.0.1:17654",
    fetchImpl: async () => { throw new Error("offline"); },
  });
  assert.strictEqual(unavailableResult.reason, "server_unavailable");
  assert.strictEqual(unavailable.state.runtimeReloads, 0);

  const withNavigators = makeChrome();
  withNavigators.chromeApi.tabs.query = (query, callback) => {
    callback([
      { id: 21, url: "https://3kingdoms.ru/navigator.php", active: true },
      { id: 17, url: "https://3kingdoms.ru/main.php", active: false },
      { id: 22, url: "https://3kingdoms.ru/navigator.php?target=6", active: false },
    ]);
  };
  const navigatorResult = await update.checkForExtensionUpdate({
    chromeApi: withNavigators.chromeApi,
    bridgeVersion: "v30",
    endpoint: "http://127.0.0.1:17654",
    fetchImpl: async () => ({
      ok: true,
      async json() { return { ok: true, any_running: false, required_version: "v31" }; },
    }),
    now: () => 2500,
  });
  assert.strictEqual(navigatorResult.requested, true);
  assert.strictEqual(navigatorResult.tabId, 17);

  const ambiguous = makeChrome();
  ambiguous.chromeApi.tabs.query = (query, callback) => {
    callback([
      { id: 17, url: "https://3kingdoms.ru/main.php", active: false },
      { id: 18, url: "https://3kingdoms.ru/fight.php", active: false },
      { id: 21, url: "https://3kingdoms.ru/navigator.php", active: true },
    ]);
  };
  const ambiguousResult = await update.checkForExtensionUpdate({
    chromeApi: ambiguous.chromeApi,
    bridgeVersion: "v30",
    endpoint: "http://127.0.0.1:17654",
    fetchImpl: async () => ({
      ok: true,
      async json() { return { ok: true, any_running: false, required_version: "v31" }; },
    }),
    now: () => 2600,
  });
  assert.strictEqual(ambiguousResult.reason, "game_tab_ambiguous");
  assert.strictEqual(ambiguous.state.runtimeReloads, 0);

  const preferred = update.selectPrimaryGameTab([
    { id: 17, url: "https://3kingdoms.ru/main.php", active: false },
    { id: 18, url: "https://3kingdoms.ru/fight.php", active: true },
    { id: 21, url: "https://3kingdoms.ru/navigator.php", active: true },
  ]);
  assert.strictEqual(preferred.tab.id, 18);
  assert.strictEqual(preferred.reason, "active_primary_tab");

  const automatic = makeChrome();
  automatic.chromeApi.tabs.query = (query, callback) => {
    automatic.state.events.push(`tabs.query:${query.url}`);
    callback([{ id: 17, url: "https://3kingdoms.ru/main.php" }]);
  };
  const autoResult = await update.checkForExtensionUpdate({
    chromeApi: automatic.chromeApi,
    bridgeVersion: "v30",
    endpoint: "http://127.0.0.1:17654",
    fetchImpl: async () => ({
      ok: true,
      async json() { return { ok: true, any_running: false, required_version: "v31" }; },
    }),
    now: () => 3000,
  });
  assert.strictEqual(autoResult.requested, true);
  assert.strictEqual(automatic.state.runtimeReloads, 1);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(automatic.state.data[update.UPDATE_ATTEMPT_KEY])), {
    currentVersion: "v30",
    expectedVersion: "v31",
    attemptedAt: 3000,
  });

  const dedupedResult = await update.checkForExtensionUpdate({
    chromeApi: automatic.chromeApi,
    bridgeVersion: "v30",
    endpoint: "http://127.0.0.1:17654",
    fetchImpl: async () => ({
      ok: true,
      async json() { return { ok: true, any_running: false, required_version: "v31" }; },
    }),
    now: () => 4000,
  });
  assert.strictEqual(dedupedResult.reason, "version_pair_already_attempted");
  assert.strictEqual(automatic.state.runtimeReloads, 1);

  const retryAfterBackoff = await update.checkForExtensionUpdate({
    chromeApi: automatic.chromeApi,
    bridgeVersion: "v30",
    endpoint: "http://127.0.0.1:17654",
    fetchImpl: async () => ({
      ok: true,
      async json() { return { ok: true, any_running: false, required_version: "v31" }; },
    }),
    now: () => 3000 + update.UPDATE_ATTEMPT_TTL_MS,
  });
  assert.strictEqual(retryAfterBackoff.requested, true);
  assert.strictEqual(automatic.state.runtimeReloads, 2);

  let fetchCalls = 0;
  const scheduled = [];
  const monitorChrome = makeChrome();
  const monitor = update.startExtensionUpdateMonitor({
    chromeApi: monitorChrome.chromeApi,
    bridgeVersion: "v31",
    endpoint: "http://127.0.0.1:17654",
    fetchImpl: async () => {
      fetchCalls += 1;
      return {
        ok: true,
        async json() { return { ok: true, any_running: false, required_version: "v31" }; },
      };
    },
    now: () => 5000,
    setTimeoutImpl(callback, delay) { scheduled.push({ callback, delay }); return scheduled.length; },
    clearTimeoutImpl() {},
  });
  assert.strictEqual(scheduled[0].delay, 1000);
  assert.strictEqual((await monitor.checkNow()).reason, "already_current");
  assert.strictEqual((await monitor.checkNow()).reason, "cooldown");
  assert.strictEqual(fetchCalls, 1);
  monitor.stop();
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=".",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
