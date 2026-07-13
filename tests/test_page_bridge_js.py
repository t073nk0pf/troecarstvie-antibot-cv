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

function setupBridge(finished, resultText = null, resourceText = "") {
  const messages = [];
  const listeners = {};
  const root = makeWindow("top", "https://3kingdoms.ru/main.php");
  const ajax = makeWindow("AJAX", "https://3kingdoms.ru/ajax.php");
  const mainFrame = makeWindow("main_frame", "https://3kingdoms.ru/main_frame.php?");
  const hidden = makeWindow("main_hidden", "https://3kingdoms.ru/main_iframe.php");
  const devnull = makeWindow("devnull", "about:blank");
  const fightWin = makeWindow("main", "https://3kingdoms.ru/fight.php?1");
  fightWin.document.body = {
    innerText: resultText == null ? (finished ? "БОЙ ОКОНЧЕН ВЫ ПОБЕДИЛИ!" : "") : resultText,
  };
  mainFrame.document.body = { innerText: resourceText };

  root.top = root;
  root.window = root;
  root.setTimeout = setTimeout;
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
  fightWin.useSkill = (slot) => {
    fightWin.usedSlot = slot;
    fightWin.fight.model.totalDmg += 1;
    return true;
  };

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
    const deadline = Date.now() + 1500;
    while (!messages.length && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
    assert.strictEqual(messages.length, 1);
    return {
      ok: messages[0].ok,
      message: JSON.parse(messages[0].message),
      usedSlot: fightWin.usedSlot,
    };
  }

  return { command };
}

