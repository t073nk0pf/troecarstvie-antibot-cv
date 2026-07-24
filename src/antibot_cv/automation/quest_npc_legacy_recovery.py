"""Explicit read-only proof for one bounded pre-v60 NPC-open recovery."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
from typing import Mapping
from urllib.parse import urlsplit

from src.antibot_cv.automation.quest_catalog_navigation import exact_game_origin_href, snapshot_epoch_seconds
from src.antibot_cv.automation.quest_director_policy import QuestRef
from src.antibot_cv.automation.quest_npc_open_navigation import (
    LEGACY_NPC_OPEN_CAPABILITY,
    LEGACY_STAGE_SOURCE,
    PendingNpcOpen,
    make_pending_npc_open,
)

LEGACY_BLOCK_REASON = 'injector_open_exact_npc_failed:{"ok":false,"message":"npc_open_postcondition_failed"}'
LEGACY_DEADLINE_S = 20.0


@dataclass(frozen=True)
class LegacyNpcOpenEvent:
    client_id: str
    profile_id: str
    tab_id: int
    session_id: str
    quest_id: str
    giver_name: str
    npc_name: str
    npc_id: str
    location_id: str
    area_snapshot_id: str
    issued_at: float


@dataclass(frozen=True)
class LegacyNpcRecoveryProof:
    eligible: bool
    reason: str
    event: LegacyNpcOpenEvent | None
    client_id: str = ""
    profile_id: str = ""
    tab_id: int | None = None
    snapshot_id: str = ""
    generated_at: float | None = None
    href: str = ""
    pending: PendingNpcOpen | None = None

    def receipt(self, *, applied: bool) -> dict[str, object]:
        event = self.event
        pending = self.pending
        return {
            "ok": self.eligible, "eligible": self.eligible, "applied": bool(applied),
            "reason": self.reason, "client_id": self.client_id[:240],
            "profile_id": self.profile_id[:240], "tab_id": self.tab_id,
            "quest_id": None if event is None else event.quest_id,
            "npc_id": None if event is None else event.npc_id,
            "event_issued_at": None if event is None else event.issued_at,
            "snapshot_id": self.snapshot_id[:120], "generated_at": self.generated_at,
            "href": self.href[:500],
            "capability_version": None if pending is None else pending.capability_version,
            "stage_source": None if pending is None else pending.stage_source,
            "area_timestamp_source": None if pending is None else pending.area_timestamp_source,
        }


def load_legacy_npc_open_event(
    path: str | Path, *, expected_ref: QuestRef, client_id: str,
    max_bytes: int = 2_000_000, max_lines: int = 20_000,
) -> LegacyNpcOpenEvent:
    if len(expected_ref.giver_names) != 1:
        raise ValueError("legacy NPC pending quest reference is invalid")
    source = Path(path)
    if source.stat().st_size > max_bytes:
        raise ValueError("legacy NPC event log is too large")
    matches: list[Mapping[str, object]] = []
    with source.open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle, 1):
            if index > max_lines:
                raise ValueError("legacy NPC event log has too many lines")
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError("legacy NPC event log is malformed") from exc
            if not isinstance(item, Mapping):
                raise ValueError("legacy NPC event log is malformed")
            if item.get("event_type") == "action_blocked" and item.get("action_type") == "open_exact_npc" and _legacy_reason(item.get("block_reason")):
                matches.append(item)
    if len(matches) != 1:
        raise ValueError("legacy NPC open event must be unique")
    raw = matches[0]
    issued = _finite(raw.get("ts_wall"))
    event_client_id = str(raw.get("injector_client_id") or "").strip()
    event_identity = _parse_client_identity(event_client_id)
    current_identity = _parse_client_identity(client_id)
    if (
        event_identity is None or current_identity is None
        or event_identity[:2] != current_identity[:2]
    ):
        raise ValueError("legacy NPC open event transport identity is invalid")
    event = LegacyNpcOpenEvent(
        event_client_id, event_identity[0], event_identity[1], event_identity[2],
        str(raw.get("quest_id") or "").strip(),
        str(raw.get("expected_dialog_name") or "").strip(),
        str(raw.get("expected_name") or "").strip(),
        str(raw.get("npc_id") or "").strip(),
        str(raw.get("expected_location_id") or "").strip(),
        str(raw.get("expected_snapshot_id") or "").strip(),
        issued if issued is not None else float("nan"),
    )
    if (
        raw.get("dry_run") is not False
        or event.quest_id != expected_ref.id or event.giver_name != expected_ref.giver_names[0]
        or not event.npc_name or len(event.npc_name) > 180
        or not _positive(event.npc_id) or not _positive(event.location_id)
        or not event.area_snapshot_id.startswith("area-npcs-") or len(event.area_snapshot_id) > 120
        or issued is None
    ):
        raise ValueError("legacy NPC open event identity is invalid")
    return event


def evaluate_legacy_npc_recovery(
    event: LegacyNpcOpenEvent, ref: QuestRef, *, client: Mapping[str, object],
    current_client_id: object, result_client_id: object, snapshot: object, now: object,
    max_snapshot_age_s: float = 5.0,
) -> LegacyNpcRecoveryProof:
    if not isinstance(event, LegacyNpcOpenEvent) or not isinstance(ref, QuestRef):
        return LegacyNpcRecoveryProof(False, "legacy_npc_recovery_proof_invalid", None)
    if not isinstance(client, Mapping):
        return LegacyNpcRecoveryProof(False, "legacy_npc_recovery_proof_invalid", event)
    if not _valid_recovery_input(event, ref):
        return LegacyNpcRecoveryProof(False, "legacy_npc_recovery_proof_invalid", event)
    if (
        event.quest_id != ref.id
        or event.giver_name != ref.giver_names[0]
    ):
        return LegacyNpcRecoveryProof(False, "legacy_npc_recovery_identity_mismatch", event)
    requested_client_id = str(current_client_id or "").strip()
    client_id = str(result_client_id or "").strip()
    profile_id = str(client.get("profile_id") or "").strip()
    tab_raw = client.get("tab_id")
    tab_id = tab_raw if isinstance(tab_raw, int) and not isinstance(tab_raw, bool) else None
    base = LegacyNpcRecoveryProof(False, "legacy_npc_recovery_proof_invalid", event, client_id, profile_id, tab_id)
    now_value = _finite(now)
    legacy_identity = _parse_client_identity(event.client_id)
    current_identity = _parse_client_identity(requested_client_id)
    if (
        now_value is None
        or requested_client_id != client_id
        or str(client.get("client_id") or "") != client_id or not profile_id or tab_id is None
        or legacy_identity is None or current_identity is None
        or (event.profile_id, event.tab_id, event.session_id) != legacy_identity
        or legacy_identity[:2] != current_identity[:2]
        or profile_id != current_identity[0] or tab_id != current_identity[1]
    ):
        return _reason(base, "legacy_npc_recovery_identity_mismatch")
    if not isinstance(snapshot, Mapping):
        return _reason(base, "legacy_npc_recovery_snapshot_missing")
    generated = snapshot_epoch_seconds(snapshot.get("generatedAt"))
    snapshot_id = str(snapshot.get("snapshotId") or "").strip()
    href = str(snapshot.get("href") or "").strip()
    proof = LegacyNpcRecoveryProof(False, "legacy_npc_recovery_snapshot_invalid", event, client_id, profile_id, tab_id, snapshot_id, generated, href)
    deadline = event.issued_at + LEGACY_DEADLINE_S
    max_age = _finite(max_snapshot_age_s)
    if (
        generated is None or max_age is None or not 0 < max_age <= 5.0
        or not deadline < generated <= now_value
        or now_value - generated > max_age
    ):
        return _reason(proof, "legacy_npc_recovery_snapshot_stale")
    headers = snapshot.get("matchingHeaders")
    actions = snapshot.get("questActions")
    exact_actions = [] if not isinstance(actions, list) else [
        item for item in actions if isinstance(item, Mapping)
        and str(item.get("questId") or "") == ref.id
        and str(item.get("npcId") or "") == event.npc_id
        and item.get("action") == "open" and str(item.get("title") or "").strip() == ref.title
        and item.get("visible") is True and item.get("disabled") is False
    ]
    parsed = urlsplit(href)
    exact = (
        snapshot.get("ok") is True and snapshot.get("truncated") is False
        and snapshot.get("pageKind") == "npc" and snapshot.get("expectedName") == event.giver_name
        and snapshot.get("identityMatches") is True
        and str(snapshot.get("expectedNpcId") or "") == event.npc_id
        and str(snapshot.get("npcId") or "") == event.npc_id
        and snapshot_id.startswith("npc-dialog-") and len(snapshot_id) <= 120
        and isinstance(headers, list) and len(headers) == 1 and bool(str(headers[0] or "").strip())
        and len(exact_actions) == 1 and exact_game_origin_href(href) and parsed.path == "/npc.php"
    )
    if not exact:
        return _reason(proof, "legacy_npc_recovery_dialog_mismatch")
    try:
        pending = make_pending_npc_open(
            client_id=client_id, profile_id=profile_id, tab_id=tab_id,
            quest_id=ref.id, quest_title=ref.title, quest_accept_ref=ref.accept_ref,
            quest_catalog_page=ref.catalog_page, giver_name=event.giver_name,
            npc_id=event.npc_id, route_ref=None, npc_name=event.npc_name, location_id=event.location_id,
            location_name=ref.location, area_snapshot_id=event.area_snapshot_id,
            area_generated_at=event.issued_at, issued_at=event.issued_at,
            settle_timeout_s=LEGACY_DEADLINE_S,
            capability_version=LEGACY_NPC_OPEN_CAPABILITY,
            stage_source=LEGACY_STAGE_SOURCE,
            area_timestamp_source="derived_event_issued_at",
        )
    except (TypeError, ValueError):
        return _reason(proof, "legacy_npc_recovery_pending_invalid")
    return LegacyNpcRecoveryProof(True, "legacy_npc_recovery_exact_dialog_proof", event, client_id, profile_id, tab_id, snapshot_id, generated, href, pending)


def _legacy_reason(value: object) -> bool:
    text = str(value or "")
    if text == LEGACY_BLOCK_REASON:
        return True
    prefix = "injector_open_exact_npc_failed:"
    if not text.startswith(prefix):
        return False
    try:
        parsed = json.loads(text[len(prefix):])
    except json.JSONDecodeError:
        return False
    return parsed == {"ok": False, "message": "npc_open_postcondition_failed"}


def _valid_recovery_input(event: LegacyNpcOpenEvent, ref: QuestRef) -> bool:
    event_identity = _parse_client_identity(event.client_id)
    return bool(
        event_identity is not None
        and (event.profile_id, event.tab_id, event.session_id) == event_identity
        and _positive(event.quest_id)
        and _bounded(event.giver_name, 180)
        and _bounded(event.npc_name, 180)
        and _positive(event.npc_id)
        and _positive(event.location_id)
        and _bounded(event.area_snapshot_id, 120)
        and event.area_snapshot_id.startswith("area-npcs-")
        and _finite(event.issued_at) is not None
        and event.issued_at > 0
        and _positive(ref.id)
        and _bounded(ref.title, 500)
        and isinstance(ref.giver_names, tuple)
        and len(ref.giver_names) == 1
        and _bounded(ref.giver_names[0], 180)
        and isinstance(ref.location, str)
        and _bounded(ref.location, 180)
        and isinstance(ref.catalog_page, int)
        and not isinstance(ref.catalog_page, bool)
        and 0 <= ref.catalog_page <= 100
        and (ref.accept_ref is None or _positive(ref.accept_ref))
    )


def _parse_client_identity(value: object) -> tuple[str, int, str] | None:
    text = str(value or "").strip()
    match = re.fullmatch(
        r"([A-Za-z0-9][A-Za-z0-9._-]{0,179})-tab-([1-9]\d{0,18})-session-([a-f0-9]{16})",
        text,
    )
    if match is None or "-tab-" in match.group(1):
        return None
    return match.group(1), int(match.group(2)), match.group(3)


def _positive(value: str) -> bool:
    return isinstance(value, str) and value.isdecimal() and int(value) > 0 and len(value) <= 80


def _bounded(value: object, limit: int) -> bool:
    return isinstance(value, str) and value == value.strip() and 0 < len(value) <= limit


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _reason(proof: LegacyNpcRecoveryProof, reason: str) -> LegacyNpcRecoveryProof:
    return LegacyNpcRecoveryProof(
        False, reason, proof.event, proof.client_id, proof.profile_id, proof.tab_id,
        proof.snapshot_id, proof.generated_at, proof.href,
    )
