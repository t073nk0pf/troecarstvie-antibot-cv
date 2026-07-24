"""Pure bounded planner for resumable NPC census collection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


MAX_CENSUS_ENDPOINTS = 256


class CensusIntentKind(str, Enum):
    OBSERVE_AREA = "observe_area"
    OPEN_ENDPOINT = "open_endpoint"
    OBSERVE_DIALOGUE = "observe_dialogue"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class CensusEndpoint:
    location_id: str
    endpoint_id: str
    endpoint_name: str
    route_ref: str

    def __post_init__(self) -> None:
        if not _identity(self.location_id, positive=True):
            raise ValueError("census location identity is invalid")
        if not _identity(self.endpoint_id, positive=False):
            raise ValueError("census endpoint identity is invalid")
        if not _identity(self.route_ref, positive=True):
            raise ValueError("census route identity is invalid")
        if (
            not isinstance(self.endpoint_name, str)
            or not self.endpoint_name
            or self.endpoint_name != self.endpoint_name.strip()
            or len(self.endpoint_name) > 180
            or any(ord(char) < 32 or ord(char) == 127 for char in self.endpoint_name)
        ):
            raise ValueError("census endpoint name is invalid")


@dataclass(frozen=True, slots=True)
class CensusCursor:
    location_id: str
    endpoints: tuple[CensusEndpoint, ...] = ()
    endpoint_index: int = 0
    phase: CensusIntentKind = CensusIntentKind.OBSERVE_AREA

    def __post_init__(self) -> None:
        if not _identity(self.location_id, positive=True):
            raise ValueError("census cursor location is invalid")
        if not isinstance(self.endpoints, tuple) or any(not isinstance(item, CensusEndpoint) for item in self.endpoints):
            raise ValueError("census cursor endpoints are invalid")
        if len(self.endpoints) > MAX_CENSUS_ENDPOINTS:
            raise ValueError("census cursor endpoint hard cap exceeded")
        if any(item.location_id != self.location_id for item in self.endpoints):
            raise ValueError("census cursor endpoint location mismatches")
        keys = tuple((int(item.endpoint_id), item.endpoint_name) for item in self.endpoints)
        if len({item.endpoint_id for item in self.endpoints}) != len(self.endpoints) or keys != tuple(sorted(keys)):
            raise ValueError("census cursor endpoints are ambiguous or unordered")
        if len({item.route_ref for item in self.endpoints}) != len(self.endpoints):
            raise ValueError("census cursor routes are ambiguous")
        if isinstance(self.endpoint_index, bool) or not isinstance(self.endpoint_index, int):
            raise ValueError("census cursor index is invalid")
        if self.phase is CensusIntentKind.OBSERVE_AREA:
            valid = not self.endpoints and self.endpoint_index == 0
        elif self.phase in {CensusIntentKind.OPEN_ENDPOINT, CensusIntentKind.OBSERVE_DIALOGUE}:
            valid = bool(self.endpoints) and 0 <= self.endpoint_index < len(self.endpoints)
        elif self.phase is CensusIntentKind.COMPLETE:
            valid = self.endpoint_index == len(self.endpoints)
        else:
            valid = False
        if not valid:
            raise ValueError("census cursor phase is inconsistent")


@dataclass(frozen=True, slots=True)
class CensusIntent:
    kind: CensusIntentKind
    endpoint: CensusEndpoint | None = None


def next_census_intent(cursor: CensusCursor) -> CensusIntent:
    if cursor.phase is CensusIntentKind.OBSERVE_AREA:
        return CensusIntent(CensusIntentKind.OBSERVE_AREA)
    if cursor.endpoint_index >= len(cursor.endpoints):
        return CensusIntent(CensusIntentKind.COMPLETE)
    endpoint = cursor.endpoints[cursor.endpoint_index]
    return CensusIntent(cursor.phase, endpoint)


def area_observed(
    cursor: CensusCursor,
    endpoints: tuple[CensusEndpoint, ...],
    *,
    max_endpoints: int = MAX_CENSUS_ENDPOINTS,
) -> CensusCursor:
    if cursor.phase is not CensusIntentKind.OBSERVE_AREA:
        raise ValueError("area observation is out of phase")
    if isinstance(max_endpoints, bool) or not isinstance(max_endpoints, int) or max_endpoints <= 0:
        raise ValueError("max_endpoints is invalid")
    if not isinstance(endpoints, tuple) or len(endpoints) > max_endpoints:
        raise ValueError("area endpoint hard cap exceeded")
    if any(not isinstance(item, CensusEndpoint) for item in endpoints):
        raise ValueError("area endpoints are invalid")
    ordered = tuple(sorted(endpoints, key=lambda item: (int(item.endpoint_id), item.endpoint_name)))
    if len({(item.location_id, item.endpoint_id) for item in ordered}) != len(ordered):
        raise ValueError("area endpoints are ambiguous")
    if len({item.route_ref for item in ordered}) != len(ordered):
        raise ValueError("area endpoint routes are ambiguous")
    if any(item.location_id != cursor.location_id for item in ordered):
        raise ValueError("area endpoint location mismatches")
    phase = CensusIntentKind.OPEN_ENDPOINT if ordered else CensusIntentKind.COMPLETE
    return CensusCursor(cursor.location_id, ordered, 0, phase)


def endpoint_opened(cursor: CensusCursor) -> CensusCursor:
    if cursor.phase is not CensusIntentKind.OPEN_ENDPOINT or cursor.endpoint_index >= len(cursor.endpoints):
        raise ValueError("endpoint open is out of phase")
    return CensusCursor(cursor.location_id, cursor.endpoints, cursor.endpoint_index, CensusIntentKind.OBSERVE_DIALOGUE)


def dialogue_observed(cursor: CensusCursor) -> CensusCursor:
    if cursor.phase is not CensusIntentKind.OBSERVE_DIALOGUE:
        raise ValueError("dialogue observation is out of phase")
    next_index = cursor.endpoint_index + 1
    phase = CensusIntentKind.COMPLETE if next_index >= len(cursor.endpoints) else CensusIntentKind.OPEN_ENDPOINT
    return CensusCursor(cursor.location_id, cursor.endpoints, next_index, phase)


def _identity(value: object, *, positive: bool) -> bool:
    return (
        isinstance(value, str)
        and value.isascii()
        and value.isdecimal()
        and len(value) <= 16
        and str(int(value)) == value
        and int(value) <= 9_007_199_254_740_991
        and (int(value) > 0 if positive else int(value) >= 0)
    )
