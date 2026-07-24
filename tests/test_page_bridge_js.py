from __future__ import annotations

import subprocess

from tests.node_bridge_harness import run_bridge_cases

def test_page_bridge_preserves_slow_navigator_search_and_classifies_preceding_header() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const moduleNames = [
  "00_core_combat.js",
  "10_hunt_inventory.js",
  "20_hunt_actions.js",
  "30_navigation_death.js",
  "40_state_layout_dispatch.js",
];
const modules = moduleNames.map((name) =>
  fs.readFileSync(`browser_injector/page_bridge_modules/${name}`, "utf8")
);
assert.ok(modules[3].includes("Math.min(15000"));
const source = `(() => {\n${modules.join("\n")}\n})();`;
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
let bodyText = "";
let candidatePresent = false;
let dispatchCount = 0;
let candidateClicks = 0;
const compass = {
  value: "", innerText: "", textContent: "", offsetWidth: 450, offsetHeight: 24,
  getAttribute(name) { return name === "name" ? "compassInput" : null; },
  querySelectorAll() { return []; }, focus() {}, dispatchEvent() { dispatchCount += 1; },
};
const routeButton = {
  value: "Проложить маршрут", innerText: "", textContent: "", offsetWidth: 0, offsetHeight: 0,
  getAttribute() { return null; }, querySelectorAll() { return []; }, click() {},
  getClientRects() { return this.offsetWidth ? [{ width: this.offsetWidth, height: this.offsetHeight }] : []; },
};
const hiddenRouteButton = {
  value: "Проложить маршрут", innerText: "", textContent: "", offsetWidth: 0, offsetHeight: 0,
  getAttribute() { return null; }, querySelectorAll() { return []; }, click() {},
  getClientRects() { return []; },
};
const heading = {
  innerText: "Монстры", textContent: "Монстры", parentElement: null,
  previousElementSibling: null, children: [],
};
const section = { innerText: "", textContent: "", parentElement: null, children: [] };
const candidate = {
  innerText: "Белая Рысь [6]", textContent: "Белая Рысь [6]", parentElement: section,
  previousElementSibling: heading, offsetWidth: 300, offsetHeight: 20, children: [],
  getAttribute() { return null; }, querySelectorAll() { return []; },
  click() {
    candidateClicks += 1;
    compass.value = "Белая Рысь [6]";
    setTimeout(() => {
      routeButton.offsetWidth = 194;
      routeButton.offsetHeight = 24;
      bodyText = "Путь займет 6 переходов";
    }, 250);
  },
};
heading.parentElement = section;
section.children = [heading, candidate];
const document = {
  title: "Навигатор",
  body: { get innerText() { return bodyText; }, get textContent() { return bodyText; } },
  querySelectorAll(selector) {
    if (selector === "input,button") return [compass, routeButton, hiddenRouteButton];
    if (selector === "div,li,a,button,[role='option']") return candidatePresent ? [heading, candidate] : [];
    return [];
  },
};
const root = {
  name: "top", location: { href: "https://3kingdoms.ru/navigator.php" }, frames: [], document, setTimeout,
  Event: class BridgeEvent { constructor(type) { this.type = type; } },
  getComputedStyle(element) {
    return { display: element === routeButton && !routeButton.offsetWidth ? "none" : "block", visibility: "visible" };
  },
  addEventListener(type, callback) { listeners[type] = callback; }, removeEventListener() {},
  postMessage(message) { messages.push(message); },
};
root.top = root;
root.window = root;
vm.runInNewContext(source, { window: root, console, setTimeout, clearTimeout });

async function command(mutationFence = null) {
  messages.length = 0;
  listeners.message({
    source: root,
    data: {
      source: `antibot-cv-content:${version}`,
      token: "slow-navigator-search",
      command: {
        type: "navigator_select_target",
        payload: {
          target: "Белая Рысь [6]", kind: "monster", searchDelayMs: 250, routeDelayMs: 600,
          ...(mutationFence ? { mutationFence } : {}),
        },
      },
    },
  });
  const deadline = Date.now() + 1500;
  while (!messages.length && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 5));
  assert.strictEqual(messages.length, 1);
  return { ok: messages[0].ok, message: JSON.parse(messages[0].message) };
}

