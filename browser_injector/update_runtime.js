(function (root) {
  "use strict";

  const UPDATE_MARKER_KEY = "antibotCvPendingExtensionUpdate";
  const UPDATE_ATTEMPT_KEY = "antibotCvExtensionUpdateAttempt";
  const UPDATE_MARKER_TTL_MS = 2 * 60 * 1000;
  const UPDATE_ATTEMPT_TTL_MS = 5 * 60 * 1000;
  const UPDATE_CHECK_INTERVAL_MS = 15 * 1000;
  const UPDATE_CHECK_COOLDOWN_MS = 15 * 1000;
  const GAME_ORIGIN = "https://3kingdoms.ru";

  function updateError(code, message) {
    const error = new Error(message);
    error.code = code;
    return error;
  }

  function chromeCallback(chromeApi, invoke) {
    return new Promise((resolve, reject) => {
      invoke((value) => {
        const error = chromeApi.runtime && chromeApi.runtime.lastError;
        if (error) {
          reject(new Error(error.message || String(error)));
          return;
        }
        resolve(value);
      });
    });
  }

  function isGameTabUrl(value) {
    try {
      const url = new URL(String(value || ""));
      return url.origin === GAME_ORIGIN && url.username === "" && url.password === "";
    } catch (_) {
      return false;
    }
  }

  function isNavigatorTabUrl(value) {
    try {
      const url = new URL(String(value || ""));
      return isGameTabUrl(value) && url.pathname.toLowerCase() === "/navigator.php";
    } catch (_) {
      return false;
    }
  }

  function selectPrimaryGameTab(tabs) {
    const candidates = (Array.isArray(tabs) ? tabs : []).filter(
      (tab) =>
        tab &&
        Number.isInteger(tab.id) &&
        isGameTabUrl(tab.url) &&
        !isNavigatorTabUrl(tab.url)
    );
    if (candidates.length === 1) {
      return { tab: candidates[0], reason: "single_primary_tab" };
    }
    const active = candidates.filter((tab) => tab.active === true);
    if (candidates.length > 1 && active.length === 1) {
      return { tab: active[0], reason: "active_primary_tab" };
    }
    return {
      tab: null,
      reason: candidates.length ? "game_tab_ambiguous" : "game_tab_missing",
    };
  }

  async function activeGameTab(chromeApi) {
    const tabs = await chromeCallback(chromeApi, (callback) => {
      chromeApi.tabs.query({ active: true, currentWindow: true }, callback);
    });
    const tab = Array.isArray(tabs) ? tabs[0] : null;
    if (!tab || !Number.isInteger(tab.id)) {
      throw updateError("active_tab_missing", "Активная вкладка не найдена.");
    }
    if (!isGameTabUrl(tab.url)) {
      throw updateError(
        "active_tab_not_game",
        "Обновление разрешено только из активной вкладки https://3kingdoms.ru/."
      );
    }
    if (isNavigatorTabUrl(tab.url)) {
      throw updateError(
        "active_tab_is_navigator",
        "Обновление разрешено только из основной игровой вкладки."
      );
    }
    return tab;
  }

  function validatedExpectedVersion(status) {
    if (!status || status.ok !== true || status.any_running !== false) {
      throw updateError(
        status && status.any_running ? "automation_running" : "automation_status_unconfirmed",
        status && status.any_running
          ? "Сначала останови все запущенные сессии бота."
          : "Не удалось подтвердить, что все сессии бота остановлены."
      );
    }
    const expectedVersion = String(status.required_version || "").trim();
    if (!expectedVersion) {
      throw updateError("expected_version_missing", "Сервер не сообщил ожидаемую версию bridge.");
    }
    return expectedVersion;
  }

  async function persistUpdateRequest({ chromeApi, tab, expectedVersion, currentVersion = "", now = Date.now }) {
    if (!tab || !Number.isInteger(tab.id) || !isGameTabUrl(tab.url)) {
      throw updateError("update_tab_invalid", "Вкладка игры для обновления не найдена.");
    }
    const createdAt = Number(now());
    if (!Number.isFinite(createdAt)) {
      throw updateError("update_clock_invalid", "Не удалось зафиксировать время обновления.");
    }
    const normalizedCurrentVersion = String(currentVersion || "").trim();
    if (!normalizedCurrentVersion) {
      throw updateError("current_version_missing", "Не удалось определить текущую версию bridge.");
    }
    const marker = {
      tabId: tab.id,
      expectedVersion,
      createdAt,
      attempts: 0,
    };
    const values = {
      [UPDATE_MARKER_KEY]: marker,
      [UPDATE_ATTEMPT_KEY]: {
        currentVersion: normalizedCurrentVersion,
        expectedVersion,
        attemptedAt: createdAt,
      },
    };
    await chromeCallback(chromeApi, (callback) => {
      chromeApi.storage.local.set(values, callback);
    });
    chromeApi.runtime.reload();
    return { ok: true, marker };
  }

  async function requestExtensionUpdate({ chromeApi, status, currentVersion, now = Date.now } = {}) {
    if (!chromeApi || !chromeApi.runtime || !chromeApi.storage || !chromeApi.tabs) {
      throw updateError("chrome_api_missing", "Chrome API недоступен.");
    }
    const expectedVersion = validatedExpectedVersion(status);
    const tab = await activeGameTab(chromeApi);
    return persistUpdateRequest({ chromeApi, tab, expectedVersion, currentVersion, now });
  }

  async function checkForExtensionUpdate({
    chromeApi,
    bridgeVersion,
    endpoint,
    fetchImpl = fetch,
    now = Date.now,
    attemptTtlMs = UPDATE_ATTEMPT_TTL_MS,
  } = {}) {
    let status;
    try {
      const response = await fetchImpl(`${String(endpoint || "").replace(/\/$/, "")}/api/status`, {
        method: "GET",
        cache: "no-store",
        headers: { accept: "application/json" },
      });
      status = response && response.ok ? await response.json() : null;
    } catch (_) {
      return { requested: false, reason: "server_unavailable" };
    }
    if (!status || status.ok !== true || status.any_running !== false) {
      return {
        requested: false,
        reason: status && status.any_running ? "automation_running" : "status_unconfirmed",
      };
    }
    const expectedVersion = String(status.required_version || "").trim();
    if (!expectedVersion) {
      return { requested: false, reason: "expected_version_missing" };
    }
    if (expectedVersion === String(bridgeVersion || "")) {
      return { requested: false, reason: "already_current" };
    }

    const stored = await chromeCallback(chromeApi, (callback) => {
      chromeApi.storage.local.get(UPDATE_ATTEMPT_KEY, callback);
    });
    const previous = stored && stored[UPDATE_ATTEMPT_KEY];
    const currentTime = Number(now());
    const previousAgeMs = currentTime - Number(previous && previous.attemptedAt);
    if (
      previous &&
      String(previous.currentVersion || "") === String(bridgeVersion || "") &&
      String(previous.expectedVersion || "") === expectedVersion &&
      Number.isFinite(previousAgeMs) &&
      previousAgeMs >= 0 &&
      previousAgeMs < attemptTtlMs
    ) {
      return { requested: false, reason: "version_pair_already_attempted" };
    }

    const tabs = await chromeCallback(chromeApi, (callback) => {
      chromeApi.tabs.query({ url: "https://3kingdoms.ru/*" }, callback);
    });
    const selected = selectPrimaryGameTab(tabs);
    if (!selected.tab) {
      return { requested: false, reason: selected.reason };
    }
    await persistUpdateRequest({
      chromeApi,
      tab: selected.tab,
      expectedVersion,
      currentVersion: bridgeVersion,
      now,
    });
    return { requested: true, reason: "runtime_reload_requested", tabId: selected.tab.id };
  }

  function startExtensionUpdateMonitor({
    chromeApi,
    bridgeVersion,
    endpoint,
    fetchImpl = fetch,
    now = Date.now,
    intervalMs = UPDATE_CHECK_INTERVAL_MS,
    cooldownMs = UPDATE_CHECK_COOLDOWN_MS,
    initialDelayMs = 1000,
    setTimeoutImpl = setTimeout,
    clearTimeoutImpl = clearTimeout,
    onError = (error) => console.error("extension_update_check_failed", error),
  } = {}) {
    let timer = null;
    let stopped = false;
    let inFlight = null;
    let lastCheckAt = Number.NEGATIVE_INFINITY;

    const schedule = (delayMs) => {
      if (!stopped) {
        timer = setTimeoutImpl(() => {
          timer = null;
          void checkNow();
        }, Math.max(0, Number(delayMs) || 0));
      }
    };
    const checkNow = () => {
      const currentTime = Number(now());
      if (inFlight) {
        return inFlight;
      }
      if (Number.isFinite(currentTime) && currentTime - lastCheckAt < cooldownMs) {
        return Promise.resolve({ requested: false, reason: "cooldown" });
      }
      if (timer != null) {
        clearTimeoutImpl(timer);
        timer = null;
      }
      lastCheckAt = currentTime;
      inFlight = checkForExtensionUpdate({ chromeApi, bridgeVersion, endpoint, fetchImpl, now })
        .catch((error) => {
          onError(error);
          return { requested: false, reason: "check_failed" };
        })
        .finally(() => {
          inFlight = null;
          schedule(intervalMs);
        });
      return inFlight;
    };
    schedule(initialDelayMs);
    return {
      checkNow,
      stop() {
        stopped = true;
        if (timer != null) {
          clearTimeoutImpl(timer);
        }
      },
    };
  }

  async function removeMarker(chromeApi) {
    await chromeCallback(chromeApi, (callback) => {
      chromeApi.storage.local.remove(UPDATE_MARKER_KEY, callback);
    });
  }

  async function consumePendingUpdate({
    chromeApi,
    bridgeVersion,
    now = Date.now,
    markerTtlMs = UPDATE_MARKER_TTL_MS,
  } = {}) {
    const stored = await chromeCallback(chromeApi, (callback) => {
      chromeApi.storage.local.get(UPDATE_MARKER_KEY, callback);
    });
    const marker = stored && stored[UPDATE_MARKER_KEY];
    if (!marker) {
      return { handled: false, reason: "marker_missing" };
    }

    const ageMs = Number(now()) - Number(marker.createdAt);
    const validMarker =
      Number.isInteger(marker.tabId) &&
      marker.tabId >= 0 &&
      Number.isFinite(ageMs) &&
      ageMs >= 0 &&
      ageMs <= markerTtlMs &&
      marker.attempts === 0 &&
      String(marker.expectedVersion || "") === String(bridgeVersion || "");
    if (!validMarker) {
      await removeMarker(chromeApi);
      return { handled: false, reason: "marker_invalid" };
    }

    let tab;
    try {
      tab = await chromeCallback(chromeApi, (callback) => {
        chromeApi.tabs.get(marker.tabId, callback);
      });
    } catch (_) {
      await removeMarker(chromeApi);
      return { handled: false, reason: "tab_missing" };
    }
    if (!tab || !isGameTabUrl(tab.url)) {
      await removeMarker(chromeApi);
      return { handled: false, reason: "tab_not_game" };
    }

    // Removing the marker before the tab reload makes this deliberately one-shot:
    // a failed reload is surfaced, but never retried by a later lifecycle event.
    await removeMarker(chromeApi);
    await chromeCallback(chromeApi, (callback) => {
      chromeApi.tabs.reload(marker.tabId, { bypassCache: true }, callback);
    });
    return { handled: true, reason: "tab_reloaded", tabId: marker.tabId };
  }

  function registerExtensionUpdateLifecycle({
    chromeApi,
    bridgeVersion,
    now = Date.now,
    markerTtlMs = UPDATE_MARKER_TTL_MS,
    onError = (error) => console.error("extension_update_failed", error),
  } = {}) {
    if (!chromeApi || !chromeApi.runtime || !chromeApi.runtime.onInstalled) {
      throw updateError("chrome_api_missing", "Chrome lifecycle API недоступен.");
    }
    let handling = null;
    const beginHandling = () => {
      if (!handling) {
        handling = consumePendingUpdate({ chromeApi, bridgeVersion, now, markerTtlMs }).catch((error) => {
          onError(error);
          return { handled: false, reason: "update_failed" };
        });
      }
      return handling;
    };
    const listener = (details) => {
      if (!details || details.reason !== "update") {
        return Promise.resolve({ handled: false, reason: "not_update" });
      }
      return beginHandling();
    };
    chromeApi.runtime.onInstalled.addListener(listener);
    // runtime.reload() always starts a fresh worker, while onInstalled(update)
    // is browser-version dependent for unpacked extensions. Consume from both
    // entry points through the same promise so the tab can only reload once.
    return { listener, startup: beginHandling() };
  }

  root.AntibotCvUpdateRuntime = Object.freeze({
    UPDATE_MARKER_KEY,
    UPDATE_ATTEMPT_KEY,
    UPDATE_MARKER_TTL_MS,
    UPDATE_ATTEMPT_TTL_MS,
    UPDATE_CHECK_INTERVAL_MS,
    UPDATE_CHECK_COOLDOWN_MS,
    isGameTabUrl,
    isNavigatorTabUrl,
    selectPrimaryGameTab,
    activeGameTab,
    requestExtensionUpdate,
    checkForExtensionUpdate,
    startExtensionUpdateMonitor,
    consumePendingUpdate,
    registerExtensionUpdateLifecycle,
  });
})(globalThis);
