  // Read-only discovery for non-combat hunt-map activities. Observation is
  // descriptor-only: native methods, accessors, and array properties are never
  // invoked. Traversal follows a bounded allowlist of known model containers.
  let gatheringSnapshotSequence = 0;
  const gatheringNodeSnapshot = (metadata = {}) => {
    const MAX_CANDIDATES = 100;
    const MAX_VISITED = 800;
    const MAX_ARRAY_ITEMS = 200;
    const MAX_DEPTH = 5;
    const CHILD_CONTAINERS = [
      "activities", "activity", "nodes", "resources", "resourceNodes",
      "gatheringNodes", "objects", "items", "map", "data", "list",
      "models", "locations", "currentLocation", "state",
    ];
    const METHOD_NAMES = [
      "gather", "collect", "harvest", "mine", "fish", "skin",
      "takeResource", "startGathering",
    ];

    const candidates = [];
    const seenCandidates = new Set();
    const visited = new WeakSet();
    let visitedCount = 0;
    let scanTruncated = false;

    const ownData = (owner, property) => {
      let descriptor;
      try { descriptor = Object.getOwnPropertyDescriptor(owner, property); } catch (_) {
        scanTruncated = true;
        return { present: false, valid: false, accessor: false, value: null };
      }
      if (!descriptor) return { present: false, valid: true, accessor: false, value: null };
      if (!("value" in descriptor)) {
        scanTruncated = true;
        return { present: true, valid: false, accessor: true, value: null };
      }
      return { present: true, valid: true, accessor: false, value: descriptor.value };
    };

    const firstTyped = (owner, names, expectedTypes, allowNull = false) => {
      for (const name of names) {
        const observed = ownData(owner, name);
        if (!observed.present) continue;
        if (!observed.valid) return { present: true, valid: false, field: name, value: null };
        if (observed.value === null && allowNull) return { present: true, valid: true, field: name, value: null };
        if (!expectedTypes.includes(typeof observed.value)) {
          return { present: true, valid: false, field: name, value: null };
        }
        if (typeof observed.value === "string") {
          const value = safeString(observed.value, 180).trim();
          return { present: true, valid: Boolean(value), field: name, value: value || null };
        }
        if (typeof observed.value === "number" && (!Number.isFinite(observed.value) || observed.value < 0)) {
          return { present: true, valid: false, field: name, value: null };
        }
        return { present: true, valid: true, field: name, value: observed.value };
      }
      return { present: false, valid: true, field: null, value: null };
    };

    const methodDescriptors = (value) => {
      const methods = [];
      const seen = new Set();
      let owner = value;
      for (let level = 0; owner && level < 2; level += 1) {
        for (const property of METHOD_NAMES) {
          const observed = ownData(owner, property);
          if (!observed.present) continue;
          if (!observed.valid || typeof observed.value !== "function") continue;
          let functionName = property;
          const nameObserved = ownData(observed.value, "name");
          if (nameObserved.present && nameObserved.valid && typeof nameObserved.value === "string") {
            functionName = safeString(nameObserved.value || property, 120);
          }
          const key = `${property}:${functionName}`;
          if (seen.has(key)) continue;
          seen.add(key);
          methods.push({
            property,
            descriptorType: "function",
            functionName,
            owner: level === 0 ? "instance" : "prototype",
          });
        }
        try { owner = Object.getPrototypeOf(owner); } catch (_) {
          scanTruncated = true;
          owner = null;
        }
      }
      return methods;
    };

    const inspectCandidate = (value, path) => {
      const nodeId = firstTyped(value, ["nodeId", "id", "uid"], ["string", "number"]);
      const resourceId = firstTyped(value, ["resourceId", "itemId"], ["string", "number"]);
      const nodeName = firstTyped(value, ["resourceName", "name", "title", "label", "resource"], ["string"]);
      const location = firstTyped(value, ["locationName", "location", "areaName", "area"], ["string"]);
      const kind = firstTyped(value, ["type", "kind", "category"], ["string"]);
      const ready = firstTyped(value, ["ready", "isReady", "available", "canGather"], ["boolean"]);
      const cooldown = firstTyped(value, ["cooldownRemaining", "cooldownLeft", "cooldown", "cd"], ["number"]);
      const tool = firstTyped(value, ["requiredTool", "toolName", "tool"], ["string"], true);
      const profession = firstTyped(value, ["requiredProfession", "professionName", "profession"], ["string"], true);
      const methods = methodDescriptors(value);
      const marker = `${kind.value || ""} ${nodeName.value || ""}`.toLowerCase();
      const resourceMarked = /resource|gather|collect|harvest|herb|fish|skin|mine|ore|wood|трав|рыб|шкур|руд|минерал|добы/.test(marker);
      if (!nodeName.value || (!resourceMarked && methods.length === 0)) return;

      const candidateKey = `${nodeId.value || ""}:${nodeName.value}:${location.value || ""}`;
      if (seenCandidates.has(candidateKey)) return;
      seenCandidates.add(candidateKey);
      const nativeMethod = methods.length === 1 ? methods[0] : null;
      const completeEvidence = Boolean(
        nodeId.present && nodeId.valid && nodeId.value != null
        && nodeName.present && nodeName.valid && nodeName.value
        && location.present && location.valid && location.value
        && ready.present && ready.valid
        && cooldown.present && cooldown.valid
        && tool.present && tool.valid
        && profession.present && profession.valid
        && nativeMethod
      );
      candidates.push({
        nodeId: nodeId.value == null ? null : safeString(nodeId.value, 180),
        resourceId: resourceId.value == null ? null : safeString(resourceId.value, 180),
        nodeName: nodeName.value,
        location: location.value,
        path: safeString(path, 240),
        nativeMethod,
        methodCandidates: methods,
        ready: ready.value,
        cooldownRemaining: cooldown.value,
        requiredTool: tool.value,
        requiredProfession: profession.value,
        evidence: {
          complete: completeEvidence,
          typedReady: ready.present && ready.valid,
          typedCooldown: cooldown.present && cooldown.valid,
          uniqueNativeMethod: methods.length === 1,
          toolRequirementObserved: tool.present && tool.valid,
          professionRequirementObserved: profession.present && profession.valid,
        },
      });
    };

    const walk = (value, path, depth) => {
      if (!value || typeof value !== "object") return;
      if (depth < 0 || candidates.length >= MAX_CANDIDATES || visitedCount >= MAX_VISITED) {
        scanTruncated = true;
        return;
      }
      if (visited.has(value)) return;
      visited.add(value);
      visitedCount += 1;

      if (Array.isArray(value)) {
        const lengthObserved = ownData(value, "length");
        if (!lengthObserved.present || !lengthObserved.valid || !Number.isSafeInteger(lengthObserved.value) || lengthObserved.value < 0) {
          scanTruncated = true;
          return;
        }
        const length = lengthObserved.value;
        if (length > MAX_ARRAY_ITEMS) scanTruncated = true;
        const count = Math.min(length, MAX_ARRAY_ITEMS);
        for (let index = 0; index < count; index += 1) {
          const item = ownData(value, String(index));
          if (!item.present) continue;
          if (!item.valid) continue;
          walk(item.value, `${path}[${index}]`, depth - 1);
        }
        return;
      }

      inspectCandidate(value, path);
      for (const key of CHILD_CONTAINERS) {
        const child = ownData(value, key);
        if (!child.present || !child.valid) continue;
        walk(child.value, `${path}.${key}`, depth - 1);
      }
    };

    const hunt = findHuntApp();
    const modelObserved = hunt && typeof hunt === "object"
      ? ownData(hunt, "model")
      : { present: false, valid: false, value: null };
    const huntModel = modelObserved.present && modelObserved.valid
      && modelObserved.value && typeof modelObserved.value === "object"
      ? modelObserved.value
      : null;
    if (!huntModel) {
      return { ok: false, message: "hunt_model_missing", candidates: [] };
    }

    walk(huntModel, "hunt.model", MAX_DEPTH);
    const normalizedMetadata = metadata && typeof metadata === "object" && !Array.isArray(metadata)
      ? metadata
      : {};
    const transportClientId = safeString(normalizedMetadata.transportClientId, 180) || null;
    const expectedCharacterName = safeString(normalizedMetadata.expectedCharacterName, 120) || null;
    const observedCharacterName = safeString(normalizedMetadata.observedCharacterName, 120) || null;
    const characterStatus = safeString(normalizedMetadata.characterStatus, 40) || null;
    const questId = safeString(normalizedMetadata.questId, 120) || null;
    const questFingerprint = safeString(normalizedMetadata.questFingerprint, 240) || null;
    const bindingComplete = Boolean(
      transportClientId && expectedCharacterName && observedCharacterName &&
      characterStatus === "available" && observedCharacterName === expectedCharacterName &&
      questId && questFingerprint
    );
    const generatedAt = safeString(normalizedMetadata.generatedAt, 80) || new Date().toISOString();
    gatheringSnapshotSequence = (gatheringSnapshotSequence + 1) % 1000000;
    const snapshotId = safeString(normalizedMetadata.snapshotId, 120) || `gather-${generatedAt}-${gatheringSnapshotSequence}`;
    return {
      ok: true,
      message: "gathering_nodes_observed",
      bridgeVersion: BRIDGE_VERSION,
      snapshotId,
      generatedAt,
      binding: {
        complete: bindingComplete,
        transportClientId,
        expectedCharacterName,
        observedCharacterName,
        characterStatus,
        requestScope: { questId, questFingerprint },
      },
      actionable: false,
      actionReason: "read_only_discovery",
      scan: {
        visited: visitedCount,
        candidateCount: candidates.length,
        truncated: scanTruncated,
      },
      candidates,
    };
  };
