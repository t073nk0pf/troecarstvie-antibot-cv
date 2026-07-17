"""Recovery-inventory bridge behavior tests."""
from __future__ import annotations

import subprocess


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
    if (name === "data-artikul") return "555";
    if (name === "cnt") return "10";
    return null;
  },
  closest() { return this; },
  getBoundingClientRect() { return { left: 10, top: 20, width: 32, height: 32 }; },
};
const questLink = {
  tagName: "A",
  innerText: "Квесты",
  textContent: "Квесты",
  value: "",
  getAttribute(name) {
    if (name === "href") return "user_iframe.php?group=4";
    return null;
  },
  click() {},
};
const doc = {
  title: "",
  body: { innerText: "" },
  documentElement: { innerHTML: "" },
  querySelectorAll(selector) { return String(selector).includes("#tab_4") ? [questLink] : [image]; },
};
const rootDoc = {
  title: "",
  body: { innerText: "" },
  documentElement: { innerHTML: "" },
  querySelectorAll() { return []; },
};
const root = {
  name: "top",
  location: { href: "https://3kingdoms.ru/main.php" },
  frames: [],
  document: rootDoc,
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
  processMenu(command) {
    if (command === "b11") {
      this.location.href = "https://3kingdoms.ru/user.php?mode=personage&submode=backpack";
    }
  },
};
const userFrame = {
  name: "user_iframe",
  location: { href: "https://3kingdoms.ru/user_iframe.php?group=1" },
  frames: [],
  document: {
    title: "",
    body: { innerText: "" },
    documentElement: { innerHTML: "" },
    querySelectorAll(selector) {
      if (String(selector) === "script") {
        return [{
          textContent: "_top().art_alt['AA_555'] = {\"title\":\"Пояс Кентавра-ветерана\",\"desc\":\"Получение: Существует небольшая вероятность получения.\",\"kind\":{\"value\":\"Квестовые предметы\"},\"slot_id\":\"quest-slot-555\"};",
        }];
      }
      // The game's cell and its inner visual element are both discoverable.
      // They must collapse to one metadata slot in a quest snapshot.
      return [image, { ...image, id: "nested-slot-image" }];
    },
  },
};
mainFrame.frames = [userFrame];
mainFrame.frames.user_iframe = userFrame;
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
  const result = await command("inventory_snapshot", { names: [], open: false });
  assert.strictEqual(result.ok, true);
  assert.strictEqual(result.message.candidates[0].src, "/images/items/burdjuk_udal.png");
  assert.deepStrictEqual(result.message.candidates[0].rect, { x: 10, y: 20, width: 32, height: 32 });
  const questStartedAt = Date.now();
  const questResult = await command("inventory_snapshot", { names: ["пояс кентавра"], open: true, category: "quest" });
  assert.ok(Date.now() - questStartedAt >= 2900);
  assert.strictEqual(questResult.ok, true);
  assert.strictEqual(questResult.message.backpackMessage, "processMenu_b11");
  assert.strictEqual(questResult.message.backpackProof.confirmed, true);
  assert.strictEqual(questResult.message.categoryConfirmed, true);
  assert.strictEqual(questResult.message.categoryLoadDelayMs, 1500);
  assert.strictEqual(questResult.message.categoryFallback.message, "quest_inventory_frame_navigated");
  assert.strictEqual(questResult.message.itemCount, 1);
  assert.strictEqual(questResult.message.truncated, false);
  assert.strictEqual(questResult.message.items.length, 1);
  assert.strictEqual(questResult.message.items[0].count, 10);
  assert.strictEqual(questResult.message.items[0].artAltTitle, "Пояс Кентавра-ветерана");
  assert.strictEqual(questResult.message.sample[0].artAltTitle, "Пояс Кентавра-ветерана");
  assert.strictEqual(questResult.message.sample[0].artAltKind, "Квестовые предметы");
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
