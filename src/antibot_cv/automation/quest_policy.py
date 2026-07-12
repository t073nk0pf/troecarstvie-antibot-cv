"""Pure, identity-bound quest selection for the leveling controller.

The module accepts observations only and returns one declarative intent.  It
never opens a page, follows a route, attacks a target, or performs network I/O.
Quest-driven automation is intentionally strict: only one fresh active combat
quest with one explicit mob and at most one explicit location can drive live
actions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import math
import re
import time
import unicodedata
from typing import Any, Callable, Mapping, Sequence


class QuestIntent(str, Enum):
    START_FARM = "START_FARM"
    SELECT_QUEST = "SELECT_QUEST"
    NAVIGATE = "NAVIGATE"
    COMPLETE = "COMPLETE"
    STOP_UNSAFE = "STOP_UNSAFE"


class QuestStatus(str, Enum):
    ACTIVE = "active"
    AVAILABLE = "available"
    COMPLETED = "completed"
    UNKNOWN = "unknown"


class QuestObjectiveKind(str, Enum):
    COMBAT = "combat"
    DELIVERY = "delivery"
    RETURN = "return"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class QuestIdentity:
    client_id: str
    profile_id: str
    tab_id: int


@dataclass(frozen=True)
class Quest:
    id: str | None
    title: str | None
    target_mobs: tuple[str, ...] = ()
    locations: tuple[str, ...] = ()
    status: QuestStatus = QuestStatus.UNKNOWN
    objective_kind: QuestObjectiveKind = QuestObjectiveKind.UNKNOWN
    suitable: bool = True


@dataclass(frozen=True)
class QuestSnapshot:
    status: str
    quests: tuple[Quest, ...] = ()
    current_location: str | None = None
    stale: bool = False
    snapshot_id: str | None = None
    generated_at: float | None = None
    client_id: str | None = None
    profile_id: str | None = None
    tab_id: int | None = None
    href: str | None = None
    page_kind: str | None = None


@dataclass(frozen=True)
class QuestDecision:
    intent: QuestIntent
    reason: str
    quest_id: str | None = None
    quest_title: str | None = None
    target_mobs: tuple[str, ...] = ()
    locations: tuple[str, ...] = ()
    snapshot_id: str | None = None

    @property
    def mob_names(self) -> tuple[str, ...]:
        return self.target_mobs

    @property
    def target_locations(self) -> tuple[str, ...]:
        return self.locations


class QuestPolicy:
    """Return a deterministic next intent from one authoritative snapshot."""

    def __init__(
        self,
        required_character_name: str,
        *,
        require_active_quest: bool = True,
        require_route_location: bool = True,
        expected_identity: QuestIdentity | None = None,
        max_snapshot_age_s: float = 15.0,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.required_character_name = _text(required_character_name)
        self.require_active_quest = require_active_quest
        self.require_route_location = require_route_location
        self.expected_identity = expected_identity
        self.max_snapshot_age_s = max_snapshot_age_s
        self.now = now

    def decide(
        self,
        *,
        character_name: str | None,
        current_level: int | None,
        current_xp: int | float | None,
        goal_level: int | None,
        quest_state: QuestSnapshot | Mapping[str, Any] | None,
        quests: Sequence[Quest | Mapping[str, Any]] | None = None,
    ) -> QuestDecision:
        if not self._configuration_valid():
            return _unsafe("invalid_policy_configuration")
        if (
            not _valid_text(character_name)
            or _normalize(character_name) != _normalize(self.required_character_name)
            or not _nonnegative_int(current_level)
            or not _finite_nonnegative_number(current_xp)
            or not _positive_int(goal_level)
        ):
            return _unsafe("invalid_or_mismatched_player_observation")
        if current_level >= goal_level:
            return QuestDecision(QuestIntent.COMPLETE, "goal_reached")

        snapshot = _coerce_snapshot(quest_state, quests)
        invalid_reason = self._snapshot_invalid_reason(snapshot)
        if invalid_reason:
            return _unsafe(invalid_reason, snapshot)
        assert snapshot is not None

        active = [quest for quest in snapshot.quests if quest.status is QuestStatus.ACTIVE]
        malformed_active = [quest for quest in active if not _quest_valid(quest)]
        if malformed_active:
            return _unsafe("active_quest_malformed", snapshot)
        combat = [
            quest
            for quest in active
            if quest.suitable and quest.objective_kind is QuestObjectiveKind.COMBAT
        ]
        if not combat:
            if self.require_active_quest:
                return _unsafe("no_active_combat_quest", snapshot)
            return QuestDecision(QuestIntent.START_FARM, "quest_optional", snapshot_id=snapshot.snapshot_id)
        if len(combat) != 1:
            return _unsafe("ambiguous_active_combat_quests", snapshot)

        quest = combat[0]
        if len(quest.target_mobs) != 1:
            return _unsafe(
                "combat_target_missing" if not quest.target_mobs else "ambiguous_combat_targets",
                snapshot,
            )
        if len(quest.locations) > 1:
            return _unsafe("ambiguous_quest_route", snapshot)
        if self.require_route_location and len(quest.locations) != 1:
            return _unsafe("quest_route_missing", snapshot)
        if quest.locations and not _valid_text(snapshot.current_location):
            return _unsafe("current_location_unknown", snapshot)

        common = dict(
            quest_id=quest.id,
            quest_title=quest.title,
            target_mobs=quest.target_mobs,
            locations=quest.locations,
            snapshot_id=snapshot.snapshot_id,
        )
        if quest.locations and _normalize(snapshot.current_location) != _normalize(quest.locations[0]):
            return QuestDecision(QuestIntent.NAVIGATE, "quest_location_differs", **common)
        return QuestDecision(QuestIntent.SELECT_QUEST, "active_combat_quest_confirmed", **common)

    def _configuration_valid(self) -> bool:
        return (
            bool(self.required_character_name)
            and isinstance(self.require_active_quest, bool)
            and isinstance(self.require_route_location, bool)
            and isinstance(self.max_snapshot_age_s, (int, float))
            and not isinstance(self.max_snapshot_age_s, bool)
            and math.isfinite(float(self.max_snapshot_age_s))
            and self.max_snapshot_age_s > 0
        )

    def _snapshot_invalid_reason(self, snapshot: QuestSnapshot | None) -> str | None:
        if snapshot is None:
            return "quest_snapshot_unavailable"
        if snapshot.stale or snapshot.status != "loaded":
            return "quest_snapshot_unavailable"
        if not snapshot.snapshot_id or not _finite_nonnegative_number(snapshot.generated_at):
            return "quest_snapshot_identity_missing"
        assert snapshot.generated_at is not None
        age = self.now() - snapshot.generated_at
        if age < 0 or age > self.max_snapshot_age_s:
            return "quest_snapshot_stale"
        if (
            not snapshot.client_id
            or not snapshot.profile_id
            or not _nonnegative_int(snapshot.tab_id)
            or not snapshot.href
            or snapshot.page_kind != "quests"
        ):
            return "quest_snapshot_identity_missing"
        if self.expected_identity is not None and (
            snapshot.profile_id != self.expected_identity.profile_id
            or snapshot.tab_id != self.expected_identity.tab_id
        ):
            return "quest_snapshot_identity_mismatch"
        return None


def _coerce_snapshot(
    value: QuestSnapshot | Mapping[str, Any] | None,
    quests: Sequence[Quest | Mapping[str, Any]] | None,
) -> QuestSnapshot | None:
    if isinstance(value, QuestSnapshot):
        items = value.quests if quests is None else _coerce_quests(quests)
        return QuestSnapshot(**{**value.__dict__, "quests": tuple(items)})
    if not isinstance(value, Mapping):
        return None
    data = value.get("data") if isinstance(value.get("data"), Mapping) else value
    raw_items = quests if quests is not None else data.get("items")
    if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
        return None
    generated_at = _timestamp(value.get("generatedAt", data.get("generatedAt")))
    load_status = data.get("loadStatus", value.get("loadStatus", value.get("status")))
    return QuestSnapshot(
        status=str(load_status or ""),
        quests=_coerce_quests(raw_items),
        current_location=_clean_optional(data.get("currentLocation", data.get("current_location"))),
        stale=_strict_bool(value.get("stale", data.get("stale", False))) is True,
        snapshot_id=_clean_optional(value.get("snapshotId", data.get("snapshotId"))),
        generated_at=generated_at,
        client_id=_clean_optional(value.get("clientId", data.get("clientId"))),
        profile_id=_clean_optional(value.get("profileId", data.get("profileId"))),
        tab_id=_strict_int(value.get("tabId", data.get("tabId"))),
        href=_clean_optional(value.get("href", data.get("href"))),
        page_kind=_clean_optional(value.get("pageKind", data.get("pageKind"))),
    )


def _coerce_quests(items: Sequence[Quest | Mapping[str, Any]]) -> tuple[Quest, ...]:
    result: list[Quest] = []
    for item in items:
        if isinstance(item, Quest):
            result.append(item)
        elif isinstance(item, Mapping):
            result.append(_quest_from_mapping(item))
        else:
            result.append(Quest(None, None))
    return tuple(result)


def _quest_from_mapping(item: Mapping[str, Any]) -> Quest:
    status = _enum_value(QuestStatus, item.get("status"), QuestStatus.UNKNOWN)
    kind = _enum_value(
        QuestObjectiveKind,
        item.get("objectiveKind", item.get("objective_kind")),
        QuestObjectiveKind.UNKNOWN,
    )
    suitable = _strict_bool(item.get("suitable", True))
    return Quest(
        id=_clean_optional(item.get("id", item.get("questId", item.get("quest_id")))),
        title=_clean_optional(item.get("title", item.get("name"))),
        target_mobs=_explicit_names(item, "targetMobs", "target_mobs", "targetMobNames", "mob_names"),
        locations=_explicit_names(item, "locations", "targetLocations", "target_locations", "locationNames"),
        status=status,
        objective_kind=kind,
        suitable=suitable is True,
    )


def _quest_valid(quest: Quest) -> bool:
    return (
        isinstance(quest, Quest)
        and _valid_text(quest.id)
        and _valid_text(quest.title)
        and isinstance(quest.status, QuestStatus)
        and isinstance(quest.objective_kind, QuestObjectiveKind)
        and isinstance(quest.suitable, bool)
        and all(_valid_text(value) for value in (*quest.target_mobs, *quest.locations))
    )


def _explicit_names(item: Mapping[str, Any], *keys: str) -> tuple[str, ...]:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str):
            values = [value]
        elif isinstance(value, Sequence) and not isinstance(value, (bytes, str)):
            values = list(value)
        else:
            continue
        cleaned = [_clean_optional(candidate) for candidate in values]
        return tuple(dict.fromkeys(candidate for candidate in cleaned if candidate is not None))
    return ()


def _enum_value(enum_type: type[Enum], value: object, default: Enum) -> Any:
    try:
        return enum_type(str(value or "").strip().casefold())
    except ValueError:
        return default


def _timestamp(value: object) -> float | None:
    if _finite_nonnegative_number(value):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except ValueError:
        return None


def _strict_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _strict_int(value: object) -> int | None:
    return value if _nonnegative_int(value) else None


def _normalize(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).replace("ё", "е").replace("Ё", "Е")
    text = re.sub(r"[–—−]", "-", text)
    return " ".join(text.casefold().split())


def _text(value: object) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split())


def _clean_optional(value: object) -> str | None:
    return _text(value) if _valid_text(value) else None


def _valid_text(value: object) -> bool:
    return isinstance(value, str) and bool(_text(value))


def _nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _positive_int(value: object) -> bool:
    return _nonnegative_int(value) and value > 0


def _finite_nonnegative_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and value >= 0
    )


def _unsafe(reason: str, snapshot: QuestSnapshot | None = None) -> QuestDecision:
    return QuestDecision(QuestIntent.STOP_UNSAFE, reason, snapshot_id=None if snapshot is None else snapshot.snapshot_id)


Intent = QuestIntent
QuestPlanner = QuestPolicy
