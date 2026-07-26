"""Quest-catalog bridge behavior tests."""
from __future__ import annotations

import subprocess


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


def test_q304_npc_instance_is_bound_separately_for_open_answer_and_done() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
let source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const start = source.indexOf("  // source: page_bridge_modules/35_npc_quests.js\n");
const end = source.indexOf("  // source: page_bridge_modules/36_quest_chat_progress.js\n");
assert.ok(start >= 0 && end > start);
source = source.slice(0, start) +
  "  // source: page_bridge_modules/35_npc_quests.js\n" +
  fs.readFileSync("browser_injector/page_bridge_modules/35_npc_quests.js", "utf8") + "\n" +
  source.slice(end);
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
const header = { innerText: "Колдунья Вилена", textContent: "Колдунья Вилена" };
let clicks = 0;
const root = {
  name: "top", location: { href: "https://3kingdoms.ru/npc.php?f_id=4&npc_id=110" },
  frames: [], setTimeout,
  addEventListener(type, callback) { listeners[type] = callback; }, removeEventListener() {},
  postMessage(message) { messages.push(message); },
};
root.top = root; root.window = root;
function showAction({ text, href, containerText = text }) {
  const action = {
    tagName: "A", innerText: text, textContent: text, disabled: false,
    getAttribute(name) { return name === "href" ? href : null; },
    getClientRects() { return [{ width: 20, height: 10 }]; },
    closest() { return { innerText: containerText, textContent: containerText }; },
    click() { clicks += 1; },
  };
  root.document = {
    title: "Цветочная болезнь", readyState: "complete",
    body: { innerText: "Колдунья Вилена Цветочная болезнь", textContent: "" },
    querySelectorAll(selector) {
      if (selector === "h2") return [header];
      if (selector === "a[href],button,input[type='button'],input[type='submit'],[onclick]") return [action, { ...action }];
      return [];
    },
  };
}
showAction({
  text: "Далее", href: "npc.php?f_id=4&npc_id=110&quest_id=304",
  containerText: "Цветочная болезньДалее",
});
vm.runInNewContext(source, { window: root, console, setTimeout, clearTimeout });
async function command(type, payload) {
  messages.length = 0;
  listeners.message({ source: root, data: {
    source: `antibot-cv-content:${version}`, token: `${type}-${Date.now()}`,
    command: { type, payload },
  } });
  const deadline = Date.now() + 1000;
  while (!messages.length && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 5));
  assert.strictEqual(messages.length, 1);
  return JSON.parse(messages[0].message);
}
async function snapshot() {
  return command("npc_dialog_snapshot", {
    expectedName: "Колдунья Вилена", expectedNpcId: "4", expectedNpcInstanceId: "110",
  });
}
;(async () => {
  let observed = await snapshot();
  assert.strictEqual(observed.identityMatches, true);
  assert.strictEqual(observed.npcInstanceId, "110");
  assert.strictEqual(observed.questActions[0].npcId, "4");
  assert.strictEqual(observed.questActions[0].npcInstanceId, "110");
  assert.strictEqual(observed.questActions.length, 1);
  const wrongInstance = await command("npc_dialog_snapshot", {
    expectedName: "Колдунья Вилена", expectedNpcId: "4", expectedNpcInstanceId: "109",
  });
  assert.strictEqual(wrongInstance.identityMatches, false);
  for (const malformed of [true, false, 110.5, 0, -1, {}, [], "110x"]) {
    const invalidInstance = await command("npc_dialog_snapshot", {
      expectedName: "Колдунья Вилена", expectedNpcId: "4", expectedNpcInstanceId: malformed,
    });
    assert.strictEqual(invalidInstance.identityMatches, false);
    assert.strictEqual(invalidInstance.expectedNpcInstanceInvalid, true);
  }
  observed = await snapshot();
  let result = await command("npc_quest_action", {
    expectedSnapshotId: observed.snapshotId, npcId: "110", expectedNpcInstanceId: "110",
    expectedName: "Колдунья Вилена", questId: "304", expectedTitle: "Цветочная болезнь", action: "open",
  });
  assert.strictEqual(result.message, "npc_dialog_identity_mismatch");
  assert.strictEqual(clicks, 0);
  result = await command("npc_quest_action", {
    expectedSnapshotId: observed.snapshotId, npcId: "4", expectedNpcInstanceId: "110x",
    questId: "304", expectedTitle: "Цветочная болезнь", action: "open",
  });
  assert.strictEqual(result.message, "npc_quest_action_invalid");
  result = await command("npc_quest_action", {
    expectedSnapshotId: observed.snapshotId, npcId: "4", expectedNpcInstanceId: "109",
    expectedName: "Колдунья Вилена", questId: "304", expectedTitle: "Цветочная болезнь", action: "open",
  });
  assert.strictEqual(result.message, "npc_quest_action_missing");
  assert.strictEqual(clicks, 0);
  result = await command("npc_quest_action", {
    expectedSnapshotId: observed.snapshotId, npcId: "4", expectedNpcInstanceId: "110",
    expectedName: "Колдунья Вилена", questId: "304", expectedTitle: "Цветочная болезнь", action: "open",
  });
  assert.strictEqual(result.message, "npc_quest_action_submitted");

  showAction({ text: "Помогу вам.", href: "npc.php?f_id=4&npc_id=110&quest_id=304&point_id=901&ref=77" });
  observed = await snapshot();
  result = await command("npc_quest_action", {
    expectedSnapshotId: observed.snapshotId, npcId: "4", expectedNpcInstanceId: "110",
    expectedName: "Колдунья Вилена", questId: "304", expectedTitle: "Цветочная болезнь", action: "answer",
    expectedRef: "77", expectedText: "Помогу вам.",
  });
  assert.strictEqual(result.message, "npc_quest_action_submitted");

  showAction({ text: "Завершить задание", href: "npc.php?f_id=4&npc_id=110&quest_id=304&point_id=902&action=done" });
  observed = await snapshot();
  result = await command("npc_quest_action", {
    expectedSnapshotId: observed.snapshotId, npcId: "4", expectedNpcInstanceId: "110",
    expectedName: "Колдунья Вилена", questId: "304", action: "done",
    expectedPointId: "902", expectedText: "Завершить задание",
  });
  assert.strictEqual(result.message, "npc_quest_action_submitted");
  assert.strictEqual(clicks, 3);
})().catch((error) => { console.error(error); process.exitCode = 1; });
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
const description = { innerText: "Отправляйтесь к волхву Алстарду и узнайте, где искать героя.", textContent: "Отправляйтесь к волхву Алстарду и узнайте, где искать героя." };
const folding = {
  getAttribute(name) { return name === "onclick" ? "quest_folding.toggle(314);" : null; },
};
const detail = { id: "quest_314" };
const card = {
  innerText: "Письмо дозорному Текущая цель: Отправляйтесь к волхву Алстарду и узнайте, где искать героя Награда: 900 опыта Местоположение: Южная застава",
  textContent: "Письмо дозорному Текущая цель: Отправляйтесь к волхву Алстарду и узнайте, где искать героя Награда: 900 опыта Местоположение: Южная застава",
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
assert.strictEqual(result.items[0].description, "Отправляйтесь к волхву Алстарду и узнайте, где искать героя.");
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


def test_page_bridge_waits_for_delayed_catalog_shell_and_rejects_wrong_page() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];

