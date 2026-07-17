  const normalizeNpcName = (value) =>
    safeString(value, 180)
      .toLocaleLowerCase("ru-RU")
      .replace(/ё/g, "е")
      .replace(/[«»"']/g, "")
      .replace(/\s+/g, " ")
      .trim();

  const npcNameStem = (value) => {
    let token = safeString(value, 80).toLocaleLowerCase("ru-RU").replace(/ё/g, "е");
    if (token.length < 4) return token;
    token = token.replace(/(?:иями|ями|ами|ого|его|ому|ему|иях|ах|ях|ам|ям|ов|ев|ой|ый|ий|ая|яя|ую|юю|ом|ем|ым|им|а|я|у|ю|ы|и|е|о)$/u, "");
    return token.length >= 3 ? token.replace(/[ьй]$/u, "") : normalizeNpcName(value);
  };

  const npcNamesEquivalent = (left, right) => {
    const signature = (value) => (normalizeNpcName(value).match(/[а-яa-z0-9]+/giu) || []).map(npcNameStem);
    const leftSignature = signature(left);
    const rightSignature = signature(right);
    return (
      leftSignature.length > 0 &&
      leftSignature.length === rightSignature.length &&
      leftSignature.every((stem, index) => stem === rightSignature[index])
    );
  };

  let lastAreaNpcObservation = null;
  let lastNpcDialogObservation = null;
  let npcObservationSequence = 0;

  const npcObservationId = (prefix) => {
    npcObservationSequence += 1;
    return `${prefix}-${Date.now().toString(36)}-${npcObservationSequence.toString(36)}`;
  };

  const positiveIntegerString = (value) => {
    const text = safeString(value, 40);
    return /^\d+$/.test(text) && parseInt(text, 10) > 0 ? text : null;
  };

  const npcIntegerString = (value) => {
    const text = safeString(value, 40);
    return /^\d+$/.test(text) && parseInt(text, 10) >= 0 ? text : null;
  };

  const npcQuerySummary = (href, baseHref) => {
    const safeHref = safeQuestHref(href, baseHref);
    if (!safeHref || !/\/npc\.php(?:\?|$)/i.test(safeHref)) return null;
    let query = "";
    try {
      query = safeHref.split("?")[1] || "";
    } catch (_) {}
    const allowed = new Set(["action", "f_id", "npc_id", "global_npc", "quest_id", "quest", "point_id", "ref"]);
    const params = {};
    for (const part of query.split("&").slice(0, 40)) {
      const separator = part.indexOf("=");
      if (separator <= 0) continue;
      const rawKey = part.slice(0, separator);
      const rawValue = part.slice(separator + 1);
      let key = "";
      let value = "";
      try {
        key = decodeURIComponent(rawKey).toLowerCase();
        value = decodeURIComponent(rawValue.replace(/\+/g, " "));
      } catch (_) {
        continue;
      }
      if (!allowed.has(key)) continue;
      params[key] = safeString(value, 120);
    }
    return { path: "/npc.php", params };
  };

  const npcActionHref = (element, baseHref) => {
    const direct = attr(element, "href");
    if (direct) return safeQuestHref(direct, baseHref);
    const formAction = element && element.form
      ? attr(element.form, "action") || element.form.action
      : null;
    if (formAction) return safeQuestHref(formAction, baseHref);
    const onclick = safeString(attr(element, "onclick"), 1200);
    if (!onclick) return null;
    const match = onclick.match(/^\s*(?:window\.)?location(?:\.href)?\s*=\s*(['"])(npc\.php\?[^'"]+)\1\s*;?\s*$/i);
    return match ? safeQuestHref(match[2], baseHref) : null;
  };

  const npcActionText = (element) => {
    const direct = safeString(
      element && (element.innerText || element.value || attr(element, "title") || attr(element, "alt")),
      1200
    );
    if (direct) return direct;
    const image = element && element.querySelector ? element.querySelector("img[alt]") : null;
    return safeString(image && attr(image, "alt"), 1200);
  };

  const areaNpcSnapshot = (expectedName = "") => {
    const context = mainContentContext();
    const documentReady = !context.doc || !context.doc.readyState || context.doc.readyState === "complete";
    const shell = context.doc && context.doc.querySelector
      ? context.doc.querySelector(".b-control-area__list,.b-control-area")
      : null;
    if (context.pageKind !== "area" || !context.doc || !documentReady || !shell) {
      return {
        ok: false,
        message: shell ? "area_npc_page_not_loaded" : "area_npc_shell_missing",
        pageKind: context.pageKind,
        href: context.href,
        expectedName: safeString(expectedName, 180) || null,
        items: [],
      };
    }
    const generatedAt = new Date().toISOString();
    const snapshotId = npcObservationId("area-npcs");
    const observedLocation = locationSnapshot();
    const locationData = observedLocation && observedLocation.data ? observedLocation.data : {};
    const items = [];
    const nodes = Array.from(context.doc.querySelectorAll(".b-control-area__list-item.npc")).slice(0, 100);
    for (const element of nodes) {
      const name = safeString(attr(element, "title") || element.innerText || element.textContent, 180);
      // Area actors use zero-based IDs in some locations (for example the
      // first house in "Земли пращуров").  Zero is a valid NPC identity,
      // not a missing value; quest/ref/point identifiers remain strictly > 0.
      const dataId = npcIntegerString(attr(element, "data-id"));
      const rawIndex = safeString(attr(element, "data-index"), 20);
      const dataIndex = /^\d+$/.test(rawIndex) ? parseInt(rawIndex, 10) : null;
      if (!name) continue;
      const visible = elementIsVisible(context.win, element);
      items.push({
        name,
        normalizedName: normalizeNpcName(name),
        dataId,
        dataIndex,
        visible,
        actionable: Boolean(dataId && dataIndex != null && visible),
      });
    }
    const idCounts = new Map();
    for (const item of items) {
      if (item.dataId) idCounts.set(item.dataId, (idCounts.get(item.dataId) || 0) + 1);
    }
    for (const item of items) {
      if (item.dataId && (idCounts.get(item.dataId) || 0) > 1) item.actionable = false;
    }
    const normalizedExpected = normalizeNpcName(expectedName);
    const exactMatches = normalizedExpected
      ? items.filter((item) => item.normalizedName === normalizedExpected)
      : [];
    const result = {
      ok: true,
      message: "area_npc_snapshot",
      snapshotId,
      generatedAt,
      pageKind: context.pageKind,
      href: context.href,
      expectedName: safeString(expectedName, 180) || null,
      exactMatchCount: exactMatches.length,
      location: {
        id: safeString(locationData.id, 80) || null,
        name: safeString(locationData.semanticName, 180) || null,
      },
      items,
      truncated: nodes.length >= 100 || items.length >= 100,
    };
    lastAreaNpcObservation = result;
    return result;
  };

  const npcHeaderCandidates = (context) => {
    if (!context.doc) return [];
    const selectors = [
      ".npc-name",
      ".npc-title",
      ".npc-header",
      ".npc-point__title",
      "h1",
      "h2",
      "h3",
      "caption",
    ];
    const result = [];
    const seen = new Set();
    for (const selector of selectors) {
      let elements = [];
      try {
        elements = Array.from(context.doc.querySelectorAll(selector)).slice(0, 30);
      } catch (_) {}
      for (const element of elements) {
        const text = safeString(element.innerText || element.textContent, 180);
        const key = normalizeNpcName(text);
        if (!text || !key || seen.has(key)) continue;
        seen.add(key);
        result.push(text);
        if (result.length >= 60) return result;
      }
    }
    return result;
  };

  const npcDialogSnapshot = (payload = {}) => {
    const context = mainContentContext();
    const expectedName = safeString(payload.expectedName, 180);
    const normalizedExpected = normalizeNpcName(expectedName);
    const expectedNpcId = payload.expectedNpcId == null ? null : npcIntegerString(payload.expectedNpcId);
    const documentReady = !context.doc || !context.doc.readyState || context.doc.readyState === "complete";
    if (context.pageKind !== "npc" || !context.doc || !documentReady) {
      return {
        ok: false,
        message: "npc_dialog_not_loaded",
        pageKind: context.pageKind,
        href: context.href,
        expectedName: expectedName || null,
        identityMatches: false,
        actions: [],
      };
    }
    const headers = npcHeaderCandidates(context);
    const matchingHeaders = normalizedExpected
      ? headers.filter((name) => npcNamesEquivalent(name, expectedName))
      : [];
    const actions = [];
    const elements = Array.from(
      context.doc.querySelectorAll("a[href],button,input[type='button'],input[type='submit'],[onclick]")
    ).slice(0, 150);
    for (let index = 0; index < elements.length; index += 1) {
      const element = elements[index];
      // Dialogue replies can be long prose.  Python binds answers with a
      // 1200-character limit, so truncating here can remove the decisive
      // continuation at the end and turn a safe sole reply into a refusal.
      const text = safeString(npcActionText(element), 1200);
      const query = npcQuerySummary(npcActionHref(element, context.href), context.href);
      if (!text && !query) continue;
      const container = element.closest
        ? element.closest(".npc-point,.npc-quest,tr,li,form,fieldset,div")
        : null;
      actions.push({
        index,
        text: text || null,
        containerText: safeString(container && (container.innerText || container.textContent), 1200) || null,
        tag: safeString(element.tagName, 24),
        query,
        visible: elementIsVisible(context.win, element),
        disabled: Boolean(element.disabled) || attr(element, "aria-disabled") === "true",
      });
    }
    const npcIds = Array.from(new Set(
      actions
        .map((action) => action.query && action.query.params ? npcIntegerString(action.query.params.f_id) : null)
        .filter(Boolean)
    ));
    const npcId = npcIds.length === 1 ? npcIds[0] : null;
    const questActions = actions
      .map((action) => {
        const questId = action.query && action.query.params
          ? positiveIntegerString(action.query.params.quest_id)
          : null;
        if (!questId || !action.text || !action.containerText) return null;
        const container = safeString(action.containerText, 1200);
        const buttonText = safeString(action.text, 180);
        const title = container.endsWith(buttonText)
          ? safeString(container.slice(0, -buttonText.length), 220)
          : null;
        if (!title) return null;
        return {
          questId,
          title,
          action: /^далее$/i.test(buttonText) ? "open" : "unknown",
          text: buttonText,
          npcId: npcIntegerString(action.query.params.f_id),
          npcInstanceId: action.query.params.npc_id || null,
          visible: action.visible,
          disabled: action.disabled,
        };
      })
      .filter(Boolean);
    const dialogActions = actions
      .map((candidate) => {
        const params = candidate.query && candidate.query.params ? candidate.query.params : null;
        const questId = params ? positiveIntegerString(params.quest_id) : null;
        const npcAction = params ? safeString(params.action, 24).toLowerCase() : "";
        const ref = params ? positiveIntegerString(params.ref) : null;
        if (!questId || npcAction !== "answer" || !ref || !candidate.text) return null;
        return {
          questId,
          action: "answer",
          ref,
          pointId: positiveIntegerString(params.point_id),
          text: candidate.text,
          npcId: npcIntegerString(params.f_id),
          npcInstanceId: positiveIntegerString(params.npc_id),
          visible: candidate.visible,
          disabled: candidate.disabled,
        };
      })
      .filter(Boolean);
    const doneActions = actions
      .map((candidate) => {
        const params = candidate.query && candidate.query.params ? candidate.query.params : null;
        const questId = params ? positiveIntegerString(params.quest_id) : null;
        const npcAction = params ? safeString(params.action, 24).toLowerCase() : "";
        if (!questId || npcAction !== "done" || !candidate.text) return null;
        return {
          questId,
          action: "done",
          pointId: positiveIntegerString(params.point_id),
          text: candidate.text,
          npcId: npcIntegerString(params.f_id),
          npcInstanceId: positiveIntegerString(params.npc_id),
          visible: candidate.visible,
          disabled: candidate.disabled,
        };
      })
      .filter(Boolean);
    const acceptActions = doneActions
      .filter((candidate) => candidate.text === "Взять задание")
      .map((candidate) => ({ ...candidate, action: "accept" }));
    const generatedAt = new Date().toISOString();
    const snapshotId = npcObservationId("npc-dialog");
    const result = {
      ok: true,
      message: "npc_dialog_snapshot",
      snapshotId,
      generatedAt,
      pageKind: context.pageKind,
      href: context.href,
      expectedName: expectedName || null,
      expectedNpcId,
      npcId,
      identityMatches: expectedNpcId && normalizedExpected
        ? expectedNpcId === npcId && matchingHeaders.length === 1
        : expectedNpcId
          ? expectedNpcId === npcId
          : (normalizedExpected ? matchingHeaders.length === 1 : null),
      matchingHeaders,
      headers,
      actions,
      questActions,
      dialogActions,
      doneActions,
      acceptActions,
      truncated: elements.length >= 150,
    };
    lastNpcDialogObservation = result;
    return result;
  };

  const submitNpcQuestAction = (payload = {}) => {
    const notIssued = (message) => ({
      ok: false, outcome: "NOT_ISSUED", mutationIssued: false,
      destination: null, issuedAt: null, message,
    });
    const expectedSnapshotId = safeString(payload.expectedSnapshotId, 120);
    const npcId = npcIntegerString(payload.npcId);
    const expectedName = safeString(payload.expectedName, 180);
    const questId = positiveIntegerString(payload.questId);
    const expectedTitle = safeString(payload.expectedTitle, 220);
    const action = safeString(payload.action, 24).toLowerCase();
    const expectedRef = payload.expectedRef == null ? null : positiveIntegerString(payload.expectedRef);
    const expectedPointId = payload.expectedPointId == null ? null : positiveIntegerString(payload.expectedPointId);
    const expectedText = safeString(payload.expectedText, 1200);
    if (
      !expectedSnapshotId || !npcId || !questId || (action !== "done" && !expectedTitle) ||
      !["open", "answer", "accept", "done"].includes(action) ||
      (["answer", "accept", "done"].includes(action) && !expectedText) ||
      (action === "done" && !expectedPointId) ||
      (action === "answer" && !expectedRef)
    ) {
      return notIssued("npc_quest_action_invalid");
    }
    const observed = lastNpcDialogObservation;
    const observedAgeMs = observed && observed.generatedAt
      ? Date.now() - Date.parse(observed.generatedAt)
      : Number.POSITIVE_INFINITY;
    if (
      !observed ||
      observed.snapshotId !== expectedSnapshotId ||
      !Number.isFinite(observedAgeMs) ||
      observedAgeMs < 0 ||
      observedAgeMs > 5000
    ) {
      return notIssued("npc_dialog_snapshot_stale");
    }
    if (
      observed.truncated || observed.npcId !== npcId || observed.identityMatches !== true ||
      !observed.expectedName || !Array.isArray(observed.matchingHeaders) || observed.matchingHeaders.length !== 1 ||
      (expectedName && !npcNamesEquivalent(observed.expectedName, expectedName))
    ) {
      return notIssued(observed.truncated ? "npc_dialog_snapshot_truncated" : "npc_dialog_identity_mismatch");
    }
    const observedMatches = action === "open"
      ? observed.questActions.filter((candidate) =>
          candidate.questId === questId &&
          candidate.npcId === npcId &&
          candidate.action === action &&
          normalizeNpcName(candidate.title) === normalizeNpcName(expectedTitle) &&
          candidate.visible === true &&
          candidate.disabled === false
        )
      : action === "answer" ? observed.dialogActions.filter((candidate) =>
          candidate.questId === questId &&
          candidate.npcId === npcId &&
          candidate.action === action &&
          candidate.ref === expectedRef &&
          normalizeNpcName(candidate.text) === normalizeNpcName(expectedText) &&
          candidate.visible === true &&
          candidate.disabled === false
        ) : action === "done" ? observed.doneActions.filter((candidate) =>
          candidate.questId === questId &&
          candidate.npcId === npcId &&
          candidate.pointId === expectedPointId &&
          candidate.action === action &&
          candidate.text === expectedText &&
          candidate.visible === true &&
          candidate.disabled === false
        ) : observed.acceptActions.filter((candidate) =>
          candidate.questId === questId &&
          candidate.npcId === npcId &&
          candidate.action === action &&
          normalizeNpcName(candidate.text) === normalizeNpcName(expectedText) &&
          candidate.visible === true &&
          candidate.disabled === false
        );
    if (observedMatches.length !== 1) {
      return notIssued(observedMatches.length ? "npc_quest_action_ambiguous" : "npc_quest_action_missing");
    }
    const context = mainContentContext();
    const elements = Array.from(
      context.doc.querySelectorAll("a[href],button,input[type='button'],input[type='submit'],[onclick]")
    ).slice(0, 150);
    const matches = elements.filter((element) => {
      if (!elementIsVisible(context.win, element) || Boolean(element.disabled) || attr(element, "aria-disabled") === "true") return false;
      const query = npcQuerySummary(npcActionHref(element, context.href), context.href);
      if (!query || !query.params) return false;
      if (npcIntegerString(query.params.f_id) !== npcId || positiveIntegerString(query.params.quest_id) !== questId) return false;
      const text = safeString(npcActionText(element), 180);
      if (action === "answer") {
        return (
          safeString(query.params.action, 24).toLowerCase() === "answer" &&
          positiveIntegerString(query.params.ref) === expectedRef &&
          normalizeNpcName(text) === normalizeNpcName(expectedText)
        );
      }
      if (action === "accept") {
        return (
          safeString(query.params.action, 24).toLowerCase() === "done" &&
          normalizeNpcName(text) === normalizeNpcName(expectedText)
        );
      }
      if (action === "done") {
        return (
          safeString(query.params.action, 24).toLowerCase() === "done" &&
          positiveIntegerString(query.params.point_id) === expectedPointId &&
          text === expectedText
        );
      }
      if (!/^далее$/i.test(text)) return false;
      const container = element.closest ? element.closest(".npc-point,.npc-quest,tr,li,form,fieldset,div") : null;
      const containerText = safeString(container && (container.innerText || container.textContent), 1200);
      const title = containerText.endsWith(text) ? safeString(containerText.slice(0, -text.length), 220) : "";
      return normalizeNpcName(title) === normalizeNpcName(expectedTitle);
    });
    if (matches.length !== 1) {
      return notIssued(matches.length ? "npc_quest_action_ambiguous" : "npc_quest_action_missing");
    }
    const issuedAt = new Date().toISOString();
    try {
      matches[0].click();
    } catch (error) {
      return {
        ok: true, outcome: "ACK_PENDING", mutationIssued: true,
        destination: mainContentContext().href || null, issuedAt,
        message: `npc_quest_action_click_ambiguous:${safeString(error && error.message ? error.message : error, 180)}`,
      };
    }
    lastNpcDialogObservation = null;
    return {
      ok: true, outcome: "ACK_PENDING", mutationIssued: true,
      destination: mainContentContext().href || null, issuedAt,
      message: "npc_quest_action_submitted", action, npcId, questId, title: expectedTitle,
    };
  };

  const openExactNpc = async (payload = {}) => {
    const notIssued = (message, extra = {}) => ({
      ok: false, outcome: "NOT_ISSUED", mutationIssued: false,
      destination: null, issuedAt: null, message, ...extra,
    });
    const expectedSnapshotId = safeString(payload.expectedSnapshotId, 120);
    const expectedLocationId = safeString(payload.expectedLocationId, 80);
    const expectedName = safeString(payload.expectedName, 180);
    const expectedDialogName = safeString(payload.expectedDialogName, 180) || expectedName;
    const expectedDataId = npcIntegerString(payload.npcId);
    if (!expectedSnapshotId || !expectedLocationId || !expectedName || !expectedDataId) {
      return notIssued("npc_identity_invalid");
    }
    const observed = lastAreaNpcObservation;
    const observedAgeMs = observed && observed.generatedAt
      ? Date.now() - Date.parse(observed.generatedAt)
      : Number.POSITIVE_INFINITY;
    if (
      !observed ||
      observed.snapshotId !== expectedSnapshotId ||
      !Number.isFinite(observedAgeMs) ||
      observedAgeMs < 0 ||
      observedAgeMs > 5000
    ) {
      return notIssued("area_npc_snapshot_stale");
    }
    if (!observed.location || String(observed.location.id || "") !== expectedLocationId) {
      return notIssued("area_npc_location_mismatch");
    }
    if (observed.truncated) {
      return notIssued("area_npc_snapshot_truncated");
    }
    const observedMatches = observed.items.filter((item) => (
      item.dataId === expectedDataId &&
      item.normalizedName === normalizeNpcName(expectedName) &&
      item.actionable === true
    ));
    if (observedMatches.length !== 1) {
      return notIssued(observedMatches.length ? "npc_match_ambiguous" : "npc_exact_match_missing");
    }
    const before = areaNpcSnapshot(expectedName);
    if (
      !before.ok ||
      before.truncated ||
      !before.location ||
      String(before.location.id || "") !== expectedLocationId
    ) {
      return notIssued(before.truncated ? "area_npc_snapshot_truncated" : before.message, { observed: before });
    }
    const context = mainContentContext();
    let candidates = Array.from(context.doc.querySelectorAll(".b-control-area__list-item.npc"))
      .filter((element) => normalizeNpcName(attr(element, "title") || element.innerText || element.textContent) === normalizeNpcName(expectedName))
      .filter((element) => elementIsVisible(context.win, element));
    candidates = candidates.filter((element) => npcIntegerString(attr(element, "data-id")) === expectedDataId);
    if (candidates.length !== 1) {
      return notIssued(candidates.length ? "npc_match_ambiguous" : "npc_exact_match_missing", {
        message: candidates.length ? "npc_match_ambiguous" : "npc_exact_match_missing",
        expectedName,
        expectedDataId,
        matchCount: candidates.length,
        snapshot: before,
      });
    }
    const target = candidates[0];
    const beforeState = {
      snapshotId: before.snapshotId || null,
      generatedAt: before.generatedAt || null,
      pageKind: before.pageKind || "area",
      href: before.href || mainContentContext().href || null,
      location: before.location || null,
    };
    const issuedAt = new Date().toISOString();
    try {
      target.click();
    } catch (error) {
      return {
        ok: true, outcome: "ACK_PENDING", mutationIssued: true,
        destination: mainContentContext().href || null, issuedAt,
        message: `npc_click_ambiguous:${safeString(error && error.message ? error.message : error, 180)}`,
        before: beforeState, after: null,
      };
    }
    const verifyTimeoutMs = Math.max(100, Math.min(5000, parseInt(payload.verifyTimeoutMs, 10) || 2000));
    const after = await adaptiveVerify(
      () => npcDialogSnapshot({ expectedName: expectedDialogName, expectedNpcId: expectedDataId }),
      (value) => value.ok && value.identityMatches === true,
      { timeoutMs: verifyTimeoutMs, fingerprint: (value) => `${value.snapshotId || ""}:${value.href || ""}:${value.identityMatches}` },
    );
    if (!after.ok || after.identityMatches !== true) {
      return {
        ok: true,
        outcome: "ACK_PENDING",
        mutationIssued: true,
        destination: after.href || mainContentContext().href || null,
        issuedAt,
        message: "npc_open_postcondition_failed",
        expectedName,
        expectedDialogName,
        expectedDataId,
        before: beforeState,
        after,
      };
    }
    return {
      ok: true,
      outcome: "CONFIRMED",
      mutationIssued: true,
      destination: after.href || mainContentContext().href || null,
      issuedAt,
      message: "npc_opened_confirmed",
      expectedName,
      expectedDialogName,
      expectedDataId,
      before: beforeState,
      after,
    };
  };
