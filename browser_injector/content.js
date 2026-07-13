(() => {
  if (window.top !== window) {
    return;
  }

  const bridgeVersion = "2026-07-13-quest-sections-v29";
  const contentSource = `antibot-cv-content:${bridgeVersion}`;
  const injectorSource = `antibot-cv-injector:${bridgeVersion}`;
  let clientId = "";
  let clientIdentity = null;
  const documentNonce = createDocumentNonce();
  const clientReady = getTabClientId();
  let busy = false;
  let lastCommandId = null;

  function getTabClientId() {
    return new Promise((resolve, reject) => {
      chrome.runtime.sendMessage({ type: "antibot-cv-tab-identity", documentNonce }, (response) => {
        const error = chrome.runtime.lastError;
        if (error) {
          reject(new Error(error.message));
          return;
        }
        if (!response || !response.ok || !response.clientId) {
          reject(new Error((response && response.error) || "tab_identity_failed"));
          return;
        }
        clientId = String(response.clientId);
        clientIdentity = response;
        resolve(clientId);
      });
    });
  }

  function createDocumentNonce() {
    try {
      const bytes = new Uint32Array(3);
      crypto.getRandomValues(bytes);
      return Array.from(bytes, (value) => value.toString(16).padStart(8, "0")).join("");
    } catch (_) {
      return `${Date.now().toString(16)}${Math.random().toString(16).slice(2)}`.slice(0, 24);
    }
  }

  function injectPageBridge() {
    if (document.documentElement.dataset.antibotCvBridgeInjected === bridgeVersion) {
      return Promise.resolve();
    }
    document.documentElement.dataset.antibotCvBridgeInjected = bridgeVersion;
    return new Promise((resolve) => {
      const script = document.createElement("script");
      script.src = chrome.runtime.getURL("page_bridge.js");
      script.async = false;
      script.onload = () => {
        script.remove();
        resolve();
      };
      script.onerror = () => {
        script.remove();
        resolve();
      };
      (document.documentElement || document.head || document.body).appendChild(script);
      window.setTimeout(resolve, 500);
    });
  }

  const bridgeReady = injectPageBridge();

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (!message || message.type !== "antibot-cv-current-client") {
      return false;
    }
    clientReady
      .then(() =>
        sendResponse({
          ok: true,
          clientId,
          version: bridgeVersion,
          href: window.location.href,
          title: document.title || "",
        })
      )
      .catch((error) => sendResponse({ ok: false, error: String(error && error.message ? error.message : error) }));
    return true;
  });

  async function poll() {
    if (busy) {
      return;
    }
    busy = true;
    try {
      await clientReady;
      const params = new URLSearchParams({
        client: clientId,
        version: bridgeVersion,
        href: window.location.href,
        title: document.title || "",
      });
      if (clientIdentity && clientIdentity.profileId) params.set("profile", String(clientIdentity.profileId));
      if (clientIdentity && Number.isInteger(clientIdentity.tabId)) params.set("tab", String(clientIdentity.tabId));
      if (clientIdentity && Number.isInteger(clientIdentity.openerTabId)) params.set("opener", String(clientIdentity.openerTabId));
      const data = await localFetch(`/next?${params.toString()}`);
      const command = data && data.command;
      if (command && command.id && command.id !== lastCommandId) {
        const targetHrefIncludes = command.payload && command.payload.targetHrefIncludes;
        if (targetHrefIncludes && !window.location.href.includes(String(targetHrefIncludes))) {
          return;
        }
        lastCommandId = command.id;
        const result = await runCommandInPage(command);
        await ack(command.id, result);
      }
    } catch (_) {
      // The Python controller is not running. Keep polling quietly.
    } finally {
      busy = false;
    }
  }

  async function ack(id, result) {
    try {
      await localFetch("/ack", {
        method: "POST",
        body: {
          id,
          ok: Boolean(result && result.ok),
          message: result && result.message ? String(result.message) : "",
          client_id: clientId,
        },
      });
    } catch (_) {
      // Best effort acknowledgement only.
    }
  }

  function localFetch(path, options = {}) {
    return new Promise((resolve, reject) => {
      chrome.runtime.sendMessage(
        {
          type: "antibot-cv-local-fetch",
          request: {
            path,
            method: options.method || "GET",
            body: options.body,
          },
        },
        (response) => {
          const error = chrome.runtime.lastError;
          if (error) {
            reject(new Error(error.message));
            return;
          }
          if (!response || !response.ok) {
            reject(new Error((response && (response.error || response.data?.error)) || "local_fetch_failed"));
            return;
          }
          resolve(response.data || {});
        }
      );
    });
  }

  function timeoutForPageCommand(payload) {
    const rawInventoryDelay = Number(payload && payload.inventoryOpenDelayMs);
    const inventoryDelay = Number.isFinite(rawInventoryDelay) ? Math.max(0, Math.min(5000, rawInventoryDelay)) : 0;
    const rawVerifyTimeout = Number(payload && payload.verifyTimeoutMs);
    const verifyTimeout = Number.isFinite(rawVerifyTimeout) ? Math.max(0, Math.min(10000, rawVerifyTimeout)) : 0;
    const rawCommandTimeout = Number(payload && payload.commandTimeoutMs);
    const commandTimeout = Number.isFinite(rawCommandTimeout) ? Math.max(0, Math.min(15000, rawCommandTimeout)) : 0;
    return Math.max(
      2000,
      Math.min(15000, Math.max(inventoryDelay + 5000, verifyTimeout + 2500, commandTimeout))
    );
  }

  function runCommandInPage(command) {
    return bridgeReady.then(
      () =>
        new Promise((resolve) => {
          const token = `antibot-cv-${command.id}`;
          const payload = command.payload || {};
          const commandTimeoutMs = timeoutForPageCommand(payload);
          const timeout = window.setTimeout(() => {
            cleanup();
            resolve({ ok: false, message: "page_command_timeout" });
          }, commandTimeoutMs);

          function cleanup() {
            window.clearTimeout(timeout);
            window.removeEventListener("message", onMessage);
          }

          function onMessage(event) {
            if (event.source !== window) {
              return;
            }
            const data = event.data || {};
            if (data.source !== injectorSource || data.token !== token) {
              return;
            }
            cleanup();
            resolve({ ok: Boolean(data.ok), message: data.message || "" });
          }

          window.addEventListener("message", onMessage);
          window.postMessage(
            {
              source: contentSource,
              token,
              command: {
                type: command.type,
                payload,
              },
            },
            "*"
          );
        })
    );
  }

  window.setInterval(poll, 350);
  poll();
})();
