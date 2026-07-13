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

  const normalizeBotIdList = (value) => {
    const raw = Array.isArray(value) ? value : value == null ? [] : [value];
    const botIds = [];
    for (const item of raw) {
      const parts = typeof item === "string" ? item.split(/[,\s]+/) : [item];
      for (const part of parts) {
        const botId = Number(part);
        if (Number.isInteger(botId) && botId > 0 && !botIds.includes(botId)) {
          botIds.push(botId);
        }
      }
    }
    return botIds.sort((left, right) => left - right);
  };

  const botIdFilterFromPayload = (payload) => {
    const raw = [];
    for (const key of ["allowedBotIds", "allowed_bot_ids", "botIds", "botId"]) {
      const value = payload && payload[key];
      if (Array.isArray(value)) raw.push(...value);
      else if (value != null) raw.push(value);
    }
    return normalizeBotIdList(raw);
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

  const normalizedNameTokens = (value) =>
    safeString(value, 180)
      .toLowerCase()
      .replace(/ё/g, "е")
      .replace(/\[\d{1,3}\]/g, " ")
      .replace(/[^a-zа-я0-9]+/gi, " ")
      .trim()
      .split(/\s+/)
      .filter((token) => token.length >= 4);

  const huntNameMatches = (targetName, allowedName) => {
    const targetTokens = normalizedNameTokens(targetName);
    const allowedTokens = normalizedNameTokens(allowedName);
    if (!targetTokens.length || !allowedTokens.length) return false;
    const targetText = targetTokens.join(" ");
    const allowedText = allowedTokens.join(" ");
    if (targetText.includes(allowedText) || allowedText.includes(targetText)) return true;
    return allowedTokens.every((allowedToken) => {
      const stem = allowedToken.slice(0, Math.min(6, allowedToken.length));
      return targetTokens.some((targetToken) => targetToken.startsWith(stem));
    });
  };

  const exactLabelMatches = (left, right) => {
    const normalize = (value) =>
      safeString(value, 240)
        .toLowerCase()
        .replace(/ё/g, "е")
        .replace(/[^a-zа-я0-9]+/gi, " ")
        .trim()
        .replace(/\s+/g, " ");
    const leftValue = normalize(left);
    const rightValue = normalize(right);
    return Boolean(leftValue && rightValue && leftValue === rightValue);
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
    const allowedBotIds = botIdFilterFromPayload(payload);
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
      .filter((bot) => !allowedBotIds.length || allowedBotIds.includes(bot.botId))
      .filter(
        (bot) =>
          !allowedNames.length ||
          allowedNames.some((name) => huntNameMatches(bot.name, name) || huntNameMatches(bot.shortName, name))
      )
      .filter((bot) => !allowedLevels.length || allowedLevels.includes(Number(bot.level)))
      .map((bot) => {
        const visible =
          bot.x >= bounds.x - margin &&
          bot.x <= bounds.x + bounds.w + margin &&
          bot.y >= bounds.y - margin &&
          bot.y <= bounds.y + bounds.h + margin;
        return {
          ...bot,
          targetPriority: allowedNames.length
            ? allowedNames.findIndex((name) => huntNameMatches(bot.name, name) || huntNameMatches(bot.shortName, name))
            : 0,
          visible,
          screenX: layer.contentX + (bot.x - bounds.x),
          screenY: layer.contentY + (bot.y - bounds.y),
          distanceToViewportCenter: Math.hypot(bot.x - centerX, bot.y - centerY),
        };
      })
      .filter((bot) => bot.visible)
      .sort((left, right) =>
        left.targetPriority - right.targetPriority ||
        left.distanceToViewportCenter - right.distanceToViewportCenter ||
        left.botId - right.botId
      );
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
      allowedBotIds,
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

  const attackVisibleBot = async (payload) => {
    const requestedNames = Array.isArray(payload && payload.names)
      ? payload.names.map((name) => safeString(name, 120)).filter(Boolean)
      : [];
    const requestedLevels = normalizeLevelList(
      payload && (payload.allowedLevels || payload.allowed_levels || payload.levels || payload.targetLevels)
    );
    const requestedBotIds = botIdFilterFromPayload(payload);
    if (!requestedNames.length && !requestedLevels.length && !requestedBotIds.length) {
      return { ok: false, message: "target_filter_required" };
    }
    const visible = visibleHuntTargets(payload || {});
    const target = visible.targets[0] || null;
    const visibleSummary = {
      bridgeVersion: visible.bridgeVersion,
      generatedAt: visible.generatedAt,
      mainHref: visible.mainHref,
      hasHunt: visible.hasHunt,
      viewBounds: visible.viewBounds,
      allowedBotIds: visible.allowedBotIds,
      allowedLevels: visible.allowedLevels,
      targetCount: visible.targets.length,
      targets: visible.targets.slice(0, 5),
    };
    if (!target) {
      return { ok: false, message: "visible_bot_missing", visible: visibleSummary };
    }
    const result = await attackBot({
      bot_id: target.botId,
      confirmed: payload && payload.confirmed == null ? 1 : payload.confirmed,
      verifyTimeoutMs: payload && payload.verifyTimeoutMs,
    });
    return {
      ok: Boolean(result.ok),
      message: result.message,
      target,
      visible: visibleSummary,
    };
  };

  const attackBot = async (payload) => {
    const root = window.top || window;
    const botId = parseInt(payload && (payload.bot_id || payload.botId), 10);
    const confirmed = parseInt(payload && payload.confirmed, 10) || 0;
    if (!botId) {
      return { ok: false, message: "missing_bot_id" };
    }
    let method = "";
    try {
      if (typeof root.huntAttack === "function") {
        root.huntAttack(botId, confirmed);
        method = "huntAttack";
      }
    } catch (error) {
      return { ok: false, message: `huntAttack_error:${safeString(error && error.message ? error.message : error, 200)}` };
    }
    try {
      if (!method && typeof root.botAttack === "function") {
        root.botAttack(botId, "/hunt.php", `&in[hunt]=1&in[confirmed]=${confirmed || 0}`, confirmed ? null : root.gebi && root.gebi("error"));
        method = "botAttack";
      }
    } catch (error) {
      return { ok: false, message: `botAttack_error:${safeString(error && error.message ? error.message : error, 200)}` };
    }
    if (!method) return { ok: false, message: "attack_function_missing" };
    const verifyTimeoutMs = Math.max(500, Math.min(6000, parseInt(payload && payload.verifyTimeoutMs, 10) || 3500));
    const deadline = Date.now() + verifyTimeoutMs;
    let battle = battleSnapshot();
    let botInfo = huntBotInfo({ bot_id: botId });
    const confirmedAttack = () =>
      Boolean(battle.hasFight) ||
      Boolean(botInfo && botInfo.ok && botInfo.bot && Number(botInfo.bot.fightId) > 0);
    while (!confirmedAttack() && Date.now() < deadline) {
      await delayMs(100);
      battle = battleSnapshot();
      botInfo = huntBotInfo({ bot_id: botId });
    }
    const actionConfirmed = confirmedAttack();
    return {
      ok: actionConfirmed,
      message: actionConfirmed ? `${method}_confirmed` : `${method}_unconfirmed`,
      method,
      botId,
      confirmed,
      verifyTimeoutMs,
      battle,
      bot: botInfo && botInfo.bot ? botInfo.bot : null,
    };
  };

  const huntNavigationSnapshot = () => {
    const root = window.top || window;
    let mainHref = "";
    try {
      mainHref = safeString(root.frames["main_frame"].frames["main"].location.href, 240);
    } catch (_) {}
    return { mainHref, hasHunt: Boolean(findHuntApp()) };
  };

  const openHunt = async (payload = {}) => {
    const root = window.top || window;
    const before = huntNavigationSnapshot();
    if (before.hasHunt || /\/hunt\.php(?:\?|$)/.test(before.mainHref)) {
      return { ok: true, message: "already_hunt", before, after: before };
    }
    try {
      if (/\/hunt\.php(?:\?|$)/.test(root.location.href)) {
        return { ok: true, message: "already_top_hunt", before, after: before };
      }
    } catch (_) {}

    let method = "";
    try {
      const mainFrame = root.frames && root.frames["main_frame"];
      if (mainFrame) {
        if (typeof mainFrame.processMenu === "function") {
          mainFrame.processMenu("b07");
          method = "processMenu_b07";
        } else if (typeof mainFrame.openHunt === "function") {
          mainFrame.openHunt();
          method = "openHunt";
        } else if (mainFrame.frames && mainFrame.frames["main"]) {
          mainFrame.frames["main"].location.href = "hunt.php?update_swf=1";
          method = "main_frame_main_hunt";
        }
      }
    } catch (error) {
      return { ok: false, message: `frame_error:${String(error && error.message ? error.message : error)}`, before };
    }

    if (!method) {
      return { ok: false, message: "open_hunt_control_missing", before };
    }
    const verifyTimeoutMs = Math.max(250, Math.min(5000, parseInt(payload && payload.verifyTimeoutMs, 10) || 2000));
    const deadline = Date.now() + verifyTimeoutMs;
    let after = huntNavigationSnapshot();
    while (!after.hasHunt && !/\/hunt\.php(?:\?|$)/.test(after.mainHref) && Date.now() < deadline) {
      await delayMs(100);
      after = huntNavigationSnapshot();
    }
    const verified = after.hasHunt || /\/hunt\.php(?:\?|$)/.test(after.mainHref);
    return {
      ok: verified,
      message: verified ? `${method}_confirmed` : `${method}_unconfirmed`,
      method,
      before,
      after,
      verifyTimeoutMs,
    };
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

  const openQuests = async (payload = {}) => {
    const root = window.top || window;
    const before = mainContentContext();
    if (before.pageKind === "quests") {
      return { ok: true, message: "already_quests", before: { pageKind: before.pageKind, href: before.href } };
    }
    let clicked = null;
    walkWindows(root, "top", 5, new Set(), (win, path) => {
      if (clicked) return;
      try {
        const elements = Array.from(win.document.querySelectorAll("a,button,[onclick]")).slice(0, 1200);
        for (const element of elements) {
          const text = safeString(elementText(element), 120).toLowerCase();
          if (text !== "квесты") continue;
          const clickable = clickableElement(element);
          if (!clickable || typeof clickable.click !== "function") continue;
          clickable.click();
          clicked = {
            path,
            text: safeString(elementText(element), 120),
            href: safeString(attr(element, "href"), 200),
            onclick: safeString(attr(element, "onclick"), 240),
          };
          break;
        }
      } catch (_) {}
    });
    if (!clicked) {
      try {
        const mainWin = findMainContentWindow(root);
        if (mainWin && mainWin.location) {
          mainWin.location.href = "user_quest.php?mode=started";
          clicked = {
            path: "main",
            text: "",
            href: "user_quest.php?mode=started",
            onclick: "",
            method: "main_frame_direct",
          };
        }
      } catch (_) {}
    }
    if (!clicked) return { ok: false, message: "quests_control_missing" };
    const verifyTimeoutMs = Math.max(250, Math.min(5000, parseInt(payload && payload.verifyTimeoutMs, 10) || 2000));
    const deadline = Date.now() + verifyTimeoutMs;
    let after = mainContentContext();
    while (after.pageKind !== "quests" && Date.now() < deadline) {
      await delayMs(100);
      after = mainContentContext();
    }
    const confirmed = after.pageKind === "quests";
    return {
      ok: confirmed,
      message: confirmed ? "quests_opened_confirmed" : "quests_open_unconfirmed",
      control: clicked,
      before: { pageKind: before.pageKind, href: before.href },
      after: { pageKind: after.pageKind, href: after.href },
      verifyTimeoutMs,
    };
  };

  const openQuestCatalog = async (payload = {}) => {
    const rawPage = payload && payload.page;
    if (typeof rawPage !== "number" || !Number.isInteger(rawPage) || rawPage < 0 || rawPage > 100) {
      return { ok: false, message: "quest_catalog_page_invalid", page: rawPage == null ? null : safeString(rawPage, 40) };
    }
    const page = rawPage;
    const root = window.top || window;
    const before = mainContentContext();
    const beforeMode = safeString((before.href.match(/[?&]mode=([^&#]+)/i) || [])[1], 24).toLowerCase();
    const beforePageMatch = before.href.match(/[?&]page=(\d+)/i);
    const beforePage = beforePageMatch ? parseInt(beforePageMatch[1], 10) : 0;
    if (before.pageKind === "quests" && beforeMode === "avail" && beforePage === page) {
      return {
        ok: true,
        message: "quest_catalog_already_open",
        page,
        before: { pageKind: before.pageKind, href: before.href },
        after: { pageKind: before.pageKind, href: before.href, mode: beforeMode, page: beforePage },
      };
    }
    const destination = `/user_quest.php?mode=avail&page=${page}`;
    let method = null;
    try {
      const mainWin = findMainContentWindow(root);
      if (mainWin && mainWin.location) {
        mainWin.location.href = destination;
        method = "main_frame_direct";
      }
    } catch (_) {}
    if (!method) return { ok: false, message: "quest_catalog_main_content_missing", page };
    const verifyTimeoutMs = Math.max(250, Math.min(5000, Number(payload && payload.verifyTimeoutMs) || 2000));
    const deadline = Date.now() + verifyTimeoutMs;
    let after = mainContentContext();
    let afterMode = safeString((after.href.match(/[?&]mode=([^&#]+)/i) || [])[1], 24).toLowerCase();
    let afterPageMatch = after.href.match(/[?&]page=(\d+)/i);
    let afterPage = afterPageMatch ? parseInt(afterPageMatch[1], 10) : 0;
    let shellLoaded = /Взятые[\s\S]*Повторяющиеся[\s\S]*Доступные[\s\S]*Завершенные/i.test(safeString(after.text, 20000));
    while ((after.pageKind !== "quests" || afterMode !== "avail" || afterPage !== page || !shellLoaded) && Date.now() < deadline) {
      await delayMs(100);
      after = mainContentContext();
      afterMode = safeString((after.href.match(/[?&]mode=([^&#]+)/i) || [])[1], 24).toLowerCase();
      afterPageMatch = after.href.match(/[?&]page=(\d+)/i);
      afterPage = afterPageMatch ? parseInt(afterPageMatch[1], 10) : 0;
      shellLoaded = /Взятые[\s\S]*Повторяющиеся[\s\S]*Доступные[\s\S]*Завершенные/i.test(safeString(after.text, 20000));
    }
    const confirmed = after.pageKind === "quests" && afterMode === "avail" && afterPage === page && shellLoaded;
    return {
      ok: confirmed,
      message: confirmed ? "quest_catalog_opened_confirmed" : "quest_catalog_open_unconfirmed",
      page,
      destination,
      method,
      before: { pageKind: before.pageKind, href: before.href },
      after: { pageKind: after.pageKind, href: after.href, mode: afterMode, page: afterPage },
      verifyTimeoutMs,
    };
  };

  const openActiveQuestPage = async (payload = {}) => {
    const rawPage = payload && payload.page;
    if (typeof rawPage !== "number" || !Number.isInteger(rawPage) || rawPage < 0 || rawPage > 100) {
      return { ok: false, message: "quest_active_page_invalid", page: rawPage == null ? null : safeString(rawPage, 40) };
    }
    const page = rawPage;
    const before = mainContentContext();
    const beforeMode = safeString((before.href.match(/[?&]mode=([^&#]+)/i) || [])[1], 24).toLowerCase();
    const beforePageMatch = before.href.match(/[?&]page=(\d+)/i);
    const beforePage = beforePageMatch ? parseInt(beforePageMatch[1], 10) : 0;
    if (before.pageKind === "quests" && beforeMode === "started" && beforePage === page) {
      return {
        ok: true,
        message: "quest_active_already_open",
        page,
        before: { pageKind: before.pageKind, href: before.href },
        after: { pageKind: before.pageKind, href: before.href, mode: beforeMode, page: beforePage },
      };
    }
    const destination = `/user_quest.php?mode=started&page=${page}`;
    let method = null;
    try {
      const mainWin = findMainContentWindow(window.top || window);
      if (mainWin && mainWin.location) {
        mainWin.location.href = destination;
        method = "main_frame_direct";
      }
    } catch (_) {}
    if (!method) return { ok: false, message: "quest_active_main_content_missing", page };
    const verifyTimeoutMs = Math.max(250, Math.min(5000, Number(payload && payload.verifyTimeoutMs) || 2000));
    const deadline = Date.now() + verifyTimeoutMs;
    let after = mainContentContext();
    let afterMode = safeString((after.href.match(/[?&]mode=([^&#]+)/i) || [])[1], 24).toLowerCase();
    let afterPageMatch = after.href.match(/[?&]page=(\d+)/i);
    let afterPage = afterPageMatch ? parseInt(afterPageMatch[1], 10) : 0;
    let shellLoaded = /Взятые[\s\S]*Повторяющиеся[\s\S]*Доступные[\s\S]*Завершенные/i.test(safeString(after.text, 20000));
    while ((after.pageKind !== "quests" || afterMode !== "started" || afterPage !== page || !shellLoaded) && Date.now() < deadline) {
      await delayMs(100);
      after = mainContentContext();
      afterMode = safeString((after.href.match(/[?&]mode=([^&#]+)/i) || [])[1], 24).toLowerCase();
      afterPageMatch = after.href.match(/[?&]page=(\d+)/i);
      afterPage = afterPageMatch ? parseInt(afterPageMatch[1], 10) : 0;
      shellLoaded = /Взятые[\s\S]*Повторяющиеся[\s\S]*Доступные[\s\S]*Завершенные/i.test(safeString(after.text, 20000));
    }
    const confirmed = after.pageKind === "quests" && afterMode === "started" && afterPage === page && shellLoaded;
    return {
      ok: confirmed,
      message: confirmed ? "quest_active_opened_confirmed" : "quest_active_open_unconfirmed",
      page,
      destination,
      method,
      before: { pageKind: before.pageKind, href: before.href },
      after: { pageKind: after.pageKind, href: after.href, mode: afterMode, page: afterPage },
      verifyTimeoutMs,
    };
  };
