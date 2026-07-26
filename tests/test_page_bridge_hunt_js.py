"""Hunt bridge behavior tests."""
from __future__ import annotations

import subprocess


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

def test_page_bridge_filters_and_attacks_visible_hunt_target_by_exact_bot_id() -> None:
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
const bots = [
  { id: 610, name: "Попутный волк [4]", shortName: "Попутный волк [4]", lvl: 4, x: 100, y: 100, fightId: 0, agrforbid: false, isBot: true },
  { id: 611, name: "Основной бес [5]", shortName: "Основной бес [5]", lvl: 5, x: 120, y: 120, fightId: 0, agrforbid: false, isBot: true },
  { id: 612, name: "Основной бес [4]", shortName: "Основной бес [4]", lvl: 4, x: 90, y: 90, fightId: 0, agrforbid: false, isBot: true },
  { id: 613, name: "Попутный волк [5]", shortName: "Попутный волк [5]", lvl: 5, x: 80, y: 80, fightId: 0, agrforbid: false, isBot: true },
];
const main = { location: { href: "https://3kingdoms.ru/hunt.php" } };
const mainFrame = { frames: [main] };
mainFrame.frames.main = main;
const root = {
  name: "top",
  location: { href: "https://3kingdoms.ru/main.php" },
  frames: [mainFrame],
  document: { title: "", querySelectorAll() { return []; } },
  addEventListener(type, callback) { listeners[type] = callback; },
  removeEventListener() {},
  postMessage(message) { messages.push(message); },
  setTimeout,
};
root.frames.main_frame = mainFrame;
root.top = root;
root.window = root;
root.hunt = {
  model: { bots: { list: bots } },
  view: {
    width: 750,
    height: 750,
    viewBounds: { x: 0, y: 0, w: 750, h: 750, ap: 0, rp: 0 },
    content: { x: 0, y: 0, bots: { x: 0, y: 0, children: [] } },
  },
};
root.getHuntApp = () => root.hunt;
let attackedBotId = null;
root.huntAttack = (botId) => {
  attackedBotId = botId;
  const bot = bots.find((candidate) => candidate.id === botId);
  if (bot) bot.fightId = 77;
};

vm.runInNewContext(source, { window: root, console, setTimeout, clearTimeout });

async function command(type, payload = {}) {
  messages.length = 0;
  listeners.message({
    source: root,
    data: { source: `antibot-cv-content:${version}`, token: "token", command: { type, payload } },
  });
  const deadline = Date.now() + 1000;
  while (!messages.length && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  assert.strictEqual(messages.length, 1);
  return { ok: messages[0].ok, message: JSON.parse(messages[0].message) };
}

(async () => {
  const prioritized = await command("visible_hunt_targets", {
    targetSpecs: [
      {name: "Основной бес", level: 5},
      {name: "Попутный волк", level: 4},
    ],
  });
  assert.deepStrictEqual(prioritized.message.targets.map((target) => target.botId), [611, 610]);
  assert.deepStrictEqual(prioritized.message.targets.map((target) => target.targetPriority), [0, 1]);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(prioritized.message.targetSpecs)), [
    {name: "Основной бес", level: 5},
    {name: "Попутный волк", level: 4},
  ]);

  const visible = await command("visible_hunt_targets", { allowedBotIds: [611] });
  assert.strictEqual(visible.ok, true);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(visible.message.allowedBotIds)), [611]);
  assert.deepStrictEqual(visible.message.targets.map((target) => target.botId), [611]);

  const alias = await command("visible_hunt_targets", { allowedBotIds: [], botId: "610" });
  assert.deepStrictEqual(JSON.parse(JSON.stringify(alias.message.allowedBotIds)), [610]);
  assert.deepStrictEqual(alias.message.targets.map((target) => target.botId), [610]);

  const attacked = await command("attack_visible_bot", { allowedBotIds: [611], confirmed: 1, verifyTimeoutMs: 500 });
  assert.strictEqual(attacked.ok, true);
  assert.strictEqual(attackedBotId, 611);
  assert.strictEqual(attacked.message.target.botId, 611);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(attacked.message.visible.allowedBotIds)), [611]);

  const missing = await command("attack_visible_bot", { allowedBotIds: [999], confirmed: 1 });
  assert.strictEqual(missing.ok, false);
  assert.strictEqual(missing.message.message, "visible_bot_missing");
  assert.deepStrictEqual(JSON.parse(JSON.stringify(missing.message.visible.allowedBotIds)), [999]);
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
