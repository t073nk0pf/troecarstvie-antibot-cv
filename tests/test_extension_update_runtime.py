from __future__ import annotations

import json
import subprocess
from pathlib import Path

from src.antibot_cv.automation.browser_injector import CURRENT_BRIDGE_VERSION


def test_extension_update_runtime_is_wired_without_new_permissions() -> None:
    manifest = json.loads(Path("browser_injector/manifest.json").read_text(encoding="utf-8"))
    background = Path("browser_injector/background.js").read_text(encoding="utf-8")
    popup = Path("browser_injector/popup.js").read_text(encoding="utf-8")
    popup_html = Path("browser_injector/popup.html").read_text(encoding="utf-8")

    assert set(manifest["permissions"]) == {"storage", "tabs"}
    assert manifest["version"] == "0.3.30"
    assert 'importScripts("update_runtime.js")' in background
    assert "registerExtensionUpdateLifecycle" in background
    assert "startExtensionUpdateMonitor" in background
    assert "const extensionUpdateLifecycle" in background
    assert "ready: extensionUpdateLifecycle.startup" in background
    assert "const extensionUpdateMonitor" in background
    assert "void extensionUpdateMonitor.checkNow()" not in background
    assert f'const bridgeVersion = "{CURRENT_BRIDGE_VERSION}"' in Path("browser_injector/content.js").read_text(encoding="utf-8")
    assert f'const BRIDGE_VERSION = "{CURRENT_BRIDGE_VERSION}"' in background
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
const refreshKey = update.TAB_REFRESH_RETRY_KEY;

