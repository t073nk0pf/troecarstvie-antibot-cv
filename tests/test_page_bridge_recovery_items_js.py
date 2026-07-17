"""Recovery-item bridge behavior tests."""
from __future__ import annotations

import subprocess


def test_page_bridge_opens_recovery_item_and_confirms_action_form() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
const menuCalls = [];
const entryPointCalls = [];
let applyClicks = 0;

function element(tag, attrs = {}, text = "") {
  return {
    tagName: tag,
    id: attrs.id || "",
    className: attrs.className || "",
    innerText: text,
    textContent: text,
  value: attrs.value || "",
  getAttribute(name) { return attrs[name] ?? null; },
  closest() { return this; },
  click() { applyClicks += 1; },
  };
}

const healthItem = element("SPAN", { "data-title": "Бурдюк здоровья", "data-artikul_id": "111", "data-cnt": "1" });
const prowessItem = element("SPAN", { "data-title": "Бурдюк удали", "data-artikul_id": "222", "data-cnt": "1" });
const applyButton = element("BUTTON", {}, "Применить");
const doc = {
  title: "",
  body: { innerText: "жизнь 20% удаль 20%" },
  documentElement: { innerHTML: "" },
  querySelectorAll(selector) {
    if (selector.startsWith("button") || selector.startsWith("a,button") || selector.includes("input[type")) return [applyButton];
    return [healthItem, prowessItem];
  },
};
const root = {
  name: "top",
  location: { href: "https://3kingdoms.ru/main.php" },
  frames: [],
  document: { body: { innerText: "" }, querySelectorAll() { return []; } },
  setTimeout,
  entry_point_request(scope, action, payload, callback) {
    entryPointCalls.push({ scope, action, payload });
    callback({ ok: true }, { ok: true });
  },
  addEventListener(type, callback) { listeners[type] = callback; },
  removeEventListener() {},
  postMessage(message) { messages.push(message); },
};
const mainFrame = {
  name: "main_frame",
  location: { href: "https://3kingdoms.ru/main_frame.php" },
  frames: [],
  document: doc,
  processMenu(code) { menuCalls.push(code); },
};
root.top = root;
root.window = root;
root.frames = [mainFrame];
root.frames.main_frame = mainFrame;
mainFrame.top = root;
mainFrame.window = mainFrame;

vm.runInNewContext(source, { window: root, console, setTimeout, clearTimeout });

function command(type, payload = {}) {
  messages.length = 0;
  listeners.message({
    source: root,
    data: {
      source: `antibot-cv-content:${version}`,
      token: "token-1",
      command: { type, payload },
    },
  });
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + 6000;
    const poll = () => {
      if (messages.length) {
        resolve({ ok: messages[0].ok, message: JSON.parse(messages[0].message) });
        return;
      }
      if (Date.now() > deadline) {
        reject(new Error("timeout"));
        return;
      }
      setTimeout(poll, 10);
    };
    poll();
  });
}

