"""Causal two-observation gate before a quest dialogue answer mutation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Mapping
from urllib.parse import urlsplit

from src.antibot_cv.automation.quest_catalog_navigation import exact_game_origin_href, snapshot_epoch_seconds
from src.antibot_cv.automation.quest_npc_action_journal import dialog_semantic_fingerprint
from src.antibot_cv.automation.quest_npc_open_navigation import PendingNpcOpen


class AnswerStabilityStatus(str, Enum):
    WAIT = "wait"
    READY = "ready"
    STOP = "stop"


@dataclass(frozen=True)
class StableAnswerCandidate:
    client_id: str
    quest_id: str
    npc_id: str
    snapshot_id: str
    generated_at: float
    semantic_fingerprint: str
    ref: str
    point_id: str | None
    text: str


def answer_snapshot_can_wait(
    stage: PendingNpcOpen, snapshot: object, *, client_id: str,
    profile_id: str, tab_id: int | None, now: float,
) -> bool:
    """Return whether an ambiguous frame is safe to replace with a fresher read."""

    return _identity_valid(
        stage, snapshot, client_id=client_id, profile_id=profile_id,
        tab_id=tab_id, now=now,
    )


def assess_answer_stability(
    previous: StableAnswerCandidate | None,
    stage: PendingNpcOpen,
    snapshot: object,
    decision: object,
    *,
    client_id: str,
    profile_id: str,
    tab_id: int | None,
    now: float,
) -> tuple[AnswerStabilityStatus, StableAnswerCandidate | None]:
    if not _identity_valid(
        stage, snapshot, client_id=client_id, profile_id=profile_id,
        tab_id=tab_id, now=now,
    ):
        return AnswerStabilityStatus.STOP, None
    intent = getattr(getattr(decision, "intent", None), "value", None)
    metadata = getattr(decision, "action_metadata", None)
    if intent != "ANSWER_DIALOG" or not isinstance(metadata, Mapping) or not isinstance(snapshot, Mapping):
        return AnswerStabilityStatus.STOP, None
    expected_ref = str(metadata.get("expected_ref") or "")
    expected_text = str(metadata.get("expected_text") or "").strip()
    actions = snapshot.get("dialogActions")
    matches = [] if not isinstance(actions, list) else [
        item for item in actions
        if isinstance(item, Mapping)
        and str(item.get("questId") or "") == stage.quest_id
        and str(item.get("npcId") or "") == stage.npc_id
        and item.get("action") == "answer"
        and str(item.get("ref") or "") == expected_ref
        and str(item.get("text") or "").strip() == expected_text
        and item.get("visible") is True and item.get("disabled") is False
    ]
    if len(matches) != 1:
        return AnswerStabilityStatus.STOP, None
    generated = snapshot_epoch_seconds(snapshot.get("generatedAt"))
    assert generated is not None
    point = str(matches[0].get("pointId") or "").strip() or None
    candidate = StableAnswerCandidate(
        client_id, stage.quest_id, stage.npc_id,
        str(snapshot.get("snapshotId") or ""), generated,
        dialog_semantic_fingerprint(snapshot), expected_ref, point, expected_text,
    )
    if (
        stage.answer_ambiguity_generated_at is not None
        and (
            generated <= stage.answer_ambiguity_generated_at
            or candidate.snapshot_id == stage.answer_ambiguity_snapshot_id
        )
    ):
        return AnswerStabilityStatus.WAIT, None
    if previous is None:
        return AnswerStabilityStatus.WAIT, candidate
    same_action = (
        previous.client_id == candidate.client_id
        and previous.quest_id == candidate.quest_id
        and previous.npc_id == candidate.npc_id
        and previous.semantic_fingerprint == candidate.semantic_fingerprint
        and previous.ref == candidate.ref
        and previous.point_id == candidate.point_id
        and previous.text == candidate.text
    )
    fresh_successor = (
        previous.snapshot_id != candidate.snapshot_id
        and previous.generated_at < candidate.generated_at
    )
    if same_action and fresh_successor:
        return AnswerStabilityStatus.READY, candidate
    return AnswerStabilityStatus.WAIT, candidate


def _identity_valid(
    stage: PendingNpcOpen, snapshot: object, *, client_id: str,
    profile_id: str, tab_id: int | None, now: float,
) -> bool:
    if (
        client_id != stage.client_id or profile_id != stage.profile_id or tab_id != stage.tab_id
        or not math.isfinite(now) or now >= stage.deadline
    ):
        return False
    if not isinstance(snapshot, Mapping):
        return False
    generated = snapshot_epoch_seconds(snapshot.get("generatedAt"))
    snapshot_id = str(snapshot.get("snapshotId") or "")
    headers = snapshot.get("matchingHeaders")
    href = str(snapshot.get("href") or "")
    parsed = urlsplit(href)
    return bool(
        snapshot.get("ok") is True and snapshot.get("truncated") is False
        and snapshot.get("pageKind") == "npc" and snapshot.get("identityMatches") is True
        and snapshot.get("expectedName") == stage.giver_name
        and str(snapshot.get("expectedNpcId") or "") == stage.npc_id
        and str(snapshot.get("npcId") or "") == stage.npc_id
        and snapshot_id.startswith("npc-dialog-") and len(snapshot_id) <= 120
        and isinstance(headers, list) and len(headers) == 1
        and bool(str(headers[0] or "").strip())
        and generated is not None and stage.issued_at < generated <= now
        and now - generated <= 5.0
        and exact_game_origin_href(href) and parsed.path == "/npc.php"
    )
