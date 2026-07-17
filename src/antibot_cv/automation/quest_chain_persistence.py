from __future__ import annotations

from typing import Mapping

from src.antibot_cv.automation.quest_chain_state import (
    PendingTurnInCompletion,
    QuarantinedQuest,
)
from src.antibot_cv.automation.quest_director_policy import QuestRef


def restore_accepted_ref(raw: object, quest_id: str, quest_title: str) -> QuestRef | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("invalid accepted quest reference")
    givers = raw.get("giver_names")
    location = str(raw.get("location") or "").strip()
    if (
        str(raw.get("id") or "").strip() != quest_id
        or str(raw.get("title") or "").strip() != quest_title
        or not location
        or not isinstance(givers, (list, tuple))
        or len(givers) != 1
        or not str(givers[0] or "").strip()
    ):
        raise ValueError("invalid accepted quest reference")
    catalog_page = raw.get("catalog_page")
    if catalog_page is not None and (
        isinstance(catalog_page, bool)
        or not isinstance(catalog_page, int)
        or catalog_page < 0
    ):
        raise ValueError("invalid accepted quest reference")
    return QuestRef(
        quest_id, quest_title, str(raw.get("accept_ref") or "").strip() or None,
        location, (str(givers[0]).strip(),), catalog_page,
    )


def valid_reason(value: str) -> bool:
    return bool(value) and len(value) <= 160 and all(
        char.islower() or char.isdigit() or char == "_" for char in value
    )


def valid_capability(value: str) -> bool:
    return bool(value) and len(value) <= 80 and all(
        char.islower() or char.isdigit() or char in {"_", "."} for char in value
    )


def serialize_quarantine(item: QuarantinedQuest) -> dict[str, object]:
    return {
        "quest_id": item.quest_id, "quest_title": item.quest_title,
        "fingerprint": item.fingerprint, "reason": item.reason,
        "capability_version": item.capability_version, "recorded_at": item.recorded_at,
    }


def restore_quarantines(raw: object, *, max_items: int) -> tuple[QuarantinedQuest, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)) or len(raw) > max_items:
        raise ValueError("invalid quest quarantine history")
    restored: list[QuarantinedQuest] = []
    quest_ids: set[str] = set()
    for value in raw:
        if not isinstance(value, Mapping):
            raise ValueError("invalid quest quarantine evidence")
        quest_id = str(value.get("quest_id") or "").strip()
        title = str(value.get("quest_title") or "").strip()
        fingerprint = str(value.get("fingerprint") or "").strip()
        reason = str(value.get("reason") or "").strip()
        capability = str(value.get("capability_version") or "").strip()
        timestamp = value.get("recorded_at")
        if (
            not quest_id.isdecimal() or int(quest_id) <= 0 or not title or not fingerprint
            or not valid_reason(reason) or not valid_capability(capability)
            or not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool)
            or timestamp <= 0 or quest_id in quest_ids
        ):
            raise ValueError("invalid quest quarantine evidence")
        quest_ids.add(quest_id)
        restored.append(QuarantinedQuest(
            quest_id, title, fingerprint, reason, capability, float(timestamp),
        ))
    return tuple(restored)


def restore_staged_ref(raw: object) -> QuestRef | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("invalid pending accepted quest reference")
    quest_id = str(raw.get("id") or "").strip()
    quest_title = str(raw.get("title") or "").strip()
    restored = restore_accepted_ref(raw, quest_id, quest_title)
    if restored is not None:
        validate_authoritative_ref(restored)
    return restored


def serialize_ref(quest_ref: QuestRef) -> dict[str, object]:
    return {
        "id": quest_ref.id, "title": quest_ref.title, "accept_ref": quest_ref.accept_ref,
        "location": quest_ref.location, "giver_names": list(quest_ref.giver_names),
        "catalog_page": quest_ref.catalog_page,
    }


def validate_authoritative_ref(quest_ref: QuestRef) -> None:
    if (
        not isinstance(quest_ref, QuestRef) or not quest_ref.id.isdecimal()
        or int(quest_ref.id) <= 0 or not quest_ref.title or not quest_ref.location
        or len(quest_ref.giver_names) != 1 or not quest_ref.giver_names[0]
    ):
        raise ValueError("accepted quest reference is not authoritative")


def serialize_turn_in_completion(evidence: PendingTurnInCompletion) -> dict[str, object]:
    return {
        "quest_id": evidence.quest_id, "quest_title": evidence.quest_title,
        "completed_fingerprint": evidence.completed_fingerprint,
        "active_catalog_revision": evidence.active_catalog_revision,
    }


def restore_turn_in_completion(
    raw: object, *, quest_id: str, quest_title: str,
) -> PendingTurnInCompletion | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("invalid pending turn-in completion")
    fingerprint = str(raw.get("completed_fingerprint") or "").strip()
    revision = raw.get("active_catalog_revision")
    if (
        str(raw.get("quest_id") or "").strip() != quest_id
        or str(raw.get("quest_title") or "").strip() != quest_title or not fingerprint
        or isinstance(revision, bool) or not isinstance(revision, int) or revision < 0
    ):
        raise ValueError("invalid pending turn-in completion")
    return PendingTurnInCompletion(quest_id, quest_title, fingerprint, revision)
