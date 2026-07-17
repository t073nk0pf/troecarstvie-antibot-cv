"""Durable, action-free quarantine evidence for exact quest intake references."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Mapping

from src.antibot_cv.automation.quest_director_policy import QuestRef


INTAKE_CAPABILITY_VERSION = "quest_intake_v1"


@dataclass(frozen=True)
class IntakeQuarantine:
    quest_ref: QuestRef
    fingerprint: str
    reason: str
    capability_version: str
    recorded_at: float

    @property
    def quest_id(self) -> str:
        return self.quest_ref.id


def intake_ref_fingerprint(quest_ref: QuestRef) -> str:
    """Hash exact intake identity; ``catalog_page`` is diagnostic and excluded."""

    payload = canonical_intake_ref(quest_ref)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_intake_ref(quest_ref: QuestRef) -> dict[str, object]:
    if not isinstance(quest_ref, QuestRef):
        raise ValueError("intake quest reference is invalid")
    quest_id = _exact_text(quest_ref.id, max_length=80)
    title = _exact_text(quest_ref.title, max_length=500)
    location = _exact_text(quest_ref.location, max_length=180)
    accept_ref = None
    if quest_ref.accept_ref is not None:
        accept_ref = _exact_text(quest_ref.accept_ref, max_length=120)
    if (
        not quest_id.isdecimal()
        or int(quest_id) <= 0
        or len(quest_ref.giver_names) != 1
    ):
        raise ValueError("intake quest reference is not authoritative")
    giver = _exact_text(quest_ref.giver_names[0], max_length=180)
    return {
        "id": quest_id,
        "title": title,
        "accept_ref": accept_ref,
        "location": location,
        "giver_names": [giver],
    }


def serialize_intake_quarantine(item: IntakeQuarantine) -> dict[str, object]:
    return {
        "quest_ref": canonical_intake_ref(item.quest_ref),
        "fingerprint": item.fingerprint,
        "reason": item.reason,
        "capability_version": item.capability_version,
        "recorded_at": item.recorded_at,
    }


def make_intake_quarantine(
    quest_ref: QuestRef,
    *,
    reason: str,
    capability_version: str,
    recorded_at: float,
) -> IntakeQuarantine:
    canonical = canonical_intake_ref(quest_ref)
    normalized_ref = _restore_canonical_ref(canonical)
    normalized_reason = str(reason or "").strip()
    normalized_capability = str(capability_version or "").strip()
    if (
        not _valid_reason(normalized_reason)
        or not _valid_capability(normalized_capability)
        or not isinstance(recorded_at, (int, float))
        or isinstance(recorded_at, bool)
        or not math.isfinite(recorded_at)
        or recorded_at <= 0
    ):
        raise ValueError("invalid intake quarantine evidence")
    return IntakeQuarantine(
        normalized_ref,
        intake_ref_fingerprint(normalized_ref),
        normalized_reason,
        normalized_capability,
        float(recorded_at),
    )


def restore_intake_quarantines(
    raw: object,
    *,
    max_items: int,
) -> tuple[IntakeQuarantine, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)) or len(raw) > max_items:
        raise ValueError("invalid intake quarantine history")
    restored: list[IntakeQuarantine] = []
    quest_ids: set[str] = set()
    for value in raw:
        if not isinstance(value, Mapping) or set(value) != {
            "quest_ref",
            "fingerprint",
            "reason",
            "capability_version",
            "recorded_at",
        }:
            raise ValueError("invalid intake quarantine evidence")
        quest_ref = _restore_canonical_ref(value.get("quest_ref"))
        fingerprint = str(value.get("fingerprint") or "").strip()
        reason = str(value.get("reason") or "").strip()
        capability = str(value.get("capability_version") or "").strip()
        timestamp = value.get("recorded_at")
        if (
            fingerprint != intake_ref_fingerprint(quest_ref)
            or not _valid_reason(reason)
            or not _valid_capability(capability)
            or not isinstance(timestamp, (int, float))
            or isinstance(timestamp, bool)
            or not math.isfinite(timestamp)
            or timestamp <= 0
            or quest_ref.id in quest_ids
        ):
            raise ValueError("invalid intake quarantine evidence")
        quest_ids.add(quest_ref.id)
        restored.append(
            IntakeQuarantine(
                quest_ref,
                fingerprint,
                reason,
                capability,
                float(timestamp),
            )
        )
    return tuple(restored)


def _restore_canonical_ref(raw: object) -> QuestRef:
    if not isinstance(raw, Mapping) or set(raw) != {
        "id",
        "title",
        "accept_ref",
        "location",
        "giver_names",
    }:
        raise ValueError("invalid intake quarantine quest reference")
    givers = raw.get("giver_names")
    if not isinstance(givers, (list, tuple)) or len(givers) != 1:
        raise ValueError("invalid intake quarantine quest reference")
    accept_ref_raw = raw.get("accept_ref")
    if accept_ref_raw is not None and not isinstance(accept_ref_raw, str):
        raise ValueError("invalid intake quarantine quest reference")
    restored = QuestRef(
        str(raw.get("id") or ""),
        str(raw.get("title") or ""),
        accept_ref_raw,
        str(raw.get("location") or ""),
        (str(givers[0] or ""),),
        None,
    )
    canonical_intake_ref(restored)
    return restored


def _exact_text(value: object, *, max_length: int) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > max_length:
        raise ValueError("intake quest reference text is invalid")
    return value


def _valid_reason(value: str) -> bool:
    return (
        bool(value)
        and len(value) <= 160
        and all(char.islower() or char.isdigit() or char == "_" for char in value)
    )


def _valid_capability(value: str) -> bool:
    return (
        bool(value)
        and len(value) <= 80
        and all(char.islower() or char.isdigit() or char in {"_", "."} for char in value)
    )
