(() => {
  const BRIDGE_VERSION = "2026-07-07-local-popup";
  const CONTENT_SOURCE = `antibot-cv-content:${BRIDGE_VERSION}`;
  const INJECTOR_SOURCE = `antibot-cv-injector:${BRIDGE_VERSION}`;

  if (window.__antibotCvPageBridgeVersion === BRIDGE_VERSION) {
    return;
  }
  window.__antibotCvPageBridgeInstalled = true;
  window.__antibotCvPageBridgeVersion = BRIDGE_VERSION;

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

  const battleSnapshot = () => {
    const item = findFightWindow();
    if (!item) {
      return {
        bridgeVersion: BRIDGE_VERSION,
        generatedAt: new Date().toISOString(),
        hasFight: false,
        rawHasFight: false,
        finished: false,
        fightPath: "",
        fightHref: "",
        useSkillAvailable: false,
        abilities: [],
      };
    }
    const fight = item.win.fight || null;
    const state = fightModelState(fight);
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
      fightPath: item.path,
      fightHref: item.href,
      useSkillAvailable: hasFight && typeof item.win.useSkill === "function",
      abilities,
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

  const useSkillSlot = (payload) => {
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
    try {
      item.win.useSkill(slot);
      return {
        ok: true,
        message: "useSkill",
        slot,
        preClickDelayMs,
        clickHoldMs,
        fightPath: item.path,
        fightHref: item.href,
        ability: abilitySummary(ability),
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

  const findHuntApp = () => {
    const root = window.top || window;
    try {
      if (typeof root.getHuntApp === "function") {
        const hunt = root.getHuntApp();
        if (hunt) {
          return hunt;
        }
      }
    } catch (_) {}
    try {
      if (root.frames && root.frames["main_frame"] && root.frames["main_frame"].frames["main"]) {
        return root.frames["main_frame"].frames["main"].hunt || null;
      }
    } catch (_) {}
    return null;
  };

  const shouldSkipObject = (value) => {
    if (!value || typeof value !== "object") {
      return true;
    }
    try {
      if (value === window || value === window.top || value.window === value) {
        return true;
      }
      if (value.nodeType || value.document || value.location) {
        return true;
      }
    } catch (_) {
      return true;
    }
    return false;
  };

  const previewValue = (value) => {
    if (value == null) {
      return value;
    }
    const type = typeof value;
    if (type === "number" || type === "boolean") {
      return value;
    }
    if (type === "string") {
      return safeString(value, 180);
    }
    return undefined;
  };

  const propertyNames = (value) => {
    const names = new Set();
    for (let obj = value; obj && obj !== Object.prototype; obj = Object.getPrototypeOf(obj)) {
      let props = [];
      try {
        props = Object.getOwnPropertyNames(obj);
      } catch (_) {
        continue;
      }
      for (const name of props) {
        if (name !== "constructor") {
          names.add(name);
        }
      }
    }
    return Array.from(names).slice(0, 120);
  };

  const summarizeObject = (value, path, depth, seen) => {
    const item = {
      path,
      type: Array.isArray(value) ? "array" : typeof value,
      constructorName: "",
      length: Array.isArray(value) ? value.length : undefined,
      primitiveProps: [],
      functionProps: [],
      childProps: [],
      skippedProps: 0,
    };
    try {
      item.constructorName = safeString(value && value.constructor && value.constructor.name, 80);
    } catch (_) {}
    if (shouldSkipObject(value)) {
      item.skipped = true;
      return item;
    }
    if (seen.has(value)) {
      item.repeated = true;
      return item;
    }
    seen.add(value);
    const names = propertyNames(value);
    for (const name of names) {
      let current;
      try {
        current = value[name];
      } catch (_) {
        item.skippedProps += 1;
        continue;
      }
      const primitive = previewValue(current);
      if (primitive !== undefined || current == null) {
        item.primitiveProps.push({ name: safeString(name, 100), value: primitive });
        continue;
      }
      if (typeof current === "function") {
        item.functionProps.push({ name: safeString(name, 100), length: current.length });
        continue;
      }
      if (typeof current === "object") {
        if (depth <= 0 || shouldSkipObject(current)) {
          item.childProps.push({
            name: safeString(name, 100),
            type: Array.isArray(current) ? "array" : "object",
            constructorName: safeString(current && current.constructor && current.constructor.name, 80),
            length: Array.isArray(current) ? current.length : undefined,
            skipped: true,
          });
        } else {
          item.childProps.push(summarizeObject(current, `${path}.${name}`, depth - 1, seen));
        }
        continue;
      }
      item.skippedProps += 1;
    }
    item.primitiveProps = item.primitiveProps.slice(0, 120);
    item.functionProps = item.functionProps.slice(0, 120);
    item.childProps = item.childProps.slice(0, 80);
    return item;
  };

  const huntSnapshot = () => {
    const root = window.top || window;
    const hunt = findHuntApp();
    let mainHref = "";
    try {
      mainHref = safeString(root.frames["main_frame"].frames["main"].location.href, 240);
    } catch (_) {}
    return {
      bridgeVersion: BRIDGE_VERSION,
      generatedAt: new Date().toISOString(),
      rootHref: safeString(root.location && root.location.href, 240),
      mainHref,
      hasHunt: Boolean(hunt),
      hunt: hunt ? summarizeObject(hunt, "hunt", 4, new Set()) : null,
    };
  };

  const objectPreview = (value, limit = 80) => {
    if (!value || typeof value !== "object") {
      return {};
    }
    const output = {};
    for (const [name, current] of primitiveEntries(value).slice(0, limit)) {
      output[name] = current;
    }
    return output;
  };

  const resourceKeyword = (text) =>
    /жизн|здоров|удал|удаль|health|life|hp|mana|mp|prowess|bravery|stamina|vigor|energy/i.test(text);

  const parseResourcePercent = (text, kind) => {
    const value = safeString(text, 500).replace(",", ".");
    const patterns = kind === "health"
      ? [
          /(?:жизн(?:ь|и)?|здоров(?:ье|ья)?|health|life|hp)[^0-9]{0,40}([0-9]+(?:\.[0-9]+)?)\s*%/i,
          /(?:жизн(?:ь|и)?|здоров(?:ье|ья)?|health|life|hp)[^0-9]{0,40}([0-9]+)\s*\/\s*([0-9]+)/i,
        ]
      : [
          /(?:удал(?:ь|и)?|mana|mp|prowess|bravery|stamina|vigor|energy)[^0-9]{0,40}([0-9]+(?:\.[0-9]+)?)\s*%/i,
          /(?:удал(?:ь|и)?|mana|mp|prowess|bravery|stamina|vigor|energy)[^0-9]{0,40}([0-9]+)\s*\/\s*([0-9]+)/i,
        ];
    for (const pattern of patterns) {
      const match = value.match(pattern);
      if (!match) {
        continue;
      }
      if (match[2]) {
        const current = Number(match[1]);
        const max = Number(match[2]);
        if (Number.isFinite(current) && Number.isFinite(max) && max > 0) {
          return Math.max(0, Math.min(100, (current / max) * 100));
        }
      }
      const percent = Number(match[1]);
      if (Number.isFinite(percent)) {
        return Math.max(0, Math.min(100, percent));
      }
    }
    return null;
  };

  const resourcePercentFromCandidate = (candidate, kind) => {
    const text = `${candidate.path || ""} ${candidate.name || ""} ${candidate.value == null ? "" : candidate.value}`;
    const parsed = parseResourcePercent(text, kind);
    if (parsed != null) {
      return parsed;
    }
    const path = `${candidate.path || ""}.${candidate.name || ""}`;
    const lowerPath = path.toLowerCase();
    const number = Number(candidate.value);
    if (!Number.isFinite(number)) {
      return null;
    }
    const looksPercent = number >= 0 && number <= 100 && /percent|pct|proc|rate|ratio|prc|%/.test(lowerPath);
    const healthName = /жизн|здоров|health|life|hp/.test(lowerPath);
    const prowessName = /удал|mana|mp|prowess|bravery|stamina|vigor|energy/.test(lowerPath);
    if (kind === "health" && healthName && looksPercent) {
      return number;
    }
    if (kind === "prowess" && prowessName && looksPercent) {
      return number;
    }
    return null;
  };

  const collectResourceCandidatesFromObject = (rootValue, rootPath, output) => {
    const seen = new Set();
    const walk = (value, path, depth) => {
      if (output.length >= 200 || depth < 0 || shouldSkipObject(value) || seen.has(value)) {
        return;
      }
      seen.add(value);
      for (const name of propertyNames(value).slice(0, 100)) {
        let current;
        try {
          current = value[name];
        } catch (_) {
          continue;
        }
        const currentPath = `${path}.${name}`;
        const primitive = previewValue(current);
        if (primitive !== undefined || current == null) {
          if (resourceKeyword(`${currentPath} ${primitive == null ? "" : primitive}`)) {
            output.push({
              source: "object",
              path,
              name: safeString(name, 100),
              value: primitive,
            });
          }
          continue;
        }
        if (current && typeof current === "object" && !shouldSkipObject(current)) {
          walk(current, currentPath, depth - 1);
        }
      }
    };
    walk(rootValue, rootPath, 4);
  };

  const collectResourceDomCandidates = (win, path, output) => {
    try {
      const doc = win.document;
      const text = safeString(doc && doc.body && doc.body.innerText, 2000);
      if (resourceKeyword(text)) {
        output.push({ source: "dom_text", path, name: "body.innerText", value: text });
      }
      const html = safeString(doc && doc.documentElement && doc.documentElement.innerHTML, 500);
      if (resourceKeyword(html)) {
        output.push({ source: "dom_html", path, name: "documentElement.innerHTML", value: html });
      }
      const elements = doc ? Array.from(doc.querySelectorAll("*")).slice(0, 1200) : [];
      for (const el of elements) {
        const value = safeString(
          [
            el.id,
            el.className,
            el.getAttribute && el.getAttribute("title"),
            el.getAttribute && el.getAttribute("alt"),
            el.getAttribute && el.getAttribute("style"),
            el.textContent,
          ].join(" "),
          500
        );
        if (resourceKeyword(value)) {
          output.push({ source: "dom_element", path, name: safeString(el.tagName, 30), value });
          if (output.length >= 200) {
            return;
          }
        }
      }
    } catch (_) {}
  };

  const resourceSnapshot = () => {
    const root = window.top || window;
    const candidates = [];
    walkWindows(root, "top", 4, new Set(), (win, path) => {
      collectResourceDomCandidates(win, path, candidates);
      collectResourceCandidatesFromObject(win, path, candidates);
    });
    let healthPercent = null;
    let prowessPercent = null;
    let healthCandidate = null;
    let prowessCandidate = null;
    for (const candidate of candidates) {
      if (healthPercent == null) {
        const percent = resourcePercentFromCandidate(candidate, "health");
        if (percent != null) {
          healthPercent = percent;
          healthCandidate = candidate;
        }
      }
      if (prowessPercent == null) {
        const percent = resourcePercentFromCandidate(candidate, "prowess");
        if (percent != null) {
          prowessPercent = percent;
          prowessCandidate = candidate;
        }
      }
      if (healthPercent != null && prowessPercent != null) {
        break;
      }
    }
    return {
      bridgeVersion: BRIDGE_VERSION,
      generatedAt: new Date().toISOString(),
      ok: healthPercent != null && prowessPercent != null,
      healthPercent,
      prowessPercent,
      healthCandidate,
      prowessCandidate,
      candidateCount: candidates.length,
      candidates: candidates.slice(0, 20),
    };
  };

  const refreshResourceSource = () => {
    const root = window.top || window;
    try {
      const mainFrame = root.frames && root.frames["main_frame"];
      if (mainFrame && mainFrame.location) {
        const href = safeString(mainFrame.location.href, 240);
        root.setTimeout(() => {
          try {
            mainFrame.location.reload();
          } catch (_) {}
        }, 50);
        return {
          ok: true,
          message: "main_frame_reload_scheduled",
          href,
        };
      }
    } catch (error) {
      return { ok: false, message: `main_frame_reload_error:${safeString(error && error.message ? error.message : error, 200)}` };
    }
    try {
      const href = safeString(root.location && root.location.href, 240);
      root.setTimeout(() => {
        try {
          root.location.reload();
        } catch (_) {}
      }, 50);
      return {
        ok: true,
        message: "top_reload_scheduled",
        href,
      };
    } catch (error) {
      return { ok: false, message: `top_reload_error:${safeString(error && error.message ? error.message : error, 200)}` };
    }
  };

  const normalizeNeedleList = (value) => {
    const raw = Array.isArray(value) ? value : value == null ? [] : [value];
    return raw
      .flatMap((item) => safeString(item, 120).split(/[,;]+/))
      .map((item) => item.trim().toLowerCase())
      .filter(Boolean);
  };

  const inventoryOpenDelayMs = (payload) => Math.max(0, Math.min(5000, toNumber(payload && payload.inventoryOpenDelayMs, 1500)));

  const attr = (el, name) => {
    try {
      return el && el.getAttribute ? el.getAttribute(name) : null;
    } catch (_) {
      return null;
    }
  };

  const inventoryText = (el) =>
    safeString(
      [
        attr(el, "data-title"),
        attr(el, "title"),
        attr(el, "alt"),
        attr(el, "name"),
        attr(el, "data-name"),
        attr(el, "data-art_name"),
        el && (el.innerText || el.textContent),
      ].join(" "),
      500
    );

  const inventoryItemFromElement = (el, path, index) => {
    const text = inventoryText(el);
    let artifactContainer = null;
    try {
      artifactContainer = el && typeof el.closest === "function" ? el.closest("[aid^='art_'],[id^='art_'],[data-id^='art_']") : null;
    } catch (_) {}
    let artifactCell = null;
    try {
      artifactCell = el && typeof el.closest === "function" ? el.closest("[div_id^='AA_'],[aid][cnt]") : null;
    } catch (_) {}
    const dataId = safeString(attr(el, "data-id") || attr(artifactContainer, "data-id"), 80);
    const aid = safeString(attr(el, "aid") || attr(artifactContainer, "aid"), 80);
    const cellAid = safeString(attr(artifactCell, "aid"), 80);
    const divId = safeString(attr(artifactCell, "div_id"), 80);
    const dataArtikul = safeString(attr(el, "data-artikul_id") || attr(el, "data-artikul-id") || attr(el, "data-artikul") || attr(artifactContainer, "data-artikul_id") || attr(artifactContainer, "data-artikul-id") || attr(artifactContainer, "data-artikul"), 80);
    const src = safeString(attr(el, "src"), 240);
    const style = safeString(attr(el, "style"), 300);
    let rect = null;
    try {
      const rawRect = el && typeof el.getBoundingClientRect === "function" ? el.getBoundingClientRect() : null;
      if (rawRect) {
        rect = {
          x: Math.round(Number(rawRect.left)),
          y: Math.round(Number(rawRect.top)),
          width: Math.round(Number(rawRect.width)),
          height: Math.round(Number(rawRect.height)),
        };
      }
    } catch (_) {}
    let closestClickable = null;
    try {
      closestClickable = el && typeof el.closest === "function" ? el.closest("a,button,input,[onclick]") : null;
    } catch (_) {}
    const elementId = safeString((el && el.id) || (artifactContainer && artifactContainer.id) || aid, 80);
    let artikulId = dataArtikul;
    for (const candidate of [dataId, aid, elementId]) {
      if (!artikulId && /^art_\d+$/i.test(candidate)) {
        artikulId = candidate.split("_").pop();
      }
    }
    const count = parseInt(attr(el, "data-cnt") || attr(el, "data-count") || attr(el, "count") || "1", 10);
    let artAlt = null;
    try {
      const topWin = window.top || window;
      const artAltKey = divId || (artikulId ? `AA_${artikulId}` : "");
      artAlt = artAltKey && topWin.art_alt ? topWin.art_alt[artAltKey] : null;
    } catch (_) {}
    const artAltTitle = safeString(artAlt && artAlt.title, 220);
    const artAltDescription = safeString(artAlt && (artAlt.desc || artAlt.description || artAlt.de_c), 500);
    const artAltKind = safeString(artAlt && artAlt.kind && artAlt.kind.value, 120);
    const artAltSlot = safeString(artAlt && artAlt.slot_id, 80);
    return {
      path,
      index,
      tag: safeString(el && el.tagName, 24),
      id: elementId,
      className: safeString(el && el.className, 120),
      title: safeString([text, artAltTitle, artAltDescription, artAltKind].join(" "), 900),
      dataId,
      aid,
      cellAid,
      divId,
      artikulId,
      artAltTitle,
      artAltKind,
      artAltSlot,
      src,
      style,
      href: safeString(attr(el, "href") || attr(closestClickable, "href"), 180),
      onclick: safeString(attr(el, "onclick") || attr(closestClickable, "onclick"), 240),
      rect,
      count: Number.isFinite(count) && count > 0 ? count : parseInt(attr(artifactCell, "cnt") || "1", 10) || 1,
    };
  };

  const inventoryElementFromElement = (el, path, index) => ({
    el,
    item: inventoryItemFromElement(el, path, index),
  });

  const clickableElement = (el) => {
    try {
      if (el && typeof el.closest === "function") {
        return el.closest("a,button,input,[onclick]") || el;
      }
    } catch (_) {}
    return el;
  };

  const elementText = (el) =>
    safeString([el && el.innerText, el && el.textContent, el && el.value, attr(el, "title"), attr(el, "alt"), attr(el, "onclick")].join(" "), 500);

  const elementCenter = (el) => {
    try {
      const rect = el && typeof el.getBoundingClientRect === "function" ? el.getBoundingClientRect() : null;
      if (!rect) {
        return null;
      }
      return {
        x: Number(rect.left) + Number(rect.width) / 2,
        y: Number(rect.top) + Number(rect.height) / 2,
        width: Number(rect.width),
        height: Number(rect.height),
      };
    } catch (_) {
      return null;
    }
  };

  const elementDescriptor = (el) => ({
    tag: safeString(el && el.tagName, 24),
    id: safeString(el && el.id, 80),
    className: safeString(el && el.className, 120),
    text: safeString(elementText(el), 160),
    rect: (() => {
      try {
        const rawRect = el && typeof el.getBoundingClientRect === "function" ? el.getBoundingClientRect() : null;
        if (!rawRect) {
          return null;
        }
        return {
          x: Math.round(Number(rawRect.left)),
          y: Math.round(Number(rawRect.top)),
          width: Math.round(Number(rawRect.width)),
          height: Math.round(Number(rawRect.height)),
        };
      } catch (_) {
        return null;
      }
    })(),
  });

  const dispatchMouseEvent = (target, type, center) => {
    if (!target || typeof target.dispatchEvent !== "function") {
      return false;
    }
    try {
      const view = target.ownerDocument && target.ownerDocument.defaultView ? target.ownerDocument.defaultView : window;
      const event = new MouseEvent(type, {
        bubbles: true,
        cancelable: true,
        view,
        clientX: center.x,
        clientY: center.y,
        button: 0,
        buttons: type === "mousedown" ? 1 : 0,
      });
      target.dispatchEvent(event);
      return true;
    } catch (_) {
      try {
        const event = target.ownerDocument.createEvent("MouseEvents");
        event.initMouseEvent(type, true, true, window, 1, center.x, center.y, center.x, center.y, false, false, false, false, 0, null);
        target.dispatchEvent(event);
        return true;
      } catch (_) {
        return false;
      }
    }
  };

  const clickElementLikeUser = (el) => {
    const target = clickableElement(el) || el;
    if (!target) {
      return { ok: false, message: "mouse_sequence_target_missing" };
    }
    const candidates = [];
    const seen = new Set();
    const addCandidate = (candidate) => {
      if (!candidate || seen.has(candidate)) {
        return;
      }
      seen.add(candidate);
      candidates.push(candidate);
    };
    const isImage = safeString(el && el.tagName, 24).toLowerCase() === "img";
    if (isImage) {
      addCandidate(el.parentElement);
      if (el.parentElement) {
        addCandidate(el.parentElement.parentElement);
      }
    }
    addCandidate(target);
    let parent = target.parentElement;
    for (let depth = 0; parent && depth < 4; depth += 1) {
      addCandidate(parent);
      parent = parent.parentElement;
    }
    const targets = [];
    let clickedFallback = false;
    let totalDispatched = 0;
    for (const candidate of candidates) {
      const center = elementCenter(candidate) || elementCenter(el) || { x: 0, y: 0 };
      const dispatched = [];
      for (const type of ["mouseover", "mouseenter", "mousemove", "mousedown", "mouseup", "click"]) {
        if (dispatchMouseEvent(candidate, type, center)) {
          dispatched.push(type);
        }
      }
      let targetClickedFallback = false;
      if (!dispatched.length && typeof candidate.click === "function") {
        try {
          candidate.click();
          targetClickedFallback = true;
          clickedFallback = true;
        } catch (_) {}
      }
      totalDispatched += dispatched.length;
      targets.push({ ...elementDescriptor(candidate), dispatched, clickedFallback: targetClickedFallback });
    }
    return {
      ok: totalDispatched > 0 || clickedFallback,
      message: "mouse_sequence_dispatched",
      dispatched: targets.flatMap((item) => item.dispatched),
      clickedFallback,
      target: targets[0] || elementDescriptor(target),
      targets,
    };
  };

  const quickSlotForElement = (el) => {
    try {
      if (!el || typeof el.closest !== "function") {
        return null;
      }
      return el.closest(".b-control-items__slots-item.slot,.b-control-items__slots-item,.slot,[data-index]");
    } catch (_) {
      return null;
    }
  };

  const useQuickSlotEffect = async (match) => {
    const slot = quickSlotForElement(match && match.el);
    if (!slot) {
      return { ok: false, message: "quick_slot_missing", item: match && match.item };
    }
    const itemId = safeString(attr(slot, "data-id"), 80);
    const slotIndex = safeString(attr(slot, "data-index"), 40);
    if (!itemId || !slotIndex) {
      return { ok: false, message: "quick_slot_identity_missing", itemId, slotIndex, slot: elementDescriptor(slot), item: match && match.item };
    }
    const win = slot.ownerDocument && slot.ownerDocument.defaultView ? slot.ownerDocument.defaultView : window.top || window;
    try {
      if (typeof win.unsetEffect === "function") {
        win.unsetEffect(itemId, slotIndex);
        await new Promise((resolve) => (window.top || window).setTimeout(resolve, 900));
        return { ok: true, message: "quick_slot_unset_effect", itemId, slotIndex, slot: elementDescriptor(slot), item: match && match.item };
      }
      if (typeof win.unsetEffect_ === "function") {
        win.unsetEffect_(itemId, slotIndex);
        await new Promise((resolve) => (window.top || window).setTimeout(resolve, 450));
        return { ok: true, message: "quick_slot_unset_effect_direct", itemId, slotIndex, slot: elementDescriptor(slot), item: match && match.item };
      }
    } catch (error) {
      return { ok: false, message: `quick_slot_unset_effect_error:${safeString(error && error.message ? error.message : error, 200)}`, itemId, slotIndex, slot: elementDescriptor(slot), item: match && match.item };
    }
    return { ok: false, message: "quick_slot_unset_effect_missing", itemId, slotIndex, slot: elementDescriptor(slot), item: match && match.item };
  };

  const hoverElement = (el) => {
    if (!el) {
      return false;
    }
    const target = clickableElement(el) || el;
    const center = elementCenter(target) || elementCenter(el) || { x: 0, y: 0 };
    for (const type of ["mouseover", "mouseenter", "mousemove"]) {
      try {
        const event = new MouseEvent(type, {
          bubbles: true,
          cancelable: true,
          view: target.ownerDocument && target.ownerDocument.defaultView ? target.ownerDocument.defaultView : window,
          clientX: center.x,
          clientY: center.y,
        });
        target.dispatchEvent(event);
      } catch (_) {
        try {
          const event = target.ownerDocument.createEvent("MouseEvents");
          event.initMouseEvent(type, true, true, window, 0, 0, 0, center.x, center.y, false, false, false, false, 0, null);
          target.dispatchEvent(event);
        } catch (_) {}
      }
    }
    return true;
  };

  const findVisibleUseControl = (sourceEl) => {
    const sourceCenter = elementCenter(sourceEl);
    const looksLikeUseControl = (text) => /использ|примен|(?:^|[^a-zа-я])(?:use|apply)(?:$|[^a-zа-я])/i.test(text);
    let best = null;
    walkWindows(window.top || window, "top", 4, new Set(), (win, path) => {
      try {
        const doc = win.document;
        const elements = doc
          ? Array.from(doc.querySelectorAll("a,button,input,[onclick],[title],[alt],span,div,img")).slice(0, 3000)
          : [];
        for (const el of elements) {
          const text = elementText(el).toLowerCase();
          if (!looksLikeUseControl(text)) {
            continue;
          }
          const clickable = clickableElement(el);
          if (!clickable || typeof clickable.click !== "function") {
            continue;
          }
          const center = elementCenter(el);
          const distance =
            sourceCenter && center
              ? Math.hypot(Number(center.x) - Number(sourceCenter.x), Number(center.y) - Number(sourceCenter.y))
              : 999999;
          const score = distance + (path === "top" ? 0 : 50);
          if (!best || score < best.score) {
            best = {
              el: clickable,
              score,
              path,
              text: safeString(text, 160),
              tag: safeString(el.tagName, 24),
              id: safeString(el.id, 80),
              className: safeString(el.className, 120),
            };
          }
        }
      } catch (_) {}
    });
    return best;
  };

  const clickHoveredUseControl = async (match) => {
    const root = window.top || window;
    hoverElement(match.el);
    await new Promise((resolve) => root.setTimeout(resolve, 300));
    const control = findVisibleUseControl(match.el);
    if (!control) {
      return { ok: false, message: "hover_use_control_missing", item: match.item };
    }
    try {
      control.el.click();
      return {
        ok: true,
        message: "hover_use_control_clicked",
        control: {
          path: control.path,
          text: control.text,
          tag: control.tag,
          id: control.id,
          className: control.className,
          score: control.score,
        },
        item: match.item,
      };
    } catch (error) {
      return { ok: false, message: `hover_use_control_click_error:${safeString(error && error.message ? error.message : error, 200)}`, item: match.item };
    }
  };

  const collectInventoryElements = () => {
    const output = [];
    walkWindows(window.top || window, "top", 4, new Set(), (win, path) => {
      try {
        const doc = win.document;
        const elements = doc
          ? Array.from(
              doc.querySelectorAll(
                "[data-title],[title],[alt],[aid],[data-id],[data-artikul_id],[data-artikul-id],[data-artikul],[src],[style],a,button,input,span,div,img"
              )
            ).slice(0, 3000)
          : [];
        elements.forEach((el, index) => {
          const text = inventoryText(el);
          const aid = safeString(attr(el, "aid"), 80);
          const dataId = safeString(attr(el, "data-id"), 80);
          const artikul = safeString(attr(el, "data-artikul_id") || attr(el, "data-artikul-id") || attr(el, "data-artikul"), 80);
          const src = safeString(attr(el, "src"), 240);
          const style = safeString(attr(el, "style"), 300);
          if (!text && !aid && !dataId && !artikul && !src && !style) {
            return;
          }
          output.push(inventoryElementFromElement(el, path, index));
        });
      } catch (_) {}
    });
    return output;
  };

  const collectInventoryItems = () => {
    return collectInventoryElements().map((entry) => entry.item);
  };

  const isControlQuickSlotItem = (item) => {
    if (!item) {
      return false;
    }
    const haystack = `${item.path} ${item.id} ${item.className} ${item.src} ${item.style}`.toLowerCase();
    return /b-control-items|control-items/.test(haystack);
  };

  const isNonBurdjukRecoveryEffect = (item) => {
    if (!item) {
      return false;
    }
    const haystack = `${item.title} ${item.artAltTitle} ${item.id} ${item.className} ${item.dataId} ${item.src} ${item.style} ${item.href} ${item.onclick}`.toLowerCase();
    if (/бурдюк|burduk|serburd|tks_qst_osobenn_burduk/.test(haystack)) {
      return false;
    }
    return /нектар|эликсир|зель|сфера|tks_legzel|tks_elik|qst_osobenn_sfera/.test(haystack);
  };

  const isInventoryArtifactCandidate = (item) => {
    if (!item) {
      return false;
    }
    const haystack = `${item.title} ${item.id} ${item.className} ${item.dataId} ${item.src} ${item.style} ${item.href} ${item.onclick}`.toLowerCase();
    if (/user_info|персонаж|игровая информация|боевые рекорды|жизнеспособность|живучесть/.test(haystack)) {
      return false;
    }
    return Boolean(item.artikulId) || /(?:малый|дивный)?\s*бурдюк (жизни|удали)|tks_qst_osobenn_burduk|serburd_(?:hp|mana|mp|udal|ydal)/i.test(haystack);
  };

  const recoverySrcMatchesKind = (item, kind) => {
    const src = `${safeString(item && item.src, 240)} ${safeString(item && item.style, 300)} ${safeString(item && item.id, 80)}`.toLowerCase();
    if (kind === "health") {
      return /бурдюк жизни|tks_qst_osobenn_burduk(?!3)|serburd_hp|burd.*hp|hp.*burd/i.test(src);
    }
    if (kind === "prowess") {
      return /бурдюк удали|tks_qst_osobenn_burduk3|serburd_(?:mana|mp|udal|ydal)|burd.*(?:mana|mp|udal|ydal)|(?:mana|mp|udal|ydal).*burd/i.test(src);
    }
    return false;
  };

  const isBackpackArtCell = (item) => {
    if (!item) {
      return false;
    }
    const haystack = `${item.id} ${item.className} ${item.style} ${item.src}`.toLowerCase();
    return /^art_\d+$/i.test(safeString(item.id, 80)) || (/serburd_(?:hp|mana|mp|udal|ydal)/i.test(haystack) && !isControlQuickSlotItem(item));
  };

	  const recoveryCandidatePriority = (entry, kind, names) => {
	    const item = entry && entry.item;
	    if (!item || !isInventoryArtifactCandidate(item)) {
	      return -1;
	    }
	    if (isControlQuickSlotItem(item)) {
	      return -1;
	    }
    if (isNonBurdjukRecoveryEffect(item)) {
      return -1;
    }
	    const haystack = `${item.title} ${item.artAltTitle} ${item.id} ${item.aid} ${item.cellAid} ${item.divId} ${item.className} ${item.dataId} ${item.src} ${item.style} ${item.href} ${item.onclick}`.toLowerCase();
    const nameMatch = names.some((needle) => haystack.includes(needle));
    const kindMatch = recoverySrcMatchesKind(item, kind);
    if (!nameMatch && !kindMatch) {
      return -1;
    }
    if (isBackpackArtCell(item) && kindMatch) {
      return 100;
    }
    if (isBackpackArtCell(item)) {
      return 90;
    }
    if (item.artikulId && !isControlQuickSlotItem(item) && kindMatch) {
      return 80;
    }
    if (item.artikulId && !isControlQuickSlotItem(item)) {
      return 70;
    }
    if (kindMatch) {
      return 20;
    }
    return 10;
  };

  const inventoryDebugItems = (entries, names) =>
    entries
      .map((entry) => entry.item)
      .filter((item) => {
        const haystack = `${item.title} ${item.id} ${item.className} ${item.dataId} ${item.src} ${item.style} ${item.href} ${item.onclick}`.toLowerCase();
        if (!names.length) {
          return item.artikulId || item.src || /item|art|artifact|inventory|slot|use|usable|рюкзак|бурдюк|serburd/i.test(haystack);
        }
        return item.artikulId || item.src || names.some((needle) => haystack.includes(needle));
      })
      .slice(0, 80);

  const rankedRecoveryMatches = (entries, kind, names) =>
    entries
      .map((entry, order) => ({ entry, order, priority: recoveryCandidatePriority(entry, kind, names) }))
      .filter((candidate) => candidate.priority >= 0)
      .sort((left, right) => right.priority - left.priority || left.order - right.order);

  const waitForRecoveryInventory = async (payload, kind, names) => {
    const root = window.top || window;
    const startedAt = Date.now();
    const baseDelayMs = inventoryOpenDelayMs(payload);
    const timeoutMs = Math.max(baseDelayMs + 1500, 1500);
    const pollMs = 250;
    let entries = [];
    let rankedMatches = [];
    while (Date.now() - startedAt <= timeoutMs) {
      entries = collectInventoryElements();
      rankedMatches = rankedRecoveryMatches(entries, kind, names);
      if (rankedMatches.length) {
        break;
      }
      await new Promise((resolve) => root.setTimeout(resolve, pollMs));
    }
    return {
      entries,
      rankedMatches,
      waitedMs: Date.now() - startedAt,
      timeoutMs,
    };
  };

  const inventorySnapshot = async (payload) => {
    const root = window.top || window;
    const names = normalizeNeedleList(payload && payload.names);
    const open = payload && payload.open != null ? Boolean(payload.open) : true;
    const backpackMessage = open ? openBackpack() : "not_opened";
    if (open) {
      await new Promise((resolve) => root.setTimeout(resolve, inventoryOpenDelayMs(payload)));
    }
    const entries = collectInventoryElements();
    return {
      ok: true,
      message: "inventory_snapshot",
      bridgeVersion: BRIDGE_VERSION,
      backpackMessage,
      itemCount: entries.length,
      names,
      candidates: inventoryDebugItems(entries, names),
      sample: entries.map((entry) => entry.item).slice(0, 80),
    };
  };

  const matchInventoryItem = (items, needles, usedIndexes) => {
    if (!needles.length) {
      return null;
    }
    return (
      items.find((item) => {
        const haystack = `${item.title} ${item.id} ${item.className} ${item.dataId} ${item.src} ${item.style} ${item.href} ${item.onclick}`.toLowerCase();
	        return (
	          isInventoryArtifactCandidate(item) &&
	          !isControlQuickSlotItem(item) &&
	          !usedIndexes.has(`${item.path}:${item.index}`) &&
	          needles.some((needle) => haystack.includes(needle))
	        );
      }) || null
    );
  };

  const findEntryPointWindow = () => {
    let found = null;
    walkWindows(window.top || window, "top", 4, new Set(), (win, path) => {
      if (found) {
        return;
      }
      try {
        if (typeof win.entry_point_request === "function") {
          found = { win, path };
        }
      } catch (_) {}
    });
    return found;
  };

  const artifactUseStatus = (data, item) => {
    let parsed = data;
    if (typeof parsed === "string") {
      try {
        parsed = JSON.parse(parsed);
      } catch (_) {}
    }
    if (!parsed || typeof parsed !== "object") {
      return { accepted: true, status: null, error: null };
    }
    const itemId = item && item.artikulId != null ? String(item.artikulId) : "";
    const direct = itemId && parsed[itemId] && typeof parsed[itemId] === "object" ? parsed[itemId] : null;
    const values = Object.values(parsed).filter((value) => value && typeof value === "object" && Object.prototype.hasOwnProperty.call(value, "status"));
    const byArtikul = itemId
      ? values.find((value) => String(value.artikul_id || value.artikulId || value.artikul || "") === itemId)
      : null;
    const record = direct || byArtikul || values[0] || null;
    if (!record || !Object.prototype.hasOwnProperty.call(record, "status")) {
      return { accepted: true, status: null, error: null };
    }
    const status = Number(record.status);
    return {
      accepted: Number.isFinite(status) ? status === 0 : true,
      status: Number.isFinite(status) ? status : record.status,
      error: record.error == null ? null : safeString(record.error, 160),
    };
  };

  const requestInventoryArtifactUse = (item) =>
    new Promise((resolve) => {
      const entry = findEntryPointWindow();
      if (!entry || !entry.win || typeof entry.win.entry_point_request !== "function") {
        resolve({ ok: false, message: "entry_point_request_missing", item });
        return;
      }
      if (!item || !item.artikulId) {
        resolve({ ok: false, message: "artikul_id_missing", item });
        return;
      }
      const requestedCount = 1;
      const payload = { artikul: [{ [item.artikulId]: requestedCount }] };
      let settled = false;
      const finish = (result) => {
        if (settled) {
          return;
        }
        settled = true;
        resolve(result);
      };
      try {
        entry.win.entry_point_request("inventory", "useArtifact", payload, (resp, data) => {
          const statusInfo = artifactUseStatus(data, item);
          finish({
            ok: statusInfo.accepted,
            message: statusInfo.accepted ? "useArtifact" : "useArtifact_rejected",
            entryPath: entry.path,
            item,
            requestedCount,
            status: statusInfo.status,
            error: statusInfo.error,
            response: resp == null ? null : JSON.stringify(resp).slice(0, 500),
            data: data == null ? null : JSON.stringify(data).slice(0, 1000),
          });
        });
        (window.top || window).setTimeout(
          () => finish({ ok: false, message: "useArtifact_timeout_unconfirmed", entryPath: entry.path, item, requestedCount }),
          3000
        );
      } catch (error) {
        finish({ ok: false, message: `useArtifact_error:${safeString(error && error.message ? error.message : error, 200)}`, item });
      }
    });

  const executeInventoryItemScript = (match) => {
    const item = match && match.item;
    const href = safeString(item && item.href, 500);
    const onclick = safeString(item && item.onclick, 1000);
    let code = "";
    let source = "";
    if (/^javascript:/i.test(href)) {
      code = href.replace(/^javascript:/i, "");
      source = "href";
    } else if (onclick) {
      code = onclick;
      source = "onclick";
    }
    if (!code) {
      return { ok: false, message: "item_script_missing", item };
    }
    const lowered = code.toLowerCase();
    if (!/(use|usable|artifact|artikul|action_form|inventory|примен|использ)/i.test(lowered) || /user_info/i.test(lowered)) {
      return { ok: false, message: "item_script_rejected", source, code: safeString(code, 300), item };
    }
    try {
      const win = match.el && match.el.ownerDocument && match.el.ownerDocument.defaultView ? match.el.ownerDocument.defaultView : window.top || window;
      (win.Function || Function)(`with (this) { ${code} }`).call(win);
      return { ok: true, message: "item_script_executed", source, code: safeString(code, 300), item };
    } catch (error) {
      return { ok: false, message: `item_script_error:${safeString(error && error.message ? error.message : error, 200)}`, source, code: safeString(code, 300), item };
    }
  };

  const openRecoveryItem = async (payload) => {
    const root = window.top || window;
    const kind = safeString(payload && payload.kind, 40).toLowerCase();
    const names = normalizeNeedleList(payload && payload.names);
    const resources = resourceSnapshot();
    const threshold = Math.max(0, Math.min(100, toNumber(payload && payload.useWhenBelowPercent, 90)));
    const useIfMissing = payload && payload.useIfResourcesMissing != null ? Boolean(payload.useIfResourcesMissing) : true;
    const forceUse = Boolean(payload && payload.forceUse);
    const percent = kind === "health" ? resources.healthPercent : kind === "prowess" ? resources.prowessPercent : null;
    const needed = forceUse ? true : percent == null ? useIfMissing : percent < threshold;
    if (!needed) {
      return { ok: true, message: "recovery_item_not_needed", kind, percent, threshold, forceUse, resources };
    }
    const backpackMessage = openBackpack();
    const inventoryWait = await waitForRecoveryInventory(payload, kind, names);
    const entries = inventoryWait.entries;
    const rankedMatches = inventoryWait.rankedMatches;
    const match = rankedMatches.length ? rankedMatches[0].entry : null;
    if (!match) {
      return {
        ok: false,
        message: "recovery_item_missing",
        kind,
        names,
        resources,
        backpackMessage,
        inventoryWaitMs: inventoryWait.waitedMs,
        inventoryWaitTimeoutMs: inventoryWait.timeoutMs,
        itemCount: entries.length,
        candidates: inventoryDebugItems(entries, names),
      };
    }
    let directUseResult = null;
    if (match.item && match.item.artikulId) {
      const useResult = await requestInventoryArtifactUse(match.item);
      directUseResult = useResult;
      if (useResult && useResult.ok) {
        return {
          ok: true,
          message: "recovery_item_clicked",
          method: "entry_point_request",
          kind,
          item: match.item,
          useResult,
          requiresConfirm: false,
          resources,
          backpackMessage,
          inventoryWaitMs: inventoryWait.waitedMs,
          itemCount: entries.length,
        };
      }
    }
	    const scriptUseResult = executeInventoryItemScript(match);
	    if (scriptUseResult && scriptUseResult.ok) {
      return {
        ok: true,
        message: "recovery_item_clicked",
        method: "item_script",
        kind,
        item: match.item,
        useResult: scriptUseResult,
        fallbackReason: directUseResult,
        resources,
        backpackMessage,
        inventoryWaitMs: inventoryWait.waitedMs,
	        itemCount: entries.length,
	      };
	    }
	    return {
	      ok: false,
	      message: directUseResult ? "recovery_item_use_failed" : "recovery_item_artikul_missing",
	      kind,
	      item: match.item,
	      fallbackReason: directUseResult || scriptUseResult,
	      resources,
	      backpackMessage,
	      inventoryWaitMs: inventoryWait.waitedMs,
	      itemCount: entries.length,
	      candidates: inventoryDebugItems(entries, names),
	    };
	  };

  const confirmActionForm = () => {
    const root = window.top || window;
    const inspected = [];
    let found = null;
    walkWindows(root, "top", 5, new Set(), (win, path) => {
      if (found) {
        return;
      }
      try {
        const href = safeString(win.location && win.location.href, 240);
        inspected.push({ path, href });
        if (!/\/action_form\.php(?:\?|$)/.test(href)) {
          return;
        }
        const doc = win.document;
        const elements = doc ? Array.from(doc.querySelectorAll("button,input[type='button'],input[type='submit'],a,[onclick]")).slice(0, 500) : [];
        const button = elements.find((el) => {
          const text = safeString([el.innerText, el.textContent, el.value, attr(el, "title"), attr(el, "onclick")].join(" "), 300).toLowerCase();
          return /(выполн|примен|использ|apply|use|ok)/i.test(text);
        });
        found = { win, path, href, button, buttonCount: elements.length };
      } catch (_) {}
    });
    if (!found) {
      return { ok: false, message: "action_form_missing", href: safeString(root.location && root.location.href, 240), inspected: inspected.slice(0, 20) };
    }
    if (!found.button || typeof found.button.click !== "function") {
      return { ok: false, message: "action_form_button_missing", href: found.href, path: found.path, buttonCount: found.buttonCount };
    }
    try {
      found.button.click();
      root.setTimeout(() => {
        try {
          const frame = root.document && root.document.getElementById ? root.document.getElementById("error") : null;
          const div = root.document && root.document.getElementById ? root.document.getElementById("error_div") : null;
          if (frame && frame.style) {
            frame.style.display = "none";
          }
          if (div && div.style) {
            div.style.display = "none";
          }
        } catch (_) {}
        try {
          found.win.close();
        } catch (_) {}
      }, 500);
      return { ok: true, message: "action_form_confirmed", href: found.href, path: found.path };
    } catch (error) {
      return { ok: false, message: `action_form_confirm_error:${safeString(error && error.message ? error.message : error, 200)}`, href: found.href, path: found.path };
    }
  };

  const useArtifact = (entry, item) =>
    new Promise((resolve) => {
      if (!entry || !entry.win || typeof entry.win.entry_point_request !== "function") {
        resolve({ ok: false, message: "entry_point_request_missing", item });
        return;
      }
      const payload = { artikul: [{ [item.artikulId]: item.count }] };
      let settled = false;
      const finish = (result) => {
        if (settled) {
          return;
        }
        settled = true;
        resolve(result);
      };
      try {
        entry.win.entry_point_request("inventory", "useArtifact", payload, (resp, data) => {
          const statusInfo = artifactUseStatus(data, item);
          finish({
            ok: statusInfo.accepted,
            message: statusInfo.accepted ? "useArtifact" : "useArtifact_rejected",
            entryPath: entry.path,
            item,
            status: statusInfo.status,
            error: statusInfo.error,
            response: resp == null ? null : JSON.stringify(resp).slice(0, 500),
            data: data == null ? null : JSON.stringify(data).slice(0, 1000),
          });
        });
        (window.top || window).setTimeout(
          () => finish({ ok: false, message: "useArtifact_timeout_unconfirmed", entryPath: entry.path, item }),
          3000
        );
      } catch (error) {
        finish({ ok: false, message: `useArtifact_error:${safeString(error && error.message ? error.message : error, 200)}`, item });
      }
    });

  const confirmOpenDialogs = () => {
    const clicked = [];
    walkWindows(window.top || window, "top", 4, new Set(), (win, path) => {
      try {
        const doc = win.document;
        const elements = doc ? Array.from(doc.querySelectorAll("button,input[type='button'],input[type='submit'],a,[onclick]")).slice(0, 800) : [];
        for (const el of elements) {
          const text = safeString([el.innerText, el.textContent, el.value, attr(el, "title"), attr(el, "onclick")].join(" "), 300).toLowerCase();
          if (!/(примен|использ|ок|yes|apply|use)/i.test(text)) {
            continue;
          }
          try {
            if (typeof el.click === "function") {
              el.click();
              clicked.push({ path, tag: safeString(el.tagName, 24), text: safeString(text, 160) });
            }
          } catch (_) {}
          break;
        }
      } catch (_) {}
    });
    return clicked;
  };

  const useRecoveryItems = async (payload) => {
    const root = window.top || window;
    const healthNames = normalizeNeedleList(payload && payload.healthNames).length
      ? normalizeNeedleList(payload && payload.healthNames)
      : ["бурдюк здоровья", "бурдюк жизни", "здоров"];
    const prowessNames = normalizeNeedleList(payload && payload.prowessNames).length
      ? normalizeNeedleList(payload && payload.prowessNames)
      : ["бурдюк удали", "удаль"];
    const resources = resourceSnapshot();
    const threshold = Math.max(0, Math.min(100, toNumber(payload && payload.useWhenBelowPercent, 90)));
    const useIfMissing = payload && payload.useIfResourcesMissing != null ? Boolean(payload.useIfResourcesMissing) : true;
    const needsHealth = resources.healthPercent == null ? useIfMissing : resources.healthPercent < threshold;
    const needsProwess = resources.prowessPercent == null ? useIfMissing : resources.prowessPercent < threshold;
    const backpackMessage = openBackpack();
    await new Promise((resolve) => root.setTimeout(resolve, inventoryOpenDelayMs(payload)));
    const items = collectInventoryItems();
    const entry = findEntryPointWindow();
    const usedIndexes = new Set();
    const attempts = [];
    if (needsHealth) {
      const item = matchInventoryItem(items, healthNames, usedIndexes);
      if (item) {
        usedIndexes.add(`${item.path}:${item.index}`);
        attempts.push({ kind: "health", ...(await useArtifact(entry, item)) });
      } else {
        attempts.push({ kind: "health", ok: false, message: "item_missing", names: healthNames });
      }
    }
    if (needsProwess) {
      const item = matchInventoryItem(items, prowessNames, usedIndexes);
      if (item) {
        usedIndexes.add(`${item.path}:${item.index}`);
        attempts.push({ kind: "prowess", ...(await useArtifact(entry, item)) });
      } else {
        attempts.push({ kind: "prowess", ok: false, message: "item_missing", names: prowessNames });
      }
    }
    await new Promise((resolve) => root.setTimeout(resolve, 350));
    const confirmed = confirmOpenDialogs();
    let huntMessage = "";
    if (payload && payload.openHuntAfter !== false) {
      huntMessage = openHunt();
    }
    return {
      ok: attempts.some((attempt) => attempt.ok) || (!needsHealth && !needsProwess),
      message: "recovery_items_done",
      backpackMessage,
      huntMessage,
      resourcesBefore: resources,
      threshold,
      needsHealth,
      needsProwess,
      itemCount: items.length,
      entryPath: entry ? entry.path : "",
      attempts,
      confirmed,
    };
  };

  const huntDebug = () => {
    const hunt = findHuntApp();
    if (!hunt) {
      return { ok: false, message: "hunt_missing", snapshot: huntSnapshot() };
    }
    const compass = hunt.view && hunt.view.compass;
    const scroller = hunt.view && hunt.view.scrollerView;
    return {
      ok: true,
      bridgeVersion: BRIDGE_VERSION,
      generatedAt: new Date().toISOString(),
      viewBounds: hunt.view && hunt.view.viewBounds ? objectPreview(hunt.view.viewBounds) : null,
      compass: compass ? {
        selected: objectPreview(compass.selected),
        north: objectPreview(compass.north),
        south: objectPreview(compass.south),
        west: objectPreview(compass.west),
        east: objectPreview(compass.east),
        setSelected: functionSource(compass, "setSelected", 2400),
        onSelect: functionSource(compass, "onSelect", 2400),
        getSelected: functionSource(compass, "getSelected", 1200),
      } : null,
      scroller: scroller ? {
        state: objectPreview(scroller),
        setViewPercent: functionSource(scroller, "setViewPercent", 2400),
        applyPosition: functionSource(scroller, "applyPosition", 2400),
      } : null,
      view: hunt.view ? {
        onScroll: functionSource(hunt.view, "onScroll", 2400),
        applyScrollPosition: functionSource(hunt.view, "applyScrollPosition", 2400),
      } : null,
      model: hunt.model ? {
        bots: hunt.model.bots ? objectPreview(hunt.model.bots) : null,
        botCount: hunt.model.bots && Array.isArray(hunt.model.bots.list) ? hunt.model.bots.list.length : null,
      } : null,
    };
  };

  const primitiveEntries = (value) => {
    const entries = [];
    for (const name of propertyNames(value)) {
      let current;
      try {
        current = value[name];
      } catch (_) {
        continue;
      }
      const primitive = previewValue(current);
      if (primitive !== undefined || current == null) {
        entries.push([String(name), primitive]);
      }
    }
    return entries;
  };

  const candidateFromObject = (value, path) => {
    const entries = primitiveEntries(value);
    const fields = Object.fromEntries(entries.slice(0, 80));
    const lowerPath = path.toLowerCase();
    const idEntry = entries.find(([name, current]) => {
      if (typeof current !== "number" && typeof current !== "string") {
        return false;
      }
      if (!/^\d+$/.test(String(current))) {
        return false;
      }
      return /^(id|bot_id|botid|mob_id|mobid|monster_id|monsterid|npc_id|npcid|unit_id|unitid)$/i.test(name)
        || /(?:bot|mob|monster|npc|unit).*id/i.test(name)
        || /id.*(?:bot|mob|monster|npc|unit)/i.test(name);
    });
    const nameEntry = entries.find(([name, current]) => current != null && /(name|title|nick|label|caption|text)$/i.test(name));
    const xEntry = entries.find(([name, current]) => typeof current === "number" && /^(x|left|map_x|pos_x|coord_x|cell_x|gx)$/i.test(name));
    const yEntry = entries.find(([name, current]) => typeof current === "number" && /^(y|top|map_y|pos_y|coord_y|cell_y|gy)$/i.test(name));
    const levelEntry = entries.find(([name, current]) => current != null && /^(level|lvl|lev|l)$/i.test(name));
    const typeEntry = entries.find(([name, current]) => current != null && /(type|kind|class|race|mob|bot|monster|npc)/i.test(name));
    let score = 0;
    const reasons = [];
    if (idEntry) {
      score += 4;
      reasons.push(`id:${idEntry[0]}`);
    }
    if (nameEntry) {
      score += 2;
      reasons.push(`name:${nameEntry[0]}`);
    }
    if (xEntry && yEntry) {
      score += 2;
      reasons.push(`xy:${xEntry[0]},${yEntry[0]}`);
    }
    if (/(bot|mob|monster|npc|unit|enemy)/i.test(lowerPath)) {
      score += 2;
      reasons.push("path_hint");
    }
    if (typeEntry && /(bot|mob|monster|npc|unit|enemy)/i.test(String(typeEntry[1]))) {
      score += 2;
      reasons.push(`type:${typeEntry[0]}`);
    }
    if (!idEntry || score < 5) {
      return null;
    }
    return {
      path,
      score,
      botId: parseInt(String(idEntry[1]), 10),
      idField: idEntry[0],
      name: nameEntry ? safeString(nameEntry[1], 120) : "",
      level: levelEntry ? safeString(levelEntry[1], 40) : "",
      type: typeEntry ? safeString(typeEntry[1], 80) : "",
      x: xEntry ? xEntry[1] : null,
      y: yEntry ? yEntry[1] : null,
      reasons,
      fields,
    };
  };

  const collectHuntCandidates = (rootValue) => {
    const seen = new Set();
    const candidates = [];
    const walk = (value, path, depth) => {
      if (candidates.length >= 120 || depth < 0 || shouldSkipObject(value) || seen.has(value)) {
        return;
      }
      seen.add(value);
      const candidate = candidateFromObject(value, path);
      if (candidate) {
        candidates.push(candidate);
      }
      for (const name of propertyNames(value).slice(0, 100)) {
        let current;
        try {
          current = value[name];
        } catch (_) {
          continue;
        }
        if (current && typeof current === "object" && !shouldSkipObject(current)) {
          walk(current, `${path}.${name}`, depth - 1);
        }
      }
    };
    walk(rootValue, "hunt", 6);
    return candidates.sort((left, right) => right.score - left.score || left.botId - right.botId).slice(0, 80);
  };

  const toNumber = (value, fallback = 0) => {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  };

  const normalizeLevelList = (value) => {
    const raw = Array.isArray(value) ? value : value == null ? [] : [value];
    const levels = [];
    for (const item of raw) {
      const parts = typeof item === "string" ? item.split(/[,\s]+/) : [item];
      for (const part of parts) {
        const level = Number(part);
        if (Number.isInteger(level) && level > 0 && !levels.includes(level)) {
          levels.push(level);
        }
      }
    }
    return levels.sort((left, right) => left - right);
  };

  const parseLevelFromName = (value) => {
    const text = safeString(value, 180);
    const match = text.match(/\[(\d{1,3})\](?!.*\[\d{1,3}\])/);
    if (!match) {
      return null;
    }
    const level = Number(match[1]);
    return Number.isFinite(level) ? level : null;
  };

  const readBotLevel = (bot) => {
    if (!bot) {
      return { level: null, source: "" };
    }
    const entries = [
      ["level", bot.level],
      ["lvl", bot.lvl],
      ["lev", bot.lev],
      ["lv", bot.lv],
      ["l", bot.l],
      ["ulevel", bot.ulevel],
      ["uLevel", bot.uLevel],
      ["botLevel", bot.botLevel],
      ["mobLevel", bot.mobLevel],
      ["monsterLevel", bot.monsterLevel],
    ];
    for (const [source, value] of entries) {
      const level = toNumber(value, null);
      if (level != null) {
        return { level, source };
      }
    }
    const parsed = parseLevelFromName(bot.name || bot.shortName || bot.title || bot.caption);
    if (parsed != null) {
      return { level: parsed, source: "name_brackets" };
    }
    return { level: null, source: "" };
  };

  const modelBotSummary = (bot) => {
    const levelInfo = readBotLevel(bot);
    return {
      botId: parseInt(String(bot && bot.id), 10),
      id: safeString(bot && bot.id, 40),
      name: safeString(bot && (bot.name || bot.shortName), 120),
      shortName: safeString(bot && bot.shortName, 120),
      level: levelInfo.level,
      levelSource: levelInfo.source,
      x: toNumber(bot && bot.x, null),
      y: toNumber(bot && bot.y, null),
      pic: safeString(bot && bot.pic, 160),
      sk: safeString(bot && bot.sk, 40),
      fightId: toNumber(bot && bot.fightId, 0),
      agrdist: toNumber(bot && bot.agrdist, 0),
      agrforbid: Boolean(bot && bot.agrforbid),
      elite: toNumber(bot && bot.elite, 0),
      ghost: toNumber(bot && bot.ghost, 0),
      isBot: Boolean(bot && bot.isBot),
    };
  };

  const displayNodeSummary = (node, index) => {
    const item = {
      index,
      constructorName: "",
      id: null,
      x: null,
      y: null,
      globalX: null,
      globalY: null,
      visible: null,
      name: "",
      cursor: "",
      childCount: null,
      primitiveProps: {},
    };
    try {
      item.constructorName = safeString(node && node.constructor && node.constructor.name, 80);
      item.id = node && node.id != null ? node.id : null;
      item.x = node && node.x != null ? node.x : null;
      item.y = node && node.y != null ? node.y : null;
      item.visible = node && node.visible != null ? Boolean(node.visible) : null;
      item.name = safeString(node && node.name, 80);
      item.cursor = safeString(node && node.cursor, 80);
      item.childCount = node && node.children && Array.isArray(node.children) ? node.children.length : null;
      if (node && typeof node.localToGlobal === "function") {
        const global = node.localToGlobal(0, 0);
        item.globalX = global && global.x != null ? global.x : null;
        item.globalY = global && global.y != null ? global.y : null;
      }
      for (const [name, current] of primitiveEntries(node || {}).slice(0, 60)) {
        item.primitiveProps[name] = current;
      }
    } catch (_) {}
    return item;
  };

  const visibleHuntTargets = (payload) => {
    const root = window.top || window;
    const hunt = findHuntApp();
    let mainHref = "";
    try {
      mainHref = safeString(root.frames["main_frame"].frames["main"].location.href, 240);
    } catch (_) {}
    if (!hunt || !hunt.model || !hunt.view) {
      return {
        bridgeVersion: BRIDGE_VERSION,
        generatedAt: new Date().toISOString(),
        rootHref: safeString(root.location && root.location.href, 240),
        mainHref,
        hasHunt: Boolean(hunt),
        viewBounds: null,
        targets: [],
        displayChildren: [],
      };
    }
    const margin = Math.max(0, toNumber(payload && payload.margin, 35));
    const allowedNames = Array.isArray(payload && payload.names)
      ? payload.names.map((name) => safeString(name, 120)).filter(Boolean)
      : [];
    const allowedLevels = normalizeLevelList(
      payload && (payload.allowedLevels || payload.allowed_levels || payload.levels || payload.targetLevels)
    );
    const viewBounds = hunt.view.viewBounds || {};
    const bounds = {
      x: toNumber(viewBounds.x, 0),
      y: toNumber(viewBounds.y, 0),
      w: toNumber(viewBounds.w || viewBounds.width, toNumber(hunt.view.width, 0)),
      h: toNumber(viewBounds.h || viewBounds.height, toNumber(hunt.view.height, 0)),
      ap: toNumber(viewBounds.ap, 0),
      rp: toNumber(viewBounds.rp, 0),
    };
    const content = hunt.view.content || (hunt.view.children && hunt.view.children[3]) || {};
    const botsLayer = content.bots || (content.children && content.children[1]) || null;
    const layer = {
      contentX: toNumber(content.x, 0),
      contentY: toNumber(content.y, 0),
      botsX: toNumber(botsLayer && botsLayer.x, -bounds.x),
      botsY: toNumber(botsLayer && botsLayer.y, -bounds.y),
      botsChildren: botsLayer && botsLayer.children ? botsLayer.children.length : 0,
    };
    const rawBots = hunt.model.bots && Array.isArray(hunt.model.bots.list) ? hunt.model.bots.list : [];
    const centerX = bounds.x + bounds.w / 2;
    const centerY = bounds.y + bounds.h / 2;
    const targets = rawBots
      .map((bot) => modelBotSummary(bot))
      .filter((bot) => bot.isBot && bot.botId && !bot.agrforbid && bot.fightId === 0 && bot.x != null && bot.y != null)
      .filter((bot) => !allowedNames.length || allowedNames.some((name) => bot.name.includes(name) || bot.shortName.includes(name)))
      .filter((bot) => !allowedLevels.length || allowedLevels.includes(Number(bot.level)))
      .map((bot) => {
        const visible =
          bot.x >= bounds.x - margin &&
          bot.x <= bounds.x + bounds.w + margin &&
          bot.y >= bounds.y - margin &&
          bot.y <= bounds.y + bounds.h + margin;
        return {
          ...bot,
          visible,
          screenX: layer.contentX + (bot.x - bounds.x),
          screenY: layer.contentY + (bot.y - bounds.y),
          distanceToViewportCenter: Math.hypot(bot.x - centerX, bot.y - centerY),
        };
      })
      .filter((bot) => bot.visible)
      .sort((left, right) => left.distanceToViewportCenter - right.distanceToViewportCenter || left.botId - right.botId);
    const displayChildren = botsLayer && Array.isArray(botsLayer.children)
      ? botsLayer.children.slice(0, 40).map((node, index) => displayNodeSummary(node, index))
      : [];
    return {
      bridgeVersion: BRIDGE_VERSION,
      generatedAt: new Date().toISOString(),
      rootHref: safeString(root.location && root.location.href, 240),
      mainHref,
      hasHunt: true,
      viewBounds: bounds,
      allowedLevels,
      layer,
      targets,
      displayChildren,
    };
  };

  const huntBotInfo = (payload) => {
    const rawBotId = payload && (payload.bot_id || payload.botId || payload.id);
    const botId = parseInt(rawBotId, 10);
    const hunt = findHuntApp();
    if (!Number.isFinite(botId) || botId <= 0) {
      return { ok: false, message: "missing_bot_id", botId: null, hasHunt: Boolean(hunt) };
    }
    const rawBots = hunt && hunt.model && hunt.model.bots && Array.isArray(hunt.model.bots.list)
      ? hunt.model.bots.list
      : [];
    const bot = rawBots.find((entry) => parseInt(String(entry && entry.id), 10) === botId) || null;
    if (!bot) {
      return {
        ok: false,
        message: "bot_not_found",
        botId,
        hasHunt: Boolean(hunt),
        botCount: rawBots.length,
      };
    }
    return {
      ok: true,
      message: "bot_found",
      bot: modelBotSummary(bot),
      primitiveProps: Object.fromEntries(primitiveEntries(bot).slice(0, 120)),
    };
  };

  const huntDirectionId = (value) => {
    const direction = safeString(value, 24).toLowerCase();
    if (direction === "north" || direction === "n") {
      return "north";
    }
    if (direction === "south" || direction === "s") {
      return "south";
    }
    if (direction === "west" || direction === "w") {
      return "west";
    }
    if (direction === "east" || direction === "e") {
      return "east";
    }
    return "";
  };

  const huntWorldBounds = (hunt, bounds) => {
    const rawBots = hunt.model && hunt.model.bots && Array.isArray(hunt.model.bots.list) ? hunt.model.bots.list : [];
    const maxBotX = rawBots.reduce((current, bot) => Math.max(current, toNumber(bot && bot.x, 0)), 0);
    const maxBotY = rawBots.reduce((current, bot) => Math.max(current, toNumber(bot && bot.y, 0)), 0);
    const view = hunt.view || {};
    const content = view.content || {};
    const contentWidth = toNumber(content.width || view.width, 0);
    const contentHeight = toNumber(content.height || view.height, 0);
    const worldWidth = Math.max(bounds.w * 2, contentWidth, maxBotX + bounds.w / 2);
    const worldHeight = Math.max(bounds.h * 2, contentHeight, maxBotY + bounds.h / 2);
    return {
      maxX: Math.max(0, Math.round(worldWidth - bounds.w)),
      maxY: Math.max(0, Math.round(worldHeight - bounds.h)),
    };
  };

  const moveHuntDirection = (payload) => {
    const hunt = findHuntApp();
    if (!hunt || !hunt.view || !hunt.view.viewBounds) {
      return { ok: false, message: "hunt_missing", snapshot: huntSnapshot() };
    }
    const direction = huntDirectionId(payload && payload.direction);
    if (!direction) {
      return { ok: false, message: "unknown_direction", direction: safeString(payload && payload.direction, 24) };
    }

    const viewBounds = hunt.view.viewBounds;
    const bounds = {
      x: toNumber(viewBounds.x, 0),
      y: toNumber(viewBounds.y, 0),
      w: toNumber(viewBounds.w || viewBounds.width, toNumber(hunt.view.width, 0)),
      h: toNumber(viewBounds.h || viewBounds.height, toNumber(hunt.view.height, 0)),
    };
    const world = huntWorldBounds(hunt, bounds);
    let nextX = bounds.x;
    let nextY = bounds.y;
    if (direction === "north") {
      nextX = Math.round(world.maxX / 2);
      nextY = 0;
    } else if (direction === "south") {
      nextX = Math.round(world.maxX / 2);
      nextY = world.maxY;
    } else if (direction === "west") {
      nextX = 0;
      nextY = Math.round(world.maxY / 2);
    } else if (direction === "east") {
      nextX = world.maxX;
      nextY = Math.round(world.maxY / 2);
    }

    viewBounds.x = Math.max(0, Math.min(world.maxX, nextX));
    viewBounds.y = Math.max(0, Math.min(world.maxY, nextY));
    try {
      if (hunt.view.compass && typeof hunt.view.compass.setSelected === "function") {
        hunt.view.compass.setSelected(direction);
      }
    } catch (_) {}
    try {
      if (hunt.view.content && typeof hunt.view.content.onScroll === "function") {
        hunt.view.content.onScroll(viewBounds);
      } else if (typeof hunt.view.applyScrollPosition === "function") {
        hunt.view.applyScrollPosition();
      }
    } catch (error) {
      return { ok: false, message: `hunt_scroll_error:${safeString(error && error.message ? error.message : error, 200)}` };
    }

    const margin = payload && payload.margin != null ? payload.margin : 35;
    const visible = visibleHuntTargets({ margin });
    return {
      ok: true,
      message: "hunt_direction_moved",
      direction,
      world,
      viewBounds: visible.viewBounds,
      targetCount: visible.targets.length,
      targets: visible.targets.slice(0, 5),
    };
  };

  const huntCandidates = () => {
    const root = window.top || window;
    const hunt = findHuntApp();
    let mainHref = "";
    try {
      mainHref = safeString(root.frames["main_frame"].frames["main"].location.href, 240);
    } catch (_) {}
    return {
      bridgeVersion: BRIDGE_VERSION,
      generatedAt: new Date().toISOString(),
      rootHref: safeString(root.location && root.location.href, 240),
      mainHref,
      hasHunt: Boolean(hunt),
      candidates: hunt ? collectHuntCandidates(hunt) : [],
    };
  };

  const attackVisibleBot = (payload) => {
    const visible = visibleHuntTargets(payload || {});
    const target = visible.targets[0] || null;
    if (!target) {
      return { ok: false, message: "visible_bot_missing", visible };
    }
    const result = attackBot({ bot_id: target.botId, confirmed: payload && payload.confirmed == null ? 1 : payload.confirmed });
    return {
      ok: Boolean(result.ok),
      message: result.message,
      target,
      visible,
    };
  };

  const attackBot = (payload) => {
    const root = window.top || window;
    const botId = parseInt(payload && (payload.bot_id || payload.botId), 10);
    const confirmed = parseInt(payload && payload.confirmed, 10) || 0;
    if (!botId) {
      return { ok: false, message: "missing_bot_id" };
    }
    try {
      if (typeof root.huntAttack === "function") {
        root.huntAttack(botId, confirmed);
        return { ok: true, message: "huntAttack", botId, confirmed };
      }
    } catch (error) {
      return { ok: false, message: `huntAttack_error:${safeString(error && error.message ? error.message : error, 200)}` };
    }
    try {
      if (typeof root.botAttack === "function") {
        root.botAttack(botId, "/hunt.php", `&in[hunt]=1&in[confirmed]=${confirmed || 0}`, confirmed ? null : root.gebi && root.gebi("error"));
        return { ok: true, message: "botAttack", botId, confirmed };
      }
    } catch (error) {
      return { ok: false, message: `botAttack_error:${safeString(error && error.message ? error.message : error, 200)}` };
    }
    return { ok: false, message: "attack_function_missing" };
  };

  const openHunt = () => {
    const root = window.top || window;
    try {
      if (/\/hunt\.php(?:\?|$)/.test(root.location.href)) {
        return "already_top_hunt";
      }
    } catch (_) {}

    try {
      const mainFrame = root.frames && root.frames["main_frame"];
      if (mainFrame) {
        if (typeof mainFrame.processMenu === "function") {
          mainFrame.processMenu("b07");
          return "processMenu_b07";
        }
        if (typeof mainFrame.openHunt === "function") {
          mainFrame.openHunt();
          return "openHunt";
        }
        if (mainFrame.frames && mainFrame.frames["main"]) {
          mainFrame.frames["main"].location.href = "hunt.php?update_swf=1";
          return "main_frame_main_hunt";
        }
      }
    } catch (error) {
      return `frame_error:${String(error && error.message ? error.message : error)}`;
    }

    try {
      root.location.href = "https://3kingdoms.ru/main.php";
      return "main_fallback";
    } catch (error) {
      return `fallback_error:${String(error && error.message ? error.message : error)}`;
    }
  };

  const openBackpack = () => {
    const root = window.top || window;
    try {
      const mainFrame = root.frames && root.frames["main_frame"];
      if (mainFrame) {
        if (typeof mainFrame.processMenu === "function") {
          mainFrame.processMenu("b11");
          return "processMenu_b11";
        }
        if (mainFrame.frames && mainFrame.frames["main"]) {
          mainFrame.frames["main"].location.href = "user.php?mode=personage&submode=backpack";
          return "main_frame_main_backpack";
        }
      }
    } catch (error) {
      return `backpack_frame_error:${safeString(error && error.message ? error.message : error, 200)}`;
    }
    try {
      root.location.href = "https://3kingdoms.ru/main.php";
      return "main_fallback";
    } catch (error) {
      return `backpack_fallback_error:${safeString(error && error.message ? error.message : error, 200)}`;
    }
  };

  const layoutTargets = (root) => {
    const docs = [];
    const addDoc = (doc) => {
      if (doc && !docs.includes(doc)) {
        docs.push(doc);
      }
    };
    addDoc(root.document);
    try {
      if (root.frames && root.frames["main_frame"]) {
        addDoc(root.frames["main_frame"].document);
      }
    } catch (_) {}
    return docs;
  };

  const findMainContentWindow = (root) => {
    try {
      if (root.frames && root.frames["main_frame"] && root.frames["main_frame"].frames && root.frames["main_frame"].frames["main"]) {
        return root.frames["main_frame"].frames["main"];
      }
    } catch (_) {}
    try {
      if (root.frames && root.frames["main_frame"]) {
        return root.frames["main_frame"];
      }
    } catch (_) {}
    return root;
  };

  const saveStyle = (el) => ({
    style: el.getAttribute("style"),
    rows: el.getAttribute("rows"),
    cols: el.getAttribute("cols"),
    scrolling: el.getAttribute("scrolling"),
    noresize: el.getAttribute("noresize"),
  });

  const restoreStyle = (el, saved) => {
    const attrs = ["style", "rows", "cols", "scrolling", "noresize"];
    for (const name of attrs) {
      if (saved && saved[name] != null) {
        el.setAttribute(name, saved[name]);
      } else {
        el.removeAttribute(name);
      }
    }
  };

  const elementRectSummary = (el) => {
    try {
      const rect = el.getBoundingClientRect();
      return {
        tag: safeString(el.tagName, 24),
        id: safeString(el.id, 80),
        className: safeString(el.className, 120),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
        left: Math.round(rect.left),
        top: Math.round(rect.top),
      };
    } catch (_) {
      return null;
    }
  };

  const layoutSnapshot = () => {
    const root = window.top || window;
    const mainWin = findMainContentWindow(root);
    const doc = mainWin && mainWin.document ? mainWin.document : null;
    const viewport = {
      width: mainWin && mainWin.innerWidth != null ? mainWin.innerWidth : null,
      height: mainWin && mainWin.innerHeight != null ? mainWin.innerHeight : null,
    };
    let elements = [];
    try {
      elements = Array.from(doc.body ? doc.body.querySelectorAll("body > *, table, img, canvas, object, embed, iframe, div") : [])
        .map(elementRectSummary)
        .filter(Boolean)
        .filter((item) => item.width >= 120 && item.height >= 80)
        .sort((left, right) => right.width * right.height - left.width * left.height)
        .slice(0, 30);
    } catch (_) {}
    return {
      ok: Boolean(doc),
      bridgeVersion: BRIDGE_VERSION,
      mainHref: safeString(mainWin && mainWin.location && mainWin.location.href, 240),
      viewport,
      elements,
    };
  };

  const applyMainContentStretch = (root, state, payload) => {
    const mainWin = findMainContentWindow(root);
    const doc = mainWin && mainWin.document ? mainWin.document : null;
    if (!doc || !doc.body) {
      return { changed: 0, reason: "main_document_missing" };
    }
    const stretchMode = safeString(payload && (payload.stretch || payload.stretchMode || payload.stretch_mode), 24).toLowerCase() || "fit-width";
    if (stretchMode === "off" || stretchMode === "none") {
      return { changed: 0, reason: "stretch_disabled" };
    }
    const viewportWidth = Math.max(0, toNumber(mainWin.innerWidth, 0));
    const viewportHeight = Math.max(0, toNumber(mainWin.innerHeight, 0));
    if (viewportWidth <= 0 || viewportHeight <= 0) {
      return { changed: 0, reason: "viewport_missing" };
    }
    const html = doc.documentElement;
    for (const el of [html, doc.body]) {
      if (!el) {
        continue;
      }
      if (!state.elements.has(el)) {
        state.elements.set(el, saveStyle(el));
      }
      el.style.margin = "0";
      el.style.padding = "0";
      el.style.width = "100%";
      el.style.height = "100%";
      el.style.overflow = "hidden";
    }
    let candidates = [];
    try {
      candidates = Array.from(doc.body.querySelectorAll("body > *, table, img, canvas, object, embed, iframe, div"))
        .map((el) => ({ el, rect: el.getBoundingClientRect() }))
        .filter((item) => item.rect.width >= 250 && item.rect.height >= 180)
        .sort((left, right) => right.rect.width * right.rect.height - left.rect.width * left.rect.height);
    } catch (_) {}
    const target = candidates[0] || null;
    if (!target) {
      return { changed: 0, reason: "target_missing" };
    }
    const currentWidth = Math.max(1, target.rect.width);
    const currentHeight = Math.max(1, target.rect.height);
    const fitWidthScale = viewportWidth / currentWidth;
    const fitHeightScale = viewportHeight / currentHeight;
    const scale = stretchMode === "fit"
      ? Math.min(fitWidthScale, fitHeightScale)
      : fitWidthScale;
    const boundedScale = Math.max(1, Math.min(1.8, scale));
    const el = target.el;
    if (!state.elements.has(el)) {
      state.elements.set(el, saveStyle(el));
    }
    el.style.transformOrigin = "top left";
    el.style.transform = `scale(${boundedScale})`;
    el.style.position = "absolute";
    el.style.left = "0";
    el.style.top = "0";
    el.style.maxWidth = "none";
    el.style.maxHeight = "none";
    return {
      changed: 1,
      stretchMode,
      scale: boundedScale,
      target: elementRectSummary(el),
      viewportWidth,
      viewportHeight,
    };
  };

  const isServiceFrameName = (name) => /^(ajax|error|smile|devnull|main_hidden|chat_hidden|chat_user)$/.test(name);
  const isChatFrameName = (name) => name === "chat" || name === "chat_frame" || name === "chat_main";
  const isMainFrameName = (name) => name === "main_frame" || name === "main" || name.includes("main");

  const applyWideFrameset = (frameset, state, chatHeightPx) => {
    const children = Array.from(frameset.children || []).filter((el) => /^(FRAME|FRAMESET|IFRAME)$/i.test(el.tagName || ""));
    if (!children.length) {
      return false;
    }
    const names = children.map((el) => safeString(el.getAttribute("name"), 80).toLowerCase());
    const mainIndex = names.findIndex(isMainFrameName);
    const hasCollapsible = names.some((name) => isServiceFrameName(name) || isChatFrameName(name));
    if (mainIndex < 0 && !hasCollapsible) {
      return false;
    }
    if (!state.elements.has(frameset)) {
      state.elements.set(frameset, saveStyle(frameset));
    }
    const sizes = names.map((name, index) => {
      if (mainIndex >= 0 && index === mainIndex) {
        return "*";
      }
      if (isChatFrameName(name)) {
        return String(chatHeightPx);
      }
      if (isServiceFrameName(name)) {
        return "0";
      }
      return "*";
    });
    if (frameset.getAttribute("rows") != null || frameset.rows != null) {
      frameset.setAttribute("rows", sizes.join(","));
    }
    if (frameset.getAttribute("cols") != null || frameset.cols != null) {
      frameset.setAttribute("cols", sizes.join(","));
    }
    return true;
  };

  const applyLayout = (payload) => {
    const root = window.top || window;
    const mode = safeString(payload && payload.mode, 24).toLowerCase() || "wide";
    const existing = root.__antibotCvLayoutState || null;
    if (mode === "normal" || mode === "reset" || mode === "off") {
      if (existing && existing.elements) {
        for (const [el, saved] of existing.elements.entries()) {
          restoreStyle(el, saved);
        }
      }
      root.__antibotCvLayoutState = null;
      return { ok: true, message: "layout_restored", mode: "normal" };
    }
    if (mode !== "wide") {
      return { ok: false, message: `unknown_layout_mode:${mode}` };
    }
    const chatHeightPx = Math.max(80, Math.min(320, Math.round(toNumber(payload && (payload.chatHeight || payload.chat_height), 140))));
    const state = existing || { elements: new Map() };
    let changed = 0;
    for (const doc of layoutTargets(root)) {
      try {
        const html = doc.documentElement;
        const body = doc.body;
        for (const el of [html, body]) {
          if (!el) {
            continue;
          }
          if (!state.elements.has(el)) {
            state.elements.set(el, saveStyle(el));
          }
          el.style.margin = "0";
          el.style.padding = "0";
          el.style.overflow = "hidden";
        }
        for (const frame of Array.from(doc.querySelectorAll("frame,iframe"))) {
          const name = safeString(frame.getAttribute("name"), 80).toLowerCase();
          if (!state.elements.has(frame)) {
            state.elements.set(frame, saveStyle(frame));
          }
          if (isServiceFrameName(name)) {
            frame.style.display = "none";
            frame.setAttribute("scrolling", "no");
            frame.setAttribute("noresize", "noresize");
            changed += 1;
          } else if (isChatFrameName(name)) {
            frame.style.display = "";
            frame.style.width = "100%";
            frame.style.height = `${chatHeightPx}px`;
            changed += 1;
          } else if (isMainFrameName(name)) {
            frame.style.display = "";
            frame.style.width = "100%";
            frame.style.height = "100%";
            changed += 1;
          }
        }
        for (const frameset of Array.from(doc.querySelectorAll("frameset"))) {
          if (applyWideFrameset(frameset, state, chatHeightPx)) {
            changed += 1;
          }
        }
      } catch (_) {}
    }
    const contentStretch = applyMainContentStretch(root, state, payload);
    changed += contentStretch.changed || 0;
    root.__antibotCvLayoutState = state;
    return { ok: true, message: "layout_applied", mode: "wide", chatHeightPx, changed, contentStretch };
  };

  window.addEventListener("message", (event) => {
    if (event.source !== window) {
      return;
    }
    const data = event.data || {};
    if (data.source !== CONTENT_SOURCE || !data.token || !data.command) {
      return;
    }
    try {
      if (data.command.type === "open_hunt") {
        send(data.token, true, openHunt());
        return;
      }
      if (data.command.type === "layout") {
        const result = applyLayout(data.command.payload || {});
        send(data.token, Boolean(result.ok), result);
        return;
      }
      if (data.command.type === "layout_snapshot") {
        const result = layoutSnapshot();
        send(data.token, Boolean(result.ok), result);
        return;
      }
      if (data.command.type === "probe_page") {
        send(data.token, true, probePage());
        return;
      }
      if (data.command.type === "inspect_functions") {
        send(data.token, true, inspectFunctions(data.command.payload || {}));
        return;
      }
      if (data.command.type === "hunt_snapshot") {
        send(data.token, true, huntSnapshot());
        return;
      }
      if (data.command.type === "hunt_debug") {
        const result = huntDebug();
        send(data.token, Boolean(result.ok), result);
        return;
      }
      if (data.command.type === "resource_snapshot") {
        const result = resourceSnapshot();
        send(data.token, Boolean(result.ok), result);
        return;
      }
      if (data.command.type === "resource_refresh") {
        const result = refreshResourceSource();
        send(data.token, Boolean(result.ok), result);
        return;
      }
      if (data.command.type === "open_recovery_item") {
        openRecoveryItem(data.command.payload || {})
          .then((result) => send(data.token, Boolean(result.ok), result))
          .catch((error) => send(data.token, false, `open_recovery_item_error:${safeString(error && error.message ? error.message : error, 200)}`));
        return;
      }
      if (data.command.type === "inventory_snapshot") {
        inventorySnapshot(data.command.payload || {})
          .then((result) => send(data.token, Boolean(result.ok), result))
          .catch((error) => send(data.token, false, `inventory_snapshot_error:${safeString(error && error.message ? error.message : error, 200)}`));
        return;
      }
      if (data.command.type === "confirm_action_form") {
        const result = confirmActionForm(data.command.payload || {});
        send(data.token, Boolean(result.ok), result);
        return;
      }
      if (data.command.type === "use_recovery_items") {
        useRecoveryItems(data.command.payload || {})
          .then((result) => send(data.token, Boolean(result.ok), result))
          .catch((error) => send(data.token, false, `use_recovery_items_error:${safeString(error && error.message ? error.message : error, 200)}`));
        return;
      }
      if (data.command.type === "hunt_candidates") {
        send(data.token, true, huntCandidates());
        return;
      }
      if (data.command.type === "visible_hunt_targets") {
        send(data.token, true, visibleHuntTargets(data.command.payload || {}));
        return;
      }
      if (data.command.type === "hunt_bot_info") {
        send(data.token, true, huntBotInfo(data.command.payload || {}));
        return;
      }
      if (data.command.type === "hunt_move_direction") {
        const result = moveHuntDirection(data.command.payload || {});
        send(data.token, Boolean(result.ok), result);
        return;
      }
      if (data.command.type === "battle_snapshot") {
        send(data.token, true, battleSnapshot());
        return;
      }
      if (data.command.type === "battle_debug") {
        const result = battleDebug();
        send(data.token, Boolean(result.ok), result);
        return;
      }
      if (data.command.type === "use_skill_slot") {
        const result = useSkillSlot(data.command.payload || {});
        send(data.token, Boolean(result.ok), result);
        return;
      }
      if (data.command.type === "attack_visible_bot") {
        const result = attackVisibleBot(data.command.payload || {});
        send(data.token, Boolean(result.ok), result);
        return;
      }
      if (data.command.type === "attack_bot") {
        const result = attackBot(data.command.payload || {});
        send(data.token, Boolean(result.ok), result);
        return;
      }
      send(data.token, false, `unknown_command:${String(data.command.type)}`);
    } catch (error) {
      send(data.token, false, String(error && error.message ? error.message : error));
    }
  });
})();
