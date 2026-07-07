from __future__ import annotations

import subprocess


def test_page_bridge_marks_finished_fight_inactive() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];

function makeWindow(name, href) {
  return {
    name,
    location: { href },
    frames: [],
    document: { title: "", querySelectorAll() { return []; } },
  };
}

function setupBridge(finished) {
  const messages = [];
  const listeners = {};
  const root = makeWindow("top", "https://3kingdoms.ru/main.php");
  const ajax = makeWindow("AJAX", "https://3kingdoms.ru/ajax.php");
  const mainFrame = makeWindow("main_frame", "https://3kingdoms.ru/main_frame.php?");
  const hidden = makeWindow("main_hidden", "https://3kingdoms.ru/main_iframe.php");
  const devnull = makeWindow("devnull", "about:blank");
  const fightWin = makeWindow("main", "https://3kingdoms.ru/fight.php?1");

  root.top = root;
  root.window = root;
  root.addEventListener = (type, callback) => { listeners[type] = callback; };
  root.removeEventListener = () => {};
  root.postMessage = (message) => { messages.push(message); };

  root.frames = [ajax, mainFrame];
  root.frames.main_frame = mainFrame;
  mainFrame.frames = [hidden, devnull, fightWin];
  mainFrame.frames.main = fightWin;

  fightWin.fight = {
    model: {
      finished,
      fightState: finished ? 2 : 1,
      oppId: finished ? 0 : 123,
      myTurn: !finished,
      enabledControl: true,
      totalDmg: 40,
      abilities: {
        all: [{ id: -4626, slot: 2, name: "skill" }],
      },
    },
  };
  fightWin.useSkill = (slot) => { fightWin.usedSlot = slot; };

  vm.runInNewContext(source, { window: root, console });

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
    assert.strictEqual(messages.length, 1);
    return {
      ok: messages[0].ok,
      message: JSON.parse(messages[0].message),
      usedSlot: fightWin.usedSlot,
    };
  }

  return { command };
}

const finished = setupBridge(true);
const finishedSnapshot = finished.command("battle_snapshot");
assert.strictEqual(finishedSnapshot.message.hasFight, false);
assert.strictEqual(finishedSnapshot.message.rawHasFight, true);
assert.strictEqual(finishedSnapshot.message.finished, true);
assert.strictEqual(finishedSnapshot.message.useSkillAvailable, false);

const finishedUseSkill = finished.command("use_skill_slot", { slot: 2 });
assert.strictEqual(finishedUseSkill.ok, false);
assert.strictEqual(finishedUseSkill.message.message, "fight_finished");

const active = setupBridge(false);
const activeSnapshot = active.command("battle_snapshot");
assert.strictEqual(activeSnapshot.message.hasFight, true);
assert.strictEqual(activeSnapshot.message.finished, false);
assert.strictEqual(activeSnapshot.message.useSkillAvailable, true);

const activeUseSkill = active.command("use_skill_slot", { slot: 2 });
assert.strictEqual(activeUseSkill.ok, true);
assert.strictEqual(activeUseSkill.usedSlot, 2);
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=".",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_page_bridge_moves_hunt_direction_and_rescans_targets() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
const root = {
  name: "top",
  location: { href: "https://3kingdoms.ru/main.php" },
  frames: [],
  document: { title: "", querySelectorAll() { return []; } },
  addEventListener(type, callback) { listeners[type] = callback; },
  removeEventListener() {},
  postMessage(message) { messages.push(message); },
};
root.top = root;
root.window = root;
root.hunt = {
  model: {
    bots: {
      list: [
        { id: 1, name: "north bot", shortName: "north bot", x: 760, y: 120, fightId: 0, agrforbid: false, isBot: true },
        { id: 2, name: "south bot", shortName: "south bot", lvl: 4, x: 760, y: 1260, fightId: 0, agrforbid: false, isBot: true },
        { id: 3, name: "south named bot[7]", shortName: "south named bot[7]", x: 770, y: 1300, fightId: 0, agrforbid: false, isBot: true },
      ],
    },
  },
  view: {
    width: 750,
    height: 750,
    viewBounds: { x: 0, y: 750, w: 750, h: 750, ap: 0, rp: 0 },
    compass: {
      selected: null,
      setSelected(direction) { this.selected = direction; },
    },
    content: {
      x: 40,
      y: 45,
      onScroll(bounds) { this.lastScroll = { x: bounds.x, y: bounds.y }; },
    },
  },
};
root.getHuntApp = () => root.hunt;

