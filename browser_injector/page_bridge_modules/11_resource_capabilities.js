  // Descriptor-only probe for discovering a stable resource model contract.
  // It reports shapes, never values, and never invokes accessors or methods.
  const resourceModelCapabilities = () => {
    const startedAt = Date.now();
    const MAX_WINDOWS = 2;
    const MAX_PATHS = 24;
    const allowedPaths = [
      ["model", "player", "healthPercent"],
      ["model", "player", "prowessPercent"],
      ["model", "resources", "health", "current"],
      ["model", "resources", "health", "max"],
      ["model", "resources", "prowess", "current"],
      ["model", "resources", "prowess", "max"],
      ["model", "area", "user", "healthPercent"],
      ["model", "area", "user", "prowessPercent"],
    ];
    const ownData = (owner, key) => {
      if (owner == null || (typeof owner !== "object" && typeof owner !== "function")) {
        return { status: "missing", value: null };
      }
      let descriptor = null;
      try { descriptor = Object.getOwnPropertyDescriptor(owner, key); } catch (_) {
        return { status: "unreadable", value: null };
      }
      if (!descriptor) return { status: "missing", value: null };
      if (!("value" in descriptor)) return { status: "accessor", value: null };
      return { status: "data", value: descriptor.value };
    };
    const describePath = (rootValue, path) => {
      let current = rootValue;
      for (const segment of path) {
        const observed = ownData(current, segment);
        if (observed.status !== "data") {
          return { path: path.join("."), status: observed.status, type: null, size: null };
        }
        current = observed.value;
      }
      const type = Array.isArray(current) ? "array" : current === null ? "null" : typeof current;
      const size = Array.isArray(current)
        ? Math.min(current.length, 10000)
        : current && typeof current === "object"
          ? Math.min(Object.keys(current).length, 1000)
          : null;
      return { path: path.join("."), status: "data", type, size };
    };
    const root = window.top || window;
    const windows = [root];
    const main = findMainContentWindow(root);
    if (main && main !== root) windows.push(main);
    const roots = [];
    for (const [windowIndex, win] of windows.slice(0, MAX_WINDOWS).entries()) {
      for (const rootName of ["fight", "hunt", "area"]) {
        const observed = ownData(win, rootName);
        roots.push({
          windowIndex,
          root: rootName,
          status: observed.status,
          fields: observed.status === "data"
            ? allowedPaths.slice(0, MAX_PATHS).map((path) => describePath(observed.value, path))
            : [],
        });
      }
    }
    return {
      ok: true,
      schemaVersion: 1,
      source: "descriptor-capability-probe",
      durationMs: Math.max(0, Date.now() - startedAt),
      domNodesScanned: 0,
      limits: { windows: MAX_WINDOWS, paths: MAX_PATHS },
      roots,
    };
  };
