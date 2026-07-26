  // Read-only capability observations for Quest Engine V2.
  // These refs are intentionally opaque: they never carry DOM targeting data,
  // routes, quest identifiers, or display text.  A later mutation boundary
  // must validate the server-side binding before it can use one.
  const V2_ACTION_REF_TTL_MS = 20 * 1000;
  const V2_ACTION_REF_MAX_ENTRIES = 64;
  const v2ActionRefs = new Map();
  let v2ActionRefSequence = 0;

  const v2ActionRefFingerprint = (snapshotId, revision, kind, evidence) => {
    const value = `${snapshotId}|${revision}|${kind}|${evidence}`;
    let hash = 2166136261;
    for (let index = 0; index < value.length; index += 1) {
      hash ^= value.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    return `v2-${(hash >>> 0).toString(36)}`;
  };

  const pruneV2ActionRefs = (now) => {
    for (const [ref, record] of v2ActionRefs) {
      if (!record || record.expiresAt <= now) v2ActionRefs.delete(ref);
    }
    while (v2ActionRefs.size >= V2_ACTION_REF_MAX_ENTRIES) {
      const oldest = v2ActionRefs.keys().next().value;
      if (!oldest) break;
      v2ActionRefs.delete(oldest);
    }
  };

  const mintV2ActionRef = (snapshotId, revision, kind, evidence, now) => {
    const fingerprint = v2ActionRefFingerprint(snapshotId, revision, kind, evidence);
    const entropy = Math.random().toString(36).slice(2, 14);
    const ref = `ref:${now.toString(36)}-${(++v2ActionRefSequence).toString(36)}-${entropy}`;
    const expiresAt = now + V2_ACTION_REF_TTL_MS;
    v2ActionRefs.set(ref, { snapshotId, revision, fingerprint, kind, expiresAt });
    return { ref, kind, snapshotId, revision, fingerprint, expiresAt };
  };

  const v2CapabilityObservation = (metadata = {}) => {
    const snapshotId = safeString(metadata.snapshotId, 120);
    const revision = Number(metadata.revision);
    if (!snapshotId || !Number.isInteger(revision) || revision <= 0) {
      return {
        status: "unavailable",
        reason: "v2_snapshot_binding_invalid",
        data: { schemaVersion: 1, snapshotId: null, revision: null, actionRefs: [] },
      };
    }
    const now = Date.now();
    pruneV2ActionRefs(now);
    const location = locationSnapshot();
    const battle = battleSnapshot(false);
    const actionRefs = [];
    if (location && location.status === "available") {
      const locationEvidence = `${safeString(location.data && location.data.semanticName, 160)}|${safeString(location.data && location.data.id, 80)}|${safeString(location.data && location.data.pageKind, 40)}`;
      actionRefs.push(mintV2ActionRef(snapshotId, revision, "Visit", locationEvidence, now));
    }
    if (battle && battle.hasFight && battle.battleIdentity) {
      actionRefs.push(mintV2ActionRef(snapshotId, revision, "Kill", safeString(battle.battleIdentity, 240), now));
    }
    return {
      status: "available",
      reason: null,
      data: {
        schemaVersion: 1,
        snapshotId,
        revision,
        issuedAt: now,
        ttlMs: V2_ACTION_REF_TTL_MS,
        actionRefs,
      },
    };
  };
