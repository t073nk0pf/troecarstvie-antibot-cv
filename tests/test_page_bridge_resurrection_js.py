"""Resurrection bridge behavior tests."""
from __future__ import annotations

import subprocess


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
