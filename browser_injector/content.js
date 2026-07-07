(() => {
  if (window.top !== window) {
    return;
  }

  const endpoint = "http://127.0.0.1:17654";
  const bridgeVersion = "2026-07-07-local-popup";
  const contentSource = `antibot-cv-content:${bridgeVersion}`;
  const injectorSource = `antibot-cv-injector:${bridgeVersion}`;
  const clientId = getStableClientId();
  let busy = false;
  let lastCommandId = null;

  function getStableClientId() {
    const key = "antibotCvClientId";
    try {
      const existing = window.sessionStorage.getItem(key);
      if (existing) {
        return existing;
      }
      const created = `chrome-${Date.now()}-${Math.random().toString(16).slice(2)}`;
      window.sessionStorage.setItem(key, created);
      return created;
    } catch (_) {
      return `chrome-${Date.now()}-${Math.random().toString(16).slice(2)}`;
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
    sendResponse({
      ok: true,
      clientId,
      version: bridgeVersion,
      href: window.location.href,
      title: document.title || "",
    });
    return false;
  });

  async function poll() {
    if (busy) {
      return;
    }
    busy = true;
    try {
      const params = new URLSearchParams({
        client: clientId,
        version: bridgeVersion,
        href: window.location.href,
        title: document.title || "",
      });
      const response = await fetch(
        `${endpoint}/next?${params.toString()}`,
        { cache: "no-store" }
      );
      const data = await response.json();
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
      await fetch(`${endpoint}/ack`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          id,
          ok: Boolean(result && result.ok),
          message: result && result.message ? String(result.message) : "",
          client_id: clientId,
        }),
      });
    } catch (_) {
      // Best effort acknowledgement only.
    }
  }

  function timeoutForPageCommand(payload) {
    const rawInventoryDelay = Number(payload && payload.inventoryOpenDelayMs);
    const inventoryDelay = Number.isFinite(rawInventoryDelay) ? Math.max(0, Math.min(5000, rawInventoryDelay)) : 0;
    return Math.max(2000, Math.min(15000, inventoryDelay + 5000));
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