function makeChrome({
  url = "https://3kingdoms.ru/main.php",
  marker = null,
  refreshRetry = null,
} = {}) {
  const data = {};
  if (marker) data[key] = marker;
  if (refreshRetry) data[refreshKey] = refreshRetry;
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
  assert.deepStrictEqual(JSON.parse(JSON.stringify(requested.state.data[refreshKey])), {
    tabId: 17,
    expectedVersion: "v31",
    createdAt: 1000,
    retryAfter: 1000 + update.TAB_REFRESH_RETRY_BACKOFF_MS,
    attempts: 0,
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
  assert.deepStrictEqual(JSON.parse(JSON.stringify(consumed.state.data[refreshKey])), {
    tabId: 17,
    expectedVersion: "v31",
    createdAt: 1050,
    retryAfter: 1050 + update.TAB_REFRESH_RETRY_BACKOFF_MS,
    attempts: 0,
  });
  assert.deepStrictEqual(consumed.state.events, [
    "onInstalled.addListener",
    "storage.get",
    "tabs.get",
    "storage.set",
    "storage.remove",
    "tabs.reload:17:true",
  ]);
  await registration.listener({ reason: "update" });
  assert.strictEqual(consumed.state.tabReloads, 1);

  for (const [marker, currentTime] of [
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

  const oldWorkerMarker = { tabId: 17, expectedVersion: "v31", createdAt: 1000, attempts: 0 };
  const oldWorkerRetry = {
    tabId: 17,
    expectedVersion: "v31",
    createdAt: 1000,
    retryAfter: 2000,
    attempts: 0,
  };
  const oldWorker = makeChrome({ marker: oldWorkerMarker, refreshRetry: oldWorkerRetry });
  const oldWorkerResult = await update.consumePendingUpdate({
    chromeApi: oldWorker.chromeApi,
    bridgeVersion: "v30",
    now: () => 1050,
  });
  assert.strictEqual(oldWorkerResult.reason, "worker_version_mismatch");
  assert.deepStrictEqual(oldWorker.state.data[key], oldWorkerMarker);
  assert.deepStrictEqual(oldWorker.state.data[refreshKey], oldWorkerRetry);
  assert.strictEqual(oldWorker.state.tabReloads, 0);

  const navigated = makeChrome({
    url: "https://example.com/after-marker",
    marker: { tabId: 17, expectedVersion: "v31", createdAt: 1000, attempts: 0 },
  });
  const navigatedResult = await update.consumePendingUpdate({
    chromeApi: navigated.chromeApi,
    bridgeVersion: "v31",
    now: () => 1050,
  });
  assert.strictEqual(navigatedResult.reason, "tab_not_primary_game");
  assert.strictEqual(navigated.state.data[key], undefined);
  assert.strictEqual(navigated.state.data[refreshKey], undefined);
  assert.strictEqual(navigated.state.tabReloads, 0);

  const refreshRecord = {
    tabId: 17,
    expectedVersion: "v31",
    createdAt: 5000,
    retryAfter: 6000,
    attempts: 0,
  };
  const beforeRefreshBackoff = makeChrome({ refreshRetry: refreshRecord });
  const backoffResult = await update.checkForExtensionUpdate({
    chromeApi: beforeRefreshBackoff.chromeApi,
    bridgeVersion: "v31",
    endpoint: "http://127.0.0.1:17654",
    fetchImpl: async () => ({
      ok: true,
      async json() { return { ok: true, any_running: false, required_version: "v31", clients: [] }; },
    }),
    now: () => 5500,
  });
  assert.strictEqual(backoffResult.reason, "tab_refresh_backoff");
  assert.strictEqual(beforeRefreshBackoff.state.tabReloads, 0);
  assert.notStrictEqual(beforeRefreshBackoff.state.data[refreshKey], undefined);

  const stalePrimary = makeChrome({ refreshRetry: refreshRecord });
  const staleResult = await update.checkForExtensionUpdate({
    chromeApi: stalePrimary.chromeApi,
    bridgeVersion: "v31",
    endpoint: "http://127.0.0.1:17654",
    fetchImpl: async () => ({
      ok: true,
      async json() {
        return {
          ok: true,
          any_running: false,
          required_version: "v31",
          clients: [
            {
              client_seen: true,
              tab_id: 17,
              client_version: "v30",
              href: "https://3kingdoms.ru/main.php",
            },
            {
              client_seen: true,
              tab_id: 21,
              client_version: "v31",
              href: "https://3kingdoms.ru/navigator.php",
            },
          ],
        };
      },
    }),
    now: () => 6000,
  });
  assert.strictEqual(staleResult.requested, true);
  assert.strictEqual(staleResult.reason, "tab_refresh_retry_requested");
  assert.strictEqual(staleResult.tabId, 17);
  assert.strictEqual(stalePrimary.state.data[refreshKey], undefined);
  assert.strictEqual(stalePrimary.state.tabReloads, 1);
  assert.deepStrictEqual(stalePrimary.state.events.slice(-3), [
    "tabs.get",
    "storage.remove",
    "tabs.reload:17:true",
  ]);
  const afterOneRetry = await update.checkForExtensionUpdate({
    chromeApi: stalePrimary.chromeApi,
    bridgeVersion: "v31",
    endpoint: "http://127.0.0.1:17654",
    fetchImpl: async () => ({
      ok: true,
      async json() { return { ok: true, any_running: false, required_version: "v31", clients: [] }; },
    }),
    now: () => 7000,
  });
  assert.strictEqual(afterOneRetry.reason, "already_current");
  assert.strictEqual(stalePrimary.state.tabReloads, 1);

  const confirmedPrimary = makeChrome({ refreshRetry: refreshRecord });
  const confirmedResult = await update.checkForExtensionUpdate({
    chromeApi: confirmedPrimary.chromeApi,
    bridgeVersion: "v31",
    endpoint: "http://127.0.0.1:17654",
    fetchImpl: async () => ({
      ok: true,
      async json() {
        return {
          ok: true,
          any_running: false,
          required_version: "v31",
          clients: [{
            client_seen: true,
            tab_id: 17,
            client_version: "v31",
            href: "https://3kingdoms.ru/main.php",
          }],
        };
      },
    }),
    now: () => 5500,
  });
  assert.strictEqual(confirmedResult.reason, "tab_refresh_confirmed");
  assert.strictEqual(confirmedPrimary.state.data[refreshKey], undefined);
  assert.strictEqual(confirmedPrimary.state.tabReloads, 0);

  const offOriginClient = makeChrome({ refreshRetry: refreshRecord });
  const offOriginResult = await update.checkForExtensionUpdate({
    chromeApi: offOriginClient.chromeApi,
    bridgeVersion: "v31",
    endpoint: "http://127.0.0.1:17654",
    fetchImpl: async () => ({
      ok: true,
      async json() {
        return {
          ok: true,
          any_running: false,
          required_version: "v31",
          clients: [{
            client_seen: true,
            tab_id: 17,
            client_version: "v31",
            href: "",
          }],
        };
      },
    }),
    now: () => 6000,
  });
  assert.strictEqual(offOriginResult.reason, "tab_refresh_retry_requested");
  assert.strictEqual(offOriginClient.state.data[refreshKey], undefined);
  assert.strictEqual(offOriginClient.state.tabReloads, 1);

  const navigatorRefresh = makeChrome({
    url: "https://3kingdoms.ru/navigator.php",
    refreshRetry: refreshRecord,
  });
  const navigatorRefreshResult = await update.checkForExtensionUpdate({
    chromeApi: navigatorRefresh.chromeApi,
    bridgeVersion: "v31",
    endpoint: "http://127.0.0.1:17654",
    fetchImpl: async () => ({
      ok: true,
      async json() { return { ok: true, any_running: false, required_version: "v31", clients: [] }; },
    }),
    now: () => 6000,
  });
  assert.strictEqual(navigatorRefreshResult.reason, "tab_refresh_tab_not_primary");
  assert.strictEqual(navigatorRefresh.state.data[refreshKey], undefined);
  assert.strictEqual(navigatorRefresh.state.tabReloads, 0);

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

  let releaseStartup;
  const startupGate = new Promise((resolve) => { releaseStartup = resolve; });
  let gatedFetchCalls = 0;
  const gatedScheduled = [];
  const gatedMonitorChrome = makeChrome();
  const gatedMonitor = update.startExtensionUpdateMonitor({
    chromeApi: gatedMonitorChrome.chromeApi,
    bridgeVersion: "v31",
    endpoint: "http://127.0.0.1:17654",
    ready: startupGate,
    fetchImpl: async () => {
      gatedFetchCalls += 1;
      return {
        ok: true,
        async json() { return { ok: true, any_running: false, required_version: "v31" }; },
      };
    },
    now: () => 8000,
    setTimeoutImpl(callback, delay) { gatedScheduled.push({ callback, delay }); return gatedScheduled.length; },
    clearTimeoutImpl() {},
  });
  const gatedCheck = gatedMonitor.checkNow();
  await Promise.resolve();
  assert.strictEqual(gatedFetchCalls, 0);
  releaseStartup({ handled: true, reason: "tab_reloaded" });
  assert.strictEqual((await gatedCheck).reason, "already_current");
  assert.strictEqual(gatedFetchCalls, 1);
  gatedMonitor.stop();
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
