  const questObjectiveProgress = (objective) => {
    const text = safeString(objective, 2000);
    const ratioPatterns = [
      /(?:убито|уничтожено|побеждено|поймано|собрано|найдено|выполнено)?\s*:?[\s(]*(\d+)\s*(?:\/|из)\s*(\d+)/i,
      /(\d+)\s*\/\s*(\d+)/,
    ];
    for (const pattern of ratioPatterns) {
      const match = text.match(pattern);
      if (!match) continue;
      const current = parseInt(match[1], 10);
      const required = parseInt(match[2], 10);
      if (!Number.isFinite(current) || !Number.isFinite(required) || required <= 0 || current < 0) continue;
      return {
        current: Math.min(current, required),
        required,
        complete: current >= required,
        evidence: "objective_ratio",
      };
    }
    const requiredMatch = text.match(/(?:уби(?:ть|йте)|уничтож(?:ить|ьте)|побед(?:ить|ите)|одол(?:еть|ейте)|пойма(?:ть|йте)|собра(?:ть|йте))\s+(\d+)\b/i);
    if (requiredMatch) {
      const required = parseInt(requiredMatch[1], 10);
      if (Number.isFinite(required) && required > 0) {
        return { current: null, required, complete: false, evidence: "objective_required_count" };
      }
    }
    return { current: null, required: null, complete: false, evidence: null };
  };

  const questSectionText = (text, label, nextLabels = []) => {
    const value = safeString(text, 5000);
    const folded = value.toLowerCase();
    const marker = String(label || "").toLowerCase();
    const startIndex = folded.indexOf(marker);
    if (startIndex < 0) return "";
    const contentStart = startIndex + marker.length;
    let contentEnd = value.length;
    for (const nextLabel of nextLabels) {
      const nextIndex = folded.indexOf(String(nextLabel || "").toLowerCase(), contentStart);
      if (nextIndex >= 0 && nextIndex < contentEnd) contentEnd = nextIndex;
    }
    return safeString(value.slice(contentStart, contentEnd), 2000);
  };

  const questSnapshot = (metadata = {}) => {
    const context = mainContentContext();
    const loaded = context.pageKind === "quests" || /Текущая цель:|Взятые\s+Повторяющиеся\s+Доступные/i.test(context.text);
    if (!loaded || !context.doc) {
      return {
        status: "not_loaded",
        reason: "quest_page_not_loaded",
        source: { framePath: "main", href: context.href },
        data: {
          loadStatus: "not_loaded",
          snapshotId: metadata.snapshotId || null,
          generatedAt: metadata.generatedAt || null,
          pageKind: context.pageKind,
          href: context.href,
          items: [],
        },
      };
    }
    const items = [];
    const seen = new Set();
    const modeMatch = context.href.match(/[?&]mode=([^&#]+)/i);
    const mode = safeString(modeMatch && modeMatch[1], 24).toLowerCase() || "started";
    const appendQuest = (container, status, questId = "") => {
      if (!container) return;
      const titleElement = container.querySelector ? container.querySelector(".npc-point__title") : null;
      const rawText = safeString(container.innerText || container.textContent, 5000);
      const title = safeString(titleElement && (titleElement.innerText || titleElement.textContent), 220) ||
        safeString(rawText.split(status === "active" ? "Отказаться" : "Награда:")[0], 220);
      const stableId = questId || (title ? `available:${title.toLowerCase()}` : "");
      const key = stableId || `${status}:${items.length}`;
      if (seen.has(key)) return;
      seen.add(key);
      const routeElements = container.querySelectorAll ? questNavigatorLinks(container).slice(0, 30) : [];
      const navigation = routeElements.map((element) => ({
        text: questNavigatorLabel(element),
        title: safeString(attr(element, "title"), 180),
        onclick: safeString(attr(element, "onclick"), 300),
      }));
      const routeLabels = new Set(navigation.map((entry) => safeString(entry.text, 180).toLowerCase()).filter(Boolean));
      const giverNames = container.querySelectorAll
        ? Array.from(container.querySelectorAll("a[href*='/info/library/'],a[href*='info/library/']"))
            .map((element) => safeString(element.innerText || element.textContent, 180))
            .filter((label) => label && !routeLabels.has(label.toLowerCase()))
            .filter((label, index, all) => all.indexOf(label) === index)
            .slice(0, 10)
        : [];
      const objectiveMarker = "Текущая цель:";
      const rewardMarker = "Награда:";
      const locationMarker = "Местоположение:";
      const objectiveIndex = rawText.indexOf(objectiveMarker);
      const rewardIndex = rawText.indexOf(rewardMarker);
      const locationIndex = rawText.indexOf(locationMarker);
      const objective = objectiveIndex >= 0
        ? safeString(rawText.slice(objectiveIndex + objectiveMarker.length, rewardIndex > objectiveIndex ? rewardIndex : undefined), 1600) || null
        : null;
      const reward = rewardIndex >= 0
        ? safeString(rawText.slice(rewardIndex + rewardMarker.length, locationIndex > rewardIndex ? locationIndex : undefined), 600) || null
        : null;
      const locationText = locationIndex >= 0
        ? safeString(rawText.slice(locationIndex + locationMarker.length), 600) || null
        : null;
      const objectiveKind = /(?:уби(?:ть|йте)|уничтож|побед|одол|сраз|атак)/i.test(objective || "")
        ? "combat"
        : "unknown";
      items.push({
        id: stableId || null,
        title: title || null,
        status,
        sourceText: safeString(rawText, 800) || null,
        objectiveKind,
        objective,
        progress: questObjectiveProgress(objective || ""),
        description: status === "available" ? safeString(rawText.split("Награда:")[0], 1600) || null : null,
        reward: safeString(reward, 600) || null,
        locationText: safeString(locationText, 600) || null,
        giverNames,
        navigation,
      });
    };
    try {
      const cancelLinks = Array.from(context.doc.querySelectorAll("a[href*='action=cancel'][href*='ref=']")).slice(0, 100);
      for (const link of cancelLinks) {
        const href = safeString(attr(link, "href"), 240);
        const idMatch = href.match(/[?&]ref=(\d+)/i);
        const questId = safeString(idMatch && idMatch[1], 40);
        const container = (
          link.closest && (
            link.closest(".npc-point") ||
            link.closest(".npc-point__cont") ||
            link.closest("table") ||
            link.closest("tr")
          )
        ) || link.parentElement;
        appendQuest(container, "active", questId);
      }
      if (mode === "avail") {
        const cards = Array.from(context.doc.querySelectorAll(".npc-point")).slice(0, 100);
        for (const card of cards) appendQuest(card, "available");
      }
    } catch (_) {}
    const pageNumbers = [];
    try {
      for (const link of Array.from(context.doc.querySelectorAll("a[href*='user_quest.php'][href*='page=']")).slice(0, 100)) {
        const href = safeString(attr(link, "href"), 240);
        const match = href.match(/[?&]page=(\d+)/i);
        const page = match ? parseInt(match[1], 10) : NaN;
        if (Number.isFinite(page) && !pageNumbers.includes(page)) pageNumbers.push(page);
      }
    } catch (_) {}
    const currentPageMatch = context.href.match(/[?&]page=(\d+)/i);
    const currentPage = currentPageMatch ? parseInt(currentPageMatch[1], 10) : 0;
    const pageCount = pageNumbers.length ? Math.max(...pageNumbers) + 1 : 1;
    return {
      status: "available",
      reason: null,
      source: { framePath: "main", href: context.href },
      data: {
        loadStatus: "loaded",
        snapshotId: metadata.snapshotId || null,
        generatedAt: metadata.generatedAt || null,
        pageKind: context.pageKind,
        href: context.href,
        mode,
        currentPage,
        pageCount,
        hasNextPage: currentPage + 1 < pageCount,
        currentLocation: null,
        activeCount: items.filter((item) => item.status === "active").length,
        availableCount: items.filter((item) => item.status === "available").length,
        items,
        truncated: items.length >= 100,
      },
    };
  };

  const shopInventorySnapshot = () => {
    const context = mainContentContext();
    const mode = context.pageKind === "inventory" ? "inventory" : context.pageKind === "shop" ? "shop" : null;
    if (!mode) {
      return { status: "not_loaded", reason: "inventory_or_shop_not_loaded", source: { framePath: "main", href: context.href }, data: { mode: null, complete: false, items: [] } };
    }
    const items = [];
    const seen = new Set();
    if (mode === "inventory") {
      for (const entry of collectInventoryElements()) {
        const item = entry.item;
        if (!item || isControlQuickSlotItem(item) || !isInventoryArtifactCandidate(item)) continue;
        const key = item.artikulId || item.cellAid || item.id || `${item.src}:${item.title}`;
        if (!key || seen.has(key)) continue;
        seen.add(key);
        items.push({
          artikulId: item.artikulId || null,
          id: item.id || null,
          title: item.title || null,
          count: item.count || 1,
          src: item.src || null,
        });
        if (items.length >= 100) break;
      }
    }
    return {
      status: mode === "inventory" ? "available" : "partial",
      reason: mode === "shop" ? "shop_price_parser_not_configured" : null,
      source: { framePath: "main", href: context.href },
      data: { mode, complete: mode === "inventory", items, truncated: items.length >= 100 },
    };
  };

  const stateSnapshot = (payload) => {
    const generatedAt = new Date().toISOString();
    stateSnapshotSequence += 1;
    const snapshotId = `${Date.now().toString(36)}-${stateSnapshotSequence.toString(36)}`;
    const allowed = new Set(["player", "location", "deathRevive", "battle", "hunt", "quests", "shopInventory"]);
    const includeProvided = Array.isArray(payload && payload.include);
    const requested = includeProvided
      ? payload.include.map((value) => safeString(value, 40)).filter((value) => allowed.has(value))
      : [];
    const included = includeProvided ? Array.from(new Set(requested)) : Array.from(allowed);
    const sections = {};
    for (const name of included) {
      if (name === "player") sections.player = playerSnapshot();
      else if (name === "location") sections.location = locationSnapshot();
      else if (name === "deathRevive") sections.deathRevive = deathReviveSnapshot();
      else if (name === "battle") sections.battle = { status: "available", reason: null, data: battleSnapshot() };
      else if (name === "hunt") sections.hunt = { status: "available", reason: null, data: huntSnapshot(true) };
      else if (name === "quests") sections.quests = questSnapshot({ snapshotId, generatedAt });
      else if (name === "shopInventory") sections.shopInventory = shopInventorySnapshot();
    }
    return {
      ok: true,
      schemaVersion: 1,
      bridgeVersion: BRIDGE_VERSION,
      snapshotId,
      generatedAt,
      included,
      sections,
    };
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
        openHunt(data.command.payload || {})
          .then((result) => send(data.token, Boolean(result.ok), result))
          .catch((error) => send(data.token, false, `open_hunt_error:${safeString(error && error.message ? error.message : error, 200)}`));
        return;
      }
      if (data.command.type === "open_quests") {
        openQuests(data.command.payload || {})
          .then((result) => send(data.token, Boolean(result.ok), result))
          .catch((error) => send(data.token, false, `open_quests_error:${safeString(error && error.message ? error.message : error, 200)}`));
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
      if (data.command.type === "state_snapshot") {
        const result = stateSnapshot(data.command.payload || {});
        send(data.token, Boolean(result.ok), result);
        return;
      }
      if (data.command.type === "location_route_snapshot") {
        const result = locationRouteSnapshot();
        send(data.token, Boolean(result.ok), result);
        return;
      }
      if (data.command.type === "location_route_step") {
        locationRouteStep(data.command.payload || {})
          .then((result) => send(data.token, Boolean(result.ok), result))
          .catch((error) => send(data.token, false, `location_route_step_error:${safeString(error && error.message ? error.message : error, 200)}`));
        return;
      }
      if (data.command.type === "revive_free") {
        reviveFree(data.command.payload || {})
          .then((result) => send(data.token, Boolean(result.ok), result))
          .catch((error) => send(data.token, false, `revive_free_error:${safeString(error && error.message ? error.message : error, 200)}`));
        return;
      }
      if (data.command.type === "close_resurrection_notice") {
        closeResurrectionNotice(data.command.payload || {})
          .then((result) => send(data.token, Boolean(result.ok), result))
          .catch((error) => send(data.token, false, `close_resurrection_notice_error:${safeString(error && error.message ? error.message : error, 200)}`));
        return;
      }
      if (data.command.type === "navigator_snapshot") {
        const result = navigatorSnapshot();
        send(data.token, Boolean(result.ok), result);
        return;
      }
      if (data.command.type === "navigator_go") {
        const result = navigatorGo(data.command.payload || {});
        send(data.token, Boolean(result.ok), result);
        return;
      }
      if (data.command.type === "navigator_select_target") {
        navigatorSelectTarget(data.command.payload || {})
          .then((result) => send(data.token, Boolean(result.ok), result))
          .catch((error) => send(data.token, false, `navigator_select_target_error:${safeString(error && error.message ? error.message : error, 200)}`));
        return;
      }
      if (data.command.type === "open_location_navigator") {
        const result = openLocationNavigator();
        send(data.token, Boolean(result.ok), result);
        return;
      }
      if (data.command.type === "open_area") {
        openArea()
          .then((result) => send(data.token, Boolean(result.ok), result))
          .catch((error) => send(data.token, false, `open_area_error:${safeString(error && error.message ? error.message : error, 200)}`));
        return;
      }
      if (data.command.type === "open_quest_navigator") {
        const result = openQuestNavigator(data.command.payload || {});
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
        useSkillSlot(data.command.payload || {})
          .then((result) => send(data.token, Boolean(result.ok), result))
          .catch((error) => send(data.token, false, `use_skill_error:${safeString(error && error.message ? error.message : error, 200)}`));
        return;
      }
      if (data.command.type === "use_battle_item") {
        useBattleItem(data.command.payload || {})
          .then((result) => send(data.token, Boolean(result.ok), result))
          .catch((error) => send(data.token, false, `use_battle_item_error:${safeString(error && error.message ? error.message : error, 200)}`));
        return;
      }
      if (data.command.type === "attack_visible_bot") {
        attackVisibleBot(data.command.payload || {})
          .then((result) => send(data.token, Boolean(result.ok), result))
          .catch((error) => send(data.token, false, `attack_visible_error:${safeString(error && error.message ? error.message : error, 200)}`));
        return;
      }
      if (data.command.type === "attack_bot") {
        attackBot(data.command.payload || {})
          .then((result) => send(data.token, Boolean(result.ok), result))
          .catch((error) => send(data.token, false, `attack_bot_error:${safeString(error && error.message ? error.message : error, 200)}`));
        return;
      }
      send(data.token, false, `unknown_command:${String(data.command.type)}`);
    } catch (error) {
      send(data.token, false, String(error && error.message ? error.message : error));
    }
  });