(async () => {
  const initial = await command();
  assert.strictEqual(initial.ok, false);
  assert.strictEqual(initial.message.message, "navigator_target_missing_in_section");
  assert.strictEqual(initial.message.inputDispatched, true);
  assert.strictEqual(dispatchCount, 3);

  candidatePresent = true;
  const retry = await command({ profile_id: "profile-a", tab_id: 17, actor_generation: 2, fencing_token: 9 });
  assert.strictEqual(retry.ok, true);
  assert.strictEqual(retry.message.message, "navigator_target_selected");
  assert.strictEqual(retry.message.section, "монстры");
  assert.strictEqual(retry.message.sectionEvidence, "preceding_sibling_header");
  assert.strictEqual(retry.message.inputDispatched, false);
  assert.strictEqual(dispatchCount, 3);
  assert.strictEqual(candidateClicks, 1);
  const stale = await command({ profile_id: "profile-a", tab_id: 17, actor_generation: 2, fencing_token: 8 });
  assert.strictEqual(stale.ok, false);
  assert.strictEqual(stale.message.message, "mutation_fence_stale");
  assert.strictEqual(candidateClicks, 1);
})().catch((error) => { console.error(error); process.exit(1); });
"""
    result = subprocess.run(["node", "-e", script], cwd=".", text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr


def test_page_bridge_treats_positive_ability_ids_as_battle_items() -> None:
    script = r"""
const assert = require("assert");
const vm = require("vm");

const source = BRIDGE_SOURCE;
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
  setTimeout,
};
root.top = root;
root.window = root;
const fightWin = {
  name: "main",
  location: { href: "https://3kingdoms.ru/fight.php?1" },
  frames: [],
  document: { title: "", querySelectorAll() { return []; } },
  fight: {
    model: {
      finished: false,
      fightState: 1,
      oppId: 123,
      myTurn: true,
      enabledControl: true,
      abilities: {
        all: [
          { id: -4626, slot: 2, name: "Возмездие I", ready: true, cooldown: 0 },
          { id: 438, slot: 5, name: "Малый бурдюк жизни", ready: true, cooldown: 0, count: 2 },
        ],
      },
    },
  },
  useSkill(slot) {
    this.usedSlot = slot;
    const entry = this.fight.model.abilities.all.find((candidate) => candidate.slot === slot);
    if (entry && entry.id > 0) entry.count -= 1;
    return true;
  },
};
root.frames = [fightWin];

vm.runInNewContext(source, { window: root, console });