;(async () => {
const finished = setupBridge(true);
const finishedSnapshot = await finished.command("battle_snapshot");
assert.strictEqual(finishedSnapshot.message.hasFight, false);
assert.strictEqual(finishedSnapshot.message.rawHasFight, true);
assert.strictEqual(finishedSnapshot.message.finished, true);
assert.strictEqual(finishedSnapshot.message.outcome, "victory");
assert.strictEqual(finishedSnapshot.message.useSkillAvailable, false);

const finishedAlive = setupBridge(true, "БОЙ ОКОНЧЕН", "Жизнь 75% Удаль 40%");
const finishedAliveSnapshot = await finishedAlive.command("battle_snapshot");
assert.strictEqual(finishedAliveSnapshot.message.outcome, "victory");
assert.strictEqual(finishedAliveSnapshot.message.outcomeEvidence, "finished_player_alive");

const finishedDead = setupBridge(true, "БОЙ ОКОНЧЕН", "Жизнь 0% Удаль 40%");
const finishedDeadSnapshot = await finishedDead.command("battle_snapshot");
assert.strictEqual(finishedDeadSnapshot.message.outcome, "defeat");
assert.strictEqual(finishedDeadSnapshot.message.outcomeEvidence, "finished_player_health_zero");

const finishedUseSkill = await finished.command("use_skill_slot", { slot: 2 });
assert.strictEqual(finishedUseSkill.ok, false);
assert.strictEqual(finishedUseSkill.message.message, "fight_finished");

const active = setupBridge(false);
const activeSnapshot = await active.command("battle_snapshot");
assert.strictEqual(activeSnapshot.message.hasFight, true);
assert.strictEqual(activeSnapshot.message.finished, false);
assert.strictEqual(activeSnapshot.message.useSkillAvailable, true);

const activeUseSkill = await active.command("use_skill_slot", { slot: 2, verifyTimeoutMs: 250 });
assert.strictEqual(activeUseSkill.ok, true);
assert.strictEqual(activeUseSkill.usedSlot, 2);
assert.strictEqual(activeUseSkill.message.message, "useSkill_confirmed");
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

    assert result.returncode == 0, result.stdout + result.stderr


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

async function command() {
  messages.length = 0;
  listeners.message({
    source: root,
    data: {
      source: `antibot-cv-content:${version}`,
      token: "slow-navigator-search",
      command: {
        type: "navigator_select_target",
        payload: { target: "Белая Рысь [6]", kind: "monster", searchDelayMs: 250, routeDelayMs: 600 },
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
  const retry = await command();
  assert.strictEqual(retry.ok, true);
  assert.strictEqual(retry.message.message, "navigator_target_selected");
  assert.strictEqual(retry.message.section, "монстры");
  assert.strictEqual(retry.message.sectionEvidence, "preceding_sibling_header");
  assert.strictEqual(retry.message.inputDispatched, false);
  assert.strictEqual(dispatchCount, 3);
  assert.strictEqual(candidateClicks, 1);
})().catch((error) => { console.error(error); process.exit(1); });
"""
    result = subprocess.run(["node", "-e", script], cwd=".", text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr


def test_page_bridge_reports_and_uses_battle_items_by_slot_or_name() -> None:
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
  setTimeout,
};
root.top = root;
root.window = root;
const fightWin = {
  name: "main",
  location: { href: "https://3kingdoms.ru/fight.php?1" },
  frames: [],
  fight: {
    model: {
      finished: false,
      fightState: 1,
      oppId: 123,
      myTurn: true,
      enabledControl: true,
      abilities: { all: [{ id: -4626, slot: 2, name: "skill" }] },
      items: [
        { id: 101, slot: 5, name: "Малый бурдюк жизни", ready: true, cooldown: 0, count: 2 },
        { id: 102, slot: 6, name: "Малый бурдюк удали", ready: true, disabled: false, cooldown: 0, count: 2 },
      ],
    },
  },
  useItemSlot(slot) {
    this.usedItemSlot = slot;
    const item = this.fight.model.items.find((candidate) => candidate.slot === slot);
    item.count -= 1;
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
assert.strictEqual(snapshot.ok, true);
assert.strictEqual(snapshot.message.items.length, 2);
assert.deepStrictEqual(snapshot.message.items.map((item) => item.name), [
  "Малый бурдюк жизни",
  "Малый бурдюк удали",
]);

const bySlot = await command("use_battle_item", { kind: "health", slots: [5], verifyDelayMs: 1 });
assert.strictEqual(bySlot.ok, true);
assert.strictEqual(bySlot.message.message, "battle_item_used");
assert.strictEqual(bySlot.message.item.name, "Малый бурдюк жизни");
assert.strictEqual(bySlot.message.method, "useItemSlot");
assert.deepStrictEqual(bySlot.message.args, [5]);
assert.strictEqual(fightWin.usedItemSlot, 5);
assert.strictEqual(bySlot.message.evidence.itemChanged, true);

const byName = await command("use_battle_item", { kind: "prowess", names: ["бурдюк удали"], verifyDelayMs: 1 });
assert.strictEqual(byName.ok, true);
assert.strictEqual(byName.message.item.slot, 6);
assert.strictEqual(byName.message.method, "useItemSlot");
assert.deepStrictEqual(byName.message.args, [6]);
assert.strictEqual(fightWin.usedItemSlot, 6);
assert.strictEqual(byName.message.evidence.itemChanged, true);

fightWin.fight.model.items[0].ready = false;
const unavailable = await command("use_battle_item", { kind: "health", slots: [5], verifyDelayMs: 1 });
assert.strictEqual(unavailable.ok, false);
assert.strictEqual(unavailable.message.message, "battle_item_not_ready");
assert.strictEqual(fightWin.fight.model.items[0].count, 1);
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

    assert result.returncode == 0, result.stdout + result.stderr


def test_page_bridge_treats_positive_ability_ids_as_battle_items() -> None:
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
    result = subprocess.run(
        ["node", "-e", script],
        cwd=".",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


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


def test_page_bridge_marks_cancelable_quest_as_active_combat_observation() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
const title = { innerText: "Охота на волка", textContent: "Охота на волка" };
const routeMarker = { getAttribute(name) { return name === "alt" ? "Проложить путь" : null; } };
const route = {
  textContent: "Лесная опушка", innerText: "Лесная опушка",
  getAttribute(name) { return name === "title" ? "Проложить путь" : name === "href" ? "#" : null; },
  querySelectorAll(selector) { return selector === "img" ? [routeMarker] : []; },
};
const container = {
  innerText: "Охота на волка Текущая цель: Убить Волка 3/5 Награда: опыт Отказаться",
  textContent: "Охота на волка Текущая цель: Убить Волка 3/5 Награда: опыт Отказаться",
  querySelector(selector) { return selector === ".npc-point__title" ? title : null; },
  querySelectorAll(selector) { return selector === "a" ? [route] : []; },
};
const cancel = {
  parentElement: container,
  getAttribute(name) { return name === "href" ? "user_quest.php?action=cancel&ref=91" : null; },
  closest(selector) { return selector === "table" ? container : null; },
};
const activePageLink = {
  getAttribute(name) { return name === "href" ? "user_quest.php?mode=started&page=1" : null; },
};
const document = {
  title: "Квесты",
  body: { innerText: `Взятые Повторяющиеся Доступные Завершенные ${container.innerText}`, textContent: `Взятые Повторяющиеся Доступные Завершенные ${container.textContent}` },
  querySelector() { return null; },
  querySelectorAll(selector) {
    if (selector.includes("action=cancel")) return [cancel];
    if (selector.includes("user_quest.php") && selector.includes("page=")) return [activePageLink];
    if (selector === "*") return [];
    return [];
  },
};
const root = {
  name: "top", location: { href: "https://3kingdoms.ru/user_quest.php?mode=started" },
  frames: [], document, setTimeout,
  addEventListener(type, callback) { listeners[type] = callback; }, removeEventListener() {},
  postMessage(message) { messages.push(message); },
};
root.top = root; root.window = root;
vm.runInNewContext(source, { window: root, console, setTimeout, clearTimeout });
listeners.message({
  source: root,
  data: {
    source: `antibot-cv-content:${version}`, token: "quests",
    command: { type: "state_snapshot", payload: { include: ["quests"] } },
  },
});
assert.strictEqual(messages.length, 1);
const result = JSON.parse(messages[0].message);
const section = result.sections.quests;
assert.strictEqual(section.data.loadStatus, "loaded");
assert.strictEqual(section.data.snapshotId, result.snapshotId);
assert.strictEqual(section.data.activeCount, 1);
assert.strictEqual(section.data.pageCount, 2);
assert.strictEqual(section.data.hasNextPage, true);
assert.strictEqual(section.data.items[0].id, "91");
assert.strictEqual(section.data.items[0].status, "active");
assert.strictEqual(section.data.items[0].objectiveKind, "combat");
assert.strictEqual(section.data.items[0].progress.current, 3);
assert.strictEqual(section.data.items[0].progress.required, 5);
assert.strictEqual(section.data.items[0].progress.complete, false);
assert.strictEqual(section.data.items[0].progress.evidence, "objective_ratio");
assert.strictEqual(section.data.items[0].navigation[0].text, "Лесная опушка");
"""
    result = subprocess.run(["node", "-e", script], cwd=".", text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr


def test_page_bridge_reads_available_quest_giver_route_and_pagination() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
const title = { innerText: "Заблудшие враги", textContent: "Заблудшие враги" };
const marker = { getAttribute(name) { return name === "alt" ? "Проложить путь" : null; } };
const route = {
  textContent: "Лес призраковПроложить путь", innerText: "Лес призраковПроложить путь",
  getAttribute(name) { return name === "href" ? "#" : null; },
  querySelectorAll(selector) { return selector === "img" ? [marker] : []; },
};
const giver = {
  textContent: "Хранитель леса Франк", innerText: "Хранитель леса Франк",
  getAttribute(name) { return name === "href" ? "/info/library/index.php?obj=cat&id=46&page=5" : null; },
  querySelectorAll() { return []; },
};
const cardText = "Заблудшие враги Награда: Опыт: 6000. Местоположение: В Лесу призраков у Хранителя леса Франка.";
const card = {
  innerText: cardText, textContent: cardText,
  querySelector(selector) { return selector === ".npc-point__title" ? title : null; },
  querySelectorAll(selector) {
    if (selector === "a") return [route, giver];
    if (selector === "a[href]") return [route, giver];
    if (selector.includes("info/library")) return [giver];
    return [];
  },
};
const pageLink = { getAttribute(name) { return name === "href" ? "user_quest.php?mode=avail&page=2" : null; } };
const document = {
  title: "Квесты", body: { innerText: `Взятые Повторяющиеся Доступные Завершенные ${cardText}`, textContent: `Взятые Повторяющиеся Доступные Завершенные ${cardText}` },
  querySelectorAll(selector) {
    if (selector.includes("action=cancel")) return [];
    if (selector === ".npc-point") return [card];
    if (selector.includes("user_quest.php") && selector.includes("page=")) return [pageLink];
    return [];
  },
};
const root = {
  name: "top", location: { href: "https://3kingdoms.ru/user_quest.php?mode=avail&page=0" },
  frames: [], document,
  addEventListener(type, callback) { listeners[type] = callback; }, removeEventListener() {},
  postMessage(message) { messages.push(message); },
};
root.top = root; root.window = root;
vm.runInNewContext(source, { window: root, console });
listeners.message({ source: root, data: {
  source: `antibot-cv-content:${version}`, token: "available-quests",
  command: { type: "state_snapshot", payload: { include: ["quests"] } },
} });
assert.strictEqual(messages.length, 1);
const result = JSON.parse(messages[0].message).sections.quests.data;
assert.strictEqual(result.mode, "avail");
assert.strictEqual(result.availableCount, 1);
assert.strictEqual(result.pageCount, 3);
assert.strictEqual(result.currentPage, 0);
assert.strictEqual(result.hasNextPage, true);
assert.strictEqual(result.nextPageHref, null);
assert.deepStrictEqual(result.catalogPages.map((entry) => entry.page), [0, 2]);
assert.strictEqual(result.catalogPages[0].current, true);
assert.strictEqual(result.catalogPages[1].href, "https://3kingdoms.ru/user_quest.php?mode=avail&page=2");
assert.strictEqual(result.items[0].status, "available");
assert.strictEqual(result.items[0].title, "Заблудшие враги");
assert.strictEqual(result.items[0].navigation[0].text, "Лес призраков");
assert.strictEqual(result.items[0].navigation[0].href, null);
assert.deepStrictEqual(result.items[0].giverNames, ["Хранитель леса Франк"]);
assert.deepStrictEqual(result.items[0].giverLinks, [{
  name: "Хранитель леса Франк",
  href: "https://3kingdoms.ru/info/library/index.php?obj=cat&id=46&page=5",
}]);
assert.strictEqual(result.items[0].catalogPage, 0);
assert.strictEqual(result.items[0].cardIndex, 0);
assert.strictEqual(result.items[0].reward, "Опыт: 6000.");
"""
    result = subprocess.run(["node", "-e", script], cwd=".", text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr


def test_page_bridge_available_quest_snapshot_reads_live_card_id_description_and_next_page() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
const title = { innerText: "Письмо дозорному", textContent: "Письмо дозорному" };
const description = { innerText: "Поговорить с Дозорным и передать письмо.", textContent: "Поговорить с Дозорным и передать письмо." };
const folding = {
  getAttribute(name) { return name === "onclick" ? "quest_folding.toggle(314);" : null; },
};
const detail = { id: "quest_314" };
const card = {
  innerText: "Письмо дозорному Текущая цель: Поговорить с Дозорным Награда: 900 опыта Местоположение: Южная застава",
  textContent: "Письмо дозорному Текущая цель: Поговорить с Дозорным Награда: 900 опыта Местоположение: Южная застава",
  querySelector(selector) {
    if (selector === ".npc-point__title") return title;
    if (selector === ".npc-quest-description") return description;
    if (selector === "[id^='quest_']") return detail;
    return null;
  },
  querySelectorAll(selector) {
    if (selector.includes("quest_folding.toggle")) return [folding];
    return [];
  },
};
const nextPage = { getAttribute(name) { return name === "href" ? "/user_quest.php?mode=avail&page=2" : null; } };
const document = {
  title: "Квесты", body: { innerText: `Взятые Повторяющиеся Доступные Завершенные ${card.innerText}`, textContent: `Взятые Повторяющиеся Доступные Завершенные ${card.textContent}` },
  querySelectorAll(selector) {
    if (selector.includes("action=cancel")) return [];
    if (selector === ".npc-point") return [card];
    if (selector.includes("user_quest.php") && selector.includes("page=")) return [nextPage];
    return [];
  },
};
const root = {
  name: "top", location: { href: "https://3kingdoms.ru/user_quest.php?mode=avail&page=1" },
  frames: [], document,
  addEventListener(type, callback) { listeners[type] = callback; }, removeEventListener() {},
  postMessage(message) { messages.push(message); },
};
root.top = root; root.window = root;
vm.runInNewContext(source, { window: root, console });
listeners.message({ source: root, data: {
  source: `antibot-cv-content:${version}`, token: "available-quest-actions",
  command: { type: "state_snapshot", payload: { include: ["quests"] } },
} });
const result = JSON.parse(messages[0].message).sections.quests.data;
assert.strictEqual(result.currentPage, 1);
assert.strictEqual(result.nextPageHref, "https://3kingdoms.ru/user_quest.php?mode=avail&page=2");
assert.deepStrictEqual(result.catalogPages.map((entry) => entry.page), [1, 2]);
assert.strictEqual(result.items[0].id, "314");
assert.strictEqual(result.items[0].idSource, "numeric_dom");
assert.strictEqual(result.items[0].description, "Поговорить с Дозорным и передать письмо.");
assert.strictEqual(result.items[0].objectiveKind, "dialogue");
"""
    result = subprocess.run(["node", "-e", script], cwd=".", text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr


def test_page_bridge_opens_exact_quest_catalog_page_and_rejects_out_of_range_page() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
const document = {
  title: "Квесты", body: { innerText: "Взятые Повторяющиеся Доступные Завершенные", textContent: "Взятые Повторяющиеся Доступные Завершенные" },
  querySelectorAll() { return []; },
};
const root = {
  name: "top", location: { href: "https://3kingdoms.ru/user_quest.php?mode=started" },
  frames: [], document,
  setTimeout(callback) { callback(); },
  addEventListener(type, callback) { listeners[type] = callback; }, removeEventListener() {},
  postMessage(message) { messages.push(message); },
};
root.top = root; root.window = root;
vm.runInNewContext(source, { window: root, console });
listeners.message({ source: root, data: {
  source: `antibot-cv-content:${version}`, token: "catalog-open",
  command: { type: "open_quest_catalog", payload: { page: 2, verifyTimeoutMs: 250 } },
} });
setImmediate(() => {
  const opened = JSON.parse(messages[0].message);
  assert.strictEqual(opened.ok, true);
  assert.strictEqual(opened.message, "quest_catalog_opened_confirmed");
  assert.strictEqual(opened.page, 2);
  assert.strictEqual(opened.after.mode, "avail");
  assert.strictEqual(opened.after.page, 2);
  listeners.message({ source: root, data: {
    source: `antibot-cv-content:${version}`, token: "active-open",
    command: { type: "open_active_quest_page", payload: { page: 1, verifyTimeoutMs: 250 } },
  } });
  setImmediate(() => {
  const active = JSON.parse(messages[1].message);
  assert.strictEqual(active.ok, true);
  assert.strictEqual(active.message, "quest_active_opened_confirmed");
  assert.strictEqual(active.after.mode, "started");
  assert.strictEqual(active.after.page, 1);
  listeners.message({ source: root, data: {
    source: `antibot-cv-content:${version}`, token: "catalog-invalid",
    command: { type: "open_quest_catalog", payload: { page: 101 } },
  } });
  setImmediate(() => {
    const rejected = JSON.parse(messages[2].message);
    assert.strictEqual(rejected.ok, false);
    assert.strictEqual(rejected.message, "quest_catalog_page_invalid");
    for (const invalidPage of ["2", null, true]) {
      listeners.message({ source: root, data: {
        source: `antibot-cv-content:${version}`, token: `catalog-invalid-${String(invalidPage)}`,
        command: { type: "open_quest_catalog", payload: { page: invalidPage } },
      } });
    }
    setImmediate(() => {
      for (const message of messages.slice(3)) {
        const invalid = JSON.parse(message.message);
        assert.strictEqual(invalid.ok, false);
        assert.strictEqual(invalid.message, "quest_catalog_page_invalid");
      }
    });
  });
  });
});
"""
    result = subprocess.run(["node", "-e", script], cwd=".", text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr


def test_page_bridge_opens_only_snapshot_bound_exact_npc() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
let clicks = 0;
let questClicks = 0;
let answerClicks = 0;
let acceptClicks = 0;
const shell = {};
const npcElement = {
  tagName: "SPAN",
  innerText: "Моряк Кентур",
  textContent: "Моряк Кентур",
  offsetWidth: 40,
  offsetHeight: 20,
  getClientRects() { return [{ width: 40, height: 20 }]; },
  getAttribute(name) {
    if (name === "title") return "Моряк Кентур";
    if (name === "data-id") return "6";
    if (name === "data-index") return "0";
    return null;
  },
  click() {
    clicks += 1;
    root.location.href = "https://3kingdoms.ru/npc.php?action=enter&ref=540&secret-token";
    root.document = npcDocument;
  },
};
const areaDocument = {
  title: "Порт",
  readyState: "complete",
  body: { innerText: "Порт безбрежного моря\nЦарство: Свет", textContent: "Порт безбрежного моря Царство: Свет" },
  querySelector(selector) {
    if (selector === ".b-control-area__list,.b-control-area") return shell;
    return null;
  },
  querySelectorAll(selector) {
    if (selector === ".b-control-area__list-item.npc") return [npcElement];
    return [];
  },
};
const header = { innerText: "Моряк Кентур", textContent: "Моряк Кентур" };
const questContainer = { innerText: "Письмо моряку Далее", textContent: "Письмо моряку Далее" };
const questAction = {
  tagName: "A", innerText: "Далее", textContent: "Далее", disabled: false,
  getAttribute(name) {
    if (name === "href") return "npc.php?f_id=6&npc_id=75&global_npc=0&quest_id=314&secret";
    return null;
  },
  getClientRects() { return [{ width: 20, height: 10 }]; },
  closest() { return questContainer; },
  click() {
    questClicks += 1;
    root.location.href = "https://3kingdoms.ru/npc.php?f_id=6&npc_id=75&quest_id=314&point_id=400";
    root.document = detailDocument;
  },
};
const detailTitle = { innerText: "Письмо моряку", textContent: "Письмо моряку" };
const answerAction = {
  tagName: "TABLE",
  innerText: "Я доставлю письмо.",
  textContent: "Я доставлю письмо.",
  disabled: false,
  getAttribute(name) {
    if (name === "onclick") return "location.href='npc.php?f_id=6&npc_id=75&quest_id=314&point_id=400&action=answer&ref=401&secret'";
    return null;
  },
  getClientRects() { return [{ width: 100, height: 30 }]; },
  closest() { return this; },
  click() {
    answerClicks += 1;
    root.location.href = "https://3kingdoms.ru/npc.php?f_id=6&npc_id=75&quest_id=314&point_id=400&action=answer&ref=401";
    root.document = terminalDocument;
  },
};
const acceptImage = {
  getAttribute(name) { return name === "alt" ? "Взять задание" : null; },
};
const acceptForm = {
  action: "npc.php?f_id=6&npc_id=75&quest_id=314&point_id=400&action=done&secret",
  getAttribute(name) { return name === "action" ? this.action : null; },
};
const acceptButton = {
  tagName: "BUTTON", innerText: "", textContent: "", disabled: false, form: acceptForm,
  getAttribute() { return null; },
  getClientRects() { return [{ width: 100, height: 30 }]; },
  querySelector(selector) { return selector === "img[alt]" ? acceptImage : null; },
  closest() { return acceptForm; },
  click() { acceptClicks += 1; },
};
const terminalDocument = {
  title: "Письмо моряку",
  readyState: "complete",
  body: { innerText: "Письмо моряку Моряк Кентур Ваша цель: доставить письмо", textContent: "" },
  querySelectorAll(selector) {
    if (selector === "h2") return [header, detailTitle];
    if (selector === "a[href],button,input[type='button'],input[type='submit'],[onclick]") return [acceptButton];
    return [];
  },
};
const detailDocument = {
  title: "Письмо моряку",
  readyState: "complete",
  body: { innerText: "Письмо моряку Моряк Кентур Я доставлю письмо.", textContent: "" },
  querySelectorAll(selector) {
    if (selector === "h2") return [header, detailTitle];
    if (selector === "a[href],button,input[type='button'],input[type='submit'],[onclick]") return [answerAction];
    return [];
  },
};
const npcDocument = {
  title: "Моряк Кентур",
  readyState: "complete",
  body: { innerText: "Моряк Кентур", textContent: "Моряк Кентур" },
  querySelectorAll(selector) {
    if (selector === "h2") return [header];
    if (selector === "a[href],button,input[type='button'],input[type='submit'],[onclick]") return [questAction];
    return [];
  },
};
const root = {
  name: "top",
  location: { href: "https://3kingdoms.ru/area.php?location_id=125" },
  frames: [],
  document: areaDocument,
  area: { model: { area: { title: "Порт безбрежного моря" } }, controller: { compass: { data: { location: 125 } } } },
  setTimeout,
  addEventListener(type, callback) { listeners[type] = callback; },
  removeEventListener() {},
  postMessage(message) { messages.push(message); },
};
root.top = root; root.window = root;
vm.runInNewContext(source, { window: root, console, setTimeout });
async function command(type, payload = {}) {
  messages.length = 0;
  listeners.message({ source: root, data: {
    source: `antibot-cv-content:${version}`, token: `${type}-token`, command: { type, payload },
  } });
  const deadline = Date.now() + 1500;
  while (!messages.length && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 5));
  assert.strictEqual(messages.length, 1);
  return { ok: messages[0].ok, message: JSON.parse(messages[0].message) };
}
;(async () => {
  const observed = await command("area_npc_snapshot", { expectedName: "Моряк Кентур" });
  assert.strictEqual(observed.ok, true);
  assert.strictEqual(observed.message.location.id, "125");
  assert.strictEqual(observed.message.items[0].actionable, true);

  const stale = await command("open_exact_npc", {
    expectedSnapshotId: "wrong", expectedLocationId: "125", npcId: "6", expectedName: "Моряк Кентур",
  });
  assert.strictEqual(stale.ok, false);
  assert.strictEqual(stale.message.message, "area_npc_snapshot_stale");
  assert.strictEqual(clicks, 0);

  const opened = await command("open_exact_npc", {
    expectedSnapshotId: observed.message.snapshotId,
    expectedLocationId: "125",
    npcId: "6",
    expectedName: "Моряк Кентур",
    expectedDialogName: "Моряка Кентура",
    verifyTimeoutMs: 250,
  });
  assert.strictEqual(opened.ok, true);
  assert.strictEqual(opened.message.message, "npc_opened_confirmed");
  assert.strictEqual(clicks, 1);
  assert.strictEqual(opened.message.observed.identityMatches, true);
  assert.strictEqual(opened.message.observed.questActions[0].questId, "314");

  const submitted = await command("npc_quest_action", {
    expectedSnapshotId: opened.message.observed.snapshotId,
    npcId: "6",
    questId: "314",
    expectedTitle: "Письмо моряку",
    action: "open",
  });
  assert.strictEqual(submitted.ok, true);
  assert.strictEqual(submitted.message.message, "npc_quest_action_submitted");
  assert.strictEqual(questClicks, 1);

  const detail = await command("npc_dialog_snapshot", { expectedName: "Моряк Кентур", expectedNpcId: "6" });
  assert.strictEqual(detail.ok, true);
  assert.strictEqual(detail.message.dialogActions.length, 1);
  assert.strictEqual(detail.message.dialogActions[0].ref, "401");
  const answered = await command("npc_quest_action", {
    expectedSnapshotId: detail.message.snapshotId,
    npcId: "6",
    questId: "314",
    expectedTitle: "Письмо моряку",
    action: "answer",
    expectedRef: "401",
    expectedText: "Я доставлю письмо.",
  });
  assert.strictEqual(answered.ok, true);
  assert.strictEqual(answerClicks, 1);
  const terminal = await command("npc_dialog_snapshot", { expectedName: "Моряк Кентур", expectedNpcId: "6" });
  assert.strictEqual(terminal.message.acceptActions.length, 1);
  assert.strictEqual(terminal.message.acceptActions[0].text, "Взять задание");
  const accepted = await command("npc_quest_action", {
    expectedSnapshotId: terminal.message.snapshotId,
    npcId: "6",
    questId: "314",
    expectedTitle: "Письмо моряку",
    action: "accept",
    expectedText: "Взять задание",
  });
  assert.strictEqual(accepted.ok, true);
  assert.strictEqual(acceptClicks, 1);
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(["node", "-e", script], cwd=".", text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr


def test_page_bridge_does_not_treat_quest_url_with_loading_dom_as_loaded_catalog() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
const document = {
  title: "Квесты",
  readyState: "complete",
  body: { innerText: "Загрузка...", textContent: "Загрузка..." },
  querySelectorAll() { return []; },
};
const root = {
  name: "top", location: { href: "https://3kingdoms.ru/user_quest.php?mode=avail&page=0" },
  frames: [], document,
  addEventListener(type, callback) { listeners[type] = callback; }, removeEventListener() {},
  postMessage(message) { messages.push(message); },
};
root.top = root; root.window = root;
vm.runInNewContext(source, { window: root, console });
listeners.message({ source: root, data: {
  source: `antibot-cv-content:${version}`, token: "loading-catalog",
  command: { type: "state_snapshot", payload: { include: ["quests"] } },
} });
const section = JSON.parse(messages[0].message).sections.quests;
assert.strictEqual(section.status, "not_loaded");
assert.strictEqual(section.data.loadStatus, "not_loaded");
assert.deepStrictEqual(section.data.items, []);
"""
    result = subprocess.run(["node", "-e", script], cwd=".", text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr


def test_page_bridge_reads_area_name_from_heading_before_realm_label() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
const text = "\n\tКурганы бренности\n\tЦарство: Артания\nКуда хотите перейти?";
const document = {
  title: "",
  body: { innerText: text, textContent: text },
  querySelector() { return null; },
  querySelectorAll() { return []; },
};
const root = {
  name: "top", location: { href: "https://3kingdoms.ru/area.php" }, frames: [], document,
  addEventListener(type, callback) { listeners[type] = callback; }, removeEventListener() {},
  postMessage(message) { messages.push(message); },
};
root.top = root; root.window = root;
vm.runInNewContext(source, { window: root, console });
listeners.message({
  source: root,
  data: {
    source: `antibot-cv-content:${version}`, token: "location",
    command: { type: "state_snapshot", payload: { include: ["location"] } },
  },
});
assert.strictEqual(messages.length, 1);
assert.strictEqual(messages[0].ok, true);
const result = JSON.parse(messages[0].message);
assert.strictEqual(result.sections.location.data.pageKind, "area");
assert.strictEqual(result.sections.location.data.semanticName, "Курганы бренности");
"""
    result = subprocess.run(["node", "-e", script], cwd=".", text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr


def test_page_bridge_submits_guarded_compass_route_step() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
const location = {
  _href: "https://3kingdoms.ru/area.php",
  get href() { return this._href; },
  set href(value) { this._href = String(value); },
};
const document = {
  title: "",
  body: { innerText: "", textContent: "" },
  querySelector() { return null; },
  querySelectorAll() { return []; },
};
const root = {
  name: "top", location, frames: [], document, setTimeout,
  addEventListener(type, callback) { listeners[type] = callback; }, removeEventListener() {},
  postMessage(message) { messages.push(message); },
  area: {
    model: {
      userGhost: false,
      area: {
        title: "Городская площадь Арсы",
        ftime: 4,
        finishTimeLocal: Date.now() - 1000,
        compassLocation: {
          id: "11", name: "Пригород Арсы", locId: "171", mode: "area",
          href: "/action_run.php?code=COME_IN&area_id=171&url_success=area.php&url_error=area.php",
          confirm: 0, hidden: false, ltime: 0, dtime: 0,
        },
      },
    },
    controller: { compass: { data: { location: 102, target: 200, foundPath: [171, 200] } } },
  },
};
root.top = root; root.window = root;
vm.runInNewContext(source, { window: root, console, setTimeout, clearTimeout });

async function command(type, payload = {}) {
  messages.length = 0;
  listeners.message({ source: root, data: { source: `antibot-cv-content:${version}`, token: "route", command: { type, payload } } });
  const deadline = Date.now() + 1000;
  while (!messages.length && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 5));
  assert.strictEqual(messages.length, 1);
  return { ok: messages[0].ok, message: JSON.parse(messages[0].message) };
}

(async () => {
  const snapshot = await command("location_route_snapshot");
  assert.strictEqual(snapshot.ok, true);
  assert.strictEqual(snapshot.message.location.semanticName, "Городская площадь Арсы");
  assert.strictEqual(snapshot.message.currentLocationId, "102");
  assert.strictEqual(snapshot.message.nextTransition.locId, "171");
  assert.strictEqual(snapshot.message.timerReady, true);
  const result = await command("location_route_step", { expectedCurrentLocationId: "102", navigationDelayMs: 25 });
  assert.strictEqual(result.ok, true);
  assert.strictEqual(result.message.submitted, true);
  assert.strictEqual(result.message.transition.nextLocationId, "171");
  await new Promise((resolve) => setTimeout(resolve, 50));
  assert.strictEqual(location.href.includes("code=COME_IN"), true);
  assert.strictEqual(location.href.includes("area_id=171"), true);
})().catch((error) => { console.error(error); process.exit(1); });
"""
    result = subprocess.run(["node", "-e", script], cwd=".", text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr


def test_page_bridge_reads_and_submits_guarded_navigator_route() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
let goClicks = 0;
const timers = [];

function input(attributes, value, visible = true) {
  return {
    value,
    textContent: "",
    innerText: "",
    offsetWidth: visible ? 80 : 0,
    offsetHeight: visible ? 24 : 0,
    getAttribute(name) { return attributes[name] || null; },
    querySelectorAll() { return []; },
    click() { goClicks += 1; },
  };
}

const compass = input({ name: "compassInput" }, "Дикий предел");
const go = input({ type: "button" }, "Дойти");
const root = {
  name: "top",
  location: { href: "https://3kingdoms.ru/navigator.php?name=test" },
  frames: [],
  document: {
    title: "Навигатор",
    body: { innerText: "Путь займет 2 перехода", textContent: "Путь займет 2 перехода" },
    querySelectorAll(selector) { return selector === "input,button" ? [compass, go] : []; },
  },
  getComputedStyle() { return { display: "block", visibility: "visible" }; },
  setTimeout(callback, delay) { timers.push({ callback, delay }); },
  addEventListener(type, callback) { listeners[type] = callback; },
  removeEventListener() {},
  postMessage(message) { messages.push(message); },
};
root.top = root;
root.window = root;

vm.runInNewContext(source, { window: root, console, setTimeout: root.setTimeout });

function command(type, payload = {}) {
  messages.length = 0;
  listeners.message({
    source: root,
    data: {
      source: `antibot-cv-content:${version}`,
      token: "navigator-token",
      command: { type, payload },
    },
  });
  assert.strictEqual(messages.length, 1);
  return { ok: messages[0].ok, message: JSON.parse(messages[0].message) };
}

const snapshot = command("navigator_snapshot");
assert.strictEqual(snapshot.ok, true);
assert.ok(snapshot.message.snapshotId);
assert.ok(snapshot.message.generatedAt);
assert.strictEqual(snapshot.message.target, "Дикий предел");
assert.strictEqual(snapshot.message.currentLocation, false);
assert.strictEqual(snapshot.message.hasRoute, true);
assert.strictEqual(snapshot.message.routeTransitions, 2);

const mismatch = command("navigator_go", { expectedTarget: "Прокаленное плато" });
assert.strictEqual(mismatch.ok, false);
assert.strictEqual(mismatch.message.message, "navigator_target_mismatch");
assert.strictEqual(goClicks, 0);

const submitted = command("navigator_go", { expectedTarget: "Дикий предел" });
assert.strictEqual(submitted.ok, true);
assert.strictEqual(submitted.message.submitted, true);
assert.strictEqual(submitted.message.message, "navigator_go_scheduled");
assert.strictEqual(goClicks, 0);
assert.strictEqual(timers.length, 1);
timers[0].callback();
assert.strictEqual(goClicks, 1);
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=".",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_page_bridge_selects_one_exact_location_before_route_submission() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
let bodyText = "";
let candidateClicks = 0;
const heading = { innerText: "Локации", textContent: "Локации", parentElement: null, children: [] };
const section = { innerText: "", textContent: "", parentElement: null, children: [heading] };
heading.parentElement = section;
const compass = {
  value: "", innerText: "", textContent: "", offsetWidth: 450, offsetHeight: 24,
  getAttribute(name) { return name === "name" ? "compassInput" : null; },
  querySelectorAll() { return []; }, focus() {}, dispatchEvent() {},
};
const routeButton = {
  value: "Проложить маршрут", innerText: "", textContent: "", offsetWidth: 194, offsetHeight: 24,
  getAttribute() { return null; }, querySelectorAll() { return []; }, click() {},
};
const candidate = {
  innerText: "Курганы бренности", textContent: "Курганы бренности", parentElement: section,
  offsetWidth: 300, offsetHeight: 20, children: [], getAttribute() { return null; }, querySelectorAll() { return []; },
  click() { candidateClicks += 1; compass.value = "Курганы бренности"; bodyText = "Путь займет 3 перехода"; },
};
const document = {
  title: "Навигатор",
  body: {
    get innerText() { return bodyText; },
    get textContent() { return bodyText; },
  },
  querySelectorAll(selector) {
    if (selector === "input,button") return [compass, routeButton];
    if (selector === "div,li,a,button,[role='option']") return [heading, candidate];
    return [];
  },
};
const root = {
  name: "top", location: { href: "https://3kingdoms.ru/navigator.php" }, frames: [], document, setTimeout,
  getComputedStyle() { return { display: "block", visibility: "visible" }; },
  addEventListener(type, callback) { listeners[type] = callback; }, removeEventListener() {},
  postMessage(message) { messages.push(message); },
};
root.top = root; root.window = root;
vm.runInNewContext(source, { window: root, console, setTimeout, clearTimeout });

(async () => {
  listeners.message({
    source: root,
    data: {
      source: `antibot-cv-content:${version}`, token: "select-location",
      command: { type: "navigator_select_target", payload: { target: "Курганы бренности", kind: "location", searchDelayMs: 1, routeDelayMs: 1 } },
    },
  });
  const deadline = Date.now() + 1500;
  while (!messages.length && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 5));
  assert.strictEqual(messages.length, 1);
  assert.strictEqual(messages[0].ok, true);
  const result = JSON.parse(messages[0].message);
  assert.strictEqual(result.message, "navigator_target_selected");
  assert.strictEqual(result.target, "Курганы бренности");
  assert.strictEqual(result.snapshot.routeTransitions, 3);
  assert.strictEqual(result.snapshot.visibleGoButtonCount, 1);
  assert.strictEqual(candidateClicks, 1);
})().catch((error) => { console.error(error); process.exit(1); });
"""
    result = subprocess.run(["node", "-e", script], cwd=".", text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr


def test_page_bridge_selects_one_exact_monster_before_route_submission() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
let bodyText = "";
let candidateClicks = 0;
const heading = { innerText: "Монстры", textContent: "Монстры", parentElement: null, children: [] };
const section = {
  innerText: "Монстры\nБродячий муравей [4]",
  textContent: "Монстры Бродячий муравей [4]",
  parentElement: null,
  children: [],
};
heading.parentElement = section;
const compass = {
  value: "", innerText: "", textContent: "", offsetWidth: 450, offsetHeight: 24,
  getAttribute(name) { return name === "name" ? "compassInput" : null; },
  querySelectorAll() { return []; }, focus() {}, dispatchEvent() {},
};
const routeButton = {
  value: "Проложить маршрут", innerText: "", textContent: "", offsetWidth: 194, offsetHeight: 24,
  getAttribute() { return null; }, querySelectorAll() { return []; }, click() {},
};
const candidate = {
  innerText: "Бродячий муравей [4]", textContent: "Бродячий муравей [4]", parentElement: section,
  offsetWidth: 300, offsetHeight: 20, children: [], getAttribute() { return null; }, querySelectorAll() { return []; },
  click() { candidateClicks += 1; compass.value = "Бродячий муравей [4]"; bodyText = "Путь займет 5 переходов"; },
};
const document = {
  title: "Навигатор",
  body: { get innerText() { return bodyText; }, get textContent() { return bodyText; } },
  querySelectorAll(selector) {
    if (selector === "input,button") return [compass, routeButton];
    if (selector === "div,li,a,button,[role='option']") return [heading, candidate];
    return [];
  },
};
const root = {
  name: "top", location: { href: "https://3kingdoms.ru/navigator.php" }, frames: [], document, setTimeout,
  getComputedStyle() { return { display: "block", visibility: "visible" }; },
  addEventListener(type, callback) { listeners[type] = callback; }, removeEventListener() {},
  postMessage(message) { messages.push(message); },
};
root.top = root; root.window = root;
vm.runInNewContext(source, { window: root, console, setTimeout, clearTimeout });

(async () => {
  listeners.message({
    source: root,
    data: {
      source: `antibot-cv-content:${version}`, token: "select-monster",
      command: { type: "navigator_select_target", payload: { target: "Бродячий муравей [4]", kind: "monster", searchDelayMs: 1, routeDelayMs: 1 } },
    },
  });
  const deadline = Date.now() + 1500;
  while (!messages.length && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 5));
  assert.strictEqual(messages.length, 1);
  assert.strictEqual(messages[0].ok, true);
  const result = JSON.parse(messages[0].message);
  assert.strictEqual(result.message, "navigator_target_selected");
  assert.strictEqual(result.target, "Бродячий муравей [4]");
  assert.strictEqual(result.snapshot.routeTransitions, 5);
  assert.strictEqual(candidateClicks, 1);
})().catch((error) => { console.error(error); process.exit(1); });
"""
    result = subprocess.run(["node", "-e", script], cwd=".", text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr


def test_page_bridge_opens_only_the_unique_location_compass_control() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
let clicks = 0;
const compass = { click() { clicks += 1; } };
const document = {
  body: { innerText: "", textContent: "" },
  querySelectorAll(selector) { return selector === "a[data-command='showExternalNavigate']" ? [compass] : []; },
};
const root = {
  name: "top", location: { href: "https://3kingdoms.ru/main.php" }, frames: [], document,
  addEventListener(type, callback) { listeners[type] = callback; }, removeEventListener() {},
  postMessage(message) { messages.push(message); },
};
root.top = root; root.window = root;
vm.runInNewContext(source, { window: root, console });
listeners.message({
  source: root,
  data: {
    source: `antibot-cv-content:${version}`, token: "open-compass",
    command: { type: "open_location_navigator", payload: {} },
  },
});
assert.strictEqual(messages.length, 1);
assert.strictEqual(messages[0].ok, true);
assert.strictEqual(JSON.parse(messages[0].message).message, "location_navigator_opened");
assert.strictEqual(clicks, 1);
"""
    result = subprocess.run(["node", "-e", script], cwd=".", text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr


def test_page_bridge_opens_quest_navigator_by_location_label() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
let clicks = 0;
const marker = { getAttribute(name) { return name === "alt" ? "Проложить путь" : null; } };
const link = {
  textContent: "Дикий предел",
  innerText: "Дикий предел",
  getAttribute(name) { return name === "href" ? "#" : null; },
  querySelectorAll(selector) { return selector === "img" ? [marker] : []; },
  click() { clicks += 1; },
};
const root = {
  name: "top",
  location: { href: "https://3kingdoms.ru/user_quest.php?mode=started" },
  frames: [],
  document: {
    title: "Квесты",
    body: { innerText: "Текущая цель: Уничтожьте мобов в Диком пределе", textContent: "" },
    querySelectorAll(selector) { return selector === "a" ? [link] : []; },
  },
  addEventListener(type, callback) { listeners[type] = callback; },
  removeEventListener() {},
  postMessage(message) { messages.push(message); },
};
root.top = root;
root.window = root;

vm.runInNewContext(source, { window: root, console });
listeners.message({
  source: root,
  data: {
    source: `antibot-cv-content:${version}`,
    token: "quest-route-token",
    command: { type: "open_quest_navigator", payload: { target: "Дикий предел" } },
  },
});
assert.strictEqual(messages.length, 1);
assert.strictEqual(messages[0].ok, true);
const result = JSON.parse(messages[0].message);
assert.strictEqual(result.target, "Дикий предел");
assert.strictEqual(clicks, 1);
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=".",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_page_bridge_opens_quests_when_sidebar_label_is_only_image_alt() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
let clicks = 0;
const icon = { getAttribute(name) { return name === "alt" ? "квесты" : null; } };
const link = {
  innerText: "",
  textContent: "",
  getAttribute(name) { return name === "href" ? "#" : null; },
  querySelectorAll(selector) { return selector === "img[alt],img[title]" ? [icon] : []; },
  closest() { return this; },
  click() {
    clicks += 1;
    root.location.href = "https://3kingdoms.ru/user_quest.php?mode=started";
  },
};
const root = {
  name: "top",
  location: { href: "https://3kingdoms.ru/main.php" },
  frames: [],
  document: {
    title: "",
    querySelectorAll(selector) { return selector === "a,button,[onclick]" ? [link] : []; },
  },
  addEventListener(type, callback) { listeners[type] = callback; },
  removeEventListener() {},
  postMessage(message) { messages.push(message); },
  setTimeout,
};
root.top = root;
root.window = root;

vm.runInNewContext(source, { window: root, console, setTimeout, clearTimeout });
(async () => {
listeners.message({
  source: root,
  data: {
    source: `antibot-cv-content:${version}`,
    token: "open-quests-token",
    command: { type: "open_quests", payload: { verifyTimeoutMs: 250 } },
  },
});
const deadline = Date.now() + 1000;
while (!messages.length && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 5));
assert.strictEqual(messages.length, 1);
assert.strictEqual(messages[0].ok, true);
assert.strictEqual(JSON.parse(messages[0].message).message, "quests_opened_confirmed");
assert.strictEqual(clicks, 1);
})().catch((error) => { console.error(error); process.exit(1); });
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
        { id: 4, name: "Волколак-живодер [5]", shortName: "Волколак-живодер [5]", lvl: 5, x: 780, y: 1320, fightId: 0, agrforbid: false, isBot: true },
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
assert.strictEqual(moved.message.targetCount, 3);
const levelFromField = moved.message.targets.find((target) => target.botId === 2);
assert.strictEqual(levelFromField.level, 4);
assert.strictEqual(levelFromField.levelSource, "lvl");
const levelFromName = moved.message.targets.find((target) => target.botId === 3);
assert.strictEqual(levelFromName.level, 7);
assert.strictEqual(levelFromName.levelSource, "name_brackets");
const onlyLevelSeven = command("visible_hunt_targets", { margin: 35, allowedLevels: [7] });
assert.strictEqual(onlyLevelSeven.message.targets.length, 1);
assert.strictEqual(onlyLevelSeven.message.targets[0].botId, 3);
const questInflection = command("visible_hunt_targets", { margin: 35, allowedLevels: [5], names: ["Волколаков-живодеров"] });
assert.strictEqual(questInflection.message.targets.length, 1);
assert.strictEqual(questInflection.message.targets[0].botId, 4);
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


def test_page_bridge_revives_only_explicit_free_option_and_verifies_alive() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
let dead = true;
let reviveClicks = 0;

function element(className, text) {
  return {
    className, textContent: text, innerText: text, value: "", parentElement: null,
    getAttribute(name) { return name === "class" ? className : null; },
    querySelector() { return null; }, querySelectorAll() { return []; },
  };
}
const hp = element("b-control-lvl__hp", "Жизнь 100%");
const mp = element("b-control-lvl__mp", "Удаль 100%");
const control = element("b-control-lvl", "5 hero Жизнь 100% Удаль 100% Опыт 10%");
hp.parentElement = control; mp.parentElement = control;
const revive = element("revive", "Воскреснуть бесплатно");
revive.parentElement = element("revive-wrap", "Бесплатно воскреснуть");
revive.click = () => { reviveClicks += 1; dead = false; };

const playerDocument = {
  title: "", scripts: [], body: { innerText: control.innerText, textContent: control.textContent },
  documentElement: { innerHTML: control.innerText },
  querySelector(selector) {
    if (selector.includes("control-lvl__hp")) return hp;
    if (selector.includes("control-lvl__mp")) return mp;
    if (selector.includes("control-lvl")) return control;
    return null;
  },
  querySelectorAll(selector) { return selector === "*" ? [control, hp, mp] : []; },
};
const deathDocument = {
  title: "", scripts: [], documentElement: { innerHTML: "" },
  body: {
    get innerText() { return dead ? "Вы погибли. Бесплатно воскреснуть" : "Персонаж жив"; },
    get textContent() { return this.innerText; },
  },
  querySelector() { return null; },
  querySelectorAll(selector) { return dead && selector.includes("button") ? [revive] : []; },
};
const mainWin = { name: "main", location: { href: "https://3kingdoms.ru/area.php" }, frames: [], document: deathDocument };
const mainFrame = { name: "main_frame", location: { href: "https://3kingdoms.ru/main_frame.php" }, frames: [mainWin], document: deathDocument };
mainFrame.frames.main = mainWin;
const root = {
  name: "top", location: { href: "https://3kingdoms.ru/main.php" }, frames: [mainFrame], document: playerDocument,
  addEventListener(type, callback) { listeners[type] = callback; }, removeEventListener() {},
  postMessage(message) { messages.push(message); }, setTimeout,
};
root.frames.main_frame = mainFrame; root.top = root; root.window = root;
vm.runInNewContext(source, { window: root, console, setTimeout, clearTimeout });

async function command(type, payload = {}) {
  messages.length = 0;
  listeners.message({ source: root, data: { source: `antibot-cv-content:${version}`, token: "token", command: { type, payload } } });
  const deadline = Date.now() + 1500;
  while (!messages.length && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 5));
  assert.strictEqual(messages.length, 1);
  return { ok: messages[0].ok, message: JSON.parse(messages[0].message) };
}

(async () => {
  const before = await command("state_snapshot", { include: ["deathRevive"] });
  assert.strictEqual(before.message.sections.deathRevive.data.dead, true);
  assert.strictEqual(before.message.sections.deathRevive.data.freeReviveAvailable, true);
  const result = await command("revive_free", { expectedCharacter: "hero", verifyDelayMs: 1 });
  assert.strictEqual(result.ok, true);
  assert.strictEqual(result.message.message, "free_revive_confirmed");
  assert.strictEqual(result.message.confirmed, true);
  assert.strictEqual(reviveClicks, 1);
})().catch((error) => { console.error(error); process.exit(1); });
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=".",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_page_bridge_detects_top_level_resurrection_yes_popup_without_cost() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
let dead = true;
let reviveClicks = 0;

function basicElement(className, text) {
  return {
    className, textContent: text, innerText: text, value: "", parentElement: null, disabled: false,
    getAttribute(name) { return name === "class" ? className : null; },
    querySelector() { return null; }, querySelectorAll() { return []; },
  };
}
const control = basicElement("b-control-lvl", "");
Object.defineProperty(control, "innerText", { get() { return `5 hero Жизнь ${dead ? 0 : 100}% Удаль ${dead ? 0 : 100}% Опыт 10%`; } });
Object.defineProperty(control, "textContent", { get() { return control.innerText; } });
const hp = basicElement("b-control-lvl__hp", "");
Object.defineProperty(hp, "innerText", { get() { return `Жизнь ${dead ? 0 : 100}%`; } });
Object.defineProperty(hp, "textContent", { get() { return hp.innerText; } });
const mp = basicElement("b-control-lvl__mp", "");
Object.defineProperty(mp, "innerText", { get() { return `Удаль ${dead ? 0 : 100}%`; } });
Object.defineProperty(mp, "textContent", { get() { return mp.innerText; } });
hp.parentElement = control; mp.parentElement = control;

const prompt = basicElement("popup-confirm", "Желаете воскреснуть? Да нет");
const yes = basicElement("popup-yes", "Да");
yes.value = "Да";
yes.getAttribute = (name) => name === "name" ? "yes" : name === "class" ? "popup-yes" : null;
yes.parentElement = prompt;
yes.getBoundingClientRect = () => ({ width: 80, height: 20 });
yes.click = () => { reviveClicks += 1; dead = false; };

const rootDocument = {
  title: "", scripts: [], documentElement: { innerHTML: "" },
  body: {
    get innerText() { return `${control.innerText} ${dead ? prompt.innerText : ""}`; },
    get textContent() { return this.innerText; },
  },
  querySelector(selector) {
    if (selector.includes("control-lvl__hp")) return hp;
    if (selector.includes("control-lvl__mp")) return mp;
    if (selector.includes("control-lvl")) return control;
    return null;
  },
  querySelectorAll(selector) {
    if (selector === "*") return [control, hp, mp];
    if (selector.includes("input[type='button']")) return dead ? [yes] : [];
    return [];
  },
};
const mainDocument = {
  title: "", scripts: [], documentElement: { innerHTML: "" },
  body: { innerText: "Лес призраков", textContent: "Лес призраков" },
  querySelector() { return null; }, querySelectorAll() { return []; },
};
const mainWin = { name: "main", location: { href: "https://3kingdoms.ru/area.php?exit=1" }, frames: [], document: mainDocument };
const mainFrame = { name: "main_frame", location: { href: "https://3kingdoms.ru/main_frame.php" }, frames: [mainWin], document: rootDocument };
mainFrame.frames.main = mainWin;
const root = {
  name: "top", location: { href: "https://3kingdoms.ru/main.php" }, frames: [mainFrame], document: rootDocument,
  addEventListener(type, callback) { listeners[type] = callback; }, removeEventListener() {},
  postMessage(message) { messages.push(message); }, setTimeout,
};
root.frames.main_frame = mainFrame; root.top = root; root.window = root;
vm.runInNewContext(source, { window: root, console, setTimeout, clearTimeout });

async function command(type, payload = {}) {
  messages.length = 0;
  listeners.message({ source: root, data: { source: `antibot-cv-content:${version}`, token: "token", command: { type, payload } } });
  const deadline = Date.now() + 1500;
  while (!messages.length && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 5));
  assert.strictEqual(messages.length, 1);
  return { ok: messages[0].ok, message: JSON.parse(messages[0].message) };
}

(async () => {
  const before = await command("state_snapshot", { include: ["deathRevive"] });
  assert.strictEqual(before.message.sections.deathRevive.data.dead, true);
  assert.strictEqual(before.message.sections.deathRevive.data.freeReviveOptionCount, 1);
  assert.strictEqual(
    before.message.sections.deathRevive.data.reviveOptions[0].freeEvidence,
    "explicit_resurrection_prompt_without_cost"
  );
  const result = await command("revive_free", { expectedCharacter: "hero", verifyDelayMs: 1 });
  assert.strictEqual(result.ok, true);
  assert.strictEqual(result.message.confirmed, true);
  assert.strictEqual(reviveClicks, 1);
})().catch((error) => { console.error(error); process.exit(1); });
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=".",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_page_bridge_uses_native_resurrect_handler_for_canvas_ghost_dialog() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
let dead = true;
let reviveCalls = 0;
let noticeVisible = false;
let noticeCloseClicks = 0;

function basicElement(className, getText) {
  return {
    className, value: "", parentElement: null, disabled: false,
    get innerText() { return getText(); },
    get textContent() { return getText(); },
    getAttribute(name) { return name === "class" ? className : null; },
    querySelector() { return null; }, querySelectorAll() { return []; },
  };
}
const control = basicElement("b-control-lvl", () => `5 hero Жизнь ${dead ? 0 : 100}% Удаль ${dead ? 0 : 100}% Опыт 10%`);
const hp = basicElement("b-control-lvl__hp", () => `Жизнь ${dead ? 0 : 100}%`);
const mp = basicElement("b-control-lvl__mp", () => `Удаль ${dead ? 0 : 100}%`);
hp.parentElement = control; mp.parentElement = control;
const noticeClose = basicElement("notice-close", () => "Закрыть");
noticeClose.click = () => { noticeCloseClicks += 1; noticeVisible = false; };
const noticeDocument = {
  body: { innerText: "Воскрешение Вы воскрешены! Войдите в новую жизнь уверенным шагом победителя! Закрыть" },
  querySelectorAll() { return noticeVisible ? [noticeClose] : []; },
};
const errorFrame = {
  id: "error",
  contentDocument: noticeDocument,
  contentWindow: { document: noticeDocument },
  getAttribute(name) { return name === "src" && noticeVisible ? "error.php?title=revive" : ""; },
};

const playerDocument = {
  title: "", scripts: [], documentElement: { innerHTML: "" },
  body: {
    get innerText() { return control.innerText; },
    get textContent() { return control.textContent; },
  },
  querySelector(selector) {
    if (selector === "iframe#error") return noticeVisible ? errorFrame : null;
    if (selector.includes("control-lvl__hp")) return hp;
    if (selector.includes("control-lvl__mp")) return mp;
    if (selector.includes("control-lvl")) return control;
    return null;
  },
  querySelectorAll(selector) { return selector === "*" ? [control, hp, mp] : []; },
};
const areaDocument = {
  title: "", scripts: [], documentElement: { innerHTML: "" },
  body: { innerText: "Лес призраков", textContent: "Лес призраков" },
  querySelector() { return null; }, querySelectorAll() { return []; },
};
const mainLocation = {
  _href: "https://3kingdoms.ru/user.php?mode=personage&submode=backpack",
  get href() { return this._href; },
  set href(value) {
    this._href = String(value);
    if (this._href.includes("code=RESURRECT")) {
      reviveCalls += 1;
      dead = false;
      noticeVisible = true;
      this._href = "https://3kingdoms.ru/area.php";
    } else if (this._href === "/area.php") {
      this._href = "https://3kingdoms.ru/area.php";
    }
  },
};
const mainWin = { name: "main", location: mainLocation, frames: [], document: areaDocument };
const mainFrame = { name: "main_frame", location: { href: "https://3kingdoms.ru/main_frame.php" }, frames: [mainWin], document: playerDocument };
mainFrame.frames.main = mainWin;
const root = {
  name: "top", location: { href: "https://3kingdoms.ru/main.php" }, frames: [mainFrame], document: playerDocument,
  addEventListener(type, callback) { listeners[type] = callback; }, removeEventListener() {},
  postMessage(message) { messages.push(message); }, setTimeout,
};
root.frames.main_frame = mainFrame; root.top = root; root.window = root;
root.browserAPI = { eatAfterResurrection() { throw new Error("must_not_open_inventory"); } };
root.resurrect = function resurrect() {
  const endpoint = "/action_run.php?code=RESURRECT&url_success=/area.php&url_error=/area.php";
  mainWin.location.href = endpoint;
  root.browserAPI.eatAfterResurrection();
};
vm.runInNewContext(source, { window: root, console, setTimeout, clearTimeout });

async function command(type, payload = {}) {
  messages.length = 0;
  listeners.message({ source: root, data: { source: `antibot-cv-content:${version}`, token: "token", command: { type, payload } } });
  const deadline = Date.now() + 1500;
  while (!messages.length && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 5));
  assert.strictEqual(messages.length, 1);
  return { ok: messages[0].ok, message: JSON.parse(messages[0].message) };
}

(async () => {
  const before = await command("state_snapshot", { include: ["deathRevive"] });
  assert.strictEqual(before.message.sections.deathRevive.data.dead, true);
  assert.strictEqual(before.message.sections.deathRevive.data.freeReviveOptionCount, 1);
  assert.strictEqual(
    before.message.sections.deathRevive.data.reviveOptions[0].freeEvidence,
    "native_resurrect_handler_zero_hp_recoverable_page"
  );
  const result = await command("revive_free", { expectedCharacter: "hero", verifyDelayMs: 1 });
  assert.strictEqual(result.ok, true);
  assert.strictEqual(result.message.message, "free_revive_confirmed");
  assert.strictEqual(result.message.confirmed, true);
  assert.strictEqual(reviveCalls, 1);
  assert.strictEqual(result.message.after.data.resurrectionNoticeAvailable, true);
  const closeResult = await command("close_resurrection_notice", { verifyDelayMs: 1 });
  assert.strictEqual(closeResult.ok, true);
  assert.strictEqual(closeResult.message.message, "resurrection_notice_closed");
  assert.strictEqual(closeResult.message.confirmed, true);
  assert.strictEqual(noticeCloseClicks, 1);
  const afterClose = await command("state_snapshot", { include: ["deathRevive"] });
  assert.strictEqual(afterClose.message.sections.deathRevive.data.resurrectionNoticeAvailable, false);
})().catch((error) => { console.error(error); process.exit(1); });
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