(async () => {
  const result = await command("open_recovery_item", {
    kind: "health",
    names: ["бурдюк здоровья"],
    useWhenBelowPercent: 90,
  });
  assert.strictEqual(result.ok, true);
  assert.deepStrictEqual(menuCalls, ["b11"]);
  assert.strictEqual(applyClicks, 0);
  assert.strictEqual(JSON.stringify(entryPointCalls), JSON.stringify([{ scope: "inventory", action: "useArtifact", payload: { artikul: [{ 111: 1 }] } }]));
  assert.strictEqual(result.message.message, "recovery_item_clicked");
  assert.strictEqual(result.message.method, "entry_point_request");
  assert.strictEqual(result.message.useResult.requestedCount, 1);
  assert.strictEqual(result.message.kind, "health");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=".",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    confirm_script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
let clicked = false;
const button = {
  tagName: "BUTTON",
  innerText: "Выполнить",
  textContent: "Выполнить",
  value: "",
  getAttribute() { return null; },
  click() { clicked = true; },
};
const root = {
  name: "action",
  location: { href: "https://3kingdoms.ru/action_form.php?x=1" },
  frames: [],
  document: {
    title: "Действие",
    querySelectorAll() { return [button]; },
  },
  setTimeout,
  close() { this.closed = true; },
  addEventListener(type, callback) { listeners[type] = callback; },
  removeEventListener() {},
  postMessage(message) { messages.push(message); },
};
root.top = root;
root.window = root;

vm.runInNewContext(source, { window: root, console, setTimeout, clearTimeout });
messages.length = 0;
listeners.message({
  source: root,
  data: {
    source: `antibot-cv-content:${version}`,
    token: "token-1",
    command: { type: "confirm_action_form", payload: {} },
  },
});
assert.strictEqual(messages.length, 1);
const result = JSON.parse(messages[0].message);
assert.strictEqual(messages[0].ok, true);
assert.strictEqual(result.message, "action_form_confirmed");
assert.strictEqual(clicked, true);
"""
    confirm_result = subprocess.run(
        ["node", "-e", confirm_script],
        cwd=".",
        text=True,
        capture_output=True,
        check=False,
    )

    assert confirm_result.returncode == 0, confirm_result.stdout + confirm_result.stderr


def test_page_bridge_opens_recovery_item_from_art_style_cell() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
const entryPointCalls = [];

const healthItem = {
  tagName: "LI",
  id: "art_3573417035",
  className: "",
  innerText: "",
  textContent: "",
  value: "",
  getAttribute(name) {
    if (name === "style") return "background-image: url('images/data/artifacts/serburd_hp.gif');";
    return null;
  },
  closest() { return this; },
  getBoundingClientRect() { return { left: 10, top: 20, width: 54, height: 54 }; },
};
const doc = {
  title: "",
  body: { innerText: "жизнь 20% удаль 20%" },
  documentElement: { innerHTML: "" },
  querySelectorAll() { return [healthItem]; },
};
const root = {
  name: "top",
  location: { href: "https://3kingdoms.ru/main.php" },
  frames: [],
  document: doc,
  setTimeout,
  entry_point_request(scope, action, payload, callback) {
    entryPointCalls.push({ scope, action, payload });
    callback({ ok: true }, { ok: true });
  },
  addEventListener(type, callback) { listeners[type] = callback; },
  removeEventListener() {},
  postMessage(message) { messages.push(message); },
};
const mainFrame = {
  name: "main_frame",
  location: { href: "https://3kingdoms.ru/main_frame.php" },
  frames: [],
  document: doc,
  processMenu() {},
};
root.top = root;
root.window = root;
root.frames = [mainFrame];
root.frames.main_frame = mainFrame;
mainFrame.top = root;
mainFrame.window = mainFrame;

vm.runInNewContext(source, { window: root, console, setTimeout, clearTimeout });

function command(type, payload = {}) {
  messages.length = 0;
  listeners.message({
    source: root,
    data: {
      source: `antibot-cv-content:${version}`,
      token: "token-1",
      command: { type, payload },
    },
  });
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + 2500;
    const poll = () => {
      if (messages.length) {
        resolve({ ok: messages[0].ok, message: JSON.parse(messages[0].message) });
        return;
      }
      if (Date.now() > deadline) {
        reject(new Error("timeout"));
        return;
      }
      setTimeout(poll, 10);
    };
    poll();
  });
}

(async () => {
  const result = await command("open_recovery_item", { kind: "health", names: ["малый бурдюк жизни"], useWhenBelowPercent: 90 });
  assert.strictEqual(result.ok, true);
  assert.strictEqual(result.message.method, "entry_point_request");
  assert.strictEqual(result.message.requiresConfirm, false);
  assert.strictEqual(result.message.item.artikulId, "3573417035");
  assert.strictEqual(JSON.stringify(entryPointCalls), JSON.stringify([{ scope: "inventory", action: "useArtifact", payload: { artikul: [{ 3573417035: 1 }] } }]));
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=".",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_page_bridge_waits_for_delayed_recovery_inventory_items() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
const entryPointCalls = [];
let queryCount = 0;

const healthItem = {
  tagName: "LI",
  id: "art_3573417035",
  className: "",
  innerText: "",
  textContent: "",
  value: "",
  getAttribute(name) {
    if (name === "style") return "background-image: url('images/data/artifacts/serburd_hp.gif');";
    return null;
  },
  closest() { return this; },
  getBoundingClientRect() { return { left: 10, top: 20, width: 54, height: 54 }; },
};
const doc = {
  title: "",
  body: { innerText: "жизнь 20% удаль 100%" },
  documentElement: { innerHTML: "" },
  querySelectorAll() {
    queryCount += 1;
    return queryCount < 4 ? [] : [healthItem];
  },
};
const root = {
  name: "top",
  location: { href: "https://3kingdoms.ru/main.php" },
  frames: [],
  document: doc,
  setTimeout,
  entry_point_request(scope, action, payload, callback) {
    entryPointCalls.push({ scope, action, payload });
    callback({ ok: true }, { ok: true });
  },
  addEventListener(type, callback) { listeners[type] = callback; },
  removeEventListener() {},
  postMessage(message) { messages.push(message); },
};
const mainFrame = {
  name: "main_frame",
  location: { href: "https://3kingdoms.ru/main_frame.php" },
  frames: [],
  document: doc,
  processMenu() {},
};
root.top = root;
root.window = root;
root.frames = [mainFrame];
root.frames.main_frame = mainFrame;
mainFrame.top = root;
mainFrame.window = mainFrame;

vm.runInNewContext(source, { window: root, console, setTimeout, clearTimeout });

function command(type, payload = {}) {
  messages.length = 0;
  listeners.message({
    source: root,
    data: {
      source: `antibot-cv-content:${version}`,
      token: "token-1",
      command: { type, payload },
    },
  });
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + 4500;
    const poll = () => {
      if (messages.length) {
        resolve({ ok: messages[0].ok, message: JSON.parse(messages[0].message) });
        return;
      }
      if (Date.now() > deadline) {
        reject(new Error("timeout"));
        return;
      }
      setTimeout(poll, 10);
    };
    poll();
  });
}

(async () => {
  const result = await command("open_recovery_item", {
    kind: "health",
    names: ["малый бурдюк жизни"],
    useWhenBelowPercent: 90,
    inventoryOpenDelayMs: 0,
  });
  assert.strictEqual(result.ok, true);
  assert.strictEqual(result.message.message, "recovery_item_clicked");
  assert.strictEqual(result.message.item.artikulId, "3573417035");
  assert.ok(result.message.inventoryWaitMs >= 0);
  assert.ok(queryCount >= 4);
  assert.strictEqual(JSON.stringify(entryPointCalls), JSON.stringify([{ scope: "inventory", action: "useArtifact", payload: { artikul: [{ 3573417035: 1 }] } }]));
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=".",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_page_bridge_reads_recovery_artikul_from_parent_aid_cell() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
const entryPointCalls = [];

const parentCell = {
  tagName: "LI",
  id: "",
  className: "",
  innerText: "",
  textContent: "",
  value: "",
  getAttribute(name) {
    if (name === "aid") return "art_3573417035";
    return null;
  },
  closest(selector) {
    if (selector && selector.includes("aid")) return this;
    return null;
  },
  getBoundingClientRect() { return { left: 77, top: 37, width: 62, height: 62 }; },
};
const healthItem = {
  tagName: "DIV",
  id: "",
  className: "art-item-2",
  innerText: "687",
  textContent: "687",
  value: "",
  getAttribute(name) {
    if (name === "style") return "background-image: url('images/data/artifacts/serburd_hp.gif');";
    return null;
  },
  closest(selector) {
    if (selector && selector.includes("aid")) return parentCell;
    return null;
  },
  getBoundingClientRect() { return { left: 78, top: 38, width: 60, height: 60 }; },
};
const doc = {
  title: "",
  body: { innerText: "жизнь 20% удаль 20%" },
  documentElement: { innerHTML: "" },
  querySelectorAll() { return [healthItem]; },
};
const root = {
  name: "top",
  location: { href: "https://3kingdoms.ru/main.php" },
  frames: [],
  document: doc,
  setTimeout,
  entry_point_request(scope, action, payload, callback) {
    entryPointCalls.push({ scope, action, payload });
    callback({ ok: true }, { ok: true });
  },
  addEventListener(type, callback) { listeners[type] = callback; },
  removeEventListener() {},
  postMessage(message) { messages.push(message); },
};
const mainFrame = {
  name: "main_frame",
  location: { href: "https://3kingdoms.ru/main_frame.php" },
  frames: [],
  document: doc,
  processMenu() {},
};
root.top = root;
root.window = root;
root.frames = [mainFrame];
root.frames.main_frame = mainFrame;
mainFrame.top = root;
mainFrame.window = mainFrame;

vm.runInNewContext(source, { window: root, console, setTimeout, clearTimeout });

function command(type, payload = {}) {
  messages.length = 0;
  listeners.message({
    source: root,
    data: {
      source: `antibot-cv-content:${version}`,
      token: "token-1",
      command: { type, payload },
    },
  });
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + 2500;
    const poll = () => {
      if (messages.length) {
        resolve({ ok: messages[0].ok, message: JSON.parse(messages[0].message) });
        return;
      }
      if (Date.now() > deadline) {
        reject(new Error("timeout"));
        return;
      }
      setTimeout(poll, 10);
    };
    poll();
  });
}

(async () => {
  const result = await command("open_recovery_item", { kind: "health", names: ["малый бурдюк жизни"], useWhenBelowPercent: 90 });
  assert.strictEqual(result.ok, true);
  assert.strictEqual(result.message.method, "entry_point_request");
  assert.strictEqual(result.message.item.id, "art_3573417035");
  assert.strictEqual(result.message.item.aid, "art_3573417035");
  assert.strictEqual(result.message.item.artikulId, "3573417035");
  assert.strictEqual(result.message.requiresConfirm, false);
  assert.strictEqual(JSON.stringify(entryPointCalls), JSON.stringify([{ scope: "inventory", action: "useArtifact", payload: { artikul: [{ 3573417035: 1 }] } }]));
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=".",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_page_bridge_matches_prowess_burdjuk_by_art_alt_title() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
const entryPointCalls = [];

const parentCell = {
  tagName: "LI",
  id: "",
  className: "",
  innerText: "",
  textContent: "",
  value: "",
  getAttribute(name) {
    if (name === "aid") return "art_3573415986";
    return null;
  },
  closest(selector) {
    if (selector && selector.includes("aid")) return this;
    return null;
  },
  getBoundingClientRect() { return { left: 139, top: 37, width: 62, height: 62 }; },
};
const burdjukCell = {
  tagName: "TD",
  id: "",
  className: "",
  innerText: "186",
  textContent: "186",
  value: "",
  getAttribute(name) {
    if (name === "aid") return "3573415986";
    if (name === "cnt") return "186";
    if (name === "div_id") return "AA_3573415986";
    return null;
  },
  closest(selector) {
    if (selector && selector.includes("div_id")) return this;
    if (selector && selector.includes("aid")) return parentCell;
    return null;
  },
  getBoundingClientRect() { return { left: 140, top: 38, width: 60, height: 60 }; },
};
const prowessItem = {
  tagName: "DIV",
  id: "",
  className: "art-item-2",
  innerText: "186",
  textContent: "186",
  value: "",
  getAttribute(name) {
    if (name === "style") return "background-image: url('images/data/artifacts/tks_qst_osobenn_burduk3.gif');";
    return null;
  },
  closest(selector) {
    if (selector && selector.includes("div_id")) return burdjukCell;
    if (selector && selector.includes("aid")) return parentCell;
    return null;
  },
  getBoundingClientRect() { return { left: 140, top: 38, width: 60, height: 60 }; },
};
const doc = {
  title: "",
  body: { innerText: "жизнь 100% удаль 20%" },
  documentElement: { innerHTML: "" },
  querySelectorAll() { return [prowessItem]; },
};
const root = {
  name: "top",
  location: { href: "https://3kingdoms.ru/main.php" },
  frames: [],
  document: doc,
  art_alt: {
    AA_3573415986: {
      title: "Дивный бурдюк удали",
      kind: { value: "Напитки" },
      desc: "Восстанавливает удаль",
    },
  },
  setTimeout,
  entry_point_request(scope, action, payload, callback) {
    entryPointCalls.push({ scope, action, payload });
    callback({ ok: true }, { 3573415986: { status: 0, param_success: { update_swf: 1 } } });
  },
  addEventListener(type, callback) { listeners[type] = callback; },
  removeEventListener() {},
  postMessage(message) { messages.push(message); },
};
const mainFrame = {
  name: "main_frame",
  location: { href: "https://3kingdoms.ru/main_frame.php" },
  frames: [],
  document: doc,
  processMenu() {},
};
root.top = root;
root.window = root;
root.frames = [mainFrame];
root.frames.main_frame = mainFrame;
mainFrame.top = root;
mainFrame.window = mainFrame;

vm.runInNewContext(source, { window: root, console, setTimeout, clearTimeout });

function command(type, payload = {}) {
  messages.length = 0;
  listeners.message({
    source: root,
    data: {
      source: `antibot-cv-content:${version}`,
      token: "token-1",
      command: { type, payload },
    },
  });
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + 2500;
    const poll = () => {
      if (messages.length) {
        resolve({ ok: messages[0].ok, message: JSON.parse(messages[0].message) });
        return;
      }
      if (Date.now() > deadline) {
        reject(new Error("timeout"));
        return;
      }
      setTimeout(poll, 10);
    };
    poll();
  });
}

(async () => {
  const result = await command("open_recovery_item", {
    kind: "prowess",
    names: ["малый бурдюк удали", "бурдюк удали"],
    useWhenBelowPercent: 90,
  });
  assert.strictEqual(result.ok, true);
  assert.strictEqual(result.message.method, "entry_point_request");
  assert.strictEqual(result.message.requiresConfirm, false);
  assert.strictEqual(result.message.item.artikulId, "3573415986");
  assert.strictEqual(result.message.item.artAltTitle, "Дивный бурдюк удали");
  assert.strictEqual(JSON.stringify(entryPointCalls), JSON.stringify([{ scope: "inventory", action: "useArtifact", payload: { artikul: [{ 3573415986: 1 }] } }]));
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=".",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_page_bridge_ignores_restricted_prowess_nectar_when_using_burdjuk() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
const entryPointCalls = [];

function item(id, title, style) {
  return {
    tagName: "DIV",
    id,
    className: "art-item-2",
    innerText: "",
    textContent: "",
    value: "",
    getAttribute(name) {
      if (name === "title") return title;
      if (name === "style") return style;
      return null;
    },
    closest() { return this; },
    getBoundingClientRect() { return { left: 10, top: 10, width: 60, height: 60 }; },
  };
}

const nectar = item(
  "art_3571629602",
  "Превосходный нектар удали",
  "background-image: url('images/data/artifacts/tks_legzelydali7.png');"
);
const burdjuk = item(
  "art_3573415986",
  "Дивный бурдюк удали",
  "background-image: url('images/data/artifacts/tks_qst_osobenn_burduk3.gif');"
);
const doc = {
  title: "",
  body: { innerText: "жизнь 100% удаль 20%" },
  documentElement: { innerHTML: "" },
  querySelectorAll() { return [nectar, burdjuk]; },
};
const root = {
  name: "top",
  location: { href: "https://3kingdoms.ru/main.php" },
  frames: [],
  document: doc,
  setTimeout,
  entry_point_request(scope, action, payload, callback) {
    entryPointCalls.push({ scope, action, payload });
    callback({ ok: true }, { "3573415986": { status: 0 }, sq: "" });
  },
  addEventListener(type, callback) { listeners[type] = callback; },
  removeEventListener() {},
  postMessage(message) { messages.push(message); },
};
const mainFrame = {
  name: "main_frame",
  location: { href: "https://3kingdoms.ru/main_frame.php" },
  frames: [],
  document: doc,
  processMenu() {},
};
root.top = root;
root.window = root;
root.frames = [mainFrame];
root.frames.main_frame = mainFrame;
mainFrame.top = root;
mainFrame.window = mainFrame;

vm.runInNewContext(source, { window: root, console, setTimeout, clearTimeout });

function command(type, payload = {}) {
  messages.length = 0;
  listeners.message({
    source: root,
    data: {
      source: `antibot-cv-content:${version}`,
      token: "token-1",
      command: { type, payload },
    },
  });
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + 2500;
    const poll = () => {
      if (messages.length) {
        resolve({ ok: messages[0].ok, message: JSON.parse(messages[0].message) });
        return;
      }
      if (Date.now() > deadline) {
        reject(new Error("timeout"));
        return;
      }
      setTimeout(poll, 10);
    };
    poll();
  });
}

(async () => {
  const result = await command("open_recovery_item", {
    kind: "prowess",
    names: ["малый бурдюк удали", "бурдюк удали"],
    useWhenBelowPercent: 90,
    inventoryOpenDelayMs: 0,
  });
  assert.strictEqual(result.ok, true);
  assert.strictEqual(result.message.message, "recovery_item_clicked");
  assert.strictEqual(result.message.item.artikulId, "3573415986");
  assert.strictEqual(JSON.stringify(entryPointCalls), JSON.stringify([{ scope: "inventory", action: "useArtifact", payload: { artikul: [{ 3573415986: 1 }] } }]));
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=".",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_page_bridge_rejects_failed_use_artifact_status() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};

const burdjuk = {
  tagName: "DIV",
  id: "art_3573415986",
  className: "art-item-2",
  innerText: "",
  textContent: "",
  value: "",
  getAttribute(name) {
    if (name === "title") return "Дивный бурдюк удали";
    if (name === "style") return "background-image: url('images/data/artifacts/tks_qst_osobenn_burduk3.gif');";
    return null;
  },
  closest() { return this; },
  getBoundingClientRect() { return { left: 10, top: 10, width: 60, height: 60 }; },
};
const doc = {
  title: "",
  body: { innerText: "жизнь 100% удаль 20%" },
  documentElement: { innerHTML: "" },
  querySelectorAll() { return [burdjuk]; },
};
const root = {
  name: "top",
  location: { href: "https://3kingdoms.ru/main.php" },
  frames: [],
  document: doc,
  setTimeout,
  entry_point_request(scope, action, payload, callback) {
    callback({ ok: true }, { "0": { status: -2, error: "restricted", artikul_id: 3573415986 }, sq: "" });
  },
  addEventListener(type, callback) { listeners[type] = callback; },
  removeEventListener() {},
  postMessage(message) { messages.push(message); },
};
const mainFrame = {
  name: "main_frame",
  location: { href: "https://3kingdoms.ru/main_frame.php" },
  frames: [],
  document: doc,
  processMenu() {},
};
root.top = root;
root.window = root;
root.frames = [mainFrame];
root.frames.main_frame = mainFrame;
mainFrame.top = root;
mainFrame.window = mainFrame;

vm.runInNewContext(source, { window: root, console, setTimeout, clearTimeout });

function command(type, payload = {}) {
  messages.length = 0;
  listeners.message({
    source: root,
    data: {
      source: `antibot-cv-content:${version}`,
      token: "token-1",
      command: { type, payload },
    },
  });
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + 2500;
    const poll = () => {
      if (messages.length) {
        resolve({ ok: messages[0].ok, message: JSON.parse(messages[0].message) });
        return;
      }
      if (Date.now() > deadline) {
        reject(new Error("timeout"));
        return;
      }
      setTimeout(poll, 10);
    };
    poll();
  });
}

(async () => {
  const result = await command("open_recovery_item", {
    kind: "prowess",
    names: ["бурдюк удали"],
    useWhenBelowPercent: 90,
    inventoryOpenDelayMs: 0,
  });
  assert.strictEqual(result.ok, false);
  assert.strictEqual(result.message.message, "recovery_item_use_failed");
  assert.strictEqual(result.message.fallbackReason.message, "useArtifact_rejected");
  assert.strictEqual(result.message.fallbackReason.status, -2);
  assert.strictEqual(result.message.fallbackReason.error, "restricted");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=".",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_page_bridge_confirms_embedded_action_form_frame() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
let clicked = false;
const button = {
  tagName: "INPUT",
  innerText: "",
  textContent: "",
  value: "Выполнить",
  getAttribute() { return null; },
  click() { clicked = true; },
};
const frameWin = {
  name: "error",
  location: { href: "https://3kingdoms.ru/action_form.php?x=1" },
  frames: [],
  document: { querySelectorAll() { return [button]; } },
  close() { this.closed = true; },
};
const errorFrame = { style: {}, getAttribute() { return null; } };
const errorDiv = { style: {}, getAttribute() { return null; } };
const root = {
  name: "top",
  location: { href: "https://3kingdoms.ru/main.php" },
  frames: [frameWin],
  document: {
    getElementById(id) {
      if (id === "error") return errorFrame;
      if (id === "error_div") return errorDiv;
      return null;
    },
    querySelectorAll() { return []; },
  },
  setTimeout,
  addEventListener(type, callback) { listeners[type] = callback; },
  removeEventListener() {},
  postMessage(message) { messages.push(message); },
};
root.top = root;
root.window = root;
frameWin.top = root;
frameWin.window = frameWin;

vm.runInNewContext(source, { window: root, console, setTimeout, clearTimeout });
messages.length = 0;
listeners.message({
  source: root,
  data: {
    source: `antibot-cv-content:${version}`,
    token: "token-1",
    command: { type: "confirm_action_form", payload: {} },
  },
});
assert.strictEqual(messages.length, 1);
const result = JSON.parse(messages[0].message);
assert.strictEqual(messages[0].ok, true);
assert.strictEqual(result.message, "action_form_confirmed");
assert.strictEqual(result.path, "top.frames[0]");
assert.strictEqual(clicked, true);
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=".",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_page_bridge_does_not_click_profile_stats_as_recovery_item() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
let profileClicks = 0;

const profileStat = {
  tagName: "A",
  id: "profile-stat",
  className: "",
  innerText: "малый бурдюк жизни",
  textContent: "малый бурдюк жизни",
  value: "",
  getAttribute(name) {
    if (name === "href") return "user_info.php?nick=player1";
    return null;
  },
  closest() { return this; },
  click() { profileClicks += 1; },
};
const doc = {
  title: "",
  body: { innerText: "жизнь 20% удаль 20%" },
  documentElement: { innerHTML: "" },
  querySelectorAll() { return [profileStat]; },
};
const root = {
  name: "top",
  location: { href: "https://3kingdoms.ru/main.php" },
  frames: [],
  document: doc,
  setTimeout,
  addEventListener(type, callback) { listeners[type] = callback; },
  removeEventListener() {},
  postMessage(message) { messages.push(message); },
};
const mainFrame = {
  name: "main_frame",
  location: { href: "https://3kingdoms.ru/main_frame.php" },
  frames: [],
  document: doc,
  processMenu() {},
};
root.top = root;
root.window = root;
root.frames = [mainFrame];
root.frames.main_frame = mainFrame;
mainFrame.top = root;
mainFrame.window = mainFrame;

vm.runInNewContext(source, { window: root, console, setTimeout, clearTimeout });

function command(type, payload = {}) {
  messages.length = 0;
  listeners.message({
    source: root,
    data: {
      source: `antibot-cv-content:${version}`,
      token: "token-1",
      command: { type, payload },
    },
  });
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + 2500;
    const poll = () => {
      if (messages.length) {
        resolve({ ok: messages[0].ok, message: JSON.parse(messages[0].message) });
        return;
      }
      if (Date.now() > deadline) {
        reject(new Error("timeout"));
        return;
      }
      setTimeout(poll, 10);
    };
    poll();
  });
}

(async () => {
  const result = await command("open_recovery_item", {
    kind: "health",
    names: ["малый бурдюк жизни"],
    useWhenBelowPercent: 90,
    inventoryOpenDelayMs: 0,
  });
  assert.strictEqual(profileClicks, 0);
  assert.strictEqual(result.ok, false);
  assert.strictEqual(result.message.message, "recovery_item_missing");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=".",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_page_bridge_can_use_recovery_item_onclick_script() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
let used = false;

const item = {
  tagName: "A",
  id: "burdjuk",
  className: "",
  innerText: "малый бурдюк удали",
  textContent: "малый бурдюк удали",
  value: "",
  getAttribute(name) {
    if (name === "onclick") return "useArtifactForTest()";
    return null;
  },
  closest() { return this; },
  click() {},
};
const doc = {
  title: "",
  body: { innerText: "жизнь 90% удаль 20%" },
  documentElement: { innerHTML: "" },
  querySelectorAll() { return [item]; },
  defaultView: null,
};
const root = {
  name: "top",
  location: { href: "https://3kingdoms.ru/main.php" },
  frames: [],
  document: doc,
  setTimeout,
  useArtifactForTest() { used = true; },
  addEventListener(type, callback) { listeners[type] = callback; },
  removeEventListener() {},
  postMessage(message) { messages.push(message); },
};
doc.defaultView = root;
item.ownerDocument = doc;
const mainFrame = {
  name: "main_frame",
  location: { href: "https://3kingdoms.ru/main_frame.php" },
  frames: [],
  document: doc,
  processMenu() {},
  useArtifactForTest: root.useArtifactForTest,
};
root.top = root;
root.window = root;
root.frames = [mainFrame];
root.frames.main_frame = mainFrame;
mainFrame.top = root;
mainFrame.window = mainFrame;

vm.runInNewContext(source, { window: root, console, setTimeout, clearTimeout });

function command(type, payload = {}) {
  messages.length = 0;
  listeners.message({
    source: root,
    data: {
      source: `antibot-cv-content:${version}`,
      token: "token-1",
      command: { type, payload },
    },
  });
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + 2500;
    const poll = () => {
      if (messages.length) {
        resolve({ ok: messages[0].ok, message: JSON.parse(messages[0].message) });
        return;
      }
      if (Date.now() > deadline) {
        reject(new Error("timeout"));
        return;
      }
      setTimeout(poll, 10);
    };
    poll();
  });
}

(async () => {
  const result = await command("open_recovery_item", {
    kind: "prowess",
    names: ["малый бурдюк удали"],
    useWhenBelowPercent: 90,
  });
  assert.strictEqual(result.ok, true);
  assert.strictEqual(result.message.method, "item_script");
  assert.strictEqual(used, true);
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=".",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
