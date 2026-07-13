  // Read-only discovery for non-combat hunt-map activities.  A node becomes
  // actionable only after its native collection method has been observed.
  const gatheringNodeSnapshot = () => {
    const hunt = findHuntApp();
    if (!hunt || !hunt.model) {
      return { ok: false, message: "hunt_model_missing", candidates: [] };
    }
    const candidates = [];
    const seen = new Set();
    const walk = (value, path, depth) => {
      if (!value || typeof value !== "object" || depth < 0 || candidates.length >= 100) return;
      if (Array.isArray(value)) {
        value.slice(0, 400).forEach((item, index) => walk(item, `${path}[${index}]`, depth - 1));
        return;
      }
      const fields = {};
      ["id", "name", "title", "label", "type", "kind", "category", "resource", "action", "state"].forEach((key) => {
        const raw = value[key];
        if (typeof raw === "string" || typeof raw === "number") fields[key] = safeString(raw, 180);
      });
      const haystack = Object.values(fields).join(" ").toLowerCase();
      const looksLikeResource = /resource|gather|collect|herb|fish|skin|mine|ore|wood|трав|рыб|шкур|руд|минерал|добы/.test(haystack);
      const looksLikeMob = /bot|mob|monster|creature|моб|монстр/.test(haystack);
      const name = safeString(fields.name || fields.title || fields.label, 180);
      if (looksLikeResource && !looksLikeMob && name) {
        const candidateId = safeString(fields.id || path, 180);
        const key = `${candidateId}:${name}`;
        if (!seen.has(key)) {
          seen.add(key);
          candidates.push({ candidateId, name, path, fields });
        }
      }
      Object.keys(value).slice(0, 80).forEach((key) => {
        if (/^(view|parent|stage|canvas|dom|element)$/i.test(key)) return;
        const child = value[key];
        if (child && typeof child === "object") walk(child, `${path}.${key}`, depth - 1);
      });
    };
    walk(hunt.model, "hunt.model", 5);
    return {
      ok: true,
      message: "gathering_nodes_observed",
      bridgeVersion: BRIDGE_VERSION,
      generatedAt: new Date().toISOString(),
      actionable: false,
      actionReason: "native_gather_action_unobserved",
      candidates,
    };
  };
