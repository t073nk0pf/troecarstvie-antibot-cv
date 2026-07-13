importScripts("update_runtime.js");

const ENDPOINT = "http://127.0.0.1:17654";
const BRIDGE_VERSION = "2026-07-13-self-update-v31";
const PROFILE_ID_KEY = "antibotCvProfileId";
let profileIdPromise = null;
const tabSessionNonceById = new Map();

globalThis.AntibotCvUpdateRuntime.registerExtensionUpdateLifecycle({
  chromeApi: chrome,
  bridgeVersion: BRIDGE_VERSION,
});
const extensionUpdateMonitor = globalThis.AntibotCvUpdateRuntime.startExtensionUpdateMonitor({
  chromeApi: chrome,
  bridgeVersion: BRIDGE_VERSION,
  endpoint: ENDPOINT,
});

const ALLOWED_PATHS = [
  /^\/health$/,
  /^\/next(?:\?|$)/,
  /^\/ack$/,
  /^\/api\//,
];

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message && message.type === "antibot-cv-tab-identity") {
    const tabId = sender && sender.tab && sender.tab.id;
    const openerTabId = sender && sender.tab && sender.tab.openerTabId;
    if (!Number.isInteger(tabId)) {
      sendResponse({ ok: false, error: "tab_id_missing" });
      return false;
    }
    Promise.all([getProfileId(), getTabSessionNonce(tabId)])
      .then(([profileId, tabSessionNonce]) => {
        const documentNonce = String(message.documentNonce || "").replace(/[^a-f0-9]/gi, "").slice(0, 32);
        sendResponse({
          ok: true,
          clientId: `${profileId}-tab-${tabId}-session-${tabSessionNonce}`,
          profileId,
          tabId,
          openerTabId: Number.isInteger(openerTabId) ? openerTabId : null,
          documentNonce,
        });
      })
      .catch((error) => sendResponse({ ok: false, error: String(error && error.message ? error.message : error) }));
    return true;
  }
  if (!message || message.type !== "antibot-cv-local-fetch") {
    return false;
  }
  void extensionUpdateMonitor.checkNow();
  localFetch(message.request || {})
    .then((response) => sendResponse(response))
    .catch((error) =>
      sendResponse({
        ok: false,
        status: 0,
        error: String(error && error.message ? error.message : error),
        version: BRIDGE_VERSION,
      })
    );
  return true;
});

chrome.tabs.onRemoved.addListener((tabId) => {
  tabSessionNonceById.delete(tabId);
  const storageArea = chrome.storage && chrome.storage.session;
  if (storageArea) {
    storageArea.remove(tabSessionStorageKey(tabId), () => void chrome.runtime.lastError);
  }
});

function getTabSessionNonce(tabId) {
  const existing = tabSessionNonceById.get(tabId);
  if (existing) return Promise.resolve(existing);
  const storageArea = chrome.storage && chrome.storage.session;
  if (!storageArea) {
    const created = createNonce(16);
    tabSessionNonceById.set(tabId, created);
    return Promise.resolve(created);
  }
  const key = tabSessionStorageKey(tabId);
  return new Promise((resolve, reject) => {
    storageArea.get(key, (stored) => {
      const error = chrome.runtime.lastError;
      if (error) {
        reject(new Error(error.message));
        return;
      }
      const storedNonce = String((stored && stored[key]) || "");
      const nonce = /^[a-f0-9]{16}$/i.test(storedNonce) ? storedNonce : createNonce(16);
      tabSessionNonceById.set(tabId, nonce);
      if (storedNonce === nonce) {
        resolve(nonce);
        return;
      }
      storageArea.set({ [key]: nonce }, () => {
        const setError = chrome.runtime.lastError;
        if (setError) {
          reject(new Error(setError.message));
          return;
        }
        resolve(nonce);
      });
    });
  });
}

function tabSessionStorageKey(tabId) {
  return `antibotCvTabSession:${tabId}`;
}

function createNonce(length) {
  const randomPart = typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? crypto.randomUUID().replace(/-/g, "")
    : Math.random().toString(16).slice(2) + Date.now().toString(16);
  return randomPart.slice(0, length);
}

function getProfileId() {
  if (profileIdPromise) {
    return profileIdPromise;
  }
  profileIdPromise = new Promise((resolve, reject) => {
    chrome.storage.local.get(PROFILE_ID_KEY, (stored) => {
      const error = chrome.runtime.lastError;
      if (error) {
        reject(new Error(error.message));
        return;
      }
      const existing = stored && stored[PROFILE_ID_KEY];
      if (existing) {
        resolve(String(existing));
        return;
      }
      const randomPart = createNonce(12);
      const created = `chrome-profile-${randomPart}`;
      chrome.storage.local.set({ [PROFILE_ID_KEY]: created }, () => {
        const setError = chrome.runtime.lastError;
        if (setError) {
          reject(new Error(setError.message));
          return;
        }
        resolve(created);
      });
    });
  });
  return profileIdPromise;
}

async function localFetch(request) {
  const path = String(request.path || "");
  if (!isAllowedPath(path)) {
    return {
      ok: false,
      status: 400,
      error: "bad_local_path",
      version: BRIDGE_VERSION,
    };
  }

  const method = String(request.method || "GET").toUpperCase();
  const options = {
    method,
    cache: "no-store",
    headers: { "content-type": "application/json" },
  };
  if (request.body != null) {
    options.body = typeof request.body === "string" ? request.body : JSON.stringify(request.body);
  }

  const response = await fetch(`${ENDPOINT}${path}`, options);
  const text = await response.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch (_) {
    data = {};
  }
  return {
    ok: response.ok,
    status: response.status,
    data,
    text,
    version: BRIDGE_VERSION,
  };
}

function isAllowedPath(path) {
  if (!path.startsWith("/") || path.startsWith("//")) {
    return false;
  }
  return ALLOWED_PATHS.some((pattern) => pattern.test(path));
}