async function runCase({ wrongPage }) {
  const messages = [];
  const listeners = {};
  let timerCalls = 0;
  let fakeNow = 1000;
  class FakeDate extends Date { static now() { fakeNow += 100; return fakeNow; } }
  const body = {
    innerText: wrongPage ? "Взятые Повторяющиеся Доступные Завершенные" : "Загрузка...",
    textContent: wrongPage ? "Взятые Повторяющиеся Доступные Завершенные" : "Загрузка...",
  };
  const document = { title: "Квесты", body, querySelectorAll() { return []; } };
  let href = "https://3kingdoms.ru/user_quest.php?mode=started&page=0";
  const location = {};
  Object.defineProperty(location, "href", {
    get() { return href; },
    set(value) {
      href = wrongPage
        ? "https://3kingdoms.ru/user_quest.php?mode=started&page=1"
        : `https://3kingdoms.ru${value}`;
    },
  });
  const root = {
    name: "top", location, frames: [], document,
    setTimeout(callback) {
      timerCalls += 1;
      if (!wrongPage && timerCalls === 3) {
        body.innerText = body.textContent = "Взятые Повторяющиеся Доступные Завершенные";
      }
      callback();
    },
    clearTimeout() {},
    addEventListener(type, callback) { listeners[type] = callback; },
    removeEventListener() {}, postMessage(message) { messages.push(message); },
  };
  root.top = root; root.window = root;
  vm.runInNewContext(source, { window: root, console, Date: FakeDate });
  listeners.message({ source: root, data: {
    source: `antibot-cv-content:${version}`,
    token: wrongPage ? "catalog-wrong" : "catalog-delayed",
    command: {
      type: "open_quest_catalog",
      payload: { page: 0, verifyTimeoutMs: wrongPage ? 500 : 5000 },
    },
  }});
  await new Promise((resolve) => setImmediate(resolve));
  return { result: JSON.parse(messages[0].message), timerCalls };
}

