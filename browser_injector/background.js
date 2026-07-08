const ENDPOINT = "http://127.0.0.1:17654";
const BRIDGE_VERSION = "2026-07-08-background-fetch";

const ALLOWED_PATHS = [
  /^\/health$/,
  /^\/next(?:\?|$)/,
  /^\/ack$/,
  /^\/api\//,
];

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || message.type !== "antibot-cv-local-fetch") {
    return false;
  }
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
