  // Read-only, bounded observation of exact server collection-progress lines.
  // The Python runtime still binds the extracted resource to one authoritative
  // quest objective and treats the line only as a reason to refresh it.
  const questChatKnownEvents = new Map();
  const questChatKnownOrder = [];
  let questChatBaselineReady = false;

  const questChatResource = (value) => {
    const raw = safeString(value, 500);
    const marker = raw.search(/Вы\s+набрали\s+(?:достаточное|необходимое)\s+количество\s+/iu);
    if (marker < 0) return null;
    const prefix = raw.slice(Math.max(0, marker - 12), marker);
    const visibleTime = prefix.match(/(\d{1,2}:\d{2})\s*$/);
    const match = raw.slice(marker).match(
      /^(Вы\s+набрали\s+(?:достаточное|необходимое)\s+количество\s+(.+?)\s*[.!])(?=\s|$)/iu
    );
    if (!match) return null;
    const text = safeString(match[1], 500);
    const resource = safeString(match[2], 220).replace(/[.!]+$/, "").trim();
    return resource.length >= 2
      ? { text, resource, discriminator: visibleTime ? `time:${visibleTime[1]}` : "" }
      : null;
  };

  const questChatResources = (value) => {
    const raw = String(value || "");
    const marker = /Вы\s+набрали\s+(?:достаточное|необходимое)\s+количество\s+/giu;
    const parsed = [];
    let match;
    while ((match = marker.exec(raw)) !== null && parsed.length < 40) {
      const item = questChatResource(
        raw.slice(Math.max(0, match.index - 12), Math.min(raw.length, match.index + 500))
      );
      if (item) parsed.push(item);
      if (marker.lastIndex <= match.index) marker.lastIndex = match.index + 1;
    }
    return parsed;
  };

  const questChatEventId = (frameKey, sourceKey, text) => {
    const value = `${frameKey}:${sourceKey}:${text}`;
    let hash = 2166136261;
    for (let offset = 0; offset < value.length; offset += 1) {
      hash ^= value.charCodeAt(offset);
      hash = Math.imul(hash, 16777619) >>> 0;
    }
    return `chat-${hash.toString(16).padStart(8, "0")}`;
  };

  const rememberQuestChatEvent = (eventId, firstSeenAt, deliverable) => {
    if (questChatKnownEvents.has(eventId)) return questChatKnownEvents.get(eventId);
    // Baseline history is never deliverable.  Events first observed after the
    // baseline stay deliverable for their bounded observedAt lifetime so a
    // transiently unavailable quest lease cannot lose a one-shot chat line.
    // Python owns final age validation and idempotent consumption.
    const record = { firstSeenAt, deliverable: deliverable === true };
    questChatKnownEvents.set(eventId, record);
    questChatKnownOrder.push(eventId);
    while (questChatKnownOrder.length > 256) {
      questChatKnownEvents.delete(questChatKnownOrder.shift());
    }
    return record;
  };

  const questChatNodeIdentity = (node) => {
    if (!node || typeof node.getAttribute !== "function") return "";
    const explicit = [
      node.getAttribute("data-message-id"),
      node.getAttribute("data-msg-id"),
      node.getAttribute("data-id"),
      node.id,
    ].map((value) => safeString(value, 160)).find(Boolean);
    return explicit ? `dom:${explicit}` : "";
  };

  const questChatProgressSnapshot = (metadata = {}) => {
    const generatedAt = safeString(metadata.generatedAt, 80) || new Date().toISOString();
    const candidates = [];
    let chatFrameCount = 0;
    walkWindows(window.top || window, "top", 4, new Set(), (win, path) => {
      try {
        const name = safeString(win.name, 80).toLowerCase();
        const href = safeString(win.location && win.location.href, 500);
        const body = win.document && win.document.body;
        if (!body) return;
        const bodyText = String(body.innerText || body.textContent || "");
        const boundedTail = bodyText.slice(-24000);
        const namedChatFrame =
          /(?:^|_)(?:chat|chat_frame|chat_main)(?:$|_)/i.test(name)
          || /\/(?:chat|ch)\.php(?:\?|$)/i.test(href);
        // Some live layouts expose the visible chat through an anonymous or
        // generically named nested frame.  Recognize it by the exact bounded
        // server line instead of depending solely on frame names/URLs.
        const boundedResources = questChatResources(boundedTail);
        const hasCollectionLine = boundedResources.length > 0;
        if (!namedChatFrame && !hasCollectionLine) return;
        chatFrameCount += 1;
        const frameKey = name || href || path;
        let matchedStableNode = false;
        if (typeof body.querySelectorAll === "function") {
          const nodes = Array.from(body.querySelectorAll(
            "[data-message-id],[data-msg-id],[data-id],.chat-message,[id^='message_'],[id^='msg_']"
          )).slice(-120);
          nodes.forEach((node) => {
            const identity = questChatNodeIdentity(node);
            const parsed = questChatResource(node && (node.innerText || node.textContent));
            if (!identity || !parsed) return;
            matchedStableNode = true;
            candidates.push({
              eventId: questChatEventId(
                frameKey,
                `${identity}:${parsed.discriminator || "no-visible-time"}`,
                parsed.text
              ),
              text: parsed.text,
              resource: parsed.resource,
            });
          });
        }
        if (!matchedStableNode) {
          const occurrences = new Map();
          boundedResources.slice(-120).forEach((parsed) => {
              const occurrenceKey = `${parsed.discriminator || "no-visible-time"}:${parsed.text}`;
              const occurrence = (occurrences.get(occurrenceKey) || 0) + 1;
              occurrences.set(occurrenceKey, occurrence);
              candidates.push({
                eventId: questChatEventId(
                  frameKey,
                  `fallback:${parsed.discriminator || "no-visible-time"}:${parsed.text}:${occurrence}`,
                  parsed.text
                ),
                text: parsed.text,
                resource: parsed.resource,
              });
          });
        }
      } catch (_) {}
    });
    const observations = candidates.slice(-40).map((candidate) => {
      const record = rememberQuestChatEvent(
        candidate.eventId,
        generatedAt,
        questChatBaselineReady
      );
      return {
        ...candidate,
        observedAt: record.firstSeenAt,
        isNew: record.deliverable,
      };
    });
    questChatBaselineReady = true;
    const truncated = candidates.length > 40;
    return {
      status: chatFrameCount > 0 ? "available" : "not_loaded",
      reason: chatFrameCount > 0 ? null : "chat_frame_not_loaded",
      source: { framePath: "chat", href: null },
      data: {
        loadStatus: chatFrameCount > 0 ? "loaded" : "not_loaded",
        snapshotId: metadata.snapshotId || null,
        generatedAt,
        observations,
        truncated,
      },
    };
  };
