  const BRIDGE_VERSION = "2026-07-17-chat-delivery-retention-v74";
  const CONTENT_SOURCE = `antibot-cv-content:${BRIDGE_VERSION}`;
  const INJECTOR_SOURCE = `antibot-cv-injector:${BRIDGE_VERSION}`;

  if (window.__antibotCvPageBridgeVersion === BRIDGE_VERSION) {
    return;
  }
  window.__antibotCvPageBridgeInstalled = true;
  window.__antibotCvPageBridgeVersion = BRIDGE_VERSION;
  let stateSnapshotSequence = 0;
  let battleObservationSequence = 0;
  const battleObservationTokens = new Map();
  const BATTLE_OBSERVATION_MAX_AGE_MS = 1200;
  const BATTLE_OBSERVATION_MAX_ENTRIES = 8;

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

  const adaptiveVerify = async (read, accept, options = {}) => {
    const timeoutMs = Math.max(100, Math.min(10000, Number(options.timeoutMs) || 2000));
    const maxStableWrong = Math.max(1, Math.min(10, Number(options.maxStableWrong) || 4));
    const stableWrongGraceMs = Math.max(100, Math.min(timeoutMs, Number(options.stableWrongGraceMs) || Math.floor(timeoutMs * 0.8)));
    const fingerprint = typeof options.fingerprint === "function" ? options.fingerprint : (value) => JSON.stringify(value);
    const started = Date.now();
    const deadline = started + timeoutMs;
    let delay = 50;
    let stableWrong = 0;
    let previousFingerprint = null;
    let value = read();
    while (!accept(value) && Date.now() < deadline) {
      const currentFingerprint = fingerprint(value);
      stableWrong = currentFingerprint === previousFingerprint ? stableWrong + 1 : 0;
      previousFingerprint = currentFingerprint;
      if (stableWrong >= maxStableWrong && Date.now() - started >= stableWrongGraceMs) break;
      await delayMs(delay);
      delay = Math.min(400, delay * 2);
      value = read();
    }
    return value;
  };

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

  const explicitBoolean = (value) => {
    if (typeof value === "boolean") return value;
    if (value === "true" || value === "1") return true;
    if (value === "false" || value === "0") return false;
    return null;
  };

  const explicitCooldown = (value) => {
    if (typeof value === "boolean" || value == null || value === "") return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
  };

  const exactInteger = (value) => {
    if (typeof value === "number") return Number.isInteger(value) ? value : null;
    if (typeof value !== "string" || !/^-?\d+$/.test(value.trim())) return null;
    const parsed = Number(value.trim());
    return Number.isSafeInteger(parsed) ? parsed : null;
  };

  const buildVisibleAbilityDomIndex = (doc) => {
    if (!doc) return [];
    let containers = [];
    try {
      containers = Array.from(doc.querySelectorAll(
        "[data-battle-abilities],[data-fight-abilities],#battleAbilities,#fightAbilities"
      ));
    } catch (_) {
      return [];
    }
    const result = [];
    const elements = [];
    for (const container of containers.slice(0, 8)) {
      try {
        elements.push(...Array.from(
          container.querySelectorAll("[data-slot],[data-skill-slot],[data-ability-slot]")
        ).slice(0, 40));
      } catch (_) {}
    }
    for (const element of elements.slice(0, 120)) {
      let visible = false;
      try {
        visible = Boolean(
          (element.getClientRects && element.getClientRects().length) ||
          (Number(element.offsetWidth) > 0 && Number(element.offsetHeight) > 0)
        );
      } catch (_) {}
      if (!visible) continue;
      const attr = (name) => {
        try { return element.getAttribute(name); } catch (_) { return null; }
      };
      const observedSlots = [attr("data-slot"), attr("data-skill-slot"), attr("data-ability-slot")]
        .filter((value) => value != null)
        .map(exactInteger);
      if (observedSlots.length === 0 || observedSlots.some((value) => value == null)) continue;
      const ids = [attr("data-ability-id"), attr("data-skill-id")]
        .filter((value) => value != null)
        .map(exactInteger);
      const names = [attr("data-ability-name"), attr("data-skill-name")]
        .filter((value) => value != null)
        .map((value) => safeString(value, 120));
      const ready = explicitBoolean(attr("data-ready"));
      const ariaDisabled = explicitBoolean(attr("aria-disabled"));
      const cooldown = [attr("data-cooldown"), attr("data-cooldown-remaining"), attr("data-cd")]
        .map(explicitCooldown)
        .find((value) => value != null);
      result.push({
        source: "visible_battle_ability_dom",
        slots: observedSlots,
        ids,
        names,
        ready: ready != null ? ready : ariaDisabled != null ? !ariaDisabled : element.disabled === true ? false : null,
        cooldown,
      });
    }
    return result;
  };

  const visibleAbilityDomEvidence = (ability, domIndex) => {
    const modelSlot = exactInteger(ability && ability.slot);
    const modelId = exactInteger(ability && ability.id);
    const modelName = safeString(ability && (ability.name || ability.title || ability.caption), 120);
    if (modelSlot == null || modelId == null || !modelName || !Array.isArray(domIndex)) return [];
    return domIndex
      .filter((entry) =>
        entry.ids.length > 0 && entry.names.length > 0 &&
        entry.slots.every((value) => value === modelSlot) &&
        entry.ids.every((value) => value === modelId) &&
        entry.names.every((value) => value === modelName)
      )
      .map((entry) => ({ source: entry.source, ready: entry.ready, cooldown: entry.cooldown }));
  };

  const abilityReadinessEvidence = (ability, domIndex, requireDomMatch = false) => {
    const observations = [];
    if (ability && typeof ability === "object") {
      for (const field of ["ready", "isReady", "canUse", "available", "enabled"]) {
        const ready = explicitBoolean(ability[field]);
        if (ready != null) observations.push({ source: `model.${field}`, ready, cooldown: null });
      }
      const disabled = explicitBoolean(ability.disabled);
      if (disabled != null) observations.push({ source: "model.disabled", ready: !disabled, cooldown: null });
      for (const field of ["cooldown", "cooldownRemaining", "cooldownLeft", "cd", "cdLeft"]) {
        const cooldown = explicitCooldown(ability[field]);
        if (cooldown != null) observations.push({ source: `model.${field}`, ready: null, cooldown });
      }
    }
    const modelReadyValues = Array.from(new Set(observations.map((value) => value.ready).filter((value) => value != null)));
    const modelCooldownValues = Array.from(new Set(observations.map((value) => value.cooldown).filter((value) => value != null)));
    if (modelCooldownValues.some((value) => value > 0)) modelReadyValues.push(false);
    else if (modelCooldownValues.includes(0)) modelReadyValues.push(true);
    const modelAuthoritative = Array.from(new Set(modelReadyValues)).length === 1 && modelCooldownValues.length === 1;
    const domObservations = visibleAbilityDomEvidence(ability, domIndex);
    observations.push(...domObservations);
    const readyValues = Array.from(new Set(observations.map((value) => value.ready).filter((value) => value != null)));
    const cooldownValues = Array.from(new Set(observations.map((value) => value.cooldown).filter((value) => value != null)));
    if (cooldownValues.some((value) => value > 0)) readyValues.push(false);
    else if (cooldownValues.includes(0)) readyValues.push(true);
    const uniqueReady = Array.from(new Set(readyValues));
    const contradictory = uniqueReady.length > 1 || cooldownValues.length > 1;
    const ready = uniqueReady.length === 1 ? uniqueReady[0] : null;
    const cooldown = cooldownValues.length === 1 ? cooldownValues[0] : null;
    const domBound = domObservations.length === 1;
    const domCapabilityPresent = Array.isArray(domIndex) && domIndex.length > 0;
    return {
      authoritative: !contradictory && ready != null && cooldown != null &&
        (!requireDomMatch || (modelAuthoritative && (domBound || !domCapabilityPresent))),
      ready,
      cooldownRemaining: cooldown,
      contradictory,
      domBound,
      bindingSource: domBound ? "model+dom" : modelAuthoritative && !domCapabilityPresent ? "model" : null,
      sources: observations.map((value) => value.source).slice(0, 12),
    };
  };

  const abilitySummary = (ability, domIndex = [], requireDomMatch = false) => {
    const readinessEvidence = abilityReadinessEvidence(ability, domIndex, requireDomMatch);
    return {
      id: exactInteger(ability && ability.id),
      slot: exactInteger(ability && ability.slot),
      name: safeString(ability && (ability.name || ability.title || ability.caption), 120),
      ready: readinessEvidence.ready,
      disabled: readinessEvidence.ready == null ? null : !readinessEvidence.ready,
      cooldown: readinessEvidence.cooldownRemaining,
      readinessEvidence,
    };
  };

  const boundedPrimitiveDescriptor = (value) => {
    if (value === null) return { type: "null", value: null };
    const type = typeof value;
    if (type === "undefined") return { type: "undefined" };
    if (type === "boolean") return { type, value };
    if (type === "number") {
      return Number.isFinite(value) ? { type, value } : { type, value: safeString(value, 32) };
    }
    if (type === "string") return { type, length: Math.min(value.length, 1000000) };
    if (type === "bigint") return { type, digits: Math.min(safeString(value, 1000001).length, 1000000) };
    if (type === "object") return { type: Array.isArray(value) ? "array" : "object" };
    return { type };
  };

  const observeSkillReturn = (value) => {
    const observation = {
      raw: boundedPrimitiveDescriptor(value),
      promise: { status: "not_promise" },
    };
    let thenable = false;
    try {
      thenable = Boolean(value && typeof value.then === "function");
    } catch (_) {}
    if (!thenable) return observation;
    observation.raw = { type: "promise" };
    observation.promise = { status: "pending" };
    Promise.resolve(value).then(
      (resolved) => {
        observation.promise = {
          status: "resolved",
          result: boundedPrimitiveDescriptor(resolved),
        };
      },
      () => {
        observation.promise = { status: "rejected" };
      }
    );
    return observation;
  };

  const boundedAllowlistedStateDescriptor = (value) => {
    if (typeof value === "string") return { type: "string", value: safeString(value, 120) };
    return boundedPrimitiveDescriptor(value);
  };

  const playerStanceState = (fight) => {
    const model = fight && fight.model;
    if (!model || typeof model !== "object") return [];
    const paths = [];
    for (const owner of ["player", "me", "self", "hero", "my", "myUnit"]) {
      for (const field of ["position", "stance", "pose", "row", "line", "battlePosition"]) {
        paths.push([owner, field]);
      }
    }
    for (const field of [
      "playerPosition",
      "playerStance",
      "myPosition",
      "myStance",
      "myRow",
      "myLine",
    ]) {
      paths.push([field]);
    }
    const result = [];
    for (const path of paths) {
      let value = model;
      try {
        for (const part of path) value = value && value[part];
      } catch (_) {
        continue;
      }
      if (value === undefined) continue;
      result.push({ path: `model.${path.join(".")}`, ...boundedAllowlistedStateDescriptor(value) });
      if (result.length >= 16) break;
    }
    return result;
  };

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

  const battleTurnEvidence = (fight) => {
    const model = fight && fight.model;
    const myTurn = model && typeof model.myTurn === "boolean" ? model.myTurn : null;
    const enabledControl = model && typeof model.enabledControl === "boolean" ? model.enabledControl : null;
    const authoritative = myTurn === false || (myTurn === true && enabledControl === true);
    return {
      authoritative,
      myTurn: authoritative ? myTurn : null,
      enabledControl,
      sources: [
        ...(myTurn == null ? [] : ["model.myTurn"]),
        ...(enabledControl == null ? [] : ["model.enabledControl"]),
      ],
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

  const battleEpoch = (fight, href = "") => {
    const model = fight && fight.model;
    const safeHref = safeString(href, 300);
    const hrefMatch = safeHref.match(/\/fight\.php\?(?:[^#]*?&)?(?:fight_id=)?(\d+)/i);
    const opaqueHrefMatch = safeHref.match(/\/fight\.php\?([^#&=]+)(?:[#&]|$)/i);
    const raw = (model && (model.battleId ?? model.fightId ?? model.id)) ??
      (hrefMatch && hrefMatch[1]) ??
      (opaqueHrefMatch && `href-${opaqueHrefMatch[1]}`);
    if ((typeof raw !== "string" && typeof raw !== "number") || !safeString(raw, 100)) return "";
    return safeString(raw, 100);
  };

  const mintBattleObservation = (snapshot) => {
    const now = Date.now();
    for (const [token, value] of battleObservationTokens) {
      if (!value || now - value.mintedAt > BATTLE_OBSERVATION_MAX_AGE_MS) battleObservationTokens.delete(token);
    }
    while (battleObservationTokens.size >= BATTLE_OBSERVATION_MAX_ENTRIES) {
      battleObservationTokens.delete(battleObservationTokens.keys().next().value);
    }
    const token = `obs-${now.toString(36)}-${(++battleObservationSequence).toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
    battleObservationTokens.set(token, {
      mintedAt: now,
      snapshotId: snapshot.snapshotId,
      battleIdentity: snapshot.battleIdentity,
    });
    return token;
  };

  const consumeBattleObservation = (token, snapshotId, battleIdentity) => {
    const value = battleObservationTokens.get(token);
    battleObservationTokens.delete(token);
    if (!value || Date.now() - value.mintedAt > BATTLE_OBSERVATION_MAX_AGE_MS) return "expired_or_unknown";
    if (value.snapshotId !== snapshotId || value.battleIdentity !== battleIdentity) return "binding_mismatch";
    return "ok";
  };

  const battleSnapshot = (mintObservation = true, requireDomAbilityEvidence = false) => {
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
        turnEvidence: { authoritative: false, myTurn: null, enabledControl: null, sources: [] },
        playerStanceState: [],
        abilities: [],
        items: [],
      };
    }
    const fight = item.win.fight || null;
    const state = fightModelState(fight);
    const snapshotId = `battle-${++stateSnapshotSequence}`;
    const epoch = battleEpoch(fight, item.href);
    const battleIdentity = state.oppId == null || !epoch
      ? ""
      : `${safeString(item.path, 120)}|battle:${epoch}|opp:${safeString(state.oppId, 80)}`;
    const result = battleOutcome(item, state);
    const hasFight = Boolean(fight) && !state.finished;
    let abilities = [];
    try {
      const all = fight && fight.model && fight.model.abilities && fight.model.abilities.all;
      const rawAbilities = Array.isArray(all) ? all : [];
      const modelEvidenceComplete = rawAbilities.every(
        (ability) => abilityReadinessEvidence(ability, []).authoritative === true
      );
      const domIndex = requireDomAbilityEvidence || !modelEvidenceComplete
        ? buildVisibleAbilityDomIndex(item.win.document)
        : [];
      abilities = rawAbilities.map((ability) => abilitySummary(ability, domIndex, requireDomAbilityEvidence));
    } catch (_) {}
    const snapshot = {
      bridgeVersion: BRIDGE_VERSION,
      generatedAt: new Date().toISOString(),
      snapshotId,
      battleIdentity,
      hasFight,
      ...state,
      outcome: result.outcome,
      outcomeEvidence: result.evidence,
      fightPath: item.path,
      fightHref: item.href,
      useSkillAvailable: hasFight && typeof item.win.useSkill === "function",
      turnEvidence: battleTurnEvidence(fight),
      playerStanceState: playerStanceState(fight),
      abilities,
      items: collectBattleItems(fight),
    };
    snapshot.observationToken = mintObservation && hasFight && battleIdentity
      ? mintBattleObservation(snapshot)
      : "";
    return snapshot;
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
      playerStanceState: playerStanceState(fight),
      fightSummary: summarizeObject(fight, "fight", 2, new Set()),
    };
  };

  const useSkillSlot = async (payload) => {
    const rawSlot = payload && (payload.slot ?? payload.slotIndex ?? payload.skill_slot);
    const slot = parseInt(rawSlot, 10);
    const preClickDelayMs = Math.max(0, Math.min(2500, parseInt(payload && payload.preClickDelayMs, 10) || 0));
    const clickHoldMs = Math.max(0, parseInt(payload && payload.clickHoldMs, 10) || 0);
    if (!Number.isFinite(slot) || slot < 0) {
      return { ok: false, message: "missing_slot", snapshot: battleSnapshot() };
    }
    const expectedSkillId = exactInteger(payload && payload.expectedSkillId);
    const expectedSkillSlot = exactInteger(payload && payload.expectedSkillSlot);
    const expectedSkillName = safeString(payload && payload.expectedSkillName, 120);
    const expectedBattleIdentity = safeString(payload && payload.expectedBattleIdentity, 240);
    const expectedSnapshotId = safeString(payload && payload.expectedSnapshotId, 160);
    const expectedObservationToken = safeString(payload && payload.expectedObservationToken, 200);
    if (
      expectedSkillId == null || expectedSkillId >= 0 || expectedSkillSlot !== slot ||
      !expectedSkillName || !expectedBattleIdentity || !expectedSnapshotId || !expectedObservationToken
    ) {
      return { ok: false, message: "skill_mutation_binding_missing", slot };
    }
    await delayMs(preClickDelayMs);
    const tokenStatus = consumeBattleObservation(
      expectedObservationToken,
      expectedSnapshotId,
      expectedBattleIdentity,
    );
    if (tokenStatus !== "ok") {
      return { ok: false, message: `skill_mutation_token_${tokenStatus}`, slot };
    }
    const item = findFightWindow();
    if (!item || typeof item.win.useSkill !== "function") {
      return { ok: false, message: "useSkill_missing", snapshot: battleSnapshot() };
    }
    const snapshot = battleSnapshot(false, true);
    if (!snapshot.snapshotId || snapshot.snapshotId === expectedSnapshotId) {
      return { ok: false, message: "skill_mutation_probe_stale", slot, snapshot };
    }
    if (!snapshot.battleIdentity || snapshot.battleIdentity !== expectedBattleIdentity) {
      return { ok: false, message: "skill_mutation_battle_mismatch", slot, snapshot };
    }
    if (!snapshot.hasFight) {
      return { ok: false, message: snapshot.finished ? "fight_finished" : "fight_inactive", slot, snapshot };
    }
    if (
      !snapshot.turnEvidence || snapshot.turnEvidence.authoritative !== true ||
      snapshot.turnEvidence.myTurn !== true
    ) {
      return { ok: false, message: "skill_mutation_turn_not_authoritative", slot, snapshot };
    }
    const matching = Array.isArray(snapshot.abilities)
      ? snapshot.abilities.filter((entry) =>
          entry && entry.slot === slot && entry.id === expectedSkillId && entry.name === expectedSkillName
        )
      : [];
    if (matching.length !== 1) {
      return { ok: false, message: "ability_slot_missing", slot, snapshot: battleSnapshot() };
    }
    const abilityBefore = matching[0];
    if (abilityBefore.id !== expectedSkillId || abilityBefore.name !== expectedSkillName) {
      return { ok: false, message: "skill_mutation_identity_mismatch", slot, ability: abilityBefore, snapshot };
    }
    const beforeResource = compactResourceSnapshot(resourceSnapshot());
    const verifyTimeoutMs = Math.max(200, Math.min(2500, parseInt(payload && payload.verifyTimeoutMs, 10) || 900));
    try {
      const returnValue = item.win.useSkill(slot);
      const returnObservation = observeSkillReturn(returnValue);
      const deadline = Date.now() + verifyTimeoutMs;
      let after = battleSnapshot(false);
      let afterResource = null;
      let abilityAfter = Array.isArray(after.abilities)
        ? after.abilities.find((entry) => entry && entry.slot === slot) || null
        : null;
      const battleChanged = () => {
        if (returnValue === true || !after.hasFight || after.finished) return true;
        for (const name of ["fightState", "oppId", "myTurn", "enabledControl", "totalDmg"]) {
          if (snapshot[name] !== after[name]) return true;
        }
        for (const name of ["ready", "disabled", "cooldown"]) {
          if (abilityAfter && abilityBefore[name] !== abilityAfter[name]) return true;
        }
        return false;
      };
      while (!battleChanged() && Date.now() < deadline) {
        await delayMs(100);
        after = battleSnapshot(false);
        abilityAfter = Array.isArray(after.abilities)
          ? after.abilities.find((entry) => entry && entry.slot === slot) || null
          : null;
      }
      let confirmed = battleChanged();
      if (!confirmed) {
        afterResource = compactResourceSnapshot(resourceSnapshot());
        confirmed = beforeResource.healthPercent !== afterResource.healthPercent ||
          beforeResource.prowessPercent !== afterResource.prowessPercent;
      }
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
        returnObservation,
        beforePlayerStanceState: snapshot.playerStanceState,
        afterPlayerStanceState: after.playerStanceState,
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
      const text = `${elementText(el)} ${attr(el, "title")} ${attr(el, "alt")} ${attr(el, "src")} ${attr(el, "style")} ${attr(el, "onclick")} ${attr(el, "data-artikul_id")} ${attr(el, "data-id")}`.toLowerCase();
      const rawSlot = attr(el, "data-slot") || attr(el, "data-index") || attr(el, "slot");
      const slot = parseInt(rawSlot, 10);
      const slotMatch = Number.isFinite(slot) && slots.includes(slot);
      const nameMatch = names.some((needle) => text.includes(needle));
      const idMatch = item && item.id != null && text.includes(String(item.id).toLowerCase());
      // Skill and item bars reuse numeric slots. Never authorize a DOM click
      // from the slot alone; require the exact item name or positive item id.
      if (!idMatch && !nameMatch) {
        continue;
      }
      const clickable = clickableElement(el);
      if (!clickable || typeof clickable.click !== "function") {
        continue;
      }
      best = { el: clickable, slot: Number.isFinite(slot) ? slot : null, idMatch, nameMatch, slotMatch };
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
    const observedItem = items.find((candidate) => battleItemMatches(candidate, itemId, slots, names)) || null;
    if (!observedItem) {
      return { ok: false, message: "battle_item_not_observed", kind, items, snapshot };
    }
    const mergeControlEvidence = (candidate, candidateSnapshot) => {
      const controls = (Array.isArray(candidateSnapshot && candidateSnapshot.abilities) ? candidateSnapshot.abilities : []).filter(
        (controlCandidate) => Number(controlCandidate && controlCandidate.id) > 0
      );
      const control = controls.find(
        (controlCandidate) =>
          (candidate.id != null && controlCandidate.id != null && String(controlCandidate.id) === String(candidate.id)) ||
          (candidate.slot != null && controlCandidate.slot != null && String(controlCandidate.slot) === String(candidate.slot) && controlCandidate.name === candidate.name)
      ) || null;
      return control
        ? {
            ...candidate,
            ready: control.ready,
            disabled: control.disabled,
            cooldown: control.cooldown,
            readinessEvidence: control.readinessEvidence || null,
          }
        : candidate;
    };
    const item = mergeControlEvidence(observedItem, snapshot);
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
    const slotCollidesWithSkill = (Array.isArray(snapshot.abilities) ? snapshot.abilities : []).some(
      (candidate) => Number(candidate && candidate.id) < 0 && Number(candidate && candidate.slot) === Number(slot)
    );
    const methodNames = customMethodNames.concat([
      "useAbility",
      ...(!slotCollidesWithSkill && item && Number(item.id) > 0 && slot != null ? ["useSkill"] : []),
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
      fightItem.win.fight,
      fightItem.win.fight && fightItem.win.fight.controller,
      fightItem.win.fight && fightItem.win.fight.ctrl,
      fightItem.win.fight && fightItem.win.fight.model,
      fightItem.win,
    ];
    const beforeResource = compactResourceSnapshot(resourceSnapshot());
    await delayMs(preClickDelayMs);
    // Prefer an exact DOM-bound item control. Numeric skill and item slots
    // overlap in this client, so the generic fight.useSkill(slot) API is unsafe.
    let actionResult = clickBattleItemElement(item, slots, names);
    for (const owner of owners) {
      if (actionResult && actionResult.ok) {
        break;
      }
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
      return { ok: false, message: "battle_item_use_failed", kind, slot, item, items, snapshot };
    }
    await delayMs(Math.max(clickHoldMs, verifyDelayMs));
    const afterSnapshot = battleSnapshot();
    const afterResource = compactResourceSnapshot(resourceSnapshot());
    const afterItems = (Array.isArray(afterSnapshot.items) ? afterSnapshot.items : []).map(
      (candidate) => mergeControlEvidence(candidate, afterSnapshot)
    );
    const evidence = battleItemPostcondition(kind, beforeResource, afterResource, item, afterItems);
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
