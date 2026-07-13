  const instancePrimitiveFields = (value) => {
    const result = {};
    if (!value || typeof value !== "object") return result;
    for (const key of Object.keys(value).slice(0, 120)) {
      try {
        const field = value[key];
        if (field == null || ["string", "number", "boolean"].includes(typeof field)) {
          result[key] = typeof field === "string" ? safeString(field, 500) : field;
        }
      } catch (_) {}
    }
    return result;
  };

  let lastInstanceEntranceObservation = null;

  const instanceItemName = (item) =>
    safeString(item && (item.name || item.title || item.label), 180).trim();

  const instanceItemHref = (item) =>
    safeString(item && (item.href || item.url || item.link), 500).trim();

  const instanceItemType = (item) =>
    safeString(item && (item.type || item.mode || item.kind), 80).trim().toLowerCase();

  const looksLikeInstanceItem = (item) => {
    const type = instanceItemType(item);
    const href = instanceItemHref(item).toLowerCase();
    return /(?:^|_)(?:inst|instance)(?:$|_)/i.test(type)
      || /(?:instance|inst_|inst\.php|instance\.php)/i.test(href);
  };

  const instanceEntranceSnapshot = (payload = {}) => {
    const context = mainContentContext();
    const expectedName = safeString(payload && payload.expectedName, 180).trim();
    const areaObject = context.win && context.win.area ? context.win.area : null;
    const areaModel = areaObject && areaObject.model && areaObject.model.area
      ? areaObject.model.area
      : null;
    const items = areaModel && Array.isArray(areaModel.items) ? areaModel.items : [];
    const allItems = items.slice(0, 100).map((item, index) => ({
      index,
      name: instanceItemName(item) || null,
      type: instanceItemType(item) || null,
      href: instanceItemHref(item) || null,
      fields: instancePrimitiveFields(item),
    }));
    const namedItems = expectedName
      ? allItems.filter((item) => exactLabelMatches(item.name, expectedName))
      : [];
    const candidates = expectedName
      ? namedItems.filter((item) => looksLikeInstanceItem(item.fields))
      : allItems.filter((item) => looksLikeInstanceItem(item.fields));
    const snapshotId = `instance-entrance-${Date.now().toString(36)}-${(++stateSnapshotSequence).toString(36)}`;
    const result = {
      ok: context.pageKind === "area" && Boolean(areaModel),
      message: context.pageKind === "area" && areaModel
        ? "instance_entrance_snapshot"
        : "instance_entrance_page_missing",
      snapshotId,
      generatedAt: new Date().toISOString(),
      href: context.href,
      pageKind: context.pageKind,
      currentLocation: safeString(areaModel && areaModel.title, 180) || null,
      currentLocationId: safeString(
        areaObject && areaObject.controller && areaObject.controller.compass
          && areaObject.controller.compass.data && areaObject.controller.compass.data.location,
        80
      ) || null,
      expectedName: expectedName || null,
      namedItemCount: namedItems.length,
      candidateCount: candidates.length,
      candidates,
      allItems,
    };
    lastInstanceEntranceObservation = result;
    return result;
  };

  const enterInstance = (payload = {}) => {
    const expectedName = safeString(payload && payload.expectedName, 180).trim();
    const expectedSnapshotId = safeString(payload && payload.expectedSnapshotId, 180).trim();
    if (!expectedName) return { ok: false, message: "instance_name_missing" };
    if (!expectedSnapshotId) return { ok: false, message: "instance_snapshot_id_missing" };
    const observed = lastInstanceEntranceObservation;
    if (!observed || observed.snapshotId !== expectedSnapshotId) {
      return { ok: false, message: "instance_snapshot_stale" };
    }
    const generatedAtMs = Date.parse(observed.generatedAt || "");
    if (!Number.isFinite(generatedAtMs) || Date.now() - generatedAtMs > 5000) {
      return { ok: false, message: "instance_snapshot_expired" };
    }
    const fresh = instanceEntranceSnapshot({ expectedName });
    if (!fresh.ok || fresh.candidateCount !== 1 || fresh.namedItemCount !== 1) {
      return {
        ok: false,
        message: fresh.candidateCount ? "instance_entrance_ambiguous" : "instance_entrance_missing",
        snapshot: fresh,
      };
    }
    if (
      observed.currentLocationId !== fresh.currentLocationId
      || observed.candidateCount !== 1
      || !exactLabelMatches(observed.candidates[0] && observed.candidates[0].name, expectedName)
    ) {
      return { ok: false, message: "instance_entrance_changed", snapshot: fresh };
    }
    const candidate = fresh.candidates[0];
    const type = safeString(candidate.type, 80).toLowerCase();
    const href = safeString(candidate.href, 500).replace(/&amp;/g, "&");
    if (!/^(?:inst|instance)$/i.test(type)) {
      return { ok: false, message: "instance_entrance_type_rejected", type, snapshot: fresh };
    }
    if (
      !/^\/(?:action_run|instance|inst|hunt)\.php(?:\?|$)/i.test(href)
      || /^(?:javascript|data):/i.test(href)
    ) {
      return { ok: false, message: "instance_entrance_href_rejected", href, snapshot: fresh };
    }
    const context = mainContentContext();
    if (context.pageKind !== "area" || !context.win || !context.win.location) {
      return { ok: false, message: "instance_entrance_page_missing", snapshot: fresh };
    }
    const delayMs = Math.max(25, Math.min(250, Number(payload.navigationDelayMs) || 75));
    context.win.setTimeout(() => {
      try {
        context.win.location.href = href;
      } catch (_) {}
    }, delayMs);
    return {
      ok: true,
      message: "instance_entry_submitted",
      submitted: true,
      expectedName,
      currentLocationId: fresh.currentLocationId,
      entrance: candidate,
      delayMs,
    };
  };
