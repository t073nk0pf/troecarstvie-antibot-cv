from __future__ import annotations

import subprocess


def test_authored_chat_module_emits_only_new_exact_collection_lines() -> None:
    script = r'''
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = "(() => {\n" +
  fs.readFileSync("browser_injector/page_bridge_modules/00_core_combat.js", "utf8") + "\n" +
  fs.readFileSync("browser_injector/page_bridge_modules/36_quest_chat_progress.js", "utf8") + "\n" +
  fs.readFileSync("browser_injector/page_bridge_modules/40_state_layout_dispatch.js", "utf8") +
  "\n})();\n";
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
const root = {
  name: "top",
  location: { href: "https://3kingdoms.ru/main.php" },
  frames: [],
  document: { querySelectorAll() { return []; } },
  setTimeout,
  addEventListener(type, callback) { listeners[type] = callback; },
  postMessage(message) { messages.push(message); },
};
root.top = root;
root.window = root;
const chatBody = {
  innerText: "03:11 Вы набрали достаточное количество древесины.\n03:12 Обычное сообщение"
};
const chat = {
  name: "chat",
  location: { href: "https://3kingdoms.ru/chat.php" },
  frames: [],
  document: { body: chatBody },
};
root.frames = [chat];

vm.runInNewContext(source, { window: root, console, setTimeout, Date, Set, Map, Math });

function snapshot() {
  messages.length = 0;
  listeners.message({
    source: root,
    data: {
      source: `antibot-cv-content:${version}`,
      token: "token-chat",
      command: { type: "state_snapshot", payload: { include: ["questChatProgress"] } },
    },
  });
  assert.strictEqual(messages.length, 1);
  return JSON.parse(messages[0].message).sections.questChatProgress.data;
}

const baseline = snapshot();
assert.strictEqual(baseline.loadStatus, "loaded");
assert.strictEqual(baseline.observations.length, 1);
assert.strictEqual(baseline.observations[0].resource, "древесины");
assert.strictEqual(baseline.observations[0].isNew, false);

chatBody.innerText = [
  "03:12 Обычное сообщение",
  "03:13 Вы набрали необходимое количество осиных крыльев!",
  "03:14 Получено достаточное количество волчьих шкур",
].join("\n");
const observed = snapshot();
assert.strictEqual(observed.truncated, false);
assert.strictEqual(observed.observations.length, 1);
assert.strictEqual(observed.observations[0].text, "Вы набрали необходимое количество осиных крыльев!");
assert.strictEqual(observed.observations[0].resource, "осиных крыльев");
assert.strictEqual(observed.observations[0].isNew, true);
const firstSeen = observed.observations[0].observedAt;

const repeated = snapshot();
assert.strictEqual(repeated.observations.length, 1);
// A new line remains deliverable for multiple snapshots.  The Python tracker
// applies the bounded age check and consumes its stable event identity once.
assert.strictEqual(repeated.observations[0].isNew, true);
assert.strictEqual(repeated.observations[0].observedAt, firstSeen);

// Removing an unrelated leading line shifts the rolling window, but fallback
// identity is occurrence-aware and must not relabel the old quest line.
chatBody.innerText = [
  "03:13 Вы набрали необходимое количество осиных крыльев!",
  "03:14 Получено достаточное количество волчьих шкур",
].join("\n");
const shifted = snapshot();
assert.strictEqual(shifted.observations.length, 1);
assert.strictEqual(shifted.observations[0].isNew, true);
assert.strictEqual(shifted.observations[0].observedAt, firstSeen);

// Once the old line has scrolled out, a later server line with the same
// semantic resource text but a different visible timestamp is a new event.
chatBody.innerText = "03:19 Обычное сообщение";
assert.strictEqual(snapshot().observations.length, 0);
chatBody.innerText = "03:20 Вы набрали необходимое количество осиных крыльев!";
const laterIdentical = snapshot();
assert.strictEqual(laterIdentical.observations.length, 1);
assert.strictEqual(laterIdentical.observations[0].text, shifted.observations[0].text);
assert.strictEqual(laterIdentical.observations[0].resource, shifted.observations[0].resource);
assert.strictEqual(laterIdentical.observations[0].isNew, true);
assert.notStrictEqual(laterIdentical.observations[0].eventId, shifted.observations[0].eventId);

// Two identical semantic messages with different visible timestamps keep
// independent stable identities when the earlier line later scrolls out.
chatBody.innerText = [
  "03:20 Вы набрали необходимое количество осиных крыльев!",
  "03:21 Вы набрали необходимое количество осиных крыльев!",
].join("\n");
const coexisting = snapshot();
assert.strictEqual(coexisting.observations.length, 2);
const secondIdentity = coexisting.observations[1].eventId;
const secondFirstSeen = coexisting.observations[1].observedAt;
chatBody.innerText = "03:21 Вы набрали необходимое количество осиных крыльев!";
const firstScrolledOut = snapshot();
assert.strictEqual(firstScrolledOut.observations.length, 1);
assert.strictEqual(firstScrolledOut.observations[0].eventId, secondIdentity);
assert.strictEqual(firstScrolledOut.observations[0].isNew, true);
assert.strictEqual(firstScrolledOut.observations[0].observedAt, secondFirstSeen);

// Live layouts may expose visible chat through a generic nested frame.
chat.name = "bottom";
chat.location.href = "https://3kingdoms.ru/game_frame.php";
chatBody.innerText = "03:22 Вы набрали необходимое количество крови кабанов!";
const anonymousChat = snapshot();
assert.strictEqual(anonymousChat.loadStatus, "loaded");
assert.strictEqual(anonymousChat.observations.length, 1);
assert.strictEqual(anonymousChat.observations[0].resource, "крови кабанов");
assert.strictEqual(anonymousChat.observations[0].isNew, true);

// The live chat visually separates system rows even when a container exposes
// several of them as one whitespace-joined DOM text node.
chatBody.innerText = [
  "x".repeat(900),
  "18:58 Гурум-корень 1 шт (теперь их 10)",
  "18:58 Вы набрали необходимое количество Гурум-корней!",
  "18:58 Награда за бой",
].join(" ");
const joinedSystemRows = snapshot();
assert.strictEqual(joinedSystemRows.observations.length, 1);
assert.strictEqual(joinedSystemRows.observations[0].text, "Вы набрали необходимое количество Гурум-корней!");
assert.strictEqual(joinedSystemRows.observations[0].resource, "Гурум-корней");
assert.strictEqual(joinedSystemRows.observations[0].isNew, true);
'''
    subprocess.run(["node", "-e", script], check=True, cwd=".")


def test_authored_chat_module_bounds_and_rejects_truncated_snapshot() -> None:
    source = (
        "browser_injector/page_bridge_modules/36_quest_chat_progress.js"
    )
    text = open(source, encoding="utf-8").read()
    assert "querySelectorAll(\"*\")" not in text
    assert "slice(-40)" in text
    assert "questChatKnownOrder.length > 256" in text
    assert "[data-message-id]" in text
    assert "node.getAttribute(\"data-msg-id\")" in text
    assert "observedAt: record.firstSeenAt" in text
    assert 'const occurrenceKey = `${parsed.discriminator || "no-visible-time"}:${parsed.text}`' in text
