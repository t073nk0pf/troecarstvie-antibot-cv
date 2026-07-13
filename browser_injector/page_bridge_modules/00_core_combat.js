  const BRIDGE_VERSION = "2026-07-13-quest-scope-v50";
  const CONTENT_SOURCE = `antibot-cv-content:${BRIDGE_VERSION}`;
  const INJECTOR_SOURCE = `antibot-cv-injector:${BRIDGE_VERSION}`;

  if (window.__antibotCvPageBridgeVersion === BRIDGE_VERSION) {
    return;
  }
  window.__antibotCvPageBridgeInstalled = true;
  window.__antibotCvPageBridgeVersion = BRIDGE_VERSION;
  let stateSnapshotSequence = 0;

  const send = (token, ok, message) => {
    const serialized = typeof message === "string" ? message : JSON.stringify(message);
    window.postMessage(
      {
        source: INJECTOR_SOURCE,
        token,
        ok,
        message: serialized,
      },
      "*"
    );
  };

  const safeString = (value, maxLength = 160) => {
    if (value == null) {
      return "";
    }
    return String(value).replace(/\s+/g, " ").trim().slice(0, maxLength);
  };

  const delayMs = (value) =>
    new Promise((resolve) => {
      const waitMs = Math.max(0, parseInt(value, 10) || 0);
      if (!waitMs) {
        resolve();
        return;
      }
      const root = window.top || window;
      const schedule =
        root && typeof root.setTimeout === "function"
          ? root.setTimeout.bind(root)
          : typeof setTimeout === "function"
            ? setTimeout
            : null;
      if (!schedule) {
        resolve();
        return;
      }
      schedule(resolve, waitMs);
    });

  const interestingName = (name) =>
    /hunt|attack|battle|fight|mob|monster|target|click|process|menu|map|move|select|use|exit|cast|ability|slot/i.test(name);

  const listFunctions = (win) => {
    const names = new Set();
    for (let obj = win; obj; obj = Object.getPrototypeOf(obj)) {
      let props = [];
      try {
        props = Object.getOwnPropertyNames(obj);
      } catch (_) {
        continue;
      }
      for (const name of props) {
        if (!interestingName(name)) {
          continue;
        }
        try {
          if (typeof win[name] === "function") {
            names.add(name);
          }
        } catch (_) {}
      }
    }
    return Array.from(names).sort().slice(0, 120);
  };

  const listClickableElements = (doc) => {
    let elements = [];
    try {
      elements = Array.from(
        doc.querySelectorAll("a[href],button,input[type='button'],input[type='submit'],area[onclick],[onclick]")
      );
    } catch (_) {
      return [];
    }
    return elements.slice(0, 120).map((el) => ({
      tag: safeString(el.tagName, 24),
      id: safeString(el.id, 80),
      name: safeString(el.getAttribute("name"), 80),
      className: safeString(el.className, 120),
      text: safeString(el.innerText || el.value || el.getAttribute("title") || el.getAttribute("alt"), 160),
      href: safeString(el.getAttribute("href"), 180),
      onclick: safeString(el.getAttribute("onclick"), 220),
    }));
  };

  const describeWindow = (win, path, depth, seen) => {
    const item = {
      path,
      accessible: false,
      name: "",
      href: "",
      title: "",
      functions: [],
      clickables: [],
      frames: [],
    };
    try {
      if (seen.has(win)) {
        return { ...item, accessible: true, repeated: true };
      }
      seen.add(win);
      item.accessible = true;
      item.name = safeString(win.name, 80);
      item.href = safeString(win.location && win.location.href, 240);
      item.title = safeString(win.document && win.document.title, 160);
      item.functions = listFunctions(win);
      item.clickables = listClickableElements(win.document);
      if (depth > 0 && win.frames) {
        for (let index = 0; index < Math.min(win.frames.length, 12); index += 1) {
          let child = null;
          try {
            child = win.frames[index];
          } catch (_) {}
          if (child) {
            item.frames.push(describeWindow(child, `${path}.frames[${index}]`, depth - 1, seen));
          }
        }
      }
    } catch (error) {
      item.error = safeString(error && error.message ? error.message : error, 200);
    }
    return item;
  };

  const probePage = () => {
    const root = window.top || window;
    return {
      bridgeVersion: BRIDGE_VERSION,
      generatedAt: new Date().toISOString(),
      root: describeWindow(root, "top", 3, new Set()),
    };
  };

  const inspectFunctionsInWindow = (win, path, names, depth, seen, output) => {
    try {
      if (seen.has(win)) {
        return;
      }
      seen.add(win);
      for (const name of names) {
        try {
          const value = win[name];
          if (typeof value !== "function") {
            continue;
          }
          output.push({
            path,
            href: safeString(win.location && win.location.href, 240),
            name,
            length: value.length,
            source: safeString(Function.prototype.toString.call(value), 3000),
          });
        } catch (_) {}
      }
      if (depth > 0 && win.frames) {
        for (let index = 0; index < Math.min(win.frames.length, 12); index += 1) {
          try {
            inspectFunctionsInWindow(win.frames[index], `${path}.frames[${index}]`, names, depth - 1, seen, output);
          } catch (_) {}
        }
      }
    } catch (_) {}
  };

  const inspectFunctions = (payload) => {
    const rawNames = Array.isArray(payload && payload.names) ? payload.names : [];
    const names = rawNames
      .map((name) => safeString(name, 80))
      .filter((name) => /^[A-Za-z_$][\w$]*$/.test(name))
      .slice(0, 40);
    const output = [];
    inspectFunctionsInWindow(window.top || window, "top", names, 3, new Set(), output);
    return {
      bridgeVersion: BRIDGE_VERSION,
      inspected: names,
      matches: output,
    };
  };

  const walkWindows = (win, path, depth, seen, visitor) => {
    try {
      if (!win || seen.has(win)) {
        return;
      }
      seen.add(win);
      visitor(win, path);
      if (depth <= 0 || !win.frames) {
        return;
      }
      for (let index = 0; index < Math.min(win.frames.length, 12); index += 1) {
        try {
          walkWindows(win.frames[index], `${path}.frames[${index}]`, depth - 1, seen, visitor);
        } catch (_) {}
      }
    } catch (_) {}
  };

  const findFightWindow = () => {
    const matches = [];
    walkWindows(window.top || window, "top", 4, new Set(), (win, path) => {
      try {
        const href = safeString(win.location && win.location.href, 240);
        if (win.fight || typeof win.useSkill === "function" || /\/fight\.php(?:\?|$)/.test(href)) {
          matches.push({ win, path, href });
        }
      } catch (_) {}
    });
    return matches.find((item) => item.win && item.win.fight) || matches.find((item) => typeof item.win.useSkill === "function") || matches[0] || null;
  };

  const abilitySummary = (ability) => ({
    id: ability && ability.id != null ? ability.id : null,
    slot: ability && ability.slot != null ? ability.slot : null,
    name: safeString(ability && (ability.name || ability.title || ability.caption), 120),
    ready: ability && ability.ready != null ? Boolean(ability.ready) : null,
    disabled: ability && ability.disabled != null ? Boolean(ability.disabled) : null,
    cooldown: ability && ability.cooldown != null ? ability.cooldown : null,
  });

  const battleItemSummary = (item) => ({
    id: item && item.id != null ? item.id : item && item.itemId != null ? item.itemId : null,
    slot: item && item.slot != null ? item.slot : item && item.slotIndex != null ? item.slotIndex : null,
    name: safeString(item && (item.name || item.title || item.caption || item.label), 120),
    ready: item && item.ready != null ? Boolean(item.ready) : null,
    disabled: item && item.disabled != null ? Boolean(item.disabled) : null,
    cooldown: item && item.cooldown != null ? item.cooldown : null,
    quantity:
      item && item.quantity != null
        ? item.quantity
        : item && item.count != null
          ? item.count
          : item && item.amount != null
            ? item.amount
            : item && item.qty != null
              ? item.qty
              : item && item.cnt != null
                ? item.cnt
                : null,
  });

  const asArray = (value) => {
    if (Array.isArray(value)) {
      return value;
    }
    if (value && typeof value === "object") {
      return Object.values(value);
    }
    return [];
  };

  const collectBattleItems = (fight) => {
    const model = fight && fight.model ? fight.model : {};
    const abilityItems = asArray(model.abilities && model.abilities.all).filter(
      (entry) => entry && typeof entry === "object" && Number(entry.id) > 0
    );
    const sources = [
      abilityItems,
      model.items && model.items.all,
      model.items,
      model.usables && model.usables.all,
      model.usables,
      model.consumables && model.consumables.all,
      model.consumables,
      model.artifacts && model.artifacts.all,
      model.artifacts,
      model.inventory && model.inventory.all,
      model.inventory,
      model.quickItems && model.quickItems.all,
      model.quickItems,
    ];
    const items = [];
    const seen = new Set();
    for (const source of sources) {
      for (const entry of asArray(source)) {
        if (!entry || typeof entry !== "object") {
          continue;
        }
        const summary = battleItemSummary(entry);
        const key = `${summary.id}:${summary.slot}:${summary.name}`;
        if (seen.has(key)) {
          continue;
        }
        seen.add(key);
        items.push(summary);
      }
    }
    return items;
  };

  const fightModelState = (fight) => {
    const model = fight && fight.model ? fight.model : null;
    return {
      rawHasFight: Boolean(fight),
      finished: Boolean(model && model.finished),
      fightState: model && model.fightState != null ? model.fightState : null,
      oppId: model && model.oppId != null ? model.oppId : null,
      myTurn: model && model.myTurn != null ? Boolean(model.myTurn) : null,
      enabledControl: model && model.enabledControl != null ? Boolean(model.enabledControl) : null,
      totalDmg: model && model.totalDmg != null ? model.totalDmg : null,
    };
  };

  const battleOutcome = (item, state) => {
    if (!state.finished) return { outcome: null, evidence: "battle_active" };
    let text = "";
    try {
      const doc = item && item.win && item.win.document;
      text = safeString(doc && doc.body && (doc.body.innerText || doc.body.textContent), 2000).toLowerCase();
    } catch (_) {}
    if (/вы\s+победили|вы\s+нанесли[^.!]{0,80}(?:последний|смертельный)|победа/.test(text)) {
      return { outcome: "victory", evidence: "fight_result_text" };
    }
    if (/вы\s+проиграли|поражение|вы\s+(?:погибли|убиты)/.test(text)) {
      return { outcome: "defeat", evidence: "fight_result_text" };
    }
    const resources = compactResourceSnapshot(resourceSnapshot());
    const healthPercent = Number(resources.healthPercent);
    if (Number.isFinite(healthPercent)) {
      if (healthPercent <= 0) {
        return { outcome: "defeat", evidence: "finished_player_health_zero" };
      }
      return { outcome: "victory", evidence: "finished_player_alive" };
    }
    return { outcome: "unknown", evidence: "finished_without_known_result" };
  };

  const battleSnapshot = () => {
    const item = findFightWindow();
    if (!item) {
      return {
        bridgeVersion: BRIDGE_VERSION,
        generatedAt: new Date().toISOString(),
        hasFight: false,
        rawHasFight: false,
        finished: false,
        outcome: null,
        outcomeEvidence: "fight_missing",
        fightPath: "",
        fightHref: "",
        useSkillAvailable: false,
        abilities: [],
        items: [],
      };
    }
    const fight = item.win.fight || null;
    const state = fightModelState(fight);
    const result = battleOutcome(item, state);
    const hasFight = Boolean(fight) && !state.finished;
    let abilities = [];
    try {
      const all = fight && fight.model && fight.model.abilities && fight.model.abilities.all;
      abilities = Array.isArray(all) ? all.map(abilitySummary) : [];
    } catch (_) {}
    return {
      bridgeVersion: BRIDGE_VERSION,
      generatedAt: new Date().toISOString(),
      hasFight,
      ...state,
      outcome: result.outcome,
      outcomeEvidence: result.evidence,
      fightPath: item.path,
      fightHref: item.href,
      useSkillAvailable: hasFight && typeof item.win.useSkill === "function",
      abilities,
      items: collectBattleItems(fight),
    };
  };

  const functionSource = (owner, name, maxLength = 1800) => {
    try {
      const value = owner && owner[name];
      if (typeof value === "function") {
        return safeString(Function.prototype.toString.call(value), maxLength);
      }
    } catch (_) {}
    return "";
  };

  const functionList = (owner) => {
    if (!owner) {
      return [];
    }
    const names = new Set();
    for (let obj = owner; obj && obj !== Object.prototype; obj = Object.getPrototypeOf(obj)) {
      let props = [];
      try {
        props = Object.getOwnPropertyNames(obj);
      } catch (_) {
        continue;
      }
      for (const name of props) {
        try {
          if (typeof owner[name] === "function") {
            names.add(name);
          }
        } catch (_) {}
      }
    }
    return Array.from(names).sort().slice(0, 160);
  };

  const battleDebug = () => {
    const item = findFightWindow();
    if (!item || !item.win.fight) {
      return { ok: false, message: "fight_missing", snapshot: battleSnapshot() };
    }
    const fight = item.win.fight;
    const model = fight.model || null;
    const controller = fight.controller || fight.ctrl || null;
    const view = fight.view || null;
    const methodNames = functionList(fight);
    const interesting = methodNames.filter((name) => /attack|ability|skill|turn|target|unit|hit|kick|select|use|finish|end|log/i.test(name));
    const sources = {};
    for (const name of interesting.slice(0, 60)) {
      const source = functionSource(fight, name);
      if (source) {
        sources[name] = source;
      }
    }
    return {
      ok: true,
      bridgeVersion: BRIDGE_VERSION,
      generatedAt: new Date().toISOString(),
      fightPath: item.path,
      fightHref: item.href,
      fightMethods: methodNames,
      interestingFightMethods: interesting,
      fightSources: sources,
      modelMethods: functionList(model),
      controllerMethods: functionList(controller),
      viewMethods: functionList(view),
      fightSummary: summarizeObject(fight, "fight", 2, new Set()),
    };
  };

  const useSkillSlot = async (payload) => {
    const rawSlot = payload && (payload.slot ?? payload.slotIndex ?? payload.skill_slot);
    const slot = parseInt(rawSlot, 10);
    const preClickDelayMs = Math.max(0, parseInt(payload && payload.preClickDelayMs, 10) || 0);
    const clickHoldMs = Math.max(0, parseInt(payload && payload.clickHoldMs, 10) || 0);
    if (!Number.isFinite(slot) || slot < 0) {
      return { ok: false, message: "missing_slot", snapshot: battleSnapshot() };
    }
    const item = findFightWindow();
    if (!item || typeof item.win.useSkill !== "function") {
      return { ok: false, message: "useSkill_missing", snapshot: battleSnapshot() };
    }
    const snapshot = battleSnapshot();
    if (!snapshot.hasFight) {
      return { ok: false, message: snapshot.finished ? "fight_finished" : "fight_inactive", slot, snapshot };
    }
    let ability = null;
    try {
      const all = item.win.fight && item.win.fight.model && item.win.fight.model.abilities && item.win.fight.model.abilities.all;
      if (Array.isArray(all)) {
        ability = all.find((entry) => entry && entry.id < 0 && entry.slot === slot) || null;
      }
    } catch (_) {}
    if (!ability) {
      return { ok: false, message: "ability_slot_missing", slot, snapshot: battleSnapshot() };
    }
    const abilityBefore = abilitySummary(ability);
    if (abilityBefore.disabled === true || abilityBefore.ready === false || Number(abilityBefore.cooldown) > 0) {
      return { ok: false, message: "ability_not_ready", slot, ability: abilityBefore, snapshot };
    }
    const beforeResource = compactResourceSnapshot(resourceSnapshot());
    const verifyTimeoutMs = Math.max(200, Math.min(2500, parseInt(payload && payload.verifyTimeoutMs, 10) || 900));
    await delayMs(preClickDelayMs);
    try {
      const returnValue = item.win.useSkill(slot);
      const deadline = Date.now() + verifyTimeoutMs;
      let after = battleSnapshot();
      let afterResource = compactResourceSnapshot(resourceSnapshot());
      let abilityAfter = Array.isArray(after.abilities)
        ? after.abilities.find((entry) => entry && entry.slot === slot) || null
        : null;
      const changed = () => {
        if (returnValue === true || !after.hasFight || after.finished) return true;
        for (const name of ["fightState", "oppId", "myTurn", "enabledControl", "totalDmg"]) {
          if (snapshot[name] !== after[name]) return true;
        }
        for (const name of ["ready", "disabled", "cooldown"]) {
          if (abilityAfter && abilityBefore[name] !== abilityAfter[name]) return true;
        }
        return beforeResource.healthPercent !== afterResource.healthPercent ||
          beforeResource.prowessPercent !== afterResource.prowessPercent;
      };
      while (!changed() && Date.now() < deadline) {
        await delayMs(100);
        after = battleSnapshot();
        afterResource = compactResourceSnapshot(resourceSnapshot());
        abilityAfter = Array.isArray(after.abilities)
          ? after.abilities.find((entry) => entry && entry.slot === slot) || null
          : null;
      }
      const confirmed = changed();
      return {
        ok: confirmed,
        message: confirmed ? "useSkill_confirmed" : "useSkill_unconfirmed",
        slot,
        preClickDelayMs,
        clickHoldMs,
        verifyTimeoutMs,
        fightPath: item.path,
        fightHref: item.href,
        ability: abilityBefore,
        abilityAfter,
        returnValue: returnValue === true ? true : returnValue === false ? false : null,
        before: snapshot,
        after,
        beforeResource,
        afterResource,
      };
    } catch (error) {
      return {
        ok: false,
        message: `useSkill_error:${safeString(error && error.message ? error.message : error, 200)}`,
        slot,
        snapshot: battleSnapshot(),
      };
    }
  };

  const normalizeNeedles = (values) =>
    (Array.isArray(values) ? values : values == null || values === "" ? [] : [values])
      .map((value) => safeString(value, 120).toLowerCase())
      .filter(Boolean);

  const normalizeSlots = (values) =>
    (Array.isArray(values) ? values : values == null || values === "" ? [] : [values])
      .map((value) => parseInt(value, 10))
      .filter((value, index, array) => Number.isFinite(value) && value >= 0 && array.indexOf(value) === index);

  const battleItemMatches = (item, itemId, slots, names) => {
    if (!item) {
      return false;
    }
    if (itemId !== "" && String(item.id) === itemId) {
      return true;
    }
    const slot = parseInt(item.slot, 10);
    if (Number.isFinite(slot) && slots.includes(slot)) {
      return true;
    }
    const haystack = `${item.name} ${item.id} ${item.slot}`.toLowerCase();
    return names.some((needle) => haystack.includes(needle));
  };

  const callBattleItemMethod = async (owner, methodNames, slot, item) => {
    if (!owner) {
      return null;
    }
    const itemId = item && item.id != null ? item.id : null;
    for (const name of methodNames) {
      let fn = null;
      try {
        fn = owner[name];
      } catch (_) {}
      if (typeof fn !== "function") {
        continue;
      }
      const argsList = [];
      if (name === "useSkill" && slot != null) {
        argsList.push([slot]);
      }
      if (/slot/i.test(name) && slot != null) {
        argsList.push([slot]);
      }
      if (name !== "useSkill" && !/slot/i.test(name) && itemId != null) {
        argsList.push([itemId]);
      }
      if (slot != null) {
        argsList.push([slot]);
      }
      if (itemId != null) {
        argsList.push([itemId]);
      }
      if (slot != null && itemId != null) {
        argsList.push([slot, itemId], [itemId, slot]);
      }
      const uniqueArgs = argsList.filter(
        (args, index, all) => all.findIndex((candidate) => JSON.stringify(candidate) === JSON.stringify(args)) === index
      );
      for (const args of uniqueArgs) {
        try {
          let result = fn.apply(owner, args);
          if (result && typeof result.then === "function") {
            const timeoutMarker = {};
            result = await Promise.race([result, delayMs(2000).then(() => timeoutMarker)]);
            if (result === timeoutMarker) {
              return { ok: false, ambiguous: true, method: name, args, result: "timeout" };
            }
          }
          if (result === false) {
            continue;
          }
          return { ok: true, method: name, args, result: safeString(result, 120) };
        } catch (_) {}
      }
    }
    return null;
  };

  const clickBattleItemElement = (item, slots, names) => {
    const fightItem = findFightWindow();
    const doc = fightItem && fightItem.win && fightItem.win.document ? fightItem.win.document : null;
    if (!doc) {
      return null;
    }
    const elements = Array.from(doc.querySelectorAll("[data-slot],[data-index],[title],[alt],[onclick],button,a,img,span,div")).slice(0, 1500);
    let best = null;
    for (const el of elements) {
      const text = `${elementText(el)} ${attr(el, "title")} ${attr(el, "alt")} ${attr(el, "src")} ${attr(el, "style")} ${attr(el, "onclick")}`.toLowerCase();
      const rawSlot = attr(el, "data-slot") || attr(el, "data-index") || attr(el, "slot");
      const slot = parseInt(rawSlot, 10);
      const slotMatch = Number.isFinite(slot) && slots.includes(slot);
      const nameMatch = names.some((needle) => text.includes(needle));
      if (!slotMatch && !nameMatch) {
        continue;
      }
      const clickable = clickableElement(el);
      if (!clickable || typeof clickable.click !== "function") {
        continue;
      }
      best = { el: clickable, slot: Number.isFinite(slot) ? slot : null, nameMatch, slotMatch };
      break;
    }
    if (!best) {
      return null;
    }
    try {
      best.el.click();
      return {
        ok: true,
        method: "dom_click",
        slot: best.slot,
        item,
        nameMatch: best.nameMatch,
        slotMatch: best.slotMatch,
      };
    } catch (error) {
      return { ok: false, message: `dom_click_error:${safeString(error && error.message ? error.message : error, 200)}` };
    }
  };

  const battleItemPostcondition = (kind, beforeResource, afterResource, beforeItem, afterItems) => {
    const resourceField = kind === "health" ? "healthPercent" : kind === "prowess" ? "prowessPercent" : "";
    const beforePercent = resourceField ? Number(beforeResource && beforeResource[resourceField]) : NaN;
    const afterPercent = resourceField ? Number(afterResource && afterResource[resourceField]) : NaN;
    const resourceIncreased = Number.isFinite(beforePercent) && Number.isFinite(afterPercent) && afterPercent > beforePercent;
    let itemChanged = false;
    let afterItem = null;
    if (beforeItem) {
      afterItem = (Array.isArray(afterItems) ? afterItems : []).find(
        (candidate) =>
          (beforeItem.id != null && candidate.id != null && String(candidate.id) === String(beforeItem.id)) ||
          (beforeItem.slot != null && candidate.slot != null && String(candidate.slot) === String(beforeItem.slot)) ||
          (beforeItem.name && candidate.name === beforeItem.name)
      ) || null;
      if (!afterItem) {
        itemChanged = true;
      } else {
        const beforeQuantity = Number(beforeItem.quantity);
        const afterQuantity = Number(afterItem.quantity);
        const beforeCooldown = Number(beforeItem.cooldown);
        const afterCooldown = Number(afterItem.cooldown);
        itemChanged =
          (Number.isFinite(beforeQuantity) && Number.isFinite(afterQuantity) && afterQuantity < beforeQuantity) ||
          (Number.isFinite(beforeCooldown) && Number.isFinite(afterCooldown) && afterCooldown > beforeCooldown) ||
          (beforeItem.ready === true && afterItem.ready === false) ||
          (beforeItem.disabled === false && afterItem.disabled === true);
      }
    }
    return {
      confirmed: resourceIncreased || itemChanged,
      resourceField,
      beforePercent: Number.isFinite(beforePercent) ? beforePercent : null,
      afterPercent: Number.isFinite(afterPercent) ? afterPercent : null,
      resourceIncreased,
      itemChanged,
      beforeItem,
      afterItem,
    };
  };

  const useBattleItem = async (payload) => {
    const kind = safeString(payload && payload.kind, 40);
    const slots = normalizeSlots(payload && (payload.slots || payload.slot));
    const names = normalizeNeedles(payload && (payload.names || payload.name));
    const itemId = safeString(payload && (payload.itemId || payload.item_id), 80);
    const preClickDelayMs = Math.max(0, parseInt(payload && payload.preClickDelayMs, 10) || 0);
    const clickHoldMs = Math.max(0, parseInt(payload && payload.clickHoldMs, 10) || 0);
    const verifyDelayMs = Math.max(0, Math.min(2000, parseInt(payload && payload.verifyDelayMs, 10) || 350));
    if (!itemId && !slots.length && !names.length) {
      return { ok: false, message: "battle_item_target_missing", kind, snapshot: battleSnapshot() };
    }
    const fightItem = findFightWindow();
    const snapshot = battleSnapshot();
    if (!fightItem || !snapshot.hasFight) {
      return { ok: false, message: snapshot.finished ? "fight_finished" : "fight_inactive", kind, snapshot };
    }
    const items = Array.isArray(snapshot.items) ? snapshot.items : [];
    const item = items.find((candidate) => battleItemMatches(candidate, itemId, slots, names)) || null;
    if (!item) {
      return { ok: false, message: "battle_item_not_observed", kind, items, snapshot };
    }
    const cooldown = Number(item.cooldown);
    const quantity = Number(item.quantity);
    if (
      item.ready !== true ||
      item.disabled === true ||
      (Number.isFinite(cooldown) && cooldown > 0) ||
      (Number.isFinite(quantity) && quantity <= 0)
    ) {
      return { ok: false, message: "battle_item_not_ready", kind, item, items, snapshot };
    }
    const slot = item && item.slot != null ? parseInt(item.slot, 10) : slots.length ? slots[0] : null;
    const customMethodNames = (Array.isArray(payload && payload.methodNames) ? payload.methodNames : [])
      .map((value) => safeString(value, 120))
      .filter(Boolean);
    const methodNames = customMethodNames.concat([
      ...(item && Number(item.id) > 0 && slot != null ? ["useSkill"] : []),
      "useItem",
      "useItemSlot",
      "useBattleItem",
      "usePotion",
      "useElixir",
      "useArtifact",
      "useEffect",
      "useThing",
      "activateItem",
    ]);
    const owners = [
      fightItem.win,
      fightItem.win.fight,
      fightItem.win.fight && fightItem.win.fight.controller,
      fightItem.win.fight && fightItem.win.fight.ctrl,
      fightItem.win.fight && fightItem.win.fight.model,
    ];
    const beforeResource = compactResourceSnapshot(resourceSnapshot());
    await delayMs(preClickDelayMs);
    let actionResult = null;
    for (const owner of owners) {
      const result = await callBattleItemMethod(owner, methodNames, slot, item);
      if (result && result.ambiguous) {
        return {
          ok: false,
          message: "battle_item_result_ambiguous",
          kind,
          slot,
          item,
          method: result.method,
          args: result.args,
          snapshot,
        };
      }
      if (result && result.ok) {
        actionResult = result;
        break;
      }
    }
    if (!actionResult) {
      const clickResult = clickBattleItemElement(item, slots, names);
      if (clickResult && clickResult.ok) {
        actionResult = clickResult;
      }
    }
    if (!actionResult) {
      return { ok: false, message: "battle_item_use_failed", kind, slot, item, items, snapshot };
    }
    await delayMs(Math.max(clickHoldMs, verifyDelayMs));
    const afterSnapshot = battleSnapshot();
    const afterResource = compactResourceSnapshot(resourceSnapshot());
    const evidence = battleItemPostcondition(kind, beforeResource, afterResource, item, afterSnapshot.items);
    return {
      ok: evidence.confirmed,
      message: evidence.confirmed ? "battle_item_used" : "battle_item_use_ambiguous",
      kind,
      slot: actionResult.slot != null ? actionResult.slot : slot,
      item,
      method: actionResult.method,
      args: actionResult.args || [],
      preClickDelayMs,
      clickHoldMs,
      verifyDelayMs,
      fightPath: fightItem.path,
      fightHref: fightItem.href,
      evidence,
      beforeResource,
      afterResource,
      afterSnapshot,
    };
  };
