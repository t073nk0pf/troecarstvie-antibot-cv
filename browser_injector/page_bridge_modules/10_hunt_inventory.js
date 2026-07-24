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

  const huntSnapshot = (compact = false) => {
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
      hunt: hunt
        ? compact
          ? {
              constructorName: safeString(hunt.constructor && hunt.constructor.name, 80),
              model: objectPreview(hunt.model, 40),
              controller: objectPreview(hunt.controller, 40),
            }
          : summarizeObject(hunt, "hunt", 4, new Set())
        : null,
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

  const ART_ALT_CACHE = new Map();

  // The game's metadata assignment is a JavaScript object, not necessarily a
  // flat JSON literal: `kind` is itself an object.  A non-greedy `/{.*?}/`
  // match therefore stops at the first nested brace and silently loses all
  // quest-item titles.  Extract one balanced object while respecting quoted
  // strings, then JSON-parse the whole value.
  const balancedObjectAt = (text, start) => {
    if (!text || text[start] !== "{") return null;
    let depth = 0;
    let quote = "";
    let escaped = false;
    for (let index = start; index < text.length; index += 1) {
      const char = text[index];
      if (quote) {
        if (escaped) escaped = false;
        else if (char === "\\") escaped = true;
        else if (char === quote) quote = "";
        continue;
      }
      if (char === "'" || char === '"') {
        quote = char;
      } else if (char === "{") {
        depth += 1;
      } else if (char === "}") {
        depth -= 1;
        if (depth === 0) return text.slice(start, index + 1);
      }
    }
    return null;
  };

  const artAltFromScripts = (key, sourceWin) => {
    const keyPattern = safeString(key, 120).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    if (!keyPattern) return null;
    const pattern = new RegExp(`(?:\\b|_)art_alt\\s*\\[\\s*['\"]${keyPattern}['\"]\\s*\\]\\s*=\\s*`);
    const windows = [];
    if (sourceWin) windows.push(sourceWin);
    walkWindows(window.top || window, "top", 4, new Set(), (win) => windows.push(win));
    const seen = new Set();
    for (const candidate of windows) {
      if (!candidate || seen.has(candidate)) continue;
      seen.add(candidate);
      try {
        const scripts = Array.from(candidate.document.querySelectorAll("script")).slice(0, 400);
        for (const script of scripts) {
          const text = safeString(script && script.textContent, 30000);
          const match = text.match(pattern);
          if (!match) continue;
          const objectText = balancedObjectAt(text, match.index + match[0].length);
          if (!objectText) continue;
          const parsed = JSON.parse(objectText);
          if (parsed && typeof parsed === "object") return parsed;
        }
      } catch (_) {}
    }
    return null;
  };

  const artAltForKey = (key, sourceWin) => {
    if (!key) return null;
    if (ART_ALT_CACHE.has(key)) return ART_ALT_CACHE.get(key);
    let result = null;
    const directWindows = [sourceWin, window, window.top || window];
    for (const candidate of directWindows) {
      try {
        if (candidate && candidate.art_alt && candidate.art_alt[key]) {
          result = candidate.art_alt[key];
          break;
        }
      } catch (_) {}
    }
    if (!result) {
      walkWindows(window.top || window, "top", 4, new Set(), (win) => {
        if (result) return;
        try {
          if (win.art_alt && win.art_alt[key]) result = win.art_alt[key];
        } catch (_) {}
      });
    }
    result = result || artAltFromScripts(key, sourceWin);
    // A temporary scan before the quest frame finishes loading must not
    // poison all subsequent snapshots for this bridge lifetime.
    if (result) ART_ALT_CACHE.set(key, result);
    return result;
  };

  const inventoryItemFromElement = (el, path, index, sourceWin) => {
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
    // Quest inventory cells use the legacy `cnt` attribute (rather than a
    // data-* attribute).  Missing it turns a visible stack of 10 trophies
    // into one item and keeps the gathering loop running.
    const count = parseInt(
      attr(el, "data-cnt") || attr(el, "data-count") || attr(el, "cnt") || attr(el, "count") || "1",
      10
    );
    let artAlt = null;
    try {
      const artAltKey = divId || (artikulId ? `AA_${artikulId}` : "");
      artAlt = artAltForKey(artAltKey, sourceWin);
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
      artAltDescription,
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

  const inventoryElementFromElement = (el, path, index, sourceWin) => ({
    el,
    item: inventoryItemFromElement(el, path, index, sourceWin),
  });

  const clickableElement = (el) => {
    try {
      if (el && typeof el.closest === "function") {
        return el.closest("a,button,input,[onclick]") || el;
      }
    } catch (_) {}
    return el;
  };

  const elementText = (el) => {
    let descendantLabels = "";
    try {
      descendantLabels = Array.from(el && el.querySelectorAll ? el.querySelectorAll("img[alt],img[title]") : [])
        .slice(0, 12)
        .flatMap((image) => [attr(image, "alt"), attr(image, "title")])
        .filter(Boolean)
        .join(" ");
    } catch (_) {}
    return safeString(
      [el && el.innerText, el && el.textContent, el && el.value, attr(el, "title"), attr(el, "alt"), descendantLabels, attr(el, "onclick")].join(" "),
      500
    );
  };

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
          output.push(inventoryElementFromElement(el, path, index, win));
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

  const selectQuestInventoryCategory = () => {
    let alreadyOpenCount = 0;
    walkWindows(window.top || window, "top", 4, new Set(), (win) => {
      try {
        const href = safeString(win.location && win.location.href, 500);
        if (/user_iframe\.php\?[^#]*\bgroup=4(?:&|$)/i.test(href)) alreadyOpenCount += 1;
      } catch (_) {}
    });
    if (alreadyOpenCount === 1) {
      return { dispatched: false, candidateCount: 1, message: "quest_inventory_category_already_open" };
    }
    const candidates = [];
    walkWindows(window.top || window, "top", 4, new Set(), (win, path) => {
      try {
        const href = safeString(win.location && win.location.href, 500).toLowerCase();
        const doc = win.document;
        const controls = doc && typeof doc.querySelectorAll === "function"
          ? Array.from(doc.querySelectorAll("#tab_4 a[href],#tab_4 [onclick],a[href*='user_iframe.php'][href*='group=4'],[onclick*='tab_click'][onclick*='4']" )).slice(0, 40)
          : [];
        controls.forEach((el) => {
          const text = safeString(
            (el && (el.innerText || el.textContent || el.value)) || attr(el, "title"),
            120
          ).replace(/\s+/g, " ").trim().toLowerCase();
          const target = `${safeString(attr(el, "href"), 240)} ${safeString(attr(el, "onclick"), 240)}`;
          if (
            !["квесты", "квестовые", "квестовые предметы"].includes(text)
            || !/(?:\bgroup=4\b|tab_click\s*\(\s*['\"]?4['\"]?\s*\))/i.test(target)
          ) return;
          candidates.push({ el, path, text });
        });
      } catch (_) {}
    });
    if (candidates.length !== 1) {
      return { dispatched: false, candidateCount: candidates.length, message: "quest_inventory_category_ambiguous" };
    }
    try {
      candidates[0].el.click();
      return {
        dispatched: true,
        candidateCount: 1,
        message: "quest_inventory_category_selected",
        framePath: candidates[0].path,
        text: candidates[0].text,
      };
    } catch (error) {
      return {
        dispatched: false,
        candidateCount: 1,
        message: `quest_inventory_category_click_error:${safeString(error && error.message ? error.message : error, 160)}`,
      };
    }
  };

  const backpackPageProof = () => {
    const pages = [];
    walkWindows(window.top || window, "top", 4, new Set(), (win, path) => {
      try {
        const href = safeString(win.location && win.location.href, 500);
        if (
          /\/user\.php\?[^#]*\bmode=personage(?:&|$)/i.test(href)
          && /(?:\?|&)submode=backpack(?:&|$)/i.test(href)
        ) pages.push({ path, href });
      } catch (_) {}
    });
    return {
      confirmed: pages.length === 1,
      pageCount: pages.length,
      framePath: pages.length === 1 ? pages[0].path : null,
      href: pages.length === 1 ? pages[0].href : null,
    };
  };

  const waitForBackpackPage = async (payload) => {
    const root = window.top || window;
    const timeoutMs = Math.max(500, Math.min(5000, toNumber(payload && payload.backpackWaitMs, 3000)));
    const startedAt = Date.now();
    let proof = backpackPageProof();
    while (!proof.confirmed && Date.now() - startedAt < timeoutMs) {
      await new Promise((resolve) => root.setTimeout(resolve, 100));
      proof = backpackPageProof();
    }
    return { ...proof, waitedMs: Date.now() - startedAt, timeoutMs };
  };

  const questInventoryCategoryProof = () => {
    const frames = [];
    walkWindows(window.top || window, "top", 4, new Set(), (win, path) => {
      try {
        const href = safeString(win.location && win.location.href, 500);
        if (/user_iframe\.php\?[^#]*\bgroup=4(?:&|$)/i.test(href)) frames.push({ path, href });
      } catch (_) {}
    });
    return {
      confirmed: frames.length === 1,
      frameCount: frames.length,
      framePath: frames.length === 1 ? frames[0].path : null,
      href: frames.length === 1 ? frames[0].href : null,
    };
  };

  const waitForQuestInventoryCategory = async (payload) => {
    const root = window.top || window;
    const timeoutMs = Math.max(500, Math.min(5000, toNumber(payload && payload.categoryWaitMs, 3000)));
    const startedAt = Date.now();
    let proof = questInventoryCategoryProof();
    while (!proof.confirmed && Date.now() - startedAt < timeoutMs) {
      await new Promise((resolve) => root.setTimeout(resolve, 150));
      proof = questInventoryCategoryProof();
    }
    return { ...proof, waitedMs: Date.now() - startedAt, timeoutMs };
  };

  const directQuestInventoryCategoryNavigation = () => {
    const candidates = [];
    walkWindows(window.top || window, "top", 4, new Set(), (win, path) => {
      try {
        const href = safeString(win.location && win.location.href, 500);
        if (
          !/\/user\.php\?[^#]*\bmode=personage(?:&|$)/i.test(href)
          || !/(?:\?|&)submode=backpack(?:&|$)/i.test(href)
        ) return;
        const userFrame = win.frames && win.frames["user_iframe"];
        if (userFrame) candidates.push({ win: userFrame, path: `${path}.user_iframe` });
      } catch (_) {}
    });
    if (candidates.length !== 1) {
      return { dispatched: false, candidateCount: candidates.length, message: "quest_inventory_frame_ambiguous" };
    }
    try {
      candidates[0].win.location.href = "user_iframe.php?group=4";
      return {
        dispatched: true,
        candidateCount: 1,
        framePath: candidates[0].path,
        message: "quest_inventory_frame_navigated",
      };
    } catch (error) {
      return {
        dispatched: false,
        candidateCount: 1,
        message: `quest_inventory_frame_navigation_error:${safeString(error && error.message ? error.message : error, 160)}`,
      };
    }
  };

  const exactQuestInventoryItems = (entries) => {
    const unique = new Map();
    entries.forEach((entry) => {
      const item = entry && entry.item;
      if (!item || !/^\s*квестовые предметы\s*$/iu.test(safeString(item.artAltKind, 120))) return;
      if (!safeString(item.artAltTitle, 220)) return;
      // A quest slot is exposed both by its table cell and its nested visual
      // element.  They have different DOM ids but the same metadata slot.
      // Prefer that stable slot id, otherwise one real trophy appears twice
      // and the Python guard correctly-but-unhelpfully treats the binding as
      // ambiguous.
      const identity = safeString(
        item.artAltSlot || `title:${safeString(item.artAltTitle, 220).toLocaleLowerCase("ru-RU")}`,
        120
      );
      if (!identity) return;
      const existing = unique.get(identity);
      // The same visible slot is observed through its cell and child node.
      // Keep the most informative representation; crucially, never sum
      // duplicate DOM observations into a false inventory quantity.
      if (!existing || Number(item.count) > Number(existing.count)) unique.set(identity, item);
    });
    return Array.from(unique.values());
  };

  const QUEST_INVENTORY_ITEM_LIMIT = 256;
  let inventorySnapshotRevision = 0;

  const questInventoryCategoryLoadDelayMs = (payload) => Math.max(
    0,
    Math.min(5000, toNumber(payload && payload.questCategoryLoadDelayMs, 1500))
  );

  const inventorySnapshot = async (payload) => {
    const root = window.top || window;
    const names = normalizeNeedleList(payload && payload.names);
    const open = payload && payload.open != null ? Boolean(payload.open) : true;
    const category = safeString(payload && payload.category, 40).toLowerCase() || null;
    const backpackMessage = open ? openBackpack() : "not_opened";
    const backpackProof = open
      ? await waitForBackpackPage(payload)
      : backpackPageProof();
    if (open && backpackProof.confirmed && inventoryOpenDelayMs(payload) > 0) {
      await new Promise((resolve) => root.setTimeout(resolve, inventoryOpenDelayMs(payload)));
    }
    const categoryResult = category === "quest"
      ? !open || backpackProof.confirmed || questInventoryCategoryProof().confirmed
        ? selectQuestInventoryCategory()
        : { dispatched: false, candidateCount: 0, message: "quest_inventory_backpack_unconfirmed" }
      : { dispatched: false, candidateCount: 0, message: "inventory_category_not_requested" };
    const initialCategoryPayload = {
      ...(payload || {}),
      categoryWaitMs: Math.min(900, toNumber(payload && payload.categoryWaitMs, 3000)),
    };
    let categoryProof = category === "quest"
      ? await waitForQuestInventoryCategory(initialCategoryPayload)
      : { confirmed: category == null, frameCount: 0, framePath: null, href: null };
    let categoryFallback = null;
    if (category === "quest" && !categoryProof.confirmed && backpackProof.confirmed) {
      categoryFallback = directQuestInventoryCategoryNavigation();
      if (categoryFallback.dispatched) {
        categoryProof = await waitForQuestInventoryCategory(payload);
      }
    }
    const categoryLoadDelayMs = category === "quest" && categoryProof.confirmed
      ? questInventoryCategoryLoadDelayMs(payload)
      : 0;
    if (categoryLoadDelayMs > 0) {
      await new Promise((resolve) => root.setTimeout(resolve, categoryLoadDelayMs));
    }
    const entries = collectInventoryElements();
    const questItems = exactQuestInventoryItems(entries);
    const categoryConfirmed = categoryProof.confirmed;
    const snapshotItems = category === "quest"
      ? questItems
      : entries.map((entry) => entry.item);
    inventorySnapshotRevision = Math.max(Date.now(), inventorySnapshotRevision + 1);
    const generatedAt = new Date().toISOString();
    return {
      ok: true,
      message: "inventory_snapshot",
      bridgeVersion: BRIDGE_VERSION,
      snapshotId: `inventory-${Date.now()}-${inventorySnapshotRevision}`,
      generatedAt,
      revision: inventorySnapshotRevision,
      backpackMessage,
      backpackProof,
      itemCount: snapshotItems.length,
      names,
      category,
      categoryConfirmed,
      categoryResult,
      categoryProof,
      categoryFallback,
      categoryLoadDelayMs,
      candidates: inventoryDebugItems(entries, names),
      items: snapshotItems.slice(0, QUEST_INVENTORY_ITEM_LIMIT),
      // Compatibility for clients that still consume the legacy field.
      sample: snapshotItems.slice(0, QUEST_INVENTORY_ITEM_LIMIT),
      truncated: snapshotItems.length > QUEST_INVENTORY_ITEM_LIMIT,
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

  const useQuestInventoryItem = async (payload) => {
    const expectedName = safeString(payload && payload.expectedName, 220).trim();
    if (!expectedName) return { ok: false, message: "quest_item_name_missing" };
    const before = await inventorySnapshot({
      ...(payload || {}), open: true, category: "quest", names: [expectedName],
    });
    if (!before || before.ok !== true || before.categoryConfirmed !== true) {
      return { ok: false, message: "quest_item_inventory_unconfirmed", before };
    }
    const normalized = normalizeNpcName(expectedName);
    const matches = (Array.isArray(before.items) ? before.items : []).filter((item) =>
      normalizeNpcName(safeString(item && item.artAltTitle, 220)) === normalized
    );
    if (matches.length !== 1) {
      return { ok: matches.length === 0, message: matches.length ? "quest_item_ambiguous" : "quest_item_absent", candidateCount: matches.length, before };
    }
    const item = matches[0];
    if (!item.artikulId) return { ok: false, message: "quest_item_artikul_missing", item, before };
    const expectedArtikulId = safeString(payload && payload.expectedArtikulId, 80).trim();
    if (expectedArtikulId && safeString(item.artikulId, 80) !== expectedArtikulId) {
      return { ok: false, message: "quest_item_artikul_mismatch", item, before };
    }
    const countBefore = Math.max(0, toNumber(item.count, 0));
    const useResult = await requestInventoryArtifactUse(item);
    if (!useResult || useResult.ok !== true) return { ok: false, message: "quest_item_use_failed", item, before, useResult };
    return {
      ok: true, message: "quest_item_use_acknowledged",
      requiresQuestReconciliation: true,
      expectedName, item, countBefore, useResult,
    };
  };

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
    const useIfMissing = payload && payload.useIfResourcesMissing != null ? Boolean(payload.useIfResourcesMissing) : false;
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
    const useIfMissing = payload && payload.useIfResourcesMissing != null ? Boolean(payload.useIfResourcesMissing) : false;
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
      huntMessage = await openHunt({ verifyTimeoutMs: 2000 });
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