async function command(type, payload = {}) {
  messages.length = 0;
  listeners.message({
    source: root,
    data: {
      source: `antibot-cv-content:${version}`,
      token: "token-1",
      command: { type, payload },
    },
  });
  const deadline = Date.now() + 1000;
  while (!messages.length && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  assert.strictEqual(messages.length, 1);
  return { ok: messages[0].ok, message: JSON.parse(messages[0].message) };
}

;(async () => {
const snapshot = await command("battle_snapshot");
assert.strictEqual(snapshot.message.items.length, 1);
assert.strictEqual(snapshot.message.items[0].id, 438);
assert.strictEqual(snapshot.message.items[0].quantity, 2);

const used = await command("use_battle_item", { kind: "health", slots: [5], verifyDelayMs: 1 });
assert.strictEqual(used.ok, true);
assert.strictEqual(used.message.message, "battle_item_used");
assert.strictEqual(used.message.method, "useSkill");
assert.deepStrictEqual(used.message.args, [5]);
assert.strictEqual(fightWin.usedSlot, 5);
assert.strictEqual(used.message.evidence.itemChanged, true);
assert.strictEqual(used.message.beforeResource.candidates, undefined);
assert.strictEqual(used.message.afterResource.candidates, undefined);
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
"""
    result = run_bridge_cases({"positive_ability_battle_items": script}, timeout=5.0)[
        "positive_ability_battle_items"
    ]
    assert result["ok"] is True, result.get("error")


def test_state_snapshot_reads_player_without_mutating_page() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
let mutationCount = 0;

function element(tag, className, text) {
  return {
    tagName: tag,
    id: "",
    className,
    textContent: text,
    innerText: text,
    parentElement: null,
    getAttribute(name) {
      if (name === "class") return className;
      return null;
    },
    querySelector() { return null; },
    querySelectorAll() { return []; },
  };
}

const hp = element("DIV", "b-control-lvl__hp", "Жизнь 100%");
const mp = element("DIV", "b-control-lvl__mp", "Удаль 87.5%");
const control = element("DIV", "b-control-lvl", "5 v3g45 Жизнь 100% Удаль 87.5% Опыт 12.5% Слава 71.3%");
hp.parentElement = control;
mp.parentElement = control;

const mainDocument = {
  title: "Квесты",
  scripts: [],
  body: { innerText: `Взятые Повторяющиеся Доступные Завершенные ${control.innerText}`, textContent: `Взятые Повторяющиеся Доступные Завершенные ${control.textContent}` },
  documentElement: { innerHTML: control.innerText },
  querySelector(selector) {
    if (selector.includes("control-lvl__hp")) return hp;
    if (selector.includes("control-lvl__mp")) return mp;
    if (selector.includes("control-lvl")) return control;
    return null;
  },
  querySelectorAll(selector) {
    if (selector === "*") return [control, hp, mp];
    return [];
  },
};

const mainWin = {
  name: "main",
  location: { href: "https://3kingdoms.ru/user_quest.php?mode=started" },
  frames: [],
  document: mainDocument,
};
const mainFrame = {
  name: "main_frame",
  location: { href: "https://3kingdoms.ru/main_frame.php" },
  frames: [mainWin],
  document: { title: "", body: { innerText: "" }, documentElement: { innerHTML: "" }, querySelectorAll() { return []; }, querySelector() { return null; } },
  processMenu() { mutationCount += 1; },
};
mainFrame.frames.main = mainWin;
const root = {
  name: "top",
  location: { href: "https://3kingdoms.ru/main.php" },
  frames: [mainFrame],
  document: { title: "", body: { innerText: "" }, documentElement: { innerHTML: "" }, querySelectorAll() { return []; }, querySelector() { return null; } },
  addEventListener(type, callback) { listeners[type] = callback; },
  removeEventListener() {},
  postMessage(message) { messages.push(message); },
  huntAttack() { mutationCount += 1; },
};
root.frames.main_frame = mainFrame;
root.top = root;
root.window = root;
const deepHuntBranch = { nested: { nested: { nested: { value: 1 } } } };
root.getHuntApp = () => ({
  moduleName: "hunt",
  model: { ready: true, botCount: 2, deepHuntBranch },
  controller: { started: true, timeout: 2000, deepHuntBranch },
  deepHuntBranch,
});

vm.runInNewContext(source, { window: root, console });
listeners.message({
  source: root,
  data: {
    source: `antibot-cv-content:${version}`,
    token: "state-token",
    command: { type: "state_snapshot", payload: {} },
  },
});

assert.strictEqual(messages.length, 1);
assert.strictEqual(messages[0].ok, true);
const result = JSON.parse(messages[0].message);
assert.strictEqual(result.schemaVersion, 1);
assert.ok(result.snapshotId);
assert.ok(result.generatedAt);
assert.strictEqual(result.sections.player.status, "available");
assert.strictEqual(result.sections.player.data.name, "v3g45");
assert.strictEqual(result.sections.player.data.level, 5);
assert.strictEqual(result.sections.player.data.xpPercent, 12.5);
assert.strictEqual(result.sections.player.data.hpPercent, 100);
assert.strictEqual(result.sections.player.data.prowessPercent, 87.5);
assert.strictEqual(result.sections.location.data.pageKind, "quests");
assert.strictEqual(result.sections.deathRevive.data.dead, false);
assert.strictEqual(result.sections.hunt.data.hasHunt, true);
assert.ok(JSON.stringify(result.sections.hunt.data).length < 5000);
assert.strictEqual(result.sections.hunt.data.hunt.deepHuntBranch, undefined);
assert.strictEqual(result.sections.quests.status, "available");
assert.strictEqual(result.sections.quests.data.loadStatus, "loaded");
assert.strictEqual(result.sections.quests.data.snapshotId, result.snapshotId);
assert.ok(result.sections.quests.data.navigationRevision);
assert.strictEqual(result.sections.shopInventory.status, "not_loaded");
assert.strictEqual(mutationCount, 0);
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


def test_page_bridge_attack_requires_observed_postcondition() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
const bot = {
  id: 42, name: "Проверочный моб [5]", shortName: "Проверочный моб", level: 5,
  x: 40, y: 40, fightId: 0, agrforbid: false, isBot: true,
};
const hunt = {
  model: { bots: { list: [bot] } },
  view: {
    width: 100, height: 100, viewBounds: { x: 0, y: 0, w: 100, h: 100 },
    content: { x: 0, y: 0, bots: { x: 0, y: 0, children: [] } },
  },
};
const main = {
  name: "main", location: { href: "https://3kingdoms.ru/hunt.php" }, frames: [],
  document: { title: "", querySelectorAll() { return []; } }, hunt,
};
const mainFrame = {
  name: "main_frame", location: { href: "https://3kingdoms.ru/main_frame.php" },
  frames: [main], document: { title: "", querySelectorAll() { return []; } },
};
mainFrame.frames.main = main;
let confirmAttack = true;
const root = {
  name: "top", location: { href: "https://3kingdoms.ru/main.php" }, frames: [mainFrame],
  document: { title: "", querySelectorAll() { return []; } }, setTimeout,
  getHuntApp() { return hunt; },
  huntAttack() { if (confirmAttack) bot.fightId = 777; },
  addEventListener(type, callback) { listeners[type] = callback; }, removeEventListener() {},
  postMessage(message) { messages.push(message); },
};
root.frames.main_frame = mainFrame; root.top = root; root.window = root;
vm.runInNewContext(source, { window: root, console, setTimeout, clearTimeout });

async function command(payload) {
  messages.length = 0;
  listeners.message({
    source: root,
    data: { source: `antibot-cv-content:${version}`, token: "token", command: { type: "attack_visible_bot", payload } },
  });
  const deadline = Date.now() + 2000;
  while (!messages.length && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 5));
  assert.strictEqual(messages.length, 1);
  return { ok: messages[0].ok, message: JSON.parse(messages[0].message) };
}

(async () => {
  const blocked = await command({ confirmed: 1, verifyTimeoutMs: 500 });
  assert.strictEqual(blocked.ok, false);
  assert.strictEqual(blocked.message.message, "target_filter_required");
  assert.strictEqual(bot.fightId, 0);

  const confirmed = await command({ allowedLevels: [5], confirmed: 1, verifyTimeoutMs: 500 });
  assert.strictEqual(confirmed.ok, true);
  assert.strictEqual(confirmed.message.message, "huntAttack_confirmed");
  assert.strictEqual(confirmed.message.target.botId, 42);

  bot.fightId = 0;
  confirmAttack = false;
  const unconfirmed = await command({ allowedLevels: [5], confirmed: 1, verifyTimeoutMs: 500 });
  assert.strictEqual(unconfirmed.ok, false);
  assert.strictEqual(unconfirmed.message.message, "huntAttack_unconfirmed");
})().catch((error) => { console.error(error); process.exit(1); });
"""
    result = subprocess.run(["node", "-e", script], cwd=".", text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr


def test_page_bridge_open_hunt_requires_observed_navigation() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
const main = {
  name: "main", location: { href: "https://3kingdoms.ru/area.php" }, frames: [],
  document: { title: "", querySelectorAll() { return []; } },
};
let confirmNavigation = true;
const mainFrame = {
  name: "main_frame", location: { href: "https://3kingdoms.ru/main_frame.php" },
  frames: [main], document: { title: "", querySelectorAll() { return []; } },
  processMenu(id) {
    assert.strictEqual(id, "b07");
    if (confirmNavigation) main.location.href = "https://3kingdoms.ru/hunt.php?update_swf=1";
  },
};
mainFrame.frames.main = main;
const root = {
  name: "top", location: { href: "https://3kingdoms.ru/main.php" }, frames: [mainFrame],
  document: { title: "", querySelectorAll() { return []; } }, setTimeout,
  addEventListener(type, callback) { listeners[type] = callback; }, removeEventListener() {},
  postMessage(message) { messages.push(message); },
};
root.frames.main_frame = mainFrame; root.top = root; root.window = root;
vm.runInNewContext(source, { window: root, console, setTimeout, clearTimeout });

async function command() {
  messages.length = 0;
  listeners.message({
    source: root,
    data: {
      source: `antibot-cv-content:${version}`, token: "token",
      command: { type: "open_hunt", payload: { verifyTimeoutMs: 250 } },
    },
  });
  const deadline = Date.now() + 1500;
  while (!messages.length && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 5));
  assert.strictEqual(messages.length, 1);
  return { ok: messages[0].ok, message: JSON.parse(messages[0].message) };
}

(async () => {
  const confirmed = await command();
  assert.strictEqual(confirmed.ok, true);
  assert.strictEqual(confirmed.message.message, "processMenu_b07_confirmed");

  main.location.href = "https://3kingdoms.ru/area.php";
  confirmNavigation = false;
  const unconfirmed = await command();
  assert.strictEqual(unconfirmed.ok, false);
  assert.strictEqual(unconfirmed.message.message, "processMenu_b07_unconfirmed");
})().catch((error) => { console.error(error); process.exit(1); });
"""
    result = subprocess.run(["node", "-e", script], cwd=".", text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr
