"""Action-free eligibility and bounded evidence for available quest entries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from src.antibot_cv.automation.quest_director_policy import QuestRef


AVAILABLE_ELIGIBILITY_CAPABILITY_VERSION = "available_eligibility_v2"


@dataclass(frozen=True)
class UnsupportedAvailableQuest:
    quest_id: str
    title: str
    catalog_page: int | None
    observed_location: str | None
    observed_giver_names: tuple[str, ...]
    missing_codes: tuple[str, ...]
    reason: str
    capability_version: str = AVAILABLE_ELIGIBILITY_CAPABILITY_VERSION

    def __post_init__(self) -> None:
        if (
            not _bounded(self.quest_id, 80)
            or not self.quest_id.isdecimal()
            or int(self.quest_id) <= 0
            or not _bounded(self.title, 500)
            or (
                self.catalog_page is not None
                and (
                    isinstance(self.catalog_page, bool)
                    or not isinstance(self.catalog_page, int)
                    or self.catalog_page < 0
                    or self.catalog_page > 100
                )
            )
            or (self.observed_location is not None and not _bounded(self.observed_location, 180))
            or len(self.observed_giver_names) > 20
            or any(not _bounded(name, 180) for name in self.observed_giver_names)
            or not self.missing_codes
            or len(self.missing_codes) > 8
            or any(not _code(value) for value in self.missing_codes)
            or not _code(self.reason)
            or not _code(self.capability_version)
        ):
            raise ValueError("invalid unsupported available quest evidence")


@dataclass(frozen=True)
class AvailableQuestEligibility:
    eligible: tuple[QuestRef, ...]
    unsupported: tuple[UnsupportedAvailableQuest, ...]
    conflict_reason: str | None = None


def classify_available_quest_refs(
    refs: Iterable[QuestRef], *, staged_ref: QuestRef | None = None
) -> AvailableQuestEligibility:
    """Split refs without fingerprinting incomplete entries; reconstruct one safe stage."""

    eligible: list[QuestRef] = []
    unsupported: list[UnsupportedAvailableQuest] = []
    seen_ids: set[str] = set()
    for ref in refs:
        if staged_ref is not None and isinstance(ref, QuestRef) and ref.id == staged_ref.id:
            reconstructed, conflict = _reconstruct_staged_ref(ref, staged_ref)
            if conflict is not None:
                return AvailableQuestEligibility(tuple(eligible), tuple(unsupported), conflict)
            if reconstructed is not None:
                eligible.append(reconstructed)
                seen_ids.add(ref.id)
                continue
        missing_codes = _missing_codes(ref)
        quest_id = str(ref.id if isinstance(ref, QuestRef) else "").strip()
        if quest_id in seen_ids:
            missing_codes = (*missing_codes, "duplicate_quest_id")
        seen_ids.add(quest_id)
        if not missing_codes:
            eligible.append(ref)
        else:
            unsupported.append(_unsupported_evidence(ref, missing_codes))
    return AvailableQuestEligibility(tuple(eligible), tuple(unsupported))


def serialize_unsupported_available(item: UnsupportedAvailableQuest) -> dict[str, object]:
    return {
        "quest_id": item.quest_id,
        "title": item.title,
        "catalog_page": item.catalog_page,
        "observed_location": item.observed_location,
        "observed_giver_names": list(item.observed_giver_names),
        "missing_codes": list(item.missing_codes),
        "reason": item.reason,
        "capability_version": item.capability_version,
    }


def restore_unsupported_available_entries(
    raw: object, *, max_items: int
) -> tuple[UnsupportedAvailableQuest, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)) or len(raw) > max_items:
        raise ValueError("invalid unsupported available quest history")
    result: list[UnsupportedAvailableQuest] = []
    seen: set[str] = set()
    for value in raw:
        if not isinstance(value, Mapping) or set(value) != {
            "quest_id", "title", "catalog_page", "observed_location",
            "observed_giver_names", "missing_codes", "reason", "capability_version",
        }:
            raise ValueError("invalid unsupported available quest evidence")
        givers = value.get("observed_giver_names")
        codes = value.get("missing_codes")
        if not isinstance(givers, (list, tuple)) or not isinstance(codes, (list, tuple)):
            raise ValueError("invalid unsupported available quest evidence")
        item = UnsupportedAvailableQuest(
            quest_id=str(value.get("quest_id") or "").strip(),
            title=str(value.get("title") or "").strip(),
            catalog_page=value.get("catalog_page") if value.get("catalog_page") is None else _strict_int(value.get("catalog_page")),
            observed_location=str(value.get("observed_location") or "").strip() or None,
            observed_giver_names=tuple(str(name or "").strip() for name in givers),
            missing_codes=tuple(str(code or "").strip() for code in codes),
            reason=str(value.get("reason") or "").strip(),
            capability_version=str(value.get("capability_version") or "").strip(),
        )
        if item.quest_id in seen:
            raise ValueError("duplicate unsupported available quest evidence")
        seen.add(item.quest_id)
        result.append(item)
    return tuple(result)


def _reconstruct_staged_ref(raw: QuestRef, staged: QuestRef) -> tuple[QuestRef | None, str | None]:
    if raw.title != staged.title:
        return None, "staged_available_title_conflict"
    if raw.location is not None and raw.location != staged.location:
        return None, "staged_available_location_conflict"
    if raw.giver_names and raw.giver_names != staged.giver_names:
        return None, "staged_available_giver_conflict"
    if _missing_codes(staged):
        return None, "staged_available_durable_identity_invalid"
    return QuestRef(
        staged.id, staged.title, staged.accept_ref, staged.location,
        staged.giver_names, raw.catalog_page,
    ), None


def _unsupported_evidence(ref: object, missing_codes: tuple[str, ...]) -> UnsupportedAvailableQuest:
    if not isinstance(ref, QuestRef):
        raise ValueError("available quest reference is invalid")
    return UnsupportedAvailableQuest(
        quest_id=ref.id,
        title=ref.title,
        catalog_page=ref.catalog_page,
        observed_location=ref.location,
        observed_giver_names=ref.giver_names,
        missing_codes=missing_codes,
        reason=missing_codes[0],
    )


def _missing_codes(ref: object) -> tuple[str, ...]:
    if not isinstance(ref, QuestRef):
        return ("invalid_reference",)
    result: list[str] = []
    if not _bounded(ref.id, 80) or not ref.id.isdecimal() or int(ref.id) <= 0:
        result.append("invalid_quest_id")
    if not _bounded(ref.title, 500):
        result.append("missing_or_invalid_title")
    if not _bounded(ref.location, 180):
        result.append("missing_or_invalid_location")
    if len(ref.giver_names) == 0:
        result.append("missing_giver")
    elif len(ref.giver_names) != 1:
        result.append("multiple_givers")
    elif not _bounded(ref.giver_names[0], 180):
        result.append("invalid_giver")
    if ref.accept_ref is not None and not _bounded(ref.accept_ref, 120):
        result.append("invalid_accept_ref")
    if ref.catalog_page is not None and (
        isinstance(ref.catalog_page, bool)
        or not isinstance(ref.catalog_page, int)
        or not 0 <= ref.catalog_page <= 100
    ):
        result.append("invalid_catalog_page")
    return tuple(result)


def _bounded(value: object, limit: int) -> bool:
    return isinstance(value, str) and value == value.strip() and 0 < len(value) <= limit


def _code(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 80 and all(ch.islower() or ch.isdigit() or ch == "_" for ch in value)


def _strict_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("integer required")
    return value
