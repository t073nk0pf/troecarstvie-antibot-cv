  const pageKindFromHref = (href) => {
    const value = safeString(href, 240).toLowerCase();
    if (/\/fight\.php(?:\?|$)/.test(value)) return "battle";
    if (/\/hunt\.php(?:\?|$)/.test(value)) return "hunt";
    if (/\/npc\.php(?:\?|$)/.test(value)) return "npc";
    if (/user_quest\.php/.test(value)) return "quests";
    if (/user\.php/.test(value) && /(?:backpack|inventory)/.test(value)) return "inventory";
    if (/(?:market|shop|trade|auction|merchant)/.test(value)) return "shop";
    if (/action_form\.php/.test(value)) return "action_form";
    if (/area\.php/.test(value)) return "area";
    if (/main\.php/.test(value)) return "main";
    return value ? "other" : "unknown";
  };

  const mainContentContext = () => {
    const root = window.top || window;
    const win = findMainContentWindow(root);
    let doc = null;
    let href = "";
    let title = "";
    let text = "";
    try {
      doc = win && win.document ? win.document : null;
      href = safeString(win && win.location && win.location.href, 240);
      title = safeString(doc && doc.title, 160);
      text = safeString(doc && doc.body && (doc.body.innerText || doc.body.textContent), 12000);
    } catch (_) {}
    return { root, win, doc, href, title, text, pageKind: pageKindFromHref(href) };
  };

  const elementIsVisible = (win, element) => {
    if (!element) return false;
    try {
      const style = win && typeof win.getComputedStyle === "function" ? win.getComputedStyle(element) : null;
      if (style && (style.display === "none" || style.visibility === "hidden")) return false;
      if (typeof element.getClientRects === "function") {
        const rects = Array.from(element.getClientRects() || []);
        return rects.some((rect) => Number(rect.width) > 0 || Number(rect.height) > 0);
      }
      if (Number(element.offsetWidth) > 0 || Number(element.offsetHeight) > 0) return true;
      return !style || style.display !== "none";
    } catch (_) {
      return false;
    }
  };

  const isQuestNavigatorLink = (element) => {
    if (!element) return false;
    const marker = [
      attr(element, "title"),
      attr(element, "aria-label"),
      elementText(element),
      ...Array.from(element.querySelectorAll ? element.querySelectorAll("img") : []).flatMap((image) => [
        attr(image, "alt"),
        attr(image, "title"),
      ]),
    ].join(" ");
    return /проложить\s+путь/i.test(marker);
  };

  const questNavigatorLinks = (doc) => {
    if (!doc || typeof doc.querySelectorAll !== "function") return [];
    return Array.from(doc.querySelectorAll("a")).filter(isQuestNavigatorLink).slice(0, 100);
  };

  const questNavigatorLabel = (element) => {
    const text = safeString(element && element.textContent, 180).replace(/проложить\s+путь/ig, "").trim();
    if (text) return text;
    return safeString(attr(element, "title"), 180).replace(/проложить\s+путь/ig, "").trim();
  };

  const navigatorSnapshot = () => {
    const generatedAt = new Date().toISOString();
    stateSnapshotSequence += 1;
    const snapshotId = `navigator-${Date.now().toString(36)}-${stateSnapshotSequence.toString(36)}`;
    const context = mainContentContext();
    if (!/\/navigator\.php(?:\?|$)/i.test(context.href) || !context.doc) {
      return { ok: false, message: "navigator_page_missing", href: context.href, snapshotId, generatedAt };
    }
    const inputs = Array.from(context.doc.querySelectorAll("input,button")).slice(0, 100);
    const targetInput = inputs.find((element) => safeString(attr(element, "name"), 80) === "compassInput") || null;
    const goButtons = inputs.filter((element) =>
      /^(?:дойти|проложить\s+маршрут)$/i.test(
        safeString(element.value || elementText(element), 80)
      )
    );
    const visibleGoButtons = goButtons.filter((element) => elementIsVisible(context.win, element));
    const currentLocation = /объект\s+находится\s+в\s+текущей\s+локации/i.test(context.text);
    const routeMatch = context.text.match(/путь\s+займет\s+(\d+)\s+переход/i);
    return {
      ok: true,
      message: "navigator_snapshot",
      snapshotId,
      generatedAt,
      href: context.href,
      target: safeString(targetInput && targetInput.value, 180) || null,
      currentLocation,
      hasRoute: visibleGoButtons.length === 1,
      routeTransitions: routeMatch ? Number(routeMatch[1]) : null,
      goButtonCount: goButtons.length,
      visibleGoButtonCount: visibleGoButtons.length,
      text: safeString(context.text, 1200),
    };
  };

  const navigatorGo = (payload) => {
    const before = navigatorSnapshot();
    if (!before.ok) return before;
    const expectedTarget = safeString(payload && payload.expectedTarget, 180);
    if (expectedTarget && before.target && !exactLabelMatches(before.target, expectedTarget)) {
      return { ok: false, message: "navigator_target_mismatch", expectedTarget, observedTarget: before.target };
    }
    if (before.currentLocation) {
      return { ok: true, message: "navigator_already_at_target", submitted: false, before };
    }
    if (!before.hasRoute || before.visibleGoButtonCount !== 1) {
      return { ok: false, message: "navigator_route_not_ready", before };
    }
    const context = mainContentContext();
    const button = Array.from(context.doc.querySelectorAll("input,button")).find(
      (element) =>
        /^(?:дойти|проложить\s+маршрут)$/i.test(
          safeString(element.value || elementText(element), 80)
        ) &&
        elementIsVisible(context.win, element)
    );
    if (!button || typeof button.click !== "function") {
      return { ok: false, message: "navigator_go_not_clickable", before };
    }
    const clickDelayMs = Math.max(25, Math.min(250, Number(payload && payload.clickDelayMs) || 75));
    setTimeout(() => {
      try {
        button.click();
      } catch (_) {}
    }, clickDelayMs);
    return { ok: true, message: "navigator_go_scheduled", submitted: true, clickDelayMs, before };
  };

  const navigatorSectionHeader = (element) => {
    const label = safeString(element && (element.innerText || element.textContent), 120)
      .trim()
      .toLowerCase();
    const match = label.match(/^(локации|ресурсы|монстры|персонажи|инстансы)\s*:?[\s]*$/);
    return match ? match[1] : "";
  };

  const navigatorSectionPrefix = (element) => {
    const label = safeString(element && (element.innerText || element.textContent), 500)
      .trim()
      .toLowerCase();
    const match = label.match(/^(локации|ресурсы|монстры|персонажи|инстансы)(?:\s|$)/);
    return { section: match ? match[1] : "", label };
  };

  const navigatorPrecedingSiblings = (element, limit = 120) => {
    const siblings = [];
    let sibling = element && element.previousElementSibling;
    while (sibling && siblings.length < limit) {
      siblings.push(sibling);
      sibling = sibling.previousElementSibling;
    }
    if (siblings.length || !element || !element.parentElement) return siblings;
    const children = Array.from(element.parentElement.children || []);
    const index = children.indexOf(element);
    if (index < 0) return siblings;
    return children.slice(Math.max(0, index - limit), index).reverse();
  };

  const navigatorCandidateSectionDiagnostic = (element) => {
    const inspected = [];
    let node = element;
    for (let depth = 0; node && depth < 8; depth += 1) {
      const preceding = navigatorPrecedingSiblings(node);
      const precedingLabels = [];
      for (const sibling of preceding) {
        const label = safeString(sibling && (sibling.innerText || sibling.textContent), 120);
        if (precedingLabels.length < 6) precedingLabels.push(label);
        const section = navigatorSectionHeader(sibling);
        if (section) {
          return {
            section,
            evidence: "preceding_sibling_header",
            depth,
            header: label,
            inspected,
          };
        }
      }
      inspected.push({ depth, precedingLabels });
      node = node.parentElement;
    }
    node = element && element.parentElement;
    for (let depth = 0; node && depth < 8; depth += 1) {
      const nodePrefix = navigatorSectionPrefix(node);
      if (nodePrefix.section) {
        return {
          section: nodePrefix.section,
          evidence: "ancestor_text_prefix",
          depth,
          header: safeString(nodePrefix.label, 120),
          inspected,
        };
      }
      const children = Array.from(node.children || []).slice(0, 8);
      for (const child of children) {
        const childPrefix = navigatorSectionPrefix(child);
        if (childPrefix.section) {
          return {
            section: childPrefix.section,
            evidence: "ancestor_child_prefix",
            depth,
            header: safeString(childPrefix.label, 120),
            inspected,
          };
        }
      }
      node = node.parentElement;
    }
    return { section: "", evidence: "none", depth: null, header: "", inspected };
  };

  const navigatorCandidateSection = (element) =>
    navigatorCandidateSectionDiagnostic(element).section;

  const setNavigatorInputValue = (context, input, value) => {
    try {
      if (typeof input.focus === "function") input.focus();
      input.value = value;
      const EventCtor = (context.win && context.win.Event) || (typeof Event === "function" ? Event : null);
      if (EventCtor && typeof input.dispatchEvent === "function") {
        for (const type of ["input", "keyup", "change"]) {
          input.dispatchEvent(new EventCtor(type, { bubbles: true }));
        }
      }
      return true;
    } catch (_) {
      return false;
    }
  };

  const navigatorSelectTarget = async (payload = {}) => {
    const context = mainContentContext();
    if (!/\/navigator\.php(?:\?|$)/i.test(context.href) || !context.doc) {
      return { ok: false, message: "navigator_page_missing", href: context.href };
    }
    const target = safeString(payload && payload.target, 180);
    const kind = safeString(payload && payload.kind, 40).toLowerCase() || "location";
    if (!target) return { ok: false, message: "navigator_target_missing" };
    if (!new Set(["auto", "location", "monster", "instance"]).has(kind)) {
      return { ok: false, message: "navigator_target_kind_forbidden", kind };
    }
    const expectedSection = kind === "monster"
      ? "монстры"
      : kind === "location"
        ? "локации"
        : kind === "instance"
          ? "инстансы"
          : null;
    const inputs = Array.from(context.doc.querySelectorAll("input,button")).slice(0, 100);
    const targetInputs = inputs.filter((element) => safeString(attr(element, "name"), 80) === "compassInput");
    if (targetInputs.length !== 1) {
      return { ok: false, message: "navigator_target_input_ambiguous", inputCount: targetInputs.length };
    }
    const input = targetInputs[0];
    const before = navigatorSnapshot();
    if (
      before.ok &&
      before.target &&
      exactLabelMatches(before.target, target) &&
      (before.currentLocation || before.hasRoute)
    ) {
      return { ok: true, message: "navigator_target_already_selected", selected: false, target, snapshot: before };
    }
    const inputAlreadyMatches = exactLabelMatches(safeString(input && input.value, 180), target);
    if (!inputAlreadyMatches && !setNavigatorInputValue(context, input, target)) {
      return { ok: false, message: "navigator_target_input_failed", target };
    }
    const searchTimeoutMs = Math.max(250, Math.min(15000, Number(payload && payload.searchDelayMs) || 1500));
    const searchDeadline = Date.now() + searchTimeoutMs;
    let scanContext = context;
    let contextChanges = 0;
    let exactCandidates = [];
    let allCandidates = [];
    let visibleCandidates = [];
    do {
      await delayMs(100);
      const currentContext = mainContentContext();
      if (currentContext.doc && /\/navigator\.php(?:\?|$)/i.test(currentContext.href)) {
        if (currentContext.doc !== scanContext.doc || currentContext.win !== scanContext.win) {
          contextChanges += 1;
        }
        scanContext = currentContext;
      }
      exactCandidates = Array.from(
        scanContext.doc.querySelectorAll("div,li,a,button,[role='option']")
      ).filter((element) =>
        exactLabelMatches(
          safeString(element && (element.innerText || element.textContent), 180),
          target
        )
      );
      allCandidates = exactCandidates.filter((element) => {
        const section = navigatorCandidateSection(element);
        return Boolean(section) && (!expectedSection || section === expectedSection);
      });
      visibleCandidates = allCandidates.filter((element) => elementIsVisible(scanContext.win, element));
    } while (!allCandidates.length && Date.now() < searchDeadline);
    const candidates = visibleCandidates.length === 1 ? visibleCandidates : allCandidates;
    if (candidates.length !== 1) {
      return {
        ok: false,
        message: candidates.length ? "navigator_target_ambiguous" : "navigator_target_missing_in_section",
        target,
        kind,
        expectedSection,
        exactCandidateCount: exactCandidates.length,
        exactCandidateSections: exactCandidates.map((element) => navigatorCandidateSection(element)),
        exactCandidateSectionDiagnostics: exactCandidates.map((element) =>
          navigatorCandidateSectionDiagnostic(element)
        ),
        candidateSections: allCandidates.map((element) => navigatorCandidateSection(element)),
        visibleCandidateCount: visibleCandidates.length,
        candidateCount: allCandidates.length,
        contextChanges,
        inputDispatched: !inputAlreadyMatches,
      };
    }
    const candidate = candidates[0];
    const candidateSectionDiagnostic = navigatorCandidateSectionDiagnostic(candidate);
    if (typeof candidate.click !== "function") {
      return { ok: false, message: "navigator_target_not_clickable", target, kind };
    }
    candidate.click();
    const routeTimeoutMs = Math.max(100, Math.min(8000, Number(payload && payload.routeDelayMs) || 350));
    const routeDeadline = Date.now() + routeTimeoutMs;
    let after = navigatorSnapshot();
    while (
      Date.now() < routeDeadline &&
      (!after.ok ||
        !after.target ||
        !exactLabelMatches(after.target, target) ||
        (!after.currentLocation && !after.hasRoute))
    ) {
      await delayMs(100);
      after = navigatorSnapshot();
    }
    if (!after.ok || !after.target || !exactLabelMatches(after.target, target)) {
      return { ok: false, message: "navigator_target_selection_unconfirmed", target, after };
    }
    if (!after.currentLocation && !after.hasRoute) {
      return { ok: false, message: "navigator_route_not_ready", target, after };
    }
    return {
      ok: true,
      message: "navigator_target_selected",
      selected: true,
      target,
      section: candidateSectionDiagnostic.section,
      sectionEvidence: candidateSectionDiagnostic.evidence,
      inputDispatched: !inputAlreadyMatches,
      snapshot: after,
    };
  };

  const openLocationNavigator = () => {
    const controls = [];
    walkWindows(window.top || window, "top", 3, new Set(), (win) => {
      try {
        for (const element of Array.from(win.document.querySelectorAll("a[data-command='showExternalNavigate']"))) {
          if (!controls.includes(element)) controls.push(element);
        }
      } catch (_) {}
    });
    if (controls.length !== 1) {
      return {
        ok: false,
        message: controls.length ? "location_navigator_control_ambiguous" : "location_navigator_control_missing",
        controlCount: controls.length,
      };
    }
    if (typeof controls[0].click !== "function") {
      return { ok: false, message: "location_navigator_control_not_clickable" };
    }
    controls[0].click();
    return { ok: true, message: "location_navigator_opened", opened: true };
  };

  const openArea = async () => {
    const before = mainContentContext();
    const battle = battleSnapshot();
    if (battle.rawHasFight === true || battle.hasFight === true) {
      return { ok: false, message: "open_area_blocked_by_battle", before: before.pageKind };
    }
    if (before.pageKind === "area") {
      return { ok: true, message: "area_already_open", opened: false, href: before.href };
    }
    const mainWindow = findMainContentWindow(window.top || window);
    if (!mainWindow || !mainWindow.location) {
      return { ok: false, message: "open_area_main_window_missing", before: before.pageKind };
    }
    mainWindow.location.href = "/area.php";
    await delayMs(500);
    const after = mainContentContext();
    return {
      ok: after.pageKind === "area",
      message: after.pageKind === "area" ? "area_opened" : "area_open_unconfirmed",
      opened: true,
      before: before.pageKind,
      after: after.pageKind,
      href: after.href,
    };
  };

  const openQuestNavigator = (payload) => {
    const context = mainContentContext();
    const target = safeString(payload && payload.target, 180);
    const explicitLinkLabel = safeString(payload && payload.linkLabel, 180);
    const linkLabel = explicitLinkLabel || target;
    if (context.pageKind !== "quests" || !context.doc) {
      return { ok: false, message: "quest_page_missing", pageKind: context.pageKind };
    }
    if (!target) return { ok: false, message: "quest_navigator_target_missing" };
    const candidates = questNavigatorLinks(context.doc);
    const matches = candidates.filter((element) => {
      if (!exactLabelMatches(questNavigatorLabel(element), linkLabel)) return false;
      if (!explicitLinkLabel) return true;
      const observedTarget = questNavigatorTargetFromOnclick(attr(element, "onclick"));
      return exactLabelMatches(observedTarget, target);
    });
    if (matches.length !== 1) {
      return {
        ok: false,
        message: matches.length ? "quest_navigator_link_ambiguous" : "quest_navigator_link_missing",
        target,
        linkLabel,
        matchCount: matches.length,
      };
    }
    const link = matches[0];
    if (typeof link.click !== "function") return { ok: false, message: "quest_navigator_link_not_clickable", target };
    const observedLinkLabel = questNavigatorLabel(link);
    link.click();
    return {
      ok: true,
      message: "quest_navigator_opened",
      submitted: true,
      target,
      linkLabel: observedLinkLabel,
    };
  };

  const percentFromText = (text, labels) => {
    for (const label of labels) {
      const match = safeString(text, 12000).match(new RegExp(`${label}\\s*[:–-]?\\s*(\\d+(?:[.,]\\d+)?)\\s*%`, "i"));
      if (match) {
        const value = Number(String(match[1]).replace(",", "."));
        if (Number.isFinite(value)) return value;
      }
    }
    return null;
  };

  const primitiveScriptField = (source, names) => {
    for (const name of names) {
      const pattern = new RegExp(`["']?${name}["']?\\s*[:=]\\s*(?:["']([^"']*)["']|(-?\\d+(?:\\.\\d+)?))`, "i");
      const match = safeString(source, 20000).match(pattern);
      if (!match) continue;
      if (match[2] != null) {
        const value = Number(match[2]);
        return Number.isFinite(value) ? value : null;
      }
      return safeString(match[1], 160);
    }
    return null;
  };

  const playerSnapshot = () => {
    const resources = resourceSnapshot();
    const candidates = [];
    walkWindows(window.top || window, "top", 4, new Set(), (win, path) => {
      try {
        const doc = win.document;
        if (!doc) return;
        const hp = doc.querySelector(".b-control-lvl__hp,[class*='control-lvl__hp']");
        const prowess = doc.querySelector(".b-control-lvl__mp,[class*='control-lvl__mp']");
        const control =
          doc.querySelector("#control-lvl,.b-control-lvl,[class*='b-control-lvl']") ||
          (hp && hp.parentElement) ||
          (prowess && prowess.parentElement);
        if (!control && !hp && !prowess) return;
        const text = safeString(
          [control && (control.innerText || control.textContent), hp && hp.textContent, prowess && prowess.textContent].join(" "),
          2000
        );
        let scriptSource = "";
        try {
          scriptSource = Array.from(doc.scripts || [])
            .map((script) => safeString(script && script.textContent, 5000))
            .filter((source) => /ControlLvl|swfData\s*\(\s*["']lvl/i.test(source))
            .join(" ");
        } catch (_) {}
        const compact = text.replace(/\s+/g, " ").trim();
        const headerMatch = compact.match(/(?:^|\s)(\d{1,3})\s+([^\s]+)\s+Жизнь\s*\d/i);
        const levelElement = control && control.querySelector
          ? control.querySelector("[class*='level'],[class*='__lvl'],[data-level]")
          : null;
        const nameElement = control && control.querySelector
          ? control.querySelector("[class*='nick'],[class*='name'],[data-nick]")
          : null;
        const scriptLevel = primitiveScriptField(scriptSource, ["lvl", "level"]);
        const elementLevel = safeString(levelElement && (attr(levelElement, "data-level") || levelElement.textContent), 40);
        const levelRaw = scriptLevel != null && scriptLevel !== "" ? scriptLevel : elementLevel || (headerMatch ? headerMatch[1] : null);
        const level = parseInt(levelRaw, 10);
        const name = safeString(
          primitiveScriptField(scriptSource, ["nick", "nickname", "name"]) ||
            (nameElement && (attr(nameElement, "data-nick") || nameElement.textContent)) ||
            (headerMatch && headerMatch[2]),
          120
        );
        const xpRaw = primitiveScriptField(scriptSource, ["exp", "experience"]);
        const xpMaxRaw = primitiveScriptField(scriptSource, ["expMax", "exp_max", "experienceMax"]);
        const xp = typeof xpRaw === "number" ? xpRaw : null;
        const xpMax = typeof xpMaxRaw === "number" ? xpMaxRaw : null;
        const calculatedXpPercent = xp != null && xpMax != null && xpMax > 0 ? Math.max(0, Math.min(100, (xp / xpMax) * 100)) : null;
        candidates.push({
          path,
          href: safeString(win.location && win.location.href, 240),
          text: compact,
          name,
          level: Number.isFinite(level) ? level : null,
          xpPercent: percentFromText(compact, ["Опыт", "Experience"]) ?? calculatedXpPercent,
          rawControl: { xp, xpMax },
        });
      } catch (_) {}
    });
    const best = candidates.find((candidate) => candidate.level != null && candidate.name) || candidates[0] || null;
    const data = {
      name: best ? best.name || null : null,
      level: best ? best.level : null,
      xpPercent: best ? best.xpPercent : null,
      hpPercent: resources.healthPercent,
      prowessPercent: resources.prowessPercent,
      rawControl: best ? best.rawControl : { xp: null, xpMax: null },
    };
    const present = data.name || data.level != null || data.xpPercent != null || data.hpPercent != null || data.prowessPercent != null;
    const complete = data.name && data.level != null && data.xpPercent != null && data.hpPercent != null && data.prowessPercent != null;
    return {
      status: complete ? "available" : present ? "partial" : "not_loaded",
      reason: present ? null : "player_control_missing",
      source: best ? { framePath: best.path, href: best.href } : null,
      data,
    };
  };

  const locationSnapshot = () => {
    const context = mainContentContext();
    let semanticName = "";
    let locationId = "";
    try {
      const titleElement = context.doc && context.doc.querySelector
        ? context.doc.querySelector("[data-location-name],.location-title,.b-location__title,[class*='location'][class*='title']")
        : null;
      semanticName = safeString(
        titleElement && (attr(titleElement, "data-location-name") || titleElement.innerText || titleElement.textContent),
        160
      );
      const areaObject = context.win && context.win.area ? context.win.area : null;
      const areaModel = areaObject && areaObject.model && areaObject.model.area ? areaObject.model.area : null;
      const compassData =
        areaObject && areaObject.controller && areaObject.controller.compass
          ? areaObject.controller.compass.data
          : null;
      if (!semanticName) semanticName = safeString(areaModel && areaModel.title, 160);
      if (compassData && compassData.location != null) locationId = safeString(compassData.location, 80);
      if (!semanticName && context.pageKind === "area") {
        const rawLocationText = String(
          (context.doc && context.doc.body && (context.doc.body.innerText || context.doc.body.textContent)) || ""
        ).slice(0, 2400);
        const compactLocationText = safeString(rawLocationText, 2400);
        const headingMatch =
          rawLocationText.match(/(?:^|\n)[\t ]*([^\n\r]{2,160}?)[\t ]*\r?\n[\t ]*Царство\s*:/i) ||
          compactLocationText.match(/^(.{2,160}?)\s+Царство\s*:/i) ||
          context.text.match(/^(.{2,160}?)\s+Царство\s*:/i);
        semanticName = safeString(headingMatch && headingMatch[1], 160).replace(/\s+/g, " ").trim();
      }
      const idMatch = context.href.match(/[?&](?:location_id|loc_id|area_id)=([^&#]+)/i);
      if (!locationId) locationId = safeString(idMatch && decodeURIComponent(idMatch[1]), 80);
    } catch (_) {}
    return {
      status: context.href ? (semanticName || locationId ? "available" : "partial") : "unknown",
      reason: context.href ? null : "main_content_unavailable",
      source: { framePath: "main", href: context.href },
      data: {
        semanticName: semanticName || null,
        id: locationId || null,
        pageKind: context.pageKind,
        viewHref: context.href,
        title: context.title,
      },
    };
  };

  const locationRouteSnapshot = () => {
    const context = mainContentContext();
    const location = locationSnapshot();
    const rawText = String(
      (context.doc && context.doc.body && (context.doc.body.innerText || context.doc.body.textContent)) || ""
    );
    const timerMatch =
      rawText.match(/время\s+до\s+перехода\s*(\d+)\s*(?:с(?:ек)?)?/i) ||
      context.text.match(/время\s+до\s+перехода\s*(\d+)/i);
    let transitionTimerSeconds = timerMatch ? Number(timerMatch[1]) : null;
    const images = [];
    if (context.doc && typeof context.doc.querySelectorAll === "function") {
      for (const image of Array.from(context.doc.querySelectorAll("img")).slice(0, 500)) {
        let parent = image.parentElement || null;
        let nearbyText = "";
        for (let depth = 0; parent && depth < 5; depth += 1) {
          const candidate = safeString(parent.innerText || parent.textContent, 180);
          if (candidate && candidate.length <= 120) {
            nearbyText = candidate;
            break;
          }
          parent = parent.parentElement || null;
        }
        let rect = null;
        try {
          const rawRect = image.getBoundingClientRect && image.getBoundingClientRect();
          if (rawRect) {
            rect = {
              width: Math.round(Number(rawRect.width) || 0),
              height: Math.round(Number(rawRect.height) || 0),
            };
          }
        } catch (_) {}
        const signature = safeString(
          [
            attr(image, "src"),
            attr(image, "alt"),
            attr(image, "title"),
            attr(image, "class"),
            attr(image, "style"),
          ].join(" "),
          500
        );
        if (!nearbyText && !/(?:compass|navigator|route|path|way)/i.test(signature)) continue;
        images.push({
          index: images.length,
          nearbyText: nearbyText || null,
          src: safeString(attr(image, "src"), 300) || null,
          alt: safeString(attr(image, "alt"), 120) || null,
          title: safeString(attr(image, "title"), 120) || null,
          className: safeString(attr(image, "class"), 160) || null,
          style: safeString(attr(image, "style"), 300) || null,
          rect,
        });
        if (images.length >= 100) break;
      }
    }
    const interactiveElements = [];
    if (context.doc && typeof context.doc.querySelectorAll === "function") {
      for (const element of Array.from(
        context.doc.querySelectorAll("a,button,[onclick],[style*='cursor']")
      ).slice(0, 1000)) {
        const text = safeString(element.innerText || element.textContent, 180);
        if (!text || text.length > 120) continue;
        let computedBackground = "";
        try {
          const computed = context.win && context.win.getComputedStyle
            ? context.win.getComputedStyle(element)
            : null;
          computedBackground = safeString(
            computed && `${computed.backgroundImage || ""} ${computed.backgroundPosition || ""}`,
            300
          );
        } catch (_) {}
        interactiveElements.push({
          index: interactiveElements.length,
          tag: safeString(element.tagName, 30),
          text,
          id: safeString(attr(element, "id"), 80) || null,
          className: safeString(attr(element, "class"), 180) || null,
          style: safeString(attr(element, "style"), 400) || null,
          onclick: safeString(attr(element, "onclick"), 300) || null,
          href: safeString(attr(element, "href"), 300) || null,
          computedBackground: computedBackground || null,
          parentHtml: safeString(element.parentElement && element.parentElement.outerHTML, 1200) || null,
        });
        if (interactiveElements.length >= 120) break;
      }
    }
    const areaObject = context.win && context.win.area ? context.win.area : null;
    const areaController = areaObject && areaObject.controller ? areaObject.controller : null;
    const areaModel = areaObject && areaObject.model && areaObject.model.area ? areaObject.model.area : null;
    const compass = areaController && areaController.compass ? areaController.compass : null;
    const compassData = compass && compass.data ? compass.data : null;
    const compassLocation = areaModel && areaModel.compassLocation ? areaModel.compassLocation : null;
    const transitionDeadlineMs = Number(areaModel && areaModel.finishTimeLocal);
    const transitionDeadlineSeconds = Number.isFinite(transitionDeadlineMs)
      ? Math.max(0, Math.ceil((transitionDeadlineMs - Date.now()) / 1000))
      : null;
    const routeWaitValues = compassLocation
      ? [compassLocation.ltime, compassLocation.dtime, transitionDeadlineSeconds]
          .map((value) => Number(value))
          .filter((value) => Number.isFinite(value) && value >= 0)
      : [];
    if (transitionTimerSeconds == null && compassLocation && routeWaitValues.length) {
      transitionTimerSeconds = Math.max(...routeWaitValues);
    }
    const primitiveFields = (value) => {
      const result = {};
      if (!value) return result;
      for (const key of Object.keys(value).slice(0, 160)) {
        try {
          const field = value[key];
          if (field == null || ["string", "number", "boolean"].includes(typeof field)) result[key] = field;
        } catch (_) {}
      }
      return result;
    };
    const boundedFields = (value, depth = 4, seen = new Set()) => {
      if (value == null || ["string", "number", "boolean"].includes(typeof value)) {
        return typeof value === "string" ? safeString(value, 400) : value;
      }
      if (typeof value === "function") return `[Function ${safeString(value.name || "anonymous", 80)}]`;
      if (depth <= 0) return Array.isArray(value) ? "[Array]" : "[Object]";
      if (seen.has(value)) return "[Circular]";
      seen.add(value);
      if (Array.isArray(value)) {
        return value.slice(0, 24).map((entry) => {
          try {
            return boundedFields(entry, depth - 1, seen);
          } catch (_) {
            return "[Unreadable]";
          }
        });
      }
      const result = {};
      let keys = [];
      try {
        keys = Object.keys(value).slice(0, 100);
      } catch (_) {
        return "[Unreadable]";
      }
      for (const key of keys) {
        try {
          result[key] = boundedFields(value[key], depth - 1, seen);
        } catch (_) {
          result[key] = "[Unreadable]";
        }
      }
      return result;
    };
    return {
      ok: context.pageKind === "area" && Boolean(context.doc),
      message: context.pageKind === "area" ? "location_route_snapshot" : "location_route_page_missing",
      href: context.href,
      pageKind: context.pageKind,
      location: location.data,
      transitionTimerSeconds,
      timerReady: Boolean(compassLocation) && transitionTimerSeconds === 0,
      currentLocationId:
        compassData && compassData.location != null ? safeString(compassData.location, 80) || null : null,
      targetLocationId:
        compassData && compassData.target != null ? safeString(compassData.target, 80) || null : null,
      foundPath:
        compassData && Array.isArray(compassData.foundPath)
          ? compassData.foundPath.slice(0, 100).map((value) => safeString(value, 80))
          : [],
      nextTransition: compassLocation
        ? {
            id: safeString(compassLocation.id, 80) || null,
            name: safeString(compassLocation.name, 160) || null,
            locId: safeString(compassLocation.locId, 80) || null,
            href: safeString(compassLocation.href, 400) || null,
            mode: safeString(compassLocation.mode, 80) || null,
            confirm: Number(compassLocation.confirm || 0),
            hidden: Boolean(compassLocation.hidden),
            ltime: Number(compassLocation.ltime || 0),
            dtime: Number(compassLocation.dtime || 0),
          }
        : null,
      rawTextLength: rawText.length,
      rawText: safeString(rawText, 1600),
      documentImageCount:
        context.doc && typeof context.doc.querySelectorAll === "function"
          ? context.doc.querySelectorAll("img").length
          : null,
      areaState: areaObject
        ? {
            fields: primitiveFields(areaObject),
            controllerFields: primitiveFields(areaController),
            keys: Object.keys(areaObject).slice(0, 160),
            controllerKeys: areaController ? Object.keys(areaController).slice(0, 160) : [],
            compass: boundedFields(areaController && areaController.compass),
            model: boundedFields(areaObject.model, 3),
            conf: boundedFields(areaObject.conf, 3),
          }
        : null,
      images,
      interactiveElements,
    };
  };

  const locationRouteStep = async (payload = {}) => {
    const context = mainContentContext();
    const snapshot = locationRouteSnapshot();
    if (!snapshot.ok || context.pageKind !== "area") {
      return { ok: false, message: "location_route_page_missing", snapshot };
    }
    const areaObject = context.win && context.win.area ? context.win.area : null;
    const areaModel = areaObject && areaObject.model && areaObject.model.area ? areaObject.model.area : null;
    const compass = areaObject && areaObject.controller ? areaObject.controller.compass : null;
    const compassData = compass && compass.data ? compass.data : null;
    const next = areaModel && areaModel.compassLocation ? areaModel.compassLocation : null;
    if (!areaObject || !areaModel || !compassData || !next) {
      return { ok: false, message: "location_route_transition_missing", snapshot };
    }
    const battle = battleSnapshot();
    if (battle && (battle.hasFight || battle.rawHasFight)) {
      return { ok: false, message: "location_route_blocked_battle", snapshot };
    }
    const resources = resourceSnapshot();
    if (areaObject.model && areaObject.model.userGhost === true) {
      return { ok: false, message: "location_route_blocked_dead", snapshot };
    }
    if (resources && resources.healthPercent != null && Number(resources.healthPercent) <= 0) {
      return { ok: false, message: "location_route_blocked_zero_health", snapshot };
    }
    const currentLocationId = safeString(compassData.location, 80);
    const nextLocationId = safeString(next.locId, 80);
    const expectedCurrentLocationId = safeString(payload.expectedCurrentLocationId, 80);
    if (expectedCurrentLocationId && expectedCurrentLocationId !== currentLocationId) {
      return {
        ok: false,
        message: "location_route_current_location_mismatch",
        expectedCurrentLocationId,
        currentLocationId,
        snapshot,
      };
    }
    const foundPath = Array.isArray(compassData.foundPath)
      ? compassData.foundPath.map((value) => safeString(value, 80)).filter(Boolean)
      : [];
    if (!nextLocationId || !foundPath.length || foundPath[0] !== nextLocationId) {
      return {
        ok: false,
        message: "location_route_next_transition_mismatch",
        currentLocationId,
        nextLocationId,
        foundPath,
        snapshot,
      };
    }
    const transitionDeadlineMs = Number(areaModel.finishTimeLocal);
    const transitionDeadlineSeconds = Number.isFinite(transitionDeadlineMs)
      ? Math.max(0, Math.ceil((transitionDeadlineMs - Date.now()) / 1000))
      : null;
    const waits = [next.ltime, next.dtime, transitionDeadlineSeconds]
      .map((value) => Number(value))
      .filter((value) => Number.isFinite(value) && value >= 0);
    const waitSeconds = waits.length ? Math.max(...waits) : null;
    if (waitSeconds == null || waitSeconds > 0) {
      return { ok: false, message: "location_route_timer_not_ready", waitSeconds, snapshot };
    }
    if (Number(next.confirm || 0) !== 0 || Boolean(next.hidden)) {
      return { ok: false, message: "location_route_transition_not_direct", snapshot };
    }
    const href = safeString(next.href, 400);
    const normalizedHref = href.replace(/&amp;/g, "&");
    const comeInPattern = /[?&]code=COME_IN(?:&|$)/i;
    const areaIdPattern = new RegExp(`[?&]area_id=${nextLocationId}(?:&|$)`, "i");
    if (
      !/^\/?action_run\.php\?/i.test(normalizedHref) ||
      !comeInPattern.test(normalizedHref) ||
      !areaIdPattern.test(normalizedHref)
    ) {
      return { ok: false, message: "location_route_href_rejected", href, nextLocationId, snapshot };
    }
    const delayMs = Math.max(25, Math.min(250, Number(payload.navigationDelayMs) || 75));
    const submitted = {
      currentLocationId,
      nextLocationId,
      targetLocationId: safeString(compassData.target, 80) || null,
      name: safeString(next.name, 160) || null,
      href: normalizedHref,
      foundPath,
    };
    context.win.setTimeout(() => {
      try {
        context.win.location.href = normalizedHref;
      } catch (_) {}
    }, delayMs);
    return { ok: true, message: "location_route_step_submitted", submitted: true, transition: submitted };
  };

  const reviveControlEntries = (context) => {
    const entries = [];
    const documents = [];
    const seenDocuments = new Set();
    const addDocument = (doc) => {
      if (doc && !seenDocuments.has(doc)) {
        seenDocuments.add(doc);
        documents.push(doc);
      }
    };
    addDocument(context.doc);
    walkWindows(window.top || window, "top", 4, new Set(), (win) => {
      try {
        addDocument(win.document);
      } catch (_) {}
    });
    for (const doc of documents) {
      let elements = [];
      try {
        elements = Array.from(
          doc.querySelectorAll("a,button,input[type='button'],input[type='submit'],[onclick]")
        ).slice(0, 1200);
      } catch (_) {
        continue;
      }
      for (const element of elements) {
        const text = elementText(element);
        const controlName = safeString(attr(element, "name"), 80).toLowerCase();
        const controlValue = safeString(element && element.value, 120);
        let surrounding = element;
        const surroundingParts = [text, controlValue];
        for (let depth = 0; depth < 6 && surrounding; depth += 1) {
          surroundingParts.push(safeString(surrounding.innerText || surrounding.textContent, 500));
          surrounding = surrounding.parentElement || null;
        }
        const surroundingText = safeString(surroundingParts.join(" "), 1200);
        const prompt = /(?:желаете\s+воскреснуть|вы\s+(?:погибли|мертвы)|персонаж\s+погиб)/i.test(surroundingText);
        const explicitlyPositive = /(?:воскрес|возрод|ожить|поднять)/i.test(text);
        const affirmative =
          controlName === "yes" || /^(?:да|yes|ok|воскреснуть)$/i.test(controlValue || text);
        const negative = controlName === "no" || /^(?:нет|no|отмена)$/i.test(controlValue || text);
        if ((!explicitlyPositive && !(prompt && affirmative)) || negative || element.disabled === true) continue;
        try {
          if (typeof element.getBoundingClientRect === "function") {
            const rect = element.getBoundingClientRect();
            if (rect && (Number(rect.width) <= 0 || Number(rect.height) <= 0)) continue;
          }
        } catch (_) {}
        const explicitFree = /(?:бесплат|без\s+платы)/i.test(surroundingText);
        const explicitZero = /(?:стоим|цена)[^\d]{0,20}0(?:[.,]0+)?(?:\s|$)/i.test(surroundingText);
        const costMentioned = /(?:стоим|цена|кругляш|золот|монет|серебр)/i.test(surroundingText);
        const explicitNoCostPrompt = prompt && affirmative && !costMentioned;
        entries.push({
          element,
          summary: {
            index: entries.length,
            optionId: `revive:${entries.length}`,
            text: safeString(text, 160),
            href: safeString(attr(element, "href"), 200),
            onclick: safeString(attr(element, "onclick"), 240),
            free: Boolean(explicitFree || explicitZero || explicitNoCostPrompt),
            safe: Boolean(explicitFree || explicitZero || explicitNoCostPrompt),
            available: true,
            freeEvidence: explicitFree
              ? "explicit_free_text"
              : explicitZero
                ? "explicit_zero_cost"
                : explicitNoCostPrompt
                  ? "explicit_resurrection_prompt_without_cost"
                  : null,
          },
        });
        if (entries.length >= 10) break;
      }
      if (entries.length >= 10) break;
    }
    return entries;
  };

  const resurrectionNoticeEntry = () => {
    const root = window.top || window;
    try {
      const frame = root.document && root.document.querySelector
        ? root.document.querySelector("iframe#error")
        : null;
      if (!frame) return null;
      const doc = frame.contentDocument || (frame.contentWindow && frame.contentWindow.document);
      const text = safeString(doc && doc.body && (doc.body.innerText || doc.body.textContent), 2000);
      if (!/Воскрешение/i.test(text) || !/Вы\s+воскрешены/i.test(text)) return null;
      const controls = Array.from(
        doc.querySelectorAll("button,input[type='button'],input[type='submit'],a,[onclick]")
      ).filter((element) =>
        [element && element.innerText, element && element.textContent, element && element.value]
          .map((value) => safeString(value, 120))
          .some((value) => value === "Закрыть")
      );
      return {
        frame,
        doc,
        controls,
        summary: {
          frameId: safeString(frame.id, 80),
          frameSrc: safeString(attr(frame, "src"), 500),
          text: safeString(text, 240),
          closeControlCount: controls.length,
        },
      };
    } catch (_) {
      return null;
    }
  };

  const closeResurrectionNotice = async (payload = {}) => {
    const before = resurrectionNoticeEntry();
    if (!before) {
      return { ok: true, message: "resurrection_notice_absent", closed: false, confirmed: true };
    }
    if (before.controls.length !== 1) {
      return {
        ok: false,
        message: "resurrection_notice_close_ambiguous",
        closed: false,
        notice: before.summary,
      };
    }
    const control = before.controls[0];
    if (!control || typeof control.click !== "function") {
      return { ok: false, message: "resurrection_notice_close_not_clickable", notice: before.summary };
    }
    try {
      control.click();
    } catch (error) {
      return {
        ok: false,
        message: `resurrection_notice_close_error:${safeString(error && error.message ? error.message : error, 200)}`,
        notice: before.summary,
      };
    }
    await delayMs(Math.max(100, Number(payload && payload.verifyDelayMs) || 250));
    const after = resurrectionNoticeEntry();
    return {
      ok: !after,
      message: after ? "resurrection_notice_close_unconfirmed" : "resurrection_notice_closed",
      closed: true,
      confirmed: !after,
      before: before.summary,
      after: after ? after.summary : null,
    };
  };

  const nativeResurrectHandler = () => {
    let match = null;
    walkWindows(window.top || window, "top", 4, new Set(), (win, path) => {
      if (match) return;
      try {
        const handler = win.resurrect;
        if (typeof handler !== "function") return;
        const source = safeString(Function.prototype.toString.call(handler), 3000);
        if (!/action_run\.php\?code=RESURRECT(?:&|['"])/i.test(source)) return;
        match = { win, path, handler, source };
      } catch (_) {}
    });
    return match;
  };

  const directPlayerHealthPercent = () => {
    let healthPercent = null;
    walkWindows(window.top || window, "top", 4, new Set(), (win) => {
      if (healthPercent != null) return;
      try {
        const element = win.document && win.document.querySelector
          ? win.document.querySelector(".b-control-lvl__hp,[class*='control-lvl__hp']")
          : null;
        const text = safeString(element && (element.innerText || element.textContent), 500);
        const parsed = percentFromText(text, ["Жизнь", "Health", "Life"]);
        if (parsed != null) healthPercent = parsed;
      } catch (_) {}
    });
    return healthPercent;
  };

  const nativeResurrectionProbe = (context, resources) => {
    const handler = nativeResurrectHandler();
    const directHealthValue = directPlayerHealthPercent();
    const directHealthPercent = directHealthValue == null ? Number.NaN : Number(directHealthValue);
    const resourceHealthPercent = Number(resources && resources.healthPercent);
    const healthPercent = Number.isFinite(directHealthPercent) ? directHealthPercent : resourceHealthPercent;
    const zeroHealth = Number.isFinite(healthPercent) && healthPercent <= 0;
    const locationText = safeString(context.text, 12000);
    const normalizedLocationText = locationText.toLocaleLowerCase("ru-RU");
    const locationLooksGhostly = (
      normalizedLocationText.includes("призрак") ||
      normalizedLocationText.includes("мертв") ||
      normalizedLocationText.includes("мёртв")
    );
    const revivePage = ["area", "inventory", "hunt", "main"].includes(context.pageKind);
    return {
      handler,
      handlerAvailable: Boolean(handler),
      handlerPath: handler ? handler.path : null,
      directHealthPercent: Number.isFinite(directHealthPercent) ? directHealthPercent : null,
      resourceHealthPercent: Number.isFinite(resourceHealthPercent) ? resourceHealthPercent : null,
      healthPercent: Number.isFinite(healthPercent) ? healthPercent : null,
      zeroHealth,
      revivePage,
      locationLooksGhostly,
      pageKind: context.pageKind,
      locationText: safeString(locationText, 240),
    };
  };

  const nativeResurrectionEntry = (context, resources, providedProbe = null) => {
    const probe = providedProbe || nativeResurrectionProbe(context, resources);
    const handler = probe.handler;
    if (!handler || !probe.zeroHealth || !probe.revivePage) return null;
    return {
      handler,
      summary: {
        index: 0,
        optionId: "revive:native-resurrect",
        text: "resurrect()",
        href: "/action_run.php?code=RESURRECT&url_success=/area.php&url_error=/area.php",
        onclick: "resurrect()",
        free: true,
        safe: true,
        available: true,
        freeEvidence: "native_resurrect_handler_zero_hp_recoverable_page",
        framePath: handler.path,
      },
    };
  };

  const reviveDebugCandidates = () => {
    const candidates = [];
    walkWindows(window.top || window, "top", 4, new Set(), (win, path) => {
      if (candidates.length >= 80) return;
      try {
        try {
          if (win.popupDialogObj && candidates.length < 80) {
            candidates.push({
              path,
              tag: "WINDOW_OBJECT",
              text: "popupDialogObj",
              attributes: "",
              rect: null,
              object: summarizeObject(win.popupDialogObj, `${path}.popupDialogObj`, 3, new Set()),
            });
          }
        } catch (_) {}
        const doc = win.document;
        const elements = doc
          ? Array.from(
              doc.querySelectorAll(
                "[class*='popup'],[id*='popup'],[class*='confirm'],[id*='confirm'],[name='yes'],[name='no'],input[type='button'],input[type='submit'],button"
              )
            ).slice(0, 1000)
          : [];
        for (let index = 0; index < elements.length && candidates.length < 80; index += 1) {
          const element = elements[index];
          const text = safeString(element.innerText || element.textContent || element.value, 500);
          const attributes = safeString(
            [
              attr(element, "id"),
              attr(element, "class"),
              attr(element, "name"),
              attr(element, "value"),
              attr(element, "onclick"),
              attr(element, "href"),
              attr(element, "src"),
              attr(element, "alt"),
              attr(element, "title"),
            ].join(" "),
            500
          );
          const resurrectionMatch = /(?:желаете\s+воскреснуть|воскрес|возрод)/i.test(
            `${text} ${attributes}`
          );
          let rect = null;
          try {
            const rawRect = element.getBoundingClientRect && element.getBoundingClientRect();
            if (rawRect) {
              rect = {
                x: Math.round(Number(rawRect.x) || 0),
                y: Math.round(Number(rawRect.y) || 0),
                width: Math.round(Number(rawRect.width) || 0),
                height: Math.round(Number(rawRect.height) || 0),
              };
            }
          } catch (_) {}
          if (!resurrectionMatch && rect && (rect.width <= 0 || rect.height <= 0)) continue;
          candidates.push({
            path,
            index,
            tag: safeString(element.tagName, 30),
            text,
            attributes,
            rect,
            html: safeString(element.outerHTML, 800),
          });
        }
      } catch (_) {}
    });
    return candidates;
  };

  const deathReviveSnapshot = () => {
    const context = mainContentContext();
    const entries = reviveControlEntries(context);
    const resurrectionNotice = resurrectionNoticeEntry();
    const resources = resourceSnapshot();
    const nativeProbe = nativeResurrectionProbe(context, resources);
    const nativeEntry = nativeResurrectionEntry(context, resources, nativeProbe);
    const controls = nativeEntry ? [nativeEntry.summary] : entries.map((entry) => entry.summary);
    let marker = /(?:желаете\s+воскреснуть|вы\s+(?:погибли|мертвы)|персонаж\s+погиб|смерть\s+персонажа)/i.test(context.text);
    if (!marker) {
      walkWindows(window.top || window, "top", 4, new Set(), (win) => {
        if (marker) return;
        try {
          const text = safeString(win.document && win.document.body && win.document.body.innerText, 4000);
          marker = /(?:желаете\s+воскреснуть|вы\s+(?:погибли|мертвы)|персонаж\s+погиб)/i.test(text);
        } catch (_) {}
      });
    }
    marker = marker || Boolean(nativeEntry);
    const alivePage = ["battle", "hunt", "quests", "inventory", "area", "main"].includes(context.pageKind);
    const aliveEvidence = !marker && controls.length === 0 && alivePage && Number(resources.healthPercent) > 0;
    const dead = marker && controls.length > 0 ? true : aliveEvidence ? false : null;
    const costMatch = context.text.match(/(?:стоим|цена)[^\d]{0,20}(\d+(?:[.,]\d+)?)[^\n]{0,40}/i);
    return {
      status: dead === true ? "available" : context.href ? "partial" : "unknown",
      reason: dead === true ? null : "death_not_confirmed",
      source: { framePath: "main", href: context.href },
      data: {
        state: dead === true ? "dead" : dead === false ? "alive" : "unknown",
        dead,
        reviveAvailable: controls.length > 0,
        freeReviveAvailable: controls.some((control) => control.free === true),
        freeReviveOptionCount: controls.filter((control) => control.free === true && control.safe === true).length,
        reviveOptions: controls,
        resurrectionNoticeAvailable: Boolean(resurrectionNotice),
        resurrectionNotice: resurrectionNotice ? resurrectionNotice.summary : null,
        costText: safeString(costMatch && costMatch[0], 120) || null,
        diagnostics: dead === null
          ? [{
              source: "native_resurrection_probe",
              handlerAvailable: nativeProbe.handlerAvailable,
              handlerPath: nativeProbe.handlerPath,
              directHealthPercent: nativeProbe.directHealthPercent,
              resourceHealthPercent: nativeProbe.resourceHealthPercent,
              healthPercent: nativeProbe.healthPercent,
              zeroHealth: nativeProbe.zeroHealth,
              revivePage: nativeProbe.revivePage,
              locationLooksGhostly: nativeProbe.locationLooksGhostly,
              pageKind: nativeProbe.pageKind,
              locationText: nativeProbe.locationText,
            }]
          : [],
      },
    };
  };

  const reviveFree = async (payload) => {
    const expectedCharacter = safeString(payload && payload.expectedCharacter, 120);
    const player = playerSnapshot();
    const observedCharacter = safeString(player && player.data && player.data.name, 120);
    if (expectedCharacter && observedCharacter && expectedCharacter.toLowerCase() !== observedCharacter.toLowerCase()) {
      return { ok: false, message: "revive_character_mismatch", expectedCharacter, observedCharacter };
    }
    const before = deathReviveSnapshot();
    if (!before.data || before.data.dead !== true) {
      return { ok: false, message: "death_not_confirmed", before };
    }
    const context = mainContentContext();
    const nativeEntry = nativeResurrectionEntry(context, resourceSnapshot());
    if (nativeEntry) {
      try {
        let mainWindow = findMainContentWindow(window.top || window);
        if (!mainWindow || !mainWindow.location) {
          return { ok: false, message: "free_revive_main_window_missing", option: nativeEntry.summary };
        }
        if (context.pageKind !== "area") {
          mainWindow.location.href = "/area.php";
          await delayMs(750);
          const areaContext = mainContentContext();
          if (areaContext.pageKind !== "area") {
            return {
              ok: false,
              message: "free_revive_area_not_opened",
              option: nativeEntry.summary,
              beforePageKind: context.pageKind,
              afterPageKind: areaContext.pageKind,
            };
          }
          mainWindow = findMainContentWindow(window.top || window);
        }
        mainWindow.location.href = "/action_run.php?code=RESURRECT&url_success=/area.php&url_error=/area.php";
      } catch (error) {
        return {
          ok: false,
          message: `free_revive_native_error:${safeString(error && error.message ? error.message : error, 200)}`,
          option: nativeEntry.summary,
        };
      }
      await delayMs(Math.max(500, Number(payload && payload.verifyDelayMs) || 1500));
      const after = deathReviveSnapshot();
      return {
        ok: true,
        message: after.data && after.data.dead === false ? "free_revive_confirmed" : "free_revive_submitted",
        submitted: true,
        confirmed: Boolean(after.data && after.data.dead === false),
        option: nativeEntry.summary,
        before,
        after,
      };
    }
    const freeEntries = reviveControlEntries(context).filter((entry) => entry.summary.free === true);
    if (freeEntries.length !== 1) {
      return {
        ok: false,
        message: freeEntries.length ? "free_revive_ambiguous" : "free_revive_not_proven",
        freeOptions: freeEntries.map((entry) => entry.summary),
      };
    }
    const selected = freeEntries[0];
    if (!selected.element || typeof selected.element.click !== "function") {
      return { ok: false, message: "free_revive_not_clickable", option: selected.summary };
    }
    try {
      selected.element.click();
    } catch (error) {
      return { ok: false, message: `free_revive_click_error:${safeString(error && error.message ? error.message : error, 200)}` };
    }
    await delayMs(Math.max(250, Number(payload && payload.verifyDelayMs) || 1000));
    const after = deathReviveSnapshot();
    return {
      ok: true,
      message: after.data && after.data.dead === false ? "free_revive_confirmed" : "free_revive_submitted",
      submitted: true,
      confirmed: Boolean(after.data && after.data.dead === false),
      option: selected.summary,
      before,
      after,
    };
  };