vm.runInNewContext(source, { window: root, console });

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
  assert.strictEqual(messages.length, 1);
  return {
    ok: messages[0].ok,
    message: JSON.parse(messages[0].message),
  };
}

const moved = command("hunt_move_direction", { direction: "SOUTH", margin: 35 });
assert.strictEqual(moved.ok, true);
assert.strictEqual(moved.message.direction, "south");
assert.strictEqual(root.hunt.view.compass.selected, "south");
assert.strictEqual(root.hunt.view.content.lastScroll.x, root.hunt.view.viewBounds.x);
assert.strictEqual(root.hunt.view.content.lastScroll.y, root.hunt.view.viewBounds.y);
assert.strictEqual(moved.message.targetCount, 2);
const levelFromField = moved.message.targets.find((target) => target.botId === 2);
assert.strictEqual(levelFromField.level, 4);
assert.strictEqual(levelFromField.levelSource, "lvl");
const levelFromName = moved.message.targets.find((target) => target.botId === 3);
assert.strictEqual(levelFromName.level, 7);
assert.strictEqual(levelFromName.levelSource, "name_brackets");
const onlyLevelSeven = command("visible_hunt_targets", { margin: 35, allowedLevels: [7] });
assert.strictEqual(onlyLevelSeven.message.targets.length, 1);
assert.strictEqual(onlyLevelSeven.message.targets[0].botId, 3);
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=".",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_page_bridge_layout_wide_keeps_chat_small_and_restores() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
const frameset = {
  tagName: "FRAMESET",
  attrs: { rows: "30,*,260,0" },
  children: [],
  style: {},
  getAttribute(name) { return this.attrs[name] ?? null; },
  setAttribute(name, value) { this.attrs[name] = String(value); },
  removeAttribute(name) { delete this.attrs[name]; },
};
function frame(name) {
  return {
    tagName: "FRAME",
    attrs: { name },
    children: [],
    style: {},
    getAttribute(attr) { return this.attrs[attr] ?? null; },
    setAttribute(attr, value) { this.attrs[attr] = String(value); },
    removeAttribute(attr) { delete this.attrs[attr]; },
  };
}
frameset.children = [frame("AJAX"), frame("main_frame"), frame("chat"), frame("error")];
const root = {
  name: "top",
  location: { href: "https://3kingdoms.ru/main.php" },
  frames: [],
  document: {
    title: "",
    documentElement: { style: {}, getAttribute() { return null; }, setAttribute() {}, removeAttribute() {} },
    body: { style: {}, getAttribute() { return null; }, setAttribute() {}, removeAttribute() {} },
    querySelectorAll(selector) {
      if (selector === "frameset") return [frameset];
      if (selector === "frame,iframe") return frameset.children;
      return [];
    },
  },
  addEventListener(type, callback) { listeners[type] = callback; },
  removeEventListener() {},
  postMessage(message) { messages.push(message); },
};
root.top = root;
root.window = root;

vm.runInNewContext(source, { window: root, console });

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
  assert.strictEqual(messages.length, 1);
  return {
    ok: messages[0].ok,
    message: JSON.parse(messages[0].message),
  };
}

