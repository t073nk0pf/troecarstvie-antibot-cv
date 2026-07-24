"""Bounded current-location NPC census orchestration over a guarded transport."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import time
from typing import Callable, Protocol

from src.antibot_cv.automation.npc_census_adapter import (
    census_observation_pair_is_causal,
    parse_area_census_observations,
    parse_dialogue_census_observation,
)
from src.antibot_cv.automation.npc_census_model import AreaNpcEndpointObservation
from src.antibot_cv.automation.npc_census_store import NpcCensusStore


class CensusCollectionStatus(str, Enum):
    COMPLETE = "complete"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class CensusCollectionResult:
    status: CensusCollectionStatus
    location_id: str | None
    observed_endpoints: int
    inspected_endpoints: int
    reason: str


class CensusTransport(Protocol):
    def area_snapshot(self) -> object: ...
    def inspect_endpoint(self, endpoint: AreaNpcEndpointObservation) -> object: ...
    def open_area(self) -> bool: ...


class NpcCensusCollector:
    def __init__(
        self,
        *,
        actor_key: str,
        transport: CensusTransport,
        store: NpcCensusStore,
        max_endpoints: int = 100,
        max_area_polls: int = 5,
        poll_delay_s: float = 0.2,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not isinstance(actor_key, str) or not actor_key:
            raise ValueError("census actor key is invalid")
        if type(max_endpoints) is not int or not 1 <= max_endpoints <= 100:
            raise ValueError("census endpoint bound is invalid")
        if type(max_area_polls) is not int or not 1 <= max_area_polls <= 20:
            raise ValueError("census poll bound is invalid")
        self.actor_key = actor_key
        self.transport = transport
        self.store = store
        self.max_endpoints = max_endpoints
        self.max_area_polls = max_area_polls
        self.poll_delay_s = max(0.0, min(2.0, float(poll_delay_s)))
        self.sleep = sleep

    def collect_current_location(self) -> CensusCollectionResult:
        try:
            authority = self._observe_area()
        except (ValueError, OSError):
            return CensusCollectionResult(
                CensusCollectionStatus.BLOCKED, None, 0, 0, "area_authority_invalid",
            )
        if len(authority) > self.max_endpoints:
            return CensusCollectionResult(
                CensusCollectionStatus.BLOCKED, authority[0].location_id if authority else None,
                len(authority), 0, "endpoint_bound_exceeded",
            )
        location_id = authority[0].location_id if authority else None
        endpoint_contract = tuple(
            (item.endpoint_id, item.endpoint_name, item.route_ref) for item in authority
        )
        completed = self._completed_contracts()
        inspected = 0
        for initial in authority:
            key = self._endpoint_contract(initial)
            if key in completed:
                continue
            try:
                current = self._find_endpoint(authority, initial.endpoint_id)
                response = self.transport.inspect_endpoint(current)
                dialogue = parse_dialogue_census_observation(
                    response,
                    actor_key=self.actor_key,
                    causal_area=current,
                )
                self.store.append_dialogue(dialogue)
                self.store.save()
            except (ValueError, OSError):
                return CensusCollectionResult(
                    CensusCollectionStatus.BLOCKED, initial.location_id,
                    len(authority), inspected, "inspection_not_reconciled",
                )
            inspected += 1
            completed.add(key)
            if not self.transport.open_area():
                return CensusCollectionResult(
                    CensusCollectionStatus.BLOCKED, initial.location_id,
                    len(authority), inspected, "return_not_issued",
                )
            refreshed = self._poll_same_area(initial.location_id)
            if refreshed is None:
                return CensusCollectionResult(
                    CensusCollectionStatus.BLOCKED, initial.location_id,
                    len(authority), inspected, "return_not_reconciled",
                )
            authority = refreshed
            refreshed_contract = tuple(
                (item.endpoint_id, item.endpoint_name, item.route_ref) for item in authority
            )
            if refreshed_contract != endpoint_contract:
                return CensusCollectionResult(
                    CensusCollectionStatus.BLOCKED, initial.location_id,
                    len(authority), inspected, "area_endpoint_set_changed",
                )
        return CensusCollectionResult(
            CensusCollectionStatus.COMPLETE, location_id, len(authority), inspected,
            "current_location_complete",
        )

    def _observe_area(self) -> tuple[AreaNpcEndpointObservation, ...]:
        observations = parse_area_census_observations(
            self.transport.area_snapshot(), actor_key=self.actor_key,
        )
        for item in observations:
            self.store.append_area(item)
        self.store.save()
        return observations

    def _poll_same_area(
        self, location_id: str,
    ) -> tuple[AreaNpcEndpointObservation, ...] | None:
        for attempt in range(self.max_area_polls):
            try:
                observed = self._observe_area()
            except (ValueError, OSError):
                observed = ()
            if observed and all(item.location_id == location_id for item in observed):
                return observed
            if attempt + 1 < self.max_area_polls:
                self.sleep(self.poll_delay_s)
        return None

    @staticmethod
    def _find_endpoint(
        observations: tuple[AreaNpcEndpointObservation, ...], endpoint_id: str,
    ) -> AreaNpcEndpointObservation:
        matches = tuple(item for item in observations if item.endpoint_id == endpoint_id)
        if len(matches) != 1:
            raise ValueError("census endpoint authority changed")
        return matches[0]

    @staticmethod
    def _endpoint_contract(item: AreaNpcEndpointObservation) -> tuple[str, ...]:
        return (
            item.actor_key, item.location_id, item.endpoint_id,
            item.endpoint_name, item.route_ref or "",
        )

    def _completed_contracts(self) -> set[tuple[str, ...]]:
        result: set[tuple[str, ...]] = set()
        areas = {
            (item.actor_key, item.snapshot_id, item.location_id, item.endpoint_id): item
            for item in self.store.areas
        }
        for dialogue in self.store.dialogues:
            area = areas.get((
                dialogue.actor_key, dialogue.causal_area_snapshot_id,
                dialogue.location_id, dialogue.endpoint_id,
            ))
            if area is not None and census_observation_pair_is_causal(area, dialogue):
                result.add(self._endpoint_contract(area))
        return result
