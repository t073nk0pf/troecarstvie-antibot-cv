  // Read-only observation only. This module deliberately exposes no purchase
  // command and never invokes click(), submit(), navigation, or page functions.
  const procurementObservationSnapshot = (metadata = {}) => {
    const normalizedMetadata = metadata && typeof metadata === "object" && !Array.isArray(metadata)
      ? metadata
      : {};
    const context = mainContentContext();
    const generatedAtProvided = Object.prototype.hasOwnProperty.call(normalizedMetadata, "generatedAt");
    const generatedAt = generatedAtProvided
      ? safeString(normalizedMetadata.generatedAt, 80)
      : new Date().toISOString();
    const strictUtcTimestamp = (value) => {
      const match = safeString(value, 80).match(
        /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})\.(\d{3})Z$/
      );
      if (!match) return false;
      const parts = match.slice(1).map((part) => Number(part));
      const [year, month, day, hour, minute, second, millisecond] = parts;
      if (year < 100 || month < 1 || month > 12 || day < 1 || hour > 23 || minute > 59 || second > 59) {
        return false;
      }
      const parsed = new Date(Date.UTC(year, month - 1, day, hour, minute, second, millisecond));
      return Number.isFinite(parsed.getTime()) &&
        parsed.getUTCFullYear() === year && parsed.getUTCMonth() === month - 1 &&
        parsed.getUTCDate() === day && parsed.getUTCHours() === hour &&
        parsed.getUTCMinutes() === minute && parsed.getUTCSeconds() === second &&
        parsed.getUTCMilliseconds() === millisecond && parsed.toISOString() === value;
    };
    const generatedAtValid = strictUtcTimestamp(generatedAt);
    const snapshotId = safeString(normalizedMetadata.snapshotId, 120) || null;
    const transportClientId = safeString(normalizedMetadata.transportClientId, 180) || null;
    const expectedCharacterName = safeString(normalizedMetadata.expectedCharacterName, 120) || null;
    const observedCharacterName = safeString(normalizedMetadata.observedCharacterName, 120) || null;
    const characterStatus = safeString(normalizedMetadata.characterStatus, 40) || null;
    const questId = safeString(normalizedMetadata.questId, 120) || null;
    const questFingerprint = safeString(normalizedMetadata.questFingerprint, 240) || null;
    const binding = {
      complete: Boolean(
        transportClientId && expectedCharacterName && observedCharacterName &&
        characterStatus === "available" && observedCharacterName === expectedCharacterName &&
        questId && questFingerprint
      ),
      transportClientId,
      expectedCharacterName,
      observedCharacterName,
      characterStatus,
      requestScope: { questId, questFingerprint },
    };
    const href = safeString(context.href, 500);
    const pathMatch = href.match(/^https:\/\/([^/?#]+)(\/[^?#]*)(?:[?#]|$)/i);
    const pathname = pathMatch && pathMatch[1].toLowerCase() === "3kingdoms.ru" ? pathMatch[2] : null;
    const source = context.pageKind === "shop" && pathname === "/auction.php"
      ? "auction"
      : context.pageKind === "shop" && pathname === "/shop.php"
        ? "npc_shop"
        : null;
    const empty = (status, reason) => ({
      status,
      reason,
      bridgeVersion: typeof BRIDGE_VERSION === "string" ? BRIDGE_VERSION : null,
      snapshotId,
      generatedAt,
      binding,
      source: { framePath: "main", href },
      data: {
        source,
        shopId: null,
        currency: null,
        balance: null,
        snapshotId,
        generatedAt,
        transportClientId,
        expectedCharacterName,
        observedCharacterName,
        characterStatus,
        requestScope: { questId, questFingerprint },
        complete: false,
        listings: [],
        truncated: false,
      },
    });
    if (!context.doc || !source) return empty("not_loaded", "procurement_page_not_loaded");

    const boundedDescendants = (root, limit = 600) => {
      const pending = root ? [root] : [];
      const nodes = [];
      while (pending.length && nodes.length < limit) {
        const node = pending.shift();
        if (!node) continue;
        nodes.push(node);
        let children = null;
        let childCount = 0;
        try {
          children = node.children || null;
          childCount = children && Number.isSafeInteger(children.length) && children.length >= 0
            ? children.length
            : 0;
        } catch (_) {
          return { nodes, truncated: true };
        }
        const remainingBudget = limit - nodes.length - pending.length;
        if (childCount > remainingBudget) return { nodes, truncated: true };
        for (let index = 0; index < childCount; index += 1) {
          let child = null;
          try {
            child = children[index];
          } catch (_) {
            return { nodes, truncated: true };
          }
          if (child) pending.push(child);
        }
      }
      return { nodes, truncated: pending.length > 0 };
    };
    const rootScan = boundedDescendants(context.doc.body, 600);
    const containers = rootScan.nodes.filter((node) => {
      const id = safeString(attr(node, "id"), 80).toLowerCase();
      const classes = safeString(attr(node, "class"), 160).toLowerCase().split(/\s+/);
      return id === "auction" || id === "shop" ||
        Boolean(attr(node, "data-auction-id")) || Boolean(attr(node, "data-shop-id")) ||
        classes.includes("auction-list") || classes.includes("shop-list");
    });
    if (rootScan.truncated) return empty("partial", "procurement_root_scan_truncated");
    if (containers.length !== 1) return empty("partial", "procurement_container_ambiguous");
    const container = containers[0];
    const shopId = safeString(
      attr(container, source === "auction" ? "data-auction-id" : "data-shop-id") ||
      attr(container, "id"),
      120
    ) || null;
    const containerScan = boundedDescendants(container, 600);
    const balanceNodes = containerScan.nodes.filter((node) =>
      Boolean(attr(node, "data-balance")) && Boolean(attr(node, "data-currency"))
    );
    const normalizeCurrency = (value) => {
      const token = safeString(value, 40).toLocaleLowerCase("ru-RU").trim();
      if (/^(?:round|кругляш(?:и|ей)?)$/.test(token)) return "round";
      if (/^(?:premium|gold|gems?|золото|золотые|бриллианты?)$/.test(token)) return "premium";
      return null;
    };
    if (balanceNodes.length !== 1) return empty("partial", "procurement_balance_ambiguous");
    const currency = normalizeCurrency(attr(balanceNodes[0], "data-currency"));
    const strictDecimal = (value) => {
      const raw = safeString(value, 40);
      if (!/^(?:0|[1-9]\d*)$/.test(raw)) return null;
      const parsed = Number(raw);
      return Number.isSafeInteger(parsed) ? parsed : null;
    };
    const balance = strictDecimal(attr(balanceNodes[0], "data-balance"));

    const nodes = containerScan.nodes.filter((node) =>
      Boolean(attr(node, "data-listing-id")) || Boolean(attr(node, "data-item-id")) ||
      Boolean(attr(node, "data-item-name")) || Boolean(attr(node, "data-unit-price")) ||
      Boolean(attr(node, "data-available-quantity"))
    );
    const truncated = containerScan.truncated || nodes.length > 100;
    const listings = [];
    const seen = new Set();
    for (const node of nodes.slice(0, 100)) {
      const listingId = safeString(attr(node, "data-listing-id"), 120) || null;
      const itemId = safeString(attr(node, "data-item-id"), 120) || null;
      const itemName = safeString(attr(node, "data-item-name"), 180) || null;
      const unitPrice = strictDecimal(attr(node, "data-unit-price"));
      const availableQuantity = strictDecimal(attr(node, "data-available-quantity"));
      const listingCurrency = normalizeCurrency(attr(node, "data-currency"));
      if (!listingId || seen.has(listingId)) {
        return empty("partial", "procurement_listing_identity_ambiguous");
      }
      seen.add(listingId);
      const safeTotal = Number.isSafeInteger(unitPrice) && unitPrice > 0 &&
        Number.isSafeInteger(availableQuantity) && availableQuantity > 0 &&
        unitPrice <= Math.floor(Number.MAX_SAFE_INTEGER / availableQuantity)
        ? unitPrice * availableQuantity
        : null;
      listings.push({
        listingId,
        itemId,
        itemName,
        unitPrice: Number.isSafeInteger(unitPrice) && unitPrice > 0 ? unitPrice : null,
        totalPrice: safeTotal,
        availableQuantity: Number.isSafeInteger(availableQuantity) && availableQuantity > 0
          ? availableQuantity
          : null,
        currency: listingCurrency,
      });
    }
    const complete = Boolean(
      snapshotId && generatedAtValid && binding.complete &&
      shopId && currency === "round" && Number.isSafeInteger(balance) && balance >= 0 &&
      listings.length && !truncated && listings.every((listing) =>
        listing.itemId && listing.itemName && listing.unitPrice && listing.totalPrice &&
        listing.availableQuantity && listing.currency
      )
    );
    return {
      status: complete ? "available" : "partial",
      reason: complete ? null : "procurement_observation_incomplete",
      bridgeVersion: typeof BRIDGE_VERSION === "string" ? BRIDGE_VERSION : null,
      snapshotId,
      generatedAt,
      binding,
      source: { framePath: "main", href },
      data: {
        source, shopId, currency, balance, snapshotId, generatedAt,
        transportClientId, expectedCharacterName, observedCharacterName, characterStatus,
        requestScope: { questId, questFingerprint },
        complete, listings, truncated,
      },
    };
  };