(async () => {
  const delayed = await runCase({ wrongPage: false });
  assert.strictEqual(delayed.result.ok, true);
  assert.strictEqual(delayed.result.outcome, "CONFIRMED");
  assert.strictEqual(delayed.result.mutationIssued, true);
  assert.strictEqual(delayed.result.shellLoaded, true);
  assert.strictEqual(delayed.result.message, "quest_catalog_opened_confirmed");
  assert.strictEqual(delayed.result.after.mode, "avail");
  assert.strictEqual(delayed.result.after.page, 0);
  assert.ok(delayed.timerCalls >= 3);

  const wrong = await runCase({ wrongPage: true });
  assert.strictEqual(wrong.result.ok, true);
  assert.strictEqual(wrong.result.outcome, "ACK_PENDING");
  assert.strictEqual(wrong.result.mutationIssued, true);
  assert.strictEqual(wrong.result.shellLoaded, true);
  assert.strictEqual(wrong.result.message, "quest_catalog_open_unconfirmed");
  assert.strictEqual(wrong.result.after.mode, "started");
  assert.strictEqual(wrong.result.after.page, 1);
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(["node", "-e", script], cwd=".", text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr


def test_page_bridge_opens_only_snapshot_bound_exact_npc() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const moduleNames = [
  "00_core_combat.js",
  "10_hunt_inventory.js",
  "20_hunt_actions.js",
  "30_navigation_death.js",
  "35_npc_quests.js",
  "40_state_layout_dispatch.js",
];
const source = `(() => {\n${moduleNames.map((name) =>
  fs.readFileSync(`browser_injector/page_bridge_modules/${name}`, "utf8")
).join("\n")}\n})();`;
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
let clicks = 0;
let questClicks = 0;
let answerClicks = 0;
let acceptClicks = 0;
let doneClicks = 0;
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
    if (name === "data-id") return "0";
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
  scripts: [{ src: "", textContent: String.raw`var area = new LocationApp({"area_conf":"<town><item id=\"0\" name=\"Моряк Кентур\" type=\"npc\" href=\"/npc.php?action=enter&amp;ref=540&amp;secret-token\" mode=\"npc\" /></town>"});` }],
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
    if (name === "href") return "npc.php?f_id=0&npc_id=75&global_npc=0&quest_id=314&secret";
    return null;
  },
  getClientRects() { return [{ width: 20, height: 10 }]; },
  closest() { return questContainer; },
  click() {
    questClicks += 1;
    root.location.href = "https://3kingdoms.ru/npc.php?f_id=0&npc_id=75&quest_id=314&point_id=400";
    root.document = detailDocument;
  },
};
const detailTitle = { innerText: "Письмо моряку", textContent: "Письмо моряку" };
const longAnswerText = "Прошу, не карай меня так, доблестный воитель! Помутилось сознание моё, когда попытался я забрать сей нож. Никогда прежде не делал я такого и в будущем не поступлю подобным образом! Что могу сделать я, дабы искупить вину?";
const answerAction = {
  tagName: "TABLE",
  innerText: longAnswerText,
  textContent: longAnswerText,
  disabled: false,
  getAttribute(name) {
    // Live NPC replies can omit action=answer and identify the reply by its
    // quest id plus ref alone.
    if (name === "onclick") return "location.href='npc.php?f_id=0&npc_id=75&quest_id=314&point_id=400&ref=401&secret'";
    return null;
  },
  getClientRects() { return [{ width: 100, height: 30 }]; },
  closest() { return this; },
  click() {
    answerClicks += 1;
    root.location.href = "https://3kingdoms.ru/npc.php?f_id=0&npc_id=75&quest_id=314&point_id=400&action=answer&ref=401";
    root.document = terminalDocument;
  },
};
const acceptImage = {
  getAttribute(name) { return name === "alt" ? "Взять задание" : null; },
};
const acceptForm = {
  action: "npc.php?f_id=0&npc_id=75&quest_id=314&point_id=400&action=done&secret",
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
const doneImage = {
  getAttribute(name) { return name === "alt" ? "Завершить задание" : null; },
};
const doneForm = {
  action: "npc.php?f_id=0&npc_id=75&quest_id=315&point_id=402&action=done&secret",
  getAttribute(name) { return name === "action" ? this.action : null; },
};
const doneButton = {
  tagName: "BUTTON", innerText: "", textContent: "", disabled: false, form: doneForm,
  getAttribute() { return null; },
  getClientRects() { return [{ width: 100, height: 30 }]; },
  querySelector(selector) { return selector === "img[alt]" ? doneImage : null; },
  closest() { return doneForm; },
  click() { doneClicks += 1; },
};
const terminalDocument = {
  title: "Письмо моряку",
  readyState: "complete",
  body: { innerText: "Письмо моряку Моряк Кентур Ваша цель: доставить письмо", textContent: "" },
  querySelectorAll(selector) {
    if (selector === "h2") return [header, detailTitle];
    if (selector === "a[href],button,input[type='button'],input[type='submit'],[onclick]") return [acceptButton, doneButton];
    return [];
  },
};
const detailDocument = {
  title: "Письмо моряку",
  readyState: "complete",
  body: { innerText: `Письмо моряку Моряк Кентур ${longAnswerText}`, textContent: "" },
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
  location: {
    href: "https://3kingdoms.ru/area.php?location_id=125",
    assign(value) {
      clicks += 1;
      this.href = new URL(value, "https://3kingdoms.ru/area.php").href;
      const parsed = new URL(this.href);
      if (parsed.searchParams.get("quest_id") === "314" && parsed.searchParams.get("ref") === "401") {
        root.document = terminalDocument;
      } else {
        root.document = npcDocument;
      }
    },
  },
  frames: [],
  document: areaDocument,
  area: { model: { area: { title: "Порт безбрежного моря" } }, controller: { compass: { data: { location: 125 } } } },
  setTimeout,
  addEventListener(type, callback) { listeners[type] = callback; },
  removeEventListener() {},
  postMessage(message) { messages.push(message); },
};
root.top = root; root.window = root;
vm.runInNewContext(source, { window: root, console, setTimeout, URL, URLSearchParams });
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
    expectedSnapshotId: "wrong", expectedLocationId: "125", npcId: "0", expectedRouteRef: "540", expectedName: "Моряк Кентур",
  });
  assert.strictEqual(stale.ok, false);
  assert.strictEqual(stale.message.message, "area_npc_snapshot_stale");
  assert.strictEqual(clicks, 0);

  const invalidInstance = await command("open_exact_npc", {
    expectedSnapshotId: observed.message.snapshotId,
    expectedLocationId: "125", npcId: "0", expectedRouteRef: "540", expectedName: "Моряк Кентур",
    expectedDialogName: "Моряка Кентура", expectedNpcInstanceId: "1".repeat(41),
  });
  assert.strictEqual(invalidInstance.message.outcome, "NOT_ISSUED");
  assert.strictEqual(invalidInstance.message.message, "npc_instance_identity_invalid");
  assert.strictEqual(clicks, 0);

  const wrongInstance = await command("open_exact_npc", {
    expectedSnapshotId: observed.message.snapshotId,
    expectedLocationId: "125", npcId: "0", expectedRouteRef: "540", expectedName: "Моряк Кентур",
    expectedDialogName: "Моряка Кентура", expectedNpcInstanceId: "74",
    verifyTimeoutMs: 100,
  });
  assert.notStrictEqual(wrongInstance.message.outcome, "CONFIRMED");
  assert.strictEqual(wrongInstance.message.outcome, "ACK_PENDING");
  assert.strictEqual(clicks, 1);
  root.location.href = "https://3kingdoms.ru/area.php?location_id=125";
  root.document = areaDocument;
  const observedAgain = await command("area_npc_snapshot", { expectedName: "Моряк Кентур" });

  const opened = await command("open_exact_npc", {
    expectedSnapshotId: observedAgain.message.snapshotId,
    expectedLocationId: "125",
    npcId: "0",
    expectedRouteRef: "540",
    expectedName: "Моряк Кентур",
    expectedDialogName: "Моряка Кентура",
    expectedNpcInstanceId: "75",
    verifyTimeoutMs: 250,
  });
  assert.strictEqual(opened.ok, true);
  assert.strictEqual(opened.message.message, "npc_opened_confirmed");
  assert.strictEqual(clicks, 2);
  assert.strictEqual(opened.message.outcome, "CONFIRMED");
  assert.strictEqual(opened.message.after.identityMatches, true);
  assert.strictEqual(opened.message.after.questActions[0].questId, "314");

  const submitted = await command("npc_quest_action", {
    expectedSnapshotId: opened.message.after.snapshotId,
    npcId: "0",
    questId: "314",
    expectedTitle: "Письмо моряку",
    action: "open",
  });
  assert.strictEqual(submitted.ok, true);
  assert.strictEqual(submitted.message.message, "npc_quest_action_submitted");
  assert.strictEqual(submitted.message.outcome, "ACK_PENDING");
  assert.strictEqual(submitted.message.mutationIssued, true);
  assert.strictEqual(questClicks, 1);

  const detail = await command("npc_dialog_snapshot", { expectedName: "Моряк Кентур", expectedNpcId: "0" });
  assert.strictEqual(detail.ok, true);
  assert.strictEqual(detail.message.dialogActions.length, 1);
  assert.strictEqual(detail.message.dialogActions[0].ref, "401");
  assert.strictEqual(detail.message.dialogActions[0].text, longAnswerText);
  assert.ok(detail.message.dialogActions[0].text.length > 180);
  header.innerText = "КУЯВСКИЙ ПОСОЛ ЩАЖАРД";
  header.textContent = "КУЯВСКИЙ ПОСОЛ ЩАЖАРД";
  const declinedRole = await command("npc_dialog_snapshot", {
    expectedName: "куявскому послу Щажарду", expectedNpcId: "0",
  });
  assert.strictEqual(declinedRole.message.identityMatches, true);
  header.innerText = "Моряк Кентур";
  header.textContent = "Моряк Кентур";
  const wrongName = await command("npc_dialog_snapshot", { expectedName: "Другой NPC", expectedNpcId: "0" });
  assert.strictEqual(wrongName.message.npcId, "0");
  assert.strictEqual(wrongName.message.identityMatches, false);
  const rejectedWrongName = await command("npc_quest_action", {
    expectedSnapshotId: wrongName.message.snapshotId,
    npcId: "0", questId: "314", expectedTitle: "Письмо моряку",
    action: "answer", expectedRef: "401", expectedText: longAnswerText,
  });
  assert.strictEqual(rejectedWrongName.ok, false);
  assert.strictEqual(rejectedWrongName.message.outcome, "NOT_ISSUED");
  assert.strictEqual(rejectedWrongName.message.message, "npc_dialog_identity_mismatch");
  assert.strictEqual(answerClicks, 0);
  const answerSnapshot = await command("npc_dialog_snapshot", { expectedName: "Моряк Кентур", expectedNpcId: "0" });
  const contradictedIdentity = await command("npc_quest_action", {
    expectedSnapshotId: answerSnapshot.message.snapshotId,
    npcId: "0", expectedName: "Другой NPC", questId: "314",
    expectedTitle: "Письмо моряку", action: "answer",
    expectedRef: "401", expectedText: longAnswerText,
  });
  assert.strictEqual(contradictedIdentity.ok, false);
  assert.strictEqual(contradictedIdentity.message.message, "npc_dialog_identity_mismatch");
  assert.strictEqual(answerClicks, 0);
  const answered = await command("npc_quest_action", {
    expectedSnapshotId: answerSnapshot.message.snapshotId,
    npcId: "0",
    expectedName: "Моряк Кентур",
    questId: "314",
    expectedTitle: "Письмо моряку",
    action: "answer",
    expectedRef: "401",
    expectedText: longAnswerText,
  });
  assert.strictEqual(answered.ok, true);
  assert.strictEqual(answered.message.outcome, "ACK_PENDING");
  assert.strictEqual(answerClicks, 0);
  assert.ok(root.location.href.includes("quest_id=314"));
  assert.ok(root.location.href.includes("ref=401"));
  const terminal = await command("npc_dialog_snapshot", { expectedName: "Моряк Кентур", expectedNpcId: "0" });
  assert.strictEqual(terminal.message.doneActions.length, 2);
  assert.deepStrictEqual(
    JSON.parse(JSON.stringify(terminal.message.doneActions[1])),
    {
      questId: "315", action: "done", pointId: "402", text: "Завершить задание",
          npcId: "0", npcInstanceId: "75", visible: true, disabled: false,
    }
  );
  assert.strictEqual(terminal.message.acceptActions.length, 1);
  assert.strictEqual(terminal.message.acceptActions[0].text, "Взять задание");
  const wrongPoint = await command("npc_quest_action", {
    expectedSnapshotId: terminal.message.snapshotId,
        npcId: "0",
    questId: "315",
    action: "done",
    expectedPointId: "401",
    expectedText: "Завершить задание",
  });
  assert.strictEqual(wrongPoint.ok, false);
  assert.strictEqual(wrongPoint.message.message, "npc_quest_action_missing");
  assert.strictEqual(doneClicks, 0);
  const completed = await command("npc_quest_action", {
    expectedSnapshotId: terminal.message.snapshotId,
        npcId: "0",
    questId: "315",
    action: "done",
    expectedPointId: "402",
    expectedText: "Завершить задание",
  });
  assert.strictEqual(completed.ok, true);
  assert.strictEqual(completed.message.outcome, "ACK_PENDING");
  assert.strictEqual(completed.message.mutationIssued, true);
  assert.strictEqual(completed.message.message, "npc_quest_action_submitted");
  assert.ok(completed.message.destination);
  assert.ok(completed.message.issuedAt);
  assert.strictEqual(doneClicks, 1);
      const acceptSnapshot = await command("npc_dialog_snapshot", { expectedName: "Моряк Кентур", expectedNpcId: "0" });
  const accepted = await command("npc_quest_action", {
    expectedSnapshotId: acceptSnapshot.message.snapshotId,
        npcId: "0",
    questId: "314",
    expectedTitle: "Письмо моряку",
    action: "accept",
    expectedText: "Взять задание",
  });
  assert.strictEqual(accepted.ok, true);
  assert.strictEqual(accepted.message.outcome, "ACK_PENDING");
  assert.strictEqual(acceptClicks, 1);
  root.location.href = "https://3kingdoms.ru/area.php?location_id=125";
  root.document = areaDocument;
  root.location.assign = () => { clicks += 1; };
  const delayedObservation = await command("area_npc_snapshot", { expectedName: "Моряк Кентур" });
  const delayed = await command("open_exact_npc", {
    expectedSnapshotId: delayedObservation.message.snapshotId,
        expectedLocationId: "125", npcId: "0", expectedRouteRef: "540", expectedName: "Моряк Кентур",
    verifyTimeoutMs: 100,
  });
  assert.strictEqual(delayed.ok, true);
  assert.strictEqual(delayed.message.outcome, "ACK_PENDING");
  assert.strictEqual(delayed.message.mutationIssued, true);
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(["node", "-e", script], cwd=".", text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr
