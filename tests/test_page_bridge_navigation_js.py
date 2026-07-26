"""Navigation and quest-control bridge behavior tests."""
from __future__ import annotations

import subprocess


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
  const originalBody = document.body;
  const originalQuerySelector = document.querySelector;
  const originalQuerySelectorAll = document.querySelectorAll;
  Object.defineProperty(document, "body", { configurable: true, get() { throw new Error("operational route touched body"); } });
  document.querySelector = () => { throw new Error("operational route queried DOM"); };
  document.querySelectorAll = () => { throw new Error("operational route scanned DOM"); };
  const snapshot = await command("location_route_snapshot");
  assert.strictEqual(snapshot.ok, true);
  assert.strictEqual(snapshot.message.source, "area-model");
  assert.strictEqual(snapshot.message.domNodesScanned, 0);
  assert.ok(JSON.stringify(snapshot.message).length < 5000);
  assert.strictEqual(snapshot.message.location.semanticName, "Городская площадь Арсы");
  assert.strictEqual(snapshot.message.currentLocationId, "102");
  assert.strictEqual(snapshot.message.nextTransition.locId, "171");
  assert.strictEqual(snapshot.message.timerReady, true);
  Object.defineProperty(document, "body", { configurable: true, value: originalBody, writable: true });
  document.querySelector = originalQuerySelector;
  document.querySelectorAll = originalQuerySelectorAll;
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


