"""Durable causal journal contract for NPC quest mutations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import hashlib
import math
from typing import Iterable, Mapping
from urllib.parse import urlsplit

from src.antibot_cv.automation.quest_catalog_navigation import exact_game_origin_href, snapshot_epoch_seconds
from src.antibot_cv.automation.quest_intake_quarantine import intake_ref_fingerprint
from src.antibot_cv.automation.quest_director_policy import QuestRef

NPC_ACTION_CAPABILITY = "npc_quest_action_journal_v1"


class NpcQuestActionKind(str, Enum):
    OPEN = "open"
    ANSWER = "answer"
    ACCEPT = "accept"
    DONE = "done"


class NpcQuestActionStatus(str, Enum):
    ACK_PENDING = "ACK_PENDING"
    NOT_ISSUED = "NOT_ISSUED"


class NpcQuestActionSettle(str, Enum):
    ACCEPT = "accept"
    WAIT = "wait"
    STOP = "stop"


class NpcQuestActionPhase(str, Enum):
    STAGED = "STAGED"
    ACK_PENDING = "ACK_PENDING"
    ACCEPT_VERIFY = "ACCEPT_VERIFY"


@dataclass(frozen=True)
class PendingNpcQuestAction:
    client_id: str
    profile_id: str
    tab_id: int
    quest_id: str
    quest_title: str
    quest_accept_ref: str | None
    quest_catalog_page: int
    quest_location: str
    quest_fingerprint: str
    giver_name: str
    npc_id: str
    action: NpcQuestActionKind
    expected_snapshot_id: str
    expected_generated_at: float
    source_semantic_fingerprint: str
    expected_ref: str | None
    expected_point_id: str | None
    expected_text: str | None
    expected_title: str
    quest_opened: bool
    dialog_steps: int
    issued_at: float
    deadline: float
    phase: NpcQuestActionPhase = NpcQuestActionPhase.STAGED
    capability_version: str = NPC_ACTION_CAPABILITY
    origin: str = "https://3kingdoms.ru"
    provenance: str = "causal_npc_dialog_snapshot"

    def __post_init__(self) -> None:
        ref = QuestRef(self.quest_id, self.quest_title, self.quest_accept_ref, self.quest_location, (self.giver_name,), self.quest_catalog_page)
        if (
            not _text(self.client_id, 240) or not _text(self.profile_id, 240)
            or isinstance(self.tab_id, bool) or not isinstance(self.tab_id, int) or self.tab_id < 0
            or not _positive(self.quest_id) or not _text(self.quest_title, 500)
            or self.quest_accept_ref is not None and not _positive(self.quest_accept_ref)
            or isinstance(self.quest_catalog_page, bool) or not isinstance(self.quest_catalog_page, int)
            or not 0 <= self.quest_catalog_page <= 10_000
            or not _optional_text(self.quest_location, 500)
            or not _text(self.giver_name, 500) or self.expected_title != self.quest_title
            or self.quest_fingerprint != intake_ref_fingerprint(ref)
            or not _npc_id(self.npc_id) or not _text(self.expected_snapshot_id, 120)
            or not self.expected_snapshot_id.startswith("npc-dialog-")
            or len(self.source_semantic_fingerprint) != 64
            or any(char not in "0123456789abcdef" for char in self.source_semantic_fingerprint)
            or snapshot_epoch_seconds(self.expected_generated_at) is None
            or not math.isfinite(self.issued_at) or not math.isfinite(self.deadline)
            or not self.expected_generated_at <= self.issued_at < self.deadline
            or not isinstance(self.quest_opened, bool)
            or isinstance(self.dialog_steps, bool) or not isinstance(self.dialog_steps, int)
            or self.deadline - self.issued_at > 120 or self.dialog_steps < 0 or self.dialog_steps > 64
            or self.capability_version != NPC_ACTION_CAPABILITY or self.origin != "https://3kingdoms.ru"
            or self.provenance != "causal_npc_dialog_snapshot"
            or not isinstance(self.phase, NpcQuestActionPhase)
        ):
            raise ValueError("invalid pending NPC quest action")
        if self.action is NpcQuestActionKind.OPEN and (
            self.quest_opened or self.expected_ref is not None
            or self.expected_point_id is not None or self.expected_text is not None
        ):
            raise ValueError("invalid NPC open journal")
        if self.action is NpcQuestActionKind.ANSWER and (
            not self.quest_opened or not _positive(self.expected_ref or "")
            or self.expected_point_id is not None or not _text(self.expected_text, 1200)
        ):
            raise ValueError("invalid NPC answer journal")
        if self.action is NpcQuestActionKind.ACCEPT and (
            not self.quest_opened or self.expected_ref is not None
            or self.expected_point_id is not None or not _text(self.expected_text, 1200)
        ):
            raise ValueError("invalid NPC accept journal")
        if self.action is NpcQuestActionKind.DONE and (
            not self.quest_opened or self.expected_ref is not None
            or not _positive(self.expected_point_id or "") or not _text(self.expected_text, 1200)
        ):
            raise ValueError("invalid NPC done journal")


@dataclass(frozen=True)
class NpcQuestActionOutcome:
    status: NpcQuestActionStatus
    client_id: str
    mutation_issued: bool
    reason: str = ""

    @property
    def dispatched(self) -> bool:
        return self.status is NpcQuestActionStatus.ACK_PENDING


def make_pending_npc_quest_action(*, ref: QuestRef, client_id: str, profile_id: str, tab_id: int,
    giver_name: str, npc_id: str, action: object, expected_snapshot_id: str,
    expected_generated_at: object, source_semantic_fingerprint: str,
    expected_ref: object = None, expected_point_id: object = None,
    expected_text: object = None, expected_title: object = None, quest_opened: bool,
    dialog_steps: int, issued_at: float, timeout_s: float = 20.0) -> PendingNpcQuestAction:
    generated = snapshot_epoch_seconds(expected_generated_at)
    kind = NpcQuestActionKind(str(action or ""))
    canonical = QuestRef(ref.id, ref.title, ref.accept_ref, ref.location, (giver_name,), ref.catalog_page)
    if generated is None or not 1 <= timeout_s <= 120:
        raise ValueError("NPC action causal baseline missing")
    return PendingNpcQuestAction(
        client_id, profile_id, tab_id, ref.id, ref.title, ref.accept_ref,
        ref.catalog_page, str(ref.location or ""), intake_ref_fingerprint(canonical), giver_name, npc_id, kind,
        expected_snapshot_id, generated, source_semantic_fingerprint, _optional(expected_ref), _optional(expected_point_id),
        _optional(expected_text), str(expected_title or ref.title).strip(), quest_opened,
        dialog_steps, issued_at, issued_at + timeout_s,
    )


def parse_npc_quest_action_outcome(*, result_ok: bool, message: object, client_id: object) -> NpcQuestActionOutcome:
    parsed = None
    try:
        candidate = json.loads(message) if isinstance(message, str) else None
        parsed = candidate if isinstance(candidate, Mapping) else None
    except json.JSONDecodeError:
        pass
    if parsed is not None and parsed.get("outcome") == "NOT_ISSUED" and parsed.get("mutationIssued") is False:
        return NpcQuestActionOutcome(NpcQuestActionStatus.NOT_ISSUED, str(client_id or ""), False, str(parsed.get("message") or ""))
    if str(message or "") == "injector_delivery_timeout":
        return NpcQuestActionOutcome(NpcQuestActionStatus.NOT_ISSUED, str(client_id or ""), False, "injector_delivery_timeout")
    return NpcQuestActionOutcome(NpcQuestActionStatus.ACK_PENDING, str(client_id or ""), True, str((parsed or {}).get("message") or message or ""))


def settle_dialog_action(pending: PendingNpcQuestAction, snapshot: object, *, client_id: str,
    profile_id: str, tab_id: int | None, now: float) -> NpcQuestActionSettle:
    if client_id != pending.client_id or profile_id != pending.profile_id or tab_id != pending.tab_id:
        return NpcQuestActionSettle.STOP
    if not math.isfinite(now) or now >= pending.deadline:
        return NpcQuestActionSettle.STOP
    if not isinstance(snapshot, Mapping):
        return NpcQuestActionSettle.WAIT
    generated = snapshot_epoch_seconds(snapshot.get("generatedAt"))
    href = urlsplit(str(snapshot.get("href") or ""))
    matching_headers = snapshot.get("matchingHeaders")
    if not (
        snapshot.get("ok") is True and snapshot.get("truncated") is False
        and snapshot.get("pageKind") == "npc" and snapshot.get("identityMatches") is True
        and snapshot.get("expectedName") == pending.giver_name
        and str(snapshot.get("expectedNpcId") or "") == pending.npc_id
        and str(snapshot.get("npcId") or "") == pending.npc_id
        and isinstance(matching_headers, list) and len(matching_headers) == 1
        and bool(str(matching_headers[0] or "").strip())
        and str(snapshot.get("snapshotId") or "") != pending.expected_snapshot_id
        and generated is not None and pending.issued_at < generated <= now
        and exact_game_origin_href(snapshot.get("href")) and href.path == "/npc.php"
    ):
        return NpcQuestActionSettle.WAIT
    old = _old_action_matches(pending, snapshot)
    changed = dialog_semantic_fingerprint(snapshot) != pending.source_semantic_fingerprint
    next_actions = snapshot.get("dialogActions") if isinstance(snapshot.get("dialogActions"), list) else []
    accept_actions = snapshot.get("acceptActions") if isinstance(snapshot.get("acceptActions"), list) else []
    answers = [item for item in next_actions if isinstance(item, Mapping)
        and str(item.get("questId") or "") == pending.quest_id
        and str(item.get("npcId") or "") == pending.npc_id and item.get("action") == "answer"
        and item.get("visible") is True and item.get("disabled") is False]
    accepts = [item for item in accept_actions if isinstance(item, Mapping)
        and str(item.get("questId") or "") == pending.quest_id
        and str(item.get("npcId") or "") == pending.npc_id and item.get("action") == "accept"
        and item.get("visible") is True and item.get("disabled") is False]
    headers = snapshot.get("headers") if isinstance(snapshot.get("headers"), list) else []
    actions_text = snapshot.get("actions") if isinstance(snapshot.get("actions"), list) else []
    terminal = len(accepts) == 1 or not accepts or (
        sum(1 for value in headers if " ".join(str(value or "").casefold().split()) == " ".join(pending.quest_title.casefold().split())) == 1
        and any("ваша цель:" in str(item.get("containerText") or "").casefold() for item in actions_text if isinstance(item, Mapping))
    )
    # Successor executability is a later policy decision.  Multiple bounded
    # successor choices still prove that the previous exact action advanced;
    # refusing to settle here used to hold the mutation journal until timeout
    # and globally stop on ordinary quest puzzles.
    bounded_next = len(next_actions) <= 20 and len(accept_actions) <= 10 and len(answers) + len(accepts) >= 1 and terminal
    return NpcQuestActionSettle.ACCEPT if not old and changed and bounded_next else NpcQuestActionSettle.WAIT


def settle_accept_action(
    pending: PendingNpcQuestAction,
    active_quests: Iterable[QuestRef],
    *,
    complete: bool,
    now: float,
) -> NpcQuestActionSettle:
    """Require one exact quest in a complete active catalog after accept."""

    if (
        pending.action is not NpcQuestActionKind.ACCEPT
        or pending.phase is not NpcQuestActionPhase.ACCEPT_VERIFY
        or not math.isfinite(now) or now >= pending.deadline
    ):
        return NpcQuestActionSettle.STOP
    if not complete:
        return NpcQuestActionSettle.WAIT
    matches = [
        quest for quest in active_quests
        if quest.id == pending.quest_id and quest.title == pending.quest_title
    ]
    return NpcQuestActionSettle.ACCEPT if len(matches) == 1 else NpcQuestActionSettle.STOP


def dialog_semantic_fingerprint(snapshot: Mapping[str, object]) -> str:
    encoded = json.dumps({
        key: snapshot.get(key) for key in ("questActions", "dialogActions", "acceptActions")
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def serialize_pending_npc_action(value: PendingNpcQuestAction) -> dict[str, object]:
    return {"schema": 1, **value.__dict__, "action": value.action.value, "phase": value.phase.value}


def restore_pending_npc_action(raw: object) -> PendingNpcQuestAction | None:
    if raw is None:
        return None
    fields = set(PendingNpcQuestAction.__dataclass_fields__)
    if not isinstance(raw, Mapping) or raw.get("schema") != 1 or set(raw) != {"schema", *fields}:
        raise ValueError("invalid pending NPC action checkpoint")
    values = {field: raw[field] for field in fields}
    try:
        values["action"] = NpcQuestActionKind(str(values["action"]))
        values["phase"] = NpcQuestActionPhase(str(values["phase"]))
        return PendingNpcQuestAction(**values)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid pending NPC action checkpoint") from exc


def _old_action_matches(pending: PendingNpcQuestAction, snapshot: Mapping[str, object]) -> bool:
    key = "questActions" if pending.action is NpcQuestActionKind.OPEN else "dialogActions"
    raw = snapshot.get(key)
    if not isinstance(raw, list):
        return True
    return any(isinstance(item, Mapping) and str(item.get("questId") or "") == pending.quest_id
        and str(item.get("npcId") or "") == pending.npc_id and item.get("action") == pending.action.value
        and (pending.action is not NpcQuestActionKind.OPEN or " ".join(str(item.get("title") or "").casefold().split()) == " ".join(pending.expected_title.casefold().split()))
        and (pending.expected_ref is None or str(item.get("ref") or "") == pending.expected_ref)
        and (pending.expected_text is None or str(item.get("text") or "").strip() == pending.expected_text)
        for item in raw)


def _optional(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _text(value: object, limit: int) -> bool:
    return isinstance(value, str) and value == value.strip() and 0 < len(value) <= limit


def _optional_text(value: object, limit: int) -> bool:
    return isinstance(value, str) and value == value.strip() and len(value) <= limit


def _positive(value: str) -> bool:
    return _text(value, 80) and value.isdecimal() and int(value) > 0


def _npc_id(value: str) -> bool:
    return _text(value, 80) and value.isdecimal() and int(value) >= 0