const wide = command("layout", { mode: "wide", chatHeight: 120 });
assert.strictEqual(wide.ok, true);
assert.strictEqual(frameset.attrs.rows, "0,*,120,0");
assert.strictEqual(frameset.children[1].style.height, "100%");
assert.strictEqual(frameset.children[2].style.height, "120px");
assert.strictEqual(frameset.children[0].style.display, "none");

const normal = command("layout", { mode: "normal" });
assert.strictEqual(normal.ok, true);
assert.strictEqual(frameset.attrs.rows, "30,*,260,0");
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=".",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_page_bridge_resource_snapshot_parses_dom_resources() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
const root = {
  name: "top",
  location: { href: "https://3kingdoms.ru/main.php" },
  frames: [],
  document: {
    title: "",
    body: { innerText: "жизнь 52.2% удаль 51.5%" },
    documentElement: { innerHTML: "" },
    querySelectorAll() { return []; },
  },
  addEventListener(type, callback) { listeners[type] = callback; },
  removeEventListener() {},
  postMessage(message) { messages.push(message); },
};
root.top = root;
root.window = root;

vm.runInNewContext(source, { window: root, console });

messages.length = 0;
listeners.message({
  source: root,
  data: {
    source: `antibot-cv-content:${version}`,
    token: "token-1",
    command: { type: "resource_snapshot", payload: {} },
  },
});
assert.strictEqual(messages.length, 1);
const result = JSON.parse(messages[0].message);
assert.strictEqual(messages[0].ok, true);
assert.strictEqual(result.ok, true);
assert.strictEqual(result.healthPercent, 52.2);
assert.strictEqual(result.prowessPercent, 51.5);
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=".",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


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


def test_page_bridge_inventory_snapshot_includes_image_candidates() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
const image = {
  tagName: "IMG",
  id: "slot-image",
  className: "inventory-slot",
  innerText: "",
  textContent: "",
  value: "",
  getAttribute(name) {
    if (name === "src") return "/images/items/burdjuk_udal.png";
    if (name === "style") return "left: 10px; top: 20px;";
    return null;
  },
  closest() { return this; },
  getBoundingClientRect() { return { left: 10, top: 20, width: 32, height: 32 }; },
};
const doc = {
  title: "",
  body: { innerText: "" },
  documentElement: { innerHTML: "" },
  querySelectorAll() { return [image]; },
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
  const result = await command("inventory_snapshot", { names: [], open: false });
  assert.strictEqual(result.ok, true);
  assert.strictEqual(result.message.candidates[0].src, "/images/items/burdjuk_udal.png");
  assert.deepStrictEqual(result.message.candidates[0].rect, { x: 10, y: 20, width: 32, height: 32 });
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


def test_page_bridge_does_not_use_control_quick_recovery_slot_by_src() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
  const messages = [];
  const listeners = {};
  let clicks = 0;
  let slotClicks = 0;
  let chatClicks = 0;
  const unsetEffectCalls = [];
  const slot = {
    tagName: "DIV",
    id: "",
    className: "b-control-items__slots-item",
    innerText: "",
    textContent: "",
    value: "",
    parentElement: null,
    getAttribute(name) {
      if (name === "data-id") return "491";
      if (name === "data-index") return "2";
      return null;
    },
    closest() { return this; },
    click() { slotClicks += 1; },
    getBoundingClientRect() { return { left: 6, top: 242, width: 52, height: 52 }; },
  };
  const image = {
  tagName: "IMG",
  id: "",
  className: "b-control-items__slots-item-picture",
  innerText: "",
  textContent: "",
  value: "",
  getAttribute(name) {
    if (name === "src") return "images/data/artifacts/tks_legzelydali7.png";
    return null;
  },
  parentElement: slot,
  closest(selector) {
    if (selector && selector.includes("slot")) return slot;
    return this;
  },
  click() { clicks += 1; },
	  getBoundingClientRect() { return { left: 8, top: 244, width: 48, height: 48 }; },
	};
	const chatLink = {
	  tagName: "A",
	  id: "",
	  className: "",
	  innerText: "приватное сообщение",
	  textContent: "приватное сообщение",
	  value: "",
	  getAttribute(name) {
	    if (name === "onclick") return "userPrvTag('физалис');return false;";
	    return null;
	  },
	  closest() { return this; },
	  click() { chatClicks += 1; },
	  getBoundingClientRect() { return { left: 180, top: 300, width: 80, height: 20 }; },
	};
const doc = {
  title: "",
      body: { innerText: "жизнь 100% удаль 100%" },
  documentElement: { innerHTML: "" },
  querySelectorAll() { return [image, chatLink]; },
};
const root = {
  name: "top",
  location: { href: "https://3kingdoms.ru/main.php" },
  frames: [],
  document: doc,
  setTimeout,
  unsetEffect(itemId, slotIndex) { unsetEffectCalls.push([itemId, slotIndex]); },
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
	    names: ["малый бурдюк удали"],
	    useWhenBelowPercent: 90,
	    forceUse: true,
	    inventoryOpenDelayMs: 0,
	  });
	  assert.strictEqual(result.ok, false);
	  assert.strictEqual(result.message.message, "recovery_item_missing");
	  assert.deepStrictEqual(unsetEffectCalls, []);
	  assert.strictEqual(clicks, 0);
	  assert.strictEqual(slotClicks, 0);
	  assert.strictEqual(chatClicks, 0);
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