def test_page_bridge_snapshots_and_enters_exact_instance() -> None:
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
  title: "", readyState: "complete",
  body: { innerText: "Заброшенные копи объекты Огненный провал", textContent: "" },
  querySelector() { return null; }, querySelectorAll() { return []; },
};
const root = {
  name: "top", location, frames: [], document, setTimeout,
  addEventListener(type, callback) { listeners[type] = callback; },
  removeEventListener() {}, postMessage(message) { messages.push(message); },
  area: {
    model: { area: { title: "Заброшенные копи", items: [
      { id: 2, name: "Огненный провал", type: "instance", href: "/instance.php?action=enter&id=79" },
      { id: 13, name: "Дом Норида", type: "npc", href: "/npc.php?action=enter&ref=822" },
    ] } },
    controller: { compass: { data: { location: 123, target: 0, foundPath: [] } } },
  },
};
root.top = root; root.window = root;
vm.runInNewContext(source, { window: root, console, setTimeout, clearTimeout });
async function command(type, payload = {}) {
  messages.length = 0;
  listeners.message({ source: root, data: { source: `antibot-cv-content:${version}`, token: "inst", command: { type, payload } } });
  const deadline = Date.now() + 1000;
  while (!messages.length && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 5));
  assert.strictEqual(messages.length, 1);
  return { ok: messages[0].ok, message: JSON.parse(messages[0].message) };
}
(async () => {
  const snapshot = await command("instance_entrance_snapshot", { expectedName: "Огненный провал" });
  assert.strictEqual(snapshot.ok, true);
  assert.strictEqual(snapshot.message.currentLocationId, "123");
  assert.strictEqual(snapshot.message.candidateCount, 1);
  assert.strictEqual(snapshot.message.candidates[0].href, "/instance.php?action=enter&id=79");
  const stale = await command("enter_instance", { expectedName: "Огненный провал", expectedSnapshotId: "wrong" });
  assert.strictEqual(stale.ok, false);
  assert.strictEqual(stale.message.message, "instance_snapshot_stale");
  const entered = await command("enter_instance", {
    expectedName: "Огненный провал",
    expectedSnapshotId: snapshot.message.snapshotId,
    navigationDelayMs: 25,
  });
  assert.strictEqual(entered.ok, true);
  assert.strictEqual(entered.message.message, "instance_entry_submitted");
  await new Promise((resolve) => setTimeout(resolve, 50));
  assert.strictEqual(location.href, "/instance.php?action=enter&id=79");
})().catch((error) => { console.error(error); process.exit(1); });
"""
    result = subprocess.run(["node", "-e", script], cwd=".", text=True, capture_output=True, check=False)
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


def test_page_bridge_opens_quest_navigator_by_exact_label_and_route_target() -> None:
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
const cancelLink = { getAttribute(name) { return name === "href" ? "user_quest.php?action=cancel&ref=355" : null; } };
const card = { parentElement: null, querySelectorAll(selector) { return selector === "a[href*='action=cancel']" ? [cancelLink] : []; } };
const link = {
  textContent: "Кабанов-секачей",
  innerText: "Кабанов-секачей",
  getAttribute(name) {
    if (name === "href") return "#";
    if (name === "onclick") return "showMsg('navigator.php?name=%CA%E0%E1%E0%ED-%F1%E5%EA%E0%F7%20%5B5%5D','Navigator',560,423);return false;";
    return null;
  },
  querySelectorAll(selector) { return selector === "img" ? [marker] : []; },
  parentElement: card,
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

vm.runInNewContext(source, { window: root, console, TextDecoder });
listeners.message({
  source: root,
  data: {
    source: `antibot-cv-content:${version}`,
    token: "quest-route-token",
    command: {
      type: "open_quest_navigator",
      payload: { target: "Кабан-секач [5]", linkLabel: "Кабанов-секачей", expectedQuestId: "355" },
    },
  },
});
assert.strictEqual(messages.length, 1);
assert.strictEqual(messages[0].ok, true);
const result = JSON.parse(messages[0].message);
assert.strictEqual(result.target, "Кабан-секач [5]");
assert.strictEqual(result.linkLabel, "Кабанов-секачей");
assert.strictEqual(result.expectedQuestId, "355");
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


def test_page_bridge_opens_active_catalog_through_quest_control_from_hunt() -> None:
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
  innerText: "", textContent: "",
  getAttribute(name) {
    if (name === "href") return "#";
    if (name === "data-command") return "openQuests";
    if (name === "class") return "b-control-right__item quests";
    return null;
  },
  querySelectorAll(selector) { return selector === "img[alt],img[title]" ? [icon] : []; },
  closest() { return this; },
  click() {
    clicks += 1;
    root.location.href = "https://3kingdoms.ru/user_quest.php?mode=started&page=0";
    root.document.title = "Квесты";
    root.document.body = {
      innerText: "Взятые Повторяющиеся Доступные Завершенные",
      textContent: "Взятые Повторяющиеся Доступные Завершенные",
    };
  },
};
const root = {
  name: "top", location: { href: "https://3kingdoms.ru/hunt.php" }, frames: [],
  document: {
    title: "Охота", body: { innerText: "Охота", textContent: "Охота" },
    querySelectorAll(selector) {
      return selector === "a,button,[onclick]" || selector === "a.b-control-right__item.quests[data-command='openQuests']" ? [link] : [];
    },
  },
  addEventListener(type, callback) { listeners[type] = callback; }, removeEventListener() {},
  postMessage(message) { messages.push(message); }, setTimeout,
};
root.top = root; root.window = root;
vm.runInNewContext(source, { window: root, console, setTimeout, clearTimeout });
(async () => {
  listeners.message({ source: root, data: {
    source: `antibot-cv-content:${version}`, token: "active-from-hunt",
    command: { type: "open_active_quest_page", payload: { page: 0, verifyTimeoutMs: 500 } },
  } });
  const deadline = Date.now() + 1500;
  while (!messages.length && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 5));
  assert.strictEqual(messages.length, 1);
  const result = JSON.parse(messages[0].message);
  assert.strictEqual(result.outcome, "CONFIRMED");
  assert.strictEqual(result.method, "quest_control_exact");
  assert.strictEqual(result.after.page, 0);
  assert.strictEqual(clicks, 1);
})().catch((error) => { console.error(error); process.exit(1); });
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=".", text=True, capture_output=True, check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
