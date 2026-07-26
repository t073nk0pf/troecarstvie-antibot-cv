"""Pure proof contract for operator clearing of an expired catalogue stage."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import re
from typing import Mapping

from src.antibot_cv.automation.quest_catalog_navigation import (
    PendingCatalogNavigation,
    exact_quest_catalog_destination,
    snapshot_epoch_seconds,
)


@dataclass(frozen=True)
class CatalogRecoveryProof:
    eligible: bool
    reason: str
    client_id: str
    profile_id: str
    tab_id: int | None
    snapshot_id: str
    generated_at: float | None
    page: int | None
    href: str

    def receipt(self, *, applied: bool) -> dict[str, object]:
        return {
            "ok": self.eligible,
            "eligible": self.eligible,
            "applied": bool(applied),
            "reason": self.reason,
            "client_id": self.client_id[:240],
            "profile_id": self.profile_id[:240],
            "tab_id": self.tab_id,
            "snapshot_id": self.snapshot_id[:240],
            "generated_at": self.generated_at,
            "page": self.page,
            "href": self.href[:500],
        }


def catalog_recovery_chain_path(runs_dir: object, character_name: object) -> Path:
    identity = str(character_name or "").strip()
    if not identity:
        raise ValueError("required character identity is missing")
    bounded = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ_-]+", "_", identity)
    if not bounded or len(bounded) > 180:
        raise ValueError("required character identity is invalid")
    return Path(str(runs_dir or "runs")) / "quest_chains" / f"{bounded}.json"


def evaluate_catalog_recovery(
    pending: PendingCatalogNavigation,
    *,
    client: Mapping[str, object],
    result_client_id: object,
    snapshot: object,
    now: object,
) -> CatalogRecoveryProof:
    now_value = _finite_number(now)
    client_id = str(result_client_id or "").strip()
    metadata_client_id = str(client.get("client_id") or "").strip()
    profile_id = str(client.get("profile_id") or "").strip()
    tab_id = client.get("tab_id")
    tab = tab_id if isinstance(tab_id, int) and not isinstance(tab_id, bool) else None
    empty = CatalogRecoveryProof(False, "catalog_recovery_proof_invalid", client_id, profile_id, tab, "", None, None, "")
    if now_value is None or now_value < pending.deadline:
        return _reason(empty, "catalog_recovery_stage_not_expired")
    if (
        not client_id
        or client_id != pending.client_id
        or metadata_client_id != client_id
        or not profile_id
        or profile_id != pending.profile_id
        or tab != pending.tab_id
    ):
        return _reason(empty, "catalog_recovery_identity_mismatch")
    if not isinstance(snapshot, Mapping):
        return _reason(empty, "catalog_recovery_snapshot_missing")
    snapshot_id = str(snapshot.get("snapshotId") or "").strip()
    generated_at = snapshot_epoch_seconds(snapshot.get("generatedAt"))
    sections = snapshot.get("sections")
    section = sections.get("quests") if isinstance(sections, Mapping) else None
    data = section.get("data") if isinstance(section, Mapping) else None
    if not isinstance(data, Mapping):
        return CatalogRecoveryProof(False, "catalog_recovery_quest_snapshot_missing", client_id, profile_id, tab, snapshot_id, generated_at, None, "")
    page_value = data.get("currentPage")
    page = page_value if isinstance(page_value, int) and not isinstance(page_value, bool) else None
    href = str(data.get("href") or "").strip()
    proof = CatalogRecoveryProof(False, "catalog_recovery_snapshot_invalid", client_id, profile_id, tab, snapshot_id, generated_at, page, href)
    data_snapshot_id = str(data.get("snapshotId") or "").strip()
    data_generated_at = snapshot_epoch_seconds(data.get("generatedAt"))
    if (
        not snapshot_id
        or snapshot_id == pending.baseline_snapshot_id
        or data_snapshot_id != snapshot_id
        or generated_at is None
        or data_generated_at != generated_at
        or generated_at < pending.issued_at
        or generated_at < pending.deadline
        or generated_at > now_value
    ):
        return _reason(proof, "catalog_recovery_snapshot_stale")
    exact = (
        data.get("loadStatus") == "loaded"
        and data.get("truncated") is False
        and data.get("pageKind") == "quests"
        and data.get("mode") == "avail"
        and page == pending.page
        and href == pending.destination
        and exact_quest_catalog_destination(href, pending.page)
    )
    if not exact:
        return _reason(proof, "catalog_recovery_catalog_mismatch")
    return CatalogRecoveryProof(True, "catalog_recovery_exact_post_stop_proof", client_id, profile_id, tab, snapshot_id, generated_at, page, href)


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _reason(proof: CatalogRecoveryProof, reason: str) -> CatalogRecoveryProof:
    return CatalogRecoveryProof(
        False, reason, proof.client_id, proof.profile_id, proof.tab_id,
        proof.snapshot_id, proof.generated_at, proof.page, proof.href,
    )