def test_page_bridge_prioritizes_backpack_art_cell_over_control_quick_slot() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
const entryPointCalls = [];
const unsetEffectCalls = [];
const slot = {
  tagName: "DIV",
  id: "",
  className: "b-control-items__slots-item",
  innerText: "",
  textContent: "",
  value: "",
  parentElement: null,
  getAttribute(name) {
    if (name === "data-id") return "491";
    if (name === "data-index") return "2";
    return null;
  },
  closest() { return this; },
  click() { throw new Error("quick slot must not be clicked"); },
  getBoundingClientRect() { return { left: 6, top: 242, width: 52, height: 52 }; },
};
const quickImage = {
  tagName: "IMG",
  id: "",
  className: "b-control-items__slots-item-picture",
  innerText: "",
  textContent: "",
  value: "",
  parentElement: slot,
  getAttribute(name) {
    if (name === "src") return "images/data/artifacts/tks_legzeliehp7.png";
    return null;
  },
  closest(selector) {
    if (selector && selector.includes("slot")) return slot;
    return this;
  },
  click() { throw new Error("quick image must not be clicked"); },
  getBoundingClientRect() { return { left: 8, top: 244, width: 48, height: 48 }; },
};
const backpackItem = {
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
  click() { throw new Error("backpack item should use entry_point_request first"); },
  getBoundingClientRect() { return { left: 10, top: 20, width: 54, height: 54 }; },
};
const doc = {
  title: "",
  body: { innerText: "жизнь 20% удаль 20%" },
  documentElement: { innerHTML: "" },
  querySelectorAll() { return [quickImage, backpackItem]; },
};
const root = {
  name: "top",
  location: { href: "https://3kingdoms.ru/main.php" },
  frames: [],
  document: doc,
  setTimeout,
  unsetEffect(itemId, slotIndex) { unsetEffectCalls.push([itemId, slotIndex]); },
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
  const result = await command("open_recovery_item", {
    kind: "health",
    names: ["малый бурдюк жизни"],
    useWhenBelowPercent: 90,
    forceUse: true,
  });
  assert.strictEqual(result.ok, true);
  assert.strictEqual(result.message.method, "entry_point_request");
  assert.strictEqual(result.message.item.id, "art_3573417035");
  assert.strictEqual(JSON.stringify(entryPointCalls), JSON.stringify([{ scope: "inventory", action: "useArtifact", payload: { artikul: [{ 3573417035: 1 }] } }]));
  assert.deepStrictEqual(unsetEffectCalls, []);
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
