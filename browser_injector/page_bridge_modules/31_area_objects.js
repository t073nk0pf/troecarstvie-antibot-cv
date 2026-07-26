  // Bounded discovery and exact interaction for the illustrated objects on an
  // area page.  These objects are usually empty image/onclick elements, so a
  // text-only DOM collector cannot see them.
  let areaObjectSnapshotSequence = 0;
  const areaObjectSnapshots = new Map();

  const areaObjectDocumentRevision = (context) => {
    const doc = context && context.doc;
    const root = doc && doc.documentElement;
    const marker = safeString(
      [
        context && context.href,
        root && root.childElementCount,
        doc && doc.body && doc.body.childElementCount,
        doc && doc.body && (doc.body.innerText || doc.body.textContent || "").length,
      ].join("|"),
      300,
    );
    return `area-${marker}`;
  };

  const areaObjectRect = (element) => {
    try {
      const rect = element && element.getBoundingClientRect ? element.getBoundingClientRect() : null;
      if (!rect) return null;
      const width = Math.round(Number(rect.width) || 0);
      const height = Math.round(Number(rect.height) || 0);
      const x = Math.round(Number(rect.left) || 0);
      const y = Math.round(Number(rect.top) || 0);
      if (width < 8 || height < 8 || width > 900 || height > 700) return null;
      return { x, y, width, height };
    } catch (_) {
      return null;
    }
  };

  const areaObjectDescriptor = (context, element, index) => {
    if (!element || !elementIsVisible(context.win, element)) return null;
    const rect = areaObjectRect(element);
    if (!rect) return null;
    const tag = safeString(element.tagName, 24).toLowerCase();
    const onclick = safeString(attr(element, "onclick"), 600);
    const href = safeString(attr(element, "href"), 500);
    const style = safeString(attr(element, "style"), 600);
    const title = safeString(
      [attr(element, "title"), attr(element, "alt"), elementText(element)].join(" "),
      220,
    );
    const image = tag === "img" ? element : element.querySelector && element.querySelector("img");
    const imageSrc = safeString(image && attr(image, "src"), 300);
    const marker = `${onclick} ${href} ${style} ${title} ${imageSrc}`.toLowerCase();
    // Navigator and ordinary area links may also be image based.  They are
    // never part of a bounded object-search pass.
    if (!onclick && !href) return null;
    if (/navigator\.php|showmsg\s*\(\s*['\"]navigator|area\.php|location_id=|compass/i.test(marker)) {
      return null;
    }
    if (/проложить\s+путь|куда\s+хотите\s+перейти|время\s+до\s+перехода/i.test(marker)) {
      return null;
    }
    const fingerprint = safeString(
      `${tag}|${attr(element, "id") || ""}|${attr(element, "class") || ""}|${onclick}|${href}|${imageSrc}|${rect.x},${rect.y},${rect.width},${rect.height}`,
      1400,
    );
    return {
      candidateId: `area-object-${index}`,
      fingerprint,
      tag,
      id: safeString(attr(element, "id"), 120) || null,
      className: safeString(attr(element, "class"), 180) || null,
      title: title || null,
      onclick: onclick || null,
      href: href || null,
      imageSrc: imageSrc || null,
      rect,
      requiresConfirmation: /confirm\s*\(|\bconfirm\b/i.test(marker),
    };
  };

  const areaObjectSnapshot = () => {
    const context = mainContentContext();
    const generatedAt = new Date().toISOString();
    areaObjectSnapshotSequence = (areaObjectSnapshotSequence + 1) % 1000000;
    if (context.pageKind !== "area" || !context.doc) {
      return { ok: false, message: "area_object_page_missing", generatedAt, href: context.href };
    }
    const candidates = [];
    const seen = new Set();
    const elements = Array.from(
      context.doc.querySelectorAll("[onclick],a[href],area[href],img[onclick],input[onclick]")
    ).slice(0, 1000);
    for (const element of elements) {
      const descriptor = areaObjectDescriptor(context, element, candidates.length);
      if (!descriptor || seen.has(descriptor.fingerprint)) continue;
      seen.add(descriptor.fingerprint);
      candidates.push({ descriptor, element });
      if (candidates.length >= 24) break;
    }
    const revision = areaObjectDocumentRevision(context);
    const snapshotId = `area-objects-${Date.now().toString(36)}-${areaObjectSnapshotSequence.toString(36)}`;
    areaObjectSnapshots.set(snapshotId, {
      href: context.href,
      documentRevision: revision,
      candidates,
      expiresAt: Date.now() + 15000,
    });
    if (areaObjectSnapshots.size > 12) {
      for (const [key, value] of areaObjectSnapshots) {
        if (key !== snapshotId && (!value || value.expiresAt <= Date.now())) areaObjectSnapshots.delete(key);
      }
    }
    return {
      ok: true,
      message: "area_objects_observed",
      snapshotId,
      generatedAt,
      href: context.href,
      documentRevision: revision,
      candidates: candidates.map((item) => item.descriptor),
      truncated: elements.length >= 1000,
    };
  };

  const inspectAreaObject = async (payload = {}) => {
    const expectedSnapshotId = safeString(payload.expectedSnapshotId, 160);
    const candidateId = safeString(payload.candidateId, 80);
    const expectedFingerprint = safeString(payload.expectedFingerprint, 1400);
    const cached = areaObjectSnapshots.get(expectedSnapshotId);
    if (!expectedSnapshotId || !candidateId || !expectedFingerprint || !cached) {
      return { ok: false, message: "area_object_snapshot_missing" };
    }
    const context = mainContentContext();
    if (context.pageKind !== "area" || !context.doc || cached.expiresAt <= Date.now()) {
      return { ok: false, message: "area_object_snapshot_stale" };
    }
    if (cached.href !== context.href || cached.documentRevision !== areaObjectDocumentRevision(context)) {
      return { ok: false, message: "area_object_snapshot_context_changed" };
    }
    const candidate = cached.candidates.find((item) =>
      item.descriptor.candidateId === candidateId && item.descriptor.fingerprint === expectedFingerprint
    );
    if (!candidate || !candidate.element || !candidate.element.isConnected) {
      return { ok: false, message: "area_object_candidate_missing" };
    }
    if (candidate.descriptor.requiresConfirmation) {
      return { ok: false, message: "area_object_confirmation_required", candidate: candidate.descriptor };
    }
    const rect = candidate.descriptor.rect;
    const center = { x: rect.x + Math.max(1, Math.floor(rect.width / 2)), y: rect.y + Math.max(1, Math.floor(rect.height / 2)) };
    try {
      for (const type of ["mouseover", "mousedown", "mouseup", "click"]) {
        candidate.element.dispatchEvent(new MouseEvent(type, {
          bubbles: true, cancelable: true, view: context.win,
          clientX: center.x, clientY: center.y, screenX: center.x, screenY: center.y,
        }));
      }
    } catch (error) {
      return { ok: false, message: `area_object_click_error:${safeString(error && error.message ? error.message : error, 180)}` };
    }
    return new Promise((resolve) => setTimeout(() => {
      const after = mainContentContext();
      const text = safeString(after.doc && after.doc.body && (after.doc.body.innerText || after.doc.body.textContent), 1800);
      resolve({
        ok: true,
        message: "area_object_click_dispatched",
        candidate: candidate.descriptor,
        href: after.href,
        pageKind: after.pageKind,
        visibleText: text || null,
      });
    }, 180));
  };
