"""Typed durable causal contract for opening one exact quest-giver NPC."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import math
from typing import Mapping
from urllib.parse import urlsplit

from src.antibot_cv.automation.quest_catalog_navigation import (
    QUEST_GAME_ORIGIN,
    exact_game_origin_href,
    snapshot_epoch_seconds,
)
from src.antibot_cv.automation.quest_director_policy import QuestRef
from src.antibot_cv.automation.quest_intake_quarantine import intake_ref_fingerprint

NPC_OPEN_CAPABILITY = "npc_open_causal_v1"
LEGACY_NPC_OPEN_CAPABILITY = "npc_open_legacy_recovery_v1"
CAUSAL_STAGE_SOURCE = "causal_area_snapshot"
LEGACY_STAGE_SOURCE = "legacy_postcondition_event"


class NpcOpenStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    ACK_PENDING = "ACK_PENDING"
    NOT_ISSUED = "NOT_ISSUED"


class NpcOpenSettleStatus(str, Enum):
    ACCEPT = "accept"
    WAIT = "wait"
    STOP_IDENTITY = "stop_identity"
    STOP_EXPIRED = "stop_expired"


@dataclass(frozen=True)
class NpcOpenOutcome:
    status: NpcOpenStatus
    mutation_issued: bool
    client_id: str
    destination: str = ""
    issued_at: float | None = None
    reason: str = ""

    @property
    def dispatched(self) -> bool:
        return self.status is not NpcOpenStatus.NOT_ISSUED


@dataclass(frozen=True)
class PendingNpcOpen:
    client_id: str
    profile_id: str
    tab_id: int
    quest_id: str
    quest_title: str
    quest_accept_ref: str | None
    quest_catalog_page: int
    quest_fingerprint: str
    capability_version: str
    giver_name: str
    npc_id: str
    npc_name: str
    location_id: str
    location_name: str
    area_snapshot_id: str
    area_generated_at: float
    issued_at: float
    deadline: float
    stage_source: str = CAUSAL_STAGE_SOURCE
    area_timestamp_source: str = "observed_area_snapshot"
    quest_opened: bool = False
    dialog_steps: int = 0
    dialog_step_fingerprints: tuple[str, ...] = ()
    answer_ambiguity_snapshot_id: str | None = None
    answer_ambiguity_generated_at: float | None = None
    game_origin: str = QUEST_GAME_ORIGIN

    def __post_init__(self) -> None:
        if (
            not _bounded(self.client_id, 240) or not _bounded(self.profile_id, 240)
            or isinstance(self.tab_id, bool) or not isinstance(self.tab_id, int) or self.tab_id < 0
            or not _positive_id(self.quest_id, 80) or not _bounded(self.quest_title, 500)
            or (self.quest_accept_ref is not None and not _bounded(self.quest_accept_ref, 120))
            or isinstance(self.quest_catalog_page, bool) or not isinstance(self.quest_catalog_page, int)
            or not 0 <= self.quest_catalog_page <= 100
            or len(self.quest_fingerprint) != 64 or any(c not in "0123456789abcdef" for c in self.quest_fingerprint)
            or (
                self.capability_version,
                self.stage_source,
                self.area_timestamp_source,
            ) not in {
                (NPC_OPEN_CAPABILITY, CAUSAL_STAGE_SOURCE, "observed_area_snapshot"),
                (LEGACY_NPC_OPEN_CAPABILITY, LEGACY_STAGE_SOURCE, "derived_event_issued_at"),
            }
            or not _bounded(self.giver_name, 180) or not _positive_id(self.npc_id, 80)
            or not _bounded(self.npc_name, 180)
            or not _positive_id(self.location_id, 80) or not _bounded(self.location_name, 180)
            or not _bounded(self.area_snapshot_id, 120) or not self.area_snapshot_id.startswith("area-npcs-")
            or not all(math.isfinite(value) for value in (self.area_generated_at, self.issued_at, self.deadline))
            or self.area_generated_at <= 0 or not self.area_generated_at <= self.issued_at < self.deadline
            or not isinstance(self.quest_opened, bool)
            or isinstance(self.dialog_steps, bool) or not 0 <= self.dialog_steps <= 20
            or len(self.dialog_step_fingerprints) != self.dialog_steps
            or any(not _bounded(value, 64) for value in self.dialog_step_fingerprints)
            or (self.answer_ambiguity_snapshot_id is None) != (self.answer_ambiguity_generated_at is None)
            or (
                self.answer_ambiguity_snapshot_id is not None
                and (
                    not _bounded(self.answer_ambiguity_snapshot_id, 120)
                    or not self.answer_ambiguity_snapshot_id.startswith("npc-dialog-")
                    or not isinstance(self.answer_ambiguity_generated_at, (int, float))
                    or isinstance(self.answer_ambiguity_generated_at, bool)
                    or not math.isfinite(float(self.answer_ambiguity_generated_at))
                    or not self.issued_at < float(self.answer_ambiguity_generated_at) < self.deadline
                )
            )
            or self.deadline - self.issued_at > 120 or self.game_origin != QUEST_GAME_ORIGIN
        ):
            raise ValueError("invalid pending NPC open")


@dataclass(frozen=True)
class NpcOpenSettleDecision:
    status: NpcOpenSettleStatus
    reason: str


def make_pending_npc_open(
    *, client_id: str, profile_id: str, tab_id: int,
    quest_id: str, quest_title: str, quest_accept_ref: str | None,
    quest_catalog_page: int, giver_name: str, npc_id: str, npc_name: str,
    location_id: str, location_name: str, area_snapshot_id: str,
    area_generated_at: object, issued_at: float, settle_timeout_s: float = 20.0,
    capability_version: str = NPC_OPEN_CAPABILITY,
    stage_source: str = CAUSAL_STAGE_SOURCE,
    area_timestamp_source: str = "observed_area_snapshot",
) -> PendingNpcOpen:
    generated = snapshot_epoch_seconds(area_generated_at)
    timeout = float(settle_timeout_s)
    if generated is None or not math.isfinite(timeout) or not 1 <= timeout <= 120:
        raise ValueError("NPC open causal baseline is incomplete")
    quest_ref = QuestRef(
        str(quest_id).strip(), str(quest_title).strip(), quest_accept_ref,
        str(location_name).strip(), (str(giver_name).strip(),), quest_catalog_page,
    )
    return PendingNpcOpen(
        str(client_id).strip(), str(profile_id).strip(), tab_id,
        quest_ref.id, quest_ref.title, quest_ref.accept_ref, quest_catalog_page,
        intake_ref_fingerprint(quest_ref), capability_version,
        str(giver_name).strip(), str(npc_id).strip(), str(npc_name).strip(),
        str(location_id).strip(), str(location_name).strip(),
        str(area_snapshot_id).strip(), generated, float(issued_at), float(issued_at) + timeout,
        stage_source, area_timestamp_source,
    )


def npc_open_command_payload(metadata: Mapping[str, object]) -> dict[str, object] | None:
    snapshot_id = str(metadata.get("expected_snapshot_id") or "").strip()
    location_id = str(metadata.get("expected_location_id") or "").strip()
    npc_id = str(metadata.get("npc_id") or "").strip()
    expected_name = str(metadata.get("expected_name") or "").strip()
    dialog_name = str(metadata.get("expected_dialog_name") or expected_name).strip()
    if (
        not snapshot_id.startswith("area-npcs-") or len(snapshot_id) > 120
        or not _bounded(location_id, 80)
        or not npc_id.isdecimal() or len(npc_id) > 80 or int(npc_id) < 0
        or not _bounded(expected_name, 180) or not _bounded(dialog_name, 180)
    ):
        return None
    return {
        "expectedSnapshotId": snapshot_id, "expectedLocationId": location_id,
        "npcId": npc_id, "expectedName": expected_name, "expectedDialogName": dialog_name,
        "verifyTimeoutMs": 2500, "commandTimeoutMs": 5500,
    }


def parse_npc_open_outcome(*, result_ok: bool, message: object, client_id: object) -> NpcOpenOutcome:
    parsed = None
    if isinstance(message, str):
        try:
            candidate = json.loads(message)
            parsed = candidate if isinstance(candidate, Mapping) else None
        except json.JSONDecodeError:
            pass
    if parsed is not None:
        mutation = parsed.get("mutationIssued") is True
        raw_outcome = parsed.get("outcome")
        try:
            status = NpcOpenStatus(str(raw_outcome or "").upper())
        except ValueError:
            status = NpcOpenStatus.ACK_PENDING
        destination = str(parsed.get("destination") or "")
        issued = snapshot_epoch_seconds(parsed.get("issuedAt"))
        if status is NpcOpenStatus.CONFIRMED and (
            not result_ok or not mutation or not exact_game_origin_href(destination)
        ):
            status = NpcOpenStatus.ACK_PENDING if mutation else NpcOpenStatus.NOT_ISSUED
        explicit_not_issued = raw_outcome == "NOT_ISSUED" and parsed.get("mutationIssued") is False
        if status is NpcOpenStatus.NOT_ISSUED and mutation:
            status = NpcOpenStatus.ACK_PENDING
        if not explicit_not_issued and status is not NpcOpenStatus.CONFIRMED:
            status = NpcOpenStatus.ACK_PENDING
            mutation = True
        return NpcOpenOutcome(status, mutation, str(client_id or ""), destination, issued, str(parsed.get("message") or ""))
    text = str(message or "")
    if text == "injector_delivery_timeout":
        return NpcOpenOutcome(NpcOpenStatus.NOT_ISSUED, False, str(client_id or ""), reason=text)
    return NpcOpenOutcome(NpcOpenStatus.ACK_PENDING, True, str(client_id or ""), reason=text)


def settle_npc_open_snapshot(
    pending: PendingNpcOpen, snapshot: object, *,
    client_id: str, profile_id: str, tab_id: int | None, now: float,
) -> NpcOpenSettleDecision:
    if client_id != pending.client_id or profile_id != pending.profile_id or tab_id != pending.tab_id:
        return NpcOpenSettleDecision(NpcOpenSettleStatus.STOP_IDENTITY, "npc_open_identity_mismatch")
    if not math.isfinite(now):
        return NpcOpenSettleDecision(NpcOpenSettleStatus.STOP_EXPIRED, "npc_open_clock_invalid")
    if now >= pending.deadline:
        return NpcOpenSettleDecision(NpcOpenSettleStatus.STOP_EXPIRED, "npc_open_settle_expired")
    if not isinstance(snapshot, Mapping):
        return _npc_wait_or_expire(pending, now)
    generated = snapshot_epoch_seconds(snapshot.get("generatedAt"))
    snapshot_id = str(snapshot.get("snapshotId") or "")
    href = urlsplit(str(snapshot.get("href") or ""))
    matching_headers = snapshot.get("matchingHeaders")
    actions = snapshot.get("questActions")
    exact_actions = [] if not isinstance(actions, list) else [
        item for item in actions
        if isinstance(item, Mapping)
        and str(item.get("questId") or "") == pending.quest_id
        and str(item.get("npcId") or "") == pending.npc_id
        and str(item.get("action") or "") == "open"
        and str(item.get("title") or "").strip() == pending.quest_title
        and item.get("visible") is True and item.get("disabled") is False
    ]
    exact = (
        snapshot.get("ok") is True and snapshot.get("truncated") is False
        and snapshot.get("pageKind") == "npc" and snapshot.get("identityMatches") is True
        and snapshot.get("expectedName") == pending.giver_name
        and str(snapshot.get("expectedNpcId") or "") == pending.npc_id
        and str(snapshot.get("npcId") or "") == pending.npc_id
        and snapshot_id.startswith("npc-dialog-") and len(snapshot_id) <= 120
        and isinstance(matching_headers, list) and len(matching_headers) == 1
        and bool(str(matching_headers[0] or "").strip())
        and generated is not None
        and pending.issued_at <= generated <= min(now, pending.deadline)
        and exact_game_origin_href(snapshot.get("href")) and href.path == "/npc.php"
        and len(exact_actions) == 1
    )
    if exact:
        return NpcOpenSettleDecision(NpcOpenSettleStatus.ACCEPT, "npc_open_exact_snapshot_confirmed")
    return _npc_wait_or_expire(pending, now)


def serialize_pending_npc_open(value: PendingNpcOpen) -> dict[str, object]:
    return {"schema": 2, **value.__dict__, "dialog_step_fingerprints": list(value.dialog_step_fingerprints)}


def restore_pending_npc_open(raw: object) -> PendingNpcOpen | None:
    if raw is None:
        return None
    fields = set(PendingNpcOpen.__dataclass_fields__)
    legacy_fields = fields - {"answer_ambiguity_snapshot_id", "answer_ambiguity_generated_at"}
    if not isinstance(raw, Mapping) or (
        raw.get("schema") == 2 and set(raw) != {"schema", *fields}
    ) or (
        raw.get("schema") == 1 and set(raw) != {"schema", *legacy_fields}
    ) or raw.get("schema") not in {1, 2}:
        raise ValueError("invalid pending NPC open checkpoint")
    try:
        values = {field: raw[field] for field in fields if field in raw}
        values.setdefault("answer_ambiguity_snapshot_id", None)
        values.setdefault("answer_ambiguity_generated_at", None)
        fingerprints = values.get("dialog_step_fingerprints")
        if not isinstance(fingerprints, (list, tuple)):
            raise ValueError("invalid pending NPC open checkpoint")
        values["dialog_step_fingerprints"] = tuple(fingerprints)
        return PendingNpcOpen(**values)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid pending NPC open checkpoint") from exc


def _npc_wait_or_expire(pending: PendingNpcOpen, now: float) -> NpcOpenSettleDecision:
    if now >= pending.deadline:
        return NpcOpenSettleDecision(NpcOpenSettleStatus.STOP_EXPIRED, "npc_open_settle_expired")
    return NpcOpenSettleDecision(NpcOpenSettleStatus.WAIT, "npc_open_snapshot_pending")


def _bounded(value: object, limit: int) -> bool:
    return isinstance(value, str) and value == value.strip() and 0 < len(value) <= limit


def _positive_id(value: object, limit: int) -> bool:
    return _bounded(value, limit) and value.isdecimal() and int(value) > 0
