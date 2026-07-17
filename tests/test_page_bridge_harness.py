"""Bridge behavior tests executed through the shared Node VM harness."""
from __future__ import annotations

from tests.node_bridge_harness import run_bridge_cases


def test_page_bridge_marks_finished_fight_inactive() -> None:
    script = r"""
const assert = require("assert");
const vm = require("vm");

const source = BRIDGE_SOURCE;
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];

function makeWindow(name, href) {
  const win = {
    name,
    location: { href },
    frames: [],
    document: { title: "", querySelectorAllCalls: 0, querySelectorAll() { this.querySelectorAllCalls += 1; return []; } },
  };
  return win;
}

function setupBridge(finished, resultText = null, resourceText = "", skillMode = "confirmed", abilityCount = 1, completeAbilityEvidence = true) {
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
      battleId: "fight-epoch-1",
      fightState: finished ? 2 : 1,
      oppId: finished ? 0 : 123,
      myTurn: !finished,
      enabledControl: true,
      totalDmg: 40,
      player: { position: "front", stance: "attack" },
      abilities: {
        all: Array.from({ length: abilityCount }, (_, index) => ({
          id: -4626 - index, slot: 2 + index, name: `skill-${index}`, ready: true,
          ...(completeAbilityEvidence ? { cooldownRemaining: 0 } : {}),
        })),
      },
    },
  };
  let omitAbilityIdentity = false;
  const abilityElements = fightWin.fight.model.abilities.all.map((ability) => ({
    disabled: false,
    offsetWidth: 20,
    offsetHeight: 20,
    getClientRects() { return [{ width: 20, height: 20 }]; },
    getAttribute(name) {
      const values = {
        "data-slot": String(ability.slot),
        "data-ability-id": String(ability.id),
        "data-ability-name": ability.name,
        "data-ready": "true",
        "data-cooldown": "0",
      };
      return omitAbilityIdentity && ["data-ability-id", "data-ability-name"].includes(name)
        ? null
        : values[name] ?? null;
    },
  }));
  const abilityContainer = { querySelectorAll() { return abilityElements; } };
  fightWin.document.querySelectorAll = function(selector) {
    this.querySelectorAllCalls += 1;
    return selector.includes("data-battle-abilities") ? [abilityContainer] : [];
  };
  fightWin.useSkill = (slot) => {
    fightWin.usedSlot = slot;
    if (skillMode === "stance-promise") {
      fightWin.fight.model.player.position = "back";
      return Promise.resolve("stance-applied");
    }
    if (skillMode === "opaque-object") {
      return { confirmed: true, secret: "must-not-be-serialized" };
    }
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

  return {
    command,
    setAbility(values) { Object.assign(fightWin.fight.model.abilities.all[0], values); },
    setTurn(myTurn, enabledControl = true) {
      fightWin.fight.model.myTurn = myTurn;
      fightWin.fight.model.enabledControl = enabledControl;
    },
    setBattleId(value) { fightWin.fight.model.battleId = value; },
    clearUsedSlot() { fightWin.usedSlot = undefined; },
    removeFight() { fightWin.fight = null; },
    abilityDomScanCount() { return fightWin.document.querySelectorAllCalls; },
    removeAbilityDomEvidence() { fightWin.document.querySelectorAll = () => []; },
    removeAbilityDomIdentity() { omitAbilityIdentity = true; },
    addBattleItemAtSkillSlot() {
      fightWin.fight.model.abilities.all.push({
        id: 3581914081, slot: 2, name: "Превосходный нектар удали", ready: true, cooldownRemaining: 0,
      });
    },
    resourceDomScanCount() { return mainFrame.document.querySelectorAllCalls; },
  };
}

const mutationPayload = (snapshot, overrides = {}) => ({
  slot: snapshot.abilities[0].slot,
  expectedSkillId: snapshot.abilities[0].id,
  expectedSkillName: snapshot.abilities[0].name,
  expectedSkillSlot: snapshot.abilities[0].slot,
  expectedBattleIdentity: snapshot.battleIdentity,
  expectedSnapshotId: snapshot.snapshotId,
  expectedObservationToken: snapshot.observationToken,
  verifyTimeoutMs: 250,
  ...overrides,
});

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

const finishedUseSkill = await finished.command("use_skill_slot", mutationPayload(finishedSnapshot.message));
assert.strictEqual(finishedUseSkill.ok, false);
assert.strictEqual(finishedUseSkill.message.message, "skill_mutation_binding_missing");

const active = setupBridge(false);
const activeSnapshot = await active.command("battle_snapshot");
assert.strictEqual(activeSnapshot.message.hasFight, true);
assert.strictEqual(activeSnapshot.message.finished, false);
assert.strictEqual(activeSnapshot.message.useSkillAvailable, true);
assert.deepStrictEqual(
  JSON.parse(JSON.stringify(activeSnapshot.message.turnEvidence)),
  {
    authoritative: true,
    myTurn: true,
    enabledControl: true,
    sources: ["model.myTurn", "model.enabledControl"],
  }
);

const indexed = setupBridge(false, null, "", "confirmed", 6);
const indexedSnapshot = await indexed.command("battle_snapshot");
assert.strictEqual(indexedSnapshot.message.abilities.length, 6);
assert.strictEqual(indexed.abilityDomScanCount(), 0);

const indexedFallback = setupBridge(false, null, "", "confirmed", 6, false);
const indexedFallbackSnapshot = await indexedFallback.command("battle_snapshot");
assert.strictEqual(indexedFallbackSnapshot.message.abilities.length, 6);
assert.strictEqual(indexedFallback.abilityDomScanCount(), 1);

const cdOnly = setupBridge(false);
cdOnly.setAbility({ ready: undefined, cooldownRemaining: undefined, cd: 0 });
const cdOnlySnapshot = await cdOnly.command("battle_snapshot");
assert.strictEqual(cdOnlySnapshot.message.abilities[0].readinessEvidence.authoritative, true);
assert.strictEqual(cdOnlySnapshot.message.abilities[0].readinessEvidence.ready, true);
assert.strictEqual(cdOnlySnapshot.message.abilities[0].readinessEvidence.cooldownRemaining, 0);
assert.strictEqual(activeSnapshot.message.abilities[0].readinessEvidence.authoritative, true);
assert.strictEqual(activeSnapshot.message.abilities[0].readinessEvidence.ready, true);
assert.strictEqual(activeSnapshot.message.abilities[0].readinessEvidence.cooldownRemaining, 0);
assert.deepStrictEqual(
  JSON.parse(JSON.stringify(activeSnapshot.message.playerStanceState)),
  [
    { path: "model.player.position", type: "string", value: "front" },
    { path: "model.player.stance", type: "string", value: "attack" },
  ]
);

const activeUseSkill = await active.command("use_skill_slot", mutationPayload(activeSnapshot.message));
assert.strictEqual(activeUseSkill.ok, true);
assert.strictEqual(activeUseSkill.usedSlot, 2);
assert.strictEqual(activeUseSkill.message.message, "useSkill_confirmed");
assert.strictEqual(active.resourceDomScanCount(), 1);

const sharedSlot = setupBridge(false);
sharedSlot.addBattleItemAtSkillSlot();
const sharedSlotSnapshot = await sharedSlot.command("battle_snapshot");
const sharedSlotUseSkill = await sharedSlot.command("use_skill_slot", mutationPayload(sharedSlotSnapshot.message));
assert.strictEqual(sharedSlotUseSkill.ok, true);
assert.strictEqual(sharedSlotUseSkill.usedSlot, 2);

const missingDom = setupBridge(false);
const missingDomSnapshot = await missingDom.command("battle_snapshot");
missingDom.removeAbilityDomEvidence();
const missingDomUseSkill = await missingDom.command("use_skill_slot", mutationPayload(missingDomSnapshot.message));
assert.strictEqual(missingDomUseSkill.ok, true);
assert.strictEqual(missingDomUseSkill.message.ability.readinessEvidence.bindingSource, "model");

const genericDom = setupBridge(false);
const genericDomSnapshot = await genericDom.command("battle_snapshot");
genericDom.removeAbilityDomIdentity();
const genericDomUseSkill = await genericDom.command("use_skill_slot", mutationPayload(genericDomSnapshot.message));
assert.strictEqual(genericDomUseSkill.ok, true);
assert.strictEqual(genericDomUseSkill.message.message, "useSkill_confirmed");

const stance = setupBridge(false, null, "", "stance-promise");
const stanceSnapshot = await stance.command("battle_snapshot");
const stanceUseSkill = await stance.command("use_skill_slot", mutationPayload(stanceSnapshot.message));
assert.strictEqual(stanceUseSkill.ok, false);
assert.strictEqual(stanceUseSkill.message.message, "useSkill_unconfirmed");
assert.strictEqual(stanceUseSkill.message.returnObservation.raw.type, "promise");
assert.strictEqual(stanceUseSkill.message.returnObservation.promise.status, "resolved");
assert.deepStrictEqual(
  JSON.parse(JSON.stringify(stanceUseSkill.message.returnObservation.promise.result)),
  { type: "string", length: 14 }
);
assert.strictEqual(JSON.stringify(stanceUseSkill.message).includes("stance-applied"), false);
assert.strictEqual(stanceUseSkill.message.beforePlayerStanceState[0].value, "front");
assert.strictEqual(stanceUseSkill.message.afterPlayerStanceState[0].value, "back");

const opaque = setupBridge(false, null, "", "opaque-object");
const opaqueSnapshot = await opaque.command("battle_snapshot");
const opaqueUseSkill = await opaque.command("use_skill_slot", mutationPayload(opaqueSnapshot.message));
assert.strictEqual(opaqueUseSkill.ok, false);
assert.strictEqual(opaqueUseSkill.message.returnObservation.raw.type, "object");
assert.strictEqual(JSON.stringify(opaqueUseSkill.message).includes("must-not-be-serialized"), false);

for (const mutation of [
  { values: { slot: 3 }, expected: "ability_slot_missing" },
]) {
  const guarded = setupBridge(false);
  const observed = await guarded.command("battle_snapshot");
  guarded.setAbility(mutation.values);
  const rejected = await guarded.command("use_skill_slot", mutationPayload(observed.message));
  assert.strictEqual(rejected.ok, false);
  assert.strictEqual(rejected.message.message, mutation.expected);
  assert.strictEqual(rejected.usedSlot, undefined);
}

for (const values of [
  { ready: false, cooldownRemaining: 2 },
  { ready: undefined, cooldownRemaining: undefined },
]) {
  const nonready = setupBridge(false);
  const observed = await nonready.command("battle_snapshot");
  nonready.setAbility(values);
  const submitted = await nonready.command("use_skill_slot", mutationPayload(observed.message));
  assert.strictEqual(submitted.ok, true);
  assert.strictEqual(submitted.usedSlot, 2);
}

const fabricated = setupBridge(false);
const fabricatedSnapshot = await fabricated.command("battle_snapshot");
const fabricatedUse = await fabricated.command(
  "use_skill_slot",
  mutationPayload(fabricatedSnapshot.message, { expectedObservationToken: "fabricated-token" }),
);
assert.strictEqual(fabricatedUse.ok, false);
assert.strictEqual(fabricatedUse.message.message, "skill_mutation_token_expired_or_unknown");
assert.strictEqual(fabricatedUse.usedSlot, undefined);

const reused = setupBridge(false);
const reusedSnapshot = await reused.command("battle_snapshot");
const firstUse = await reused.command("use_skill_slot", mutationPayload(reusedSnapshot.message));
assert.strictEqual(firstUse.ok, true);
reused.clearUsedSlot();
const reusedUse = await reused.command("use_skill_slot", mutationPayload(reusedSnapshot.message));
assert.strictEqual(reusedUse.ok, false);
assert.strictEqual(reusedUse.message.message, "skill_mutation_token_expired_or_unknown");
assert.strictEqual(reusedUse.usedSlot, undefined);

const expired = setupBridge(false);
const expiredSnapshot = await expired.command("battle_snapshot");
await new Promise((resolve) => setTimeout(resolve, 1250));
const expiredUse = await expired.command("use_skill_slot", mutationPayload(expiredSnapshot.message));
assert.strictEqual(expiredUse.ok, false);
assert.strictEqual(expiredUse.message.message, "skill_mutation_token_expired_or_unknown");
assert.strictEqual(expiredUse.usedSlot, undefined);

const newEpoch = setupBridge(false);
const priorEpochSnapshot = await newEpoch.command("battle_snapshot");
newEpoch.setBattleId("fight-epoch-2");
const priorEpochUse = await newEpoch.command("use_skill_slot", mutationPayload(priorEpochSnapshot.message));
assert.strictEqual(priorEpochUse.ok, false);
assert.strictEqual(priorEpochUse.message.message, "skill_mutation_battle_mismatch");
assert.strictEqual(priorEpochUse.usedSlot, undefined);

const malformedEpoch = setupBridge(false);
malformedEpoch.setBattleId(null);
const malformedSnapshot = await malformedEpoch.command("battle_snapshot");
assert.ok(malformedSnapshot.message.battleIdentity.includes("battle:1"));
assert.ok(malformedSnapshot.message.observationToken);
const malformedUse = await malformedEpoch.command("use_skill_slot", mutationPayload(malformedSnapshot.message));
assert.strictEqual(malformedUse.ok, true);
assert.strictEqual(malformedUse.usedSlot, 2);

const turnFlip = setupBridge(false);
const turnSnapshot = await turnFlip.command("battle_snapshot");
turnFlip.setTurn(false, false);
const turnUse = await turnFlip.command("use_skill_slot", mutationPayload(turnSnapshot.message));
assert.strictEqual(turnUse.ok, false);
assert.strictEqual(turnUse.message.message, "skill_mutation_turn_not_authoritative");
assert.strictEqual(turnUse.usedSlot, undefined);

const failedProbe = setupBridge(false);
const failedProbeSnapshot = await failedProbe.command("battle_snapshot");
failedProbe.removeFight();
const failedProbeUse = await failedProbe.command("use_skill_slot", mutationPayload(failedProbeSnapshot.message));
assert.strictEqual(failedProbeUse.ok, false);
assert.strictEqual(failedProbeUse.message.message, "skill_mutation_battle_mismatch");
assert.strictEqual(failedProbeUse.usedSlot, undefined);
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
"""
    result = run_bridge_cases({"finished_fight": script})["finished_fight"]
    assert result["ok"] is True, result.get("error")

def test_page_bridge_reports_and_uses_battle_items_by_slot_or_name() -> None:
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
    result = run_bridge_cases({"battle_items": script})["battle_items"]
    assert result["ok"] is True, result.get("error")
