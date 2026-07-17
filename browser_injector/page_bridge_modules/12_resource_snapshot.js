  // Read-only resource discovery is kept separate from hunt/inventory actions.
  const resourceKeyword = (text) =>
    /жизн|здоров|удал|удаль|health|life|hp|mana|mp|prowess|bravery|stamina|vigor|energy/i.test(text);

  const parseResourcePercent = (text, kind) => {
    const value = safeString(text, 500).replace(",", ".");
    const patterns = kind === "health"
      ? [/(?:жизн(?:ь|и)?|здоров(?:ье|ья)?|health|life|hp)[^0-9]{0,40}([0-9]+(?:\.[0-9]+)?)\s*%/i, /(?:жизн(?:ь|и)?|здоров(?:ье|ья)?|health|life|hp)[^0-9]{0,40}([0-9]+)\s*\/\s*([0-9]+)/i]
      : [/(?:удал(?:ь|и)?|mana|mp|prowess|bravery|stamina|vigor|energy)[^0-9]{0,40}([0-9]+(?:\.[0-9]+)?)\s*%/i, /(?:удал(?:ь|и)?|mana|mp|prowess|bravery|stamina|vigor|energy)[^0-9]{0,40}([0-9]+)\s*\/\s*([0-9]+)/i];
    for (const pattern of patterns) {
      const match = value.match(pattern);
      if (!match) continue;
      if (match[2]) {
        const current = Number(match[1]); const max = Number(match[2]);
        if (Number.isFinite(current) && Number.isFinite(max) && max > 0) return Math.max(0, Math.min(100, (current / max) * 100));
      }
      const percent = Number(match[1]);
      if (Number.isFinite(percent)) return Math.max(0, Math.min(100, percent));
    }
    return null;
  };

  const resourcePercentFromCandidate = (candidate, kind) => {
    const text = `${candidate.path || ""} ${candidate.name || ""} ${candidate.value == null ? "" : candidate.value}`;
    const parsed = parseResourcePercent(text, kind);
    if (parsed != null) return parsed;
    const lowerPath = `${candidate.path || ""}.${candidate.name || ""}`.toLowerCase();
    const number = Number(candidate.value);
    if (!Number.isFinite(number)) return null;
    const looksPercent = number >= 0 && number <= 100 && /percent|pct|proc|rate|ratio|prc|%/.test(lowerPath);
    const healthName = /жизн|здоров|health|life|hp/.test(lowerPath);
    const prowessName = /удал|mana|mp|prowess|bravery|stamina|vigor|energy/.test(lowerPath);
    return kind === "health" && healthName && looksPercent ? number : kind === "prowess" && prowessName && looksPercent ? number : null;
  };

  const collectResourceCandidatesFromObject = (rootValue, rootPath, output) => {
    const seen = new Set();
    const walk = (value, path, depth) => {
      if (output.length >= 200 || depth < 0 || shouldSkipObject(value) || seen.has(value)) return;
      seen.add(value);
      for (const name of propertyNames(value).slice(0, 100)) {
        let current; try { current = value[name]; } catch (_) { continue; }
        const currentPath = `${path}.${name}`;
        const primitive = previewValue(current);
        if (primitive !== undefined || current == null) {
          if (resourceKeyword(`${currentPath} ${primitive == null ? "" : primitive}`)) output.push({ source: "object", path, name: safeString(name, 100), value: primitive });
        } else if (current && typeof current === "object" && !shouldSkipObject(current)) walk(current, currentPath, depth - 1);
      }
    };
    walk(rootValue, rootPath, 4);
  };

  const collectResourceDomCandidates = (win, path, output) => {
    try {
      const doc = win.document;
      const text = safeString(doc && doc.body && doc.body.innerText, 2000);
      if (resourceKeyword(text)) output.push({ source: "dom_text", path, name: "body.innerText", value: text });
      const html = safeString(doc && doc.documentElement && doc.documentElement.innerHTML, 500);
      if (resourceKeyword(html)) output.push({ source: "dom_html", path, name: "documentElement.innerHTML", value: html });
      const elements = doc ? Array.from(doc.querySelectorAll("*")).slice(0, 1200) : [];
      for (const el of elements) {
        const value = safeString([el.id, el.className, el.getAttribute && el.getAttribute("title"), el.getAttribute && el.getAttribute("alt"), el.getAttribute && el.getAttribute("style"), el.textContent].join(" "), 500);
        if (resourceKeyword(value)) {
          output.push({ source: "dom_element", path, name: safeString(el.tagName, 30), value });
          if (output.length >= 200) return;
        }
      }
    } catch (_) {}
  };

  const resourceSnapshot = () => {
    const root = window.top || window;
    const candidates = [];
    walkWindows(root, "top", 4, new Set(), (win, path) => { collectResourceDomCandidates(win, path, candidates); collectResourceCandidatesFromObject(win, path, candidates); });
    let healthPercent = null; let prowessPercent = null; let healthCandidate = null; let prowessCandidate = null;
    for (const candidate of candidates) {
      if (healthPercent == null) { const percent = resourcePercentFromCandidate(candidate, "health"); if (percent != null) { healthPercent = percent; healthCandidate = candidate; } }
      if (prowessPercent == null) { const percent = resourcePercentFromCandidate(candidate, "prowess"); if (percent != null) { prowessPercent = percent; prowessCandidate = candidate; } }
      if (healthPercent != null && prowessPercent != null) break;
    }
    return { bridgeVersion: BRIDGE_VERSION, generatedAt: new Date().toISOString(), ok: healthPercent != null && prowessPercent != null, healthPercent, prowessPercent, healthCandidate, prowessCandidate, candidateCount: candidates.length, candidates: candidates.slice(0, 20) };
  };

  const compactResourceSnapshot = (snapshot) => ({ ok: Boolean(snapshot && snapshot.ok), healthPercent: snapshot && snapshot.healthPercent != null ? snapshot.healthPercent : null, prowessPercent: snapshot && snapshot.prowessPercent != null ? snapshot.prowessPercent : null });

  const refreshResourceSource = () => {
    const root = window.top || window;
    try {
      const mainFrame = root.frames && root.frames["main_frame"];
      if (mainFrame && mainFrame.location) {
        const href = safeString(mainFrame.location.href, 240);
        root.setTimeout(() => { try { mainFrame.location.reload(); } catch (_) {} }, 50);
        return { ok: true, message: "main_frame_reload_scheduled", href };
      }
    } catch (error) { return { ok: false, message: `main_frame_reload_error:${safeString(error && error.message ? error.message : error, 200)}` }; }
    try {
      const href = safeString(root.location && root.location.href, 240);
      root.setTimeout(() => { try { root.location.reload(); } catch (_) {} }, 50);
      return { ok: true, message: "top_reload_scheduled", href };
    } catch (error) { return { ok: false, message: `top_reload_error:${safeString(error && error.message ? error.message : error, 200)}` }; }
  };
