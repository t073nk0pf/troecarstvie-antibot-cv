"""Pure, fail-closed route planning for game locations.

The planner consumes data collected by the JS navigator/map/compass bridge.  It
never clicks, reloads, follows URLs, or performs network I/O.  A caller may
execute a returned ``MOVE`` only after independently checking the same
snapshot and transition.

Accepted snapshot shape (extra fields are ignored)::

    {"current": {"name": "...", "id": "1", "url": "..."},
     "desired": {"name": "..."},
     "map": {"locations": [...], "edges": [...]},
     "compass": {"neighbors": [...]},
     "stale": False, "snapshot_id": "..."}

``locations`` may contain ``name``/``names``/``aliases`` and a confirmed
``id`` or ``url``.  Edges may use ``from``/``to`` (or ``source``/``destination``)
and optionally a direction.  Name-only endpoint references are accepted only
when they resolve to one confirmed location.
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Mapping, Sequence


class RouteAction(str, Enum):
    ARRIVED = "ARRIVED"
    MOVE = "MOVE"
    REFRESH = "REFRESH"
    STOP_UNSAFE = "STOP_UNSAFE"


@dataclass(frozen=True)
class LocationRef:
    name: str
    id: str | None = None
    url: str | None = None
    aliases: tuple[str, ...] = ()

    @property
    def confirmed(self) -> bool:
        return bool(self.id or self.url)

    @property
    def names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


@dataclass(frozen=True)
class RouteTransition:
    source: LocationRef
    destination: LocationRef
    direction: str | None = None


@dataclass(frozen=True)
class RouteDecision:
    action: RouteAction
    destination: LocationRef | None
    transition: RouteTransition | None
    reason: str
    snapshot_id: str | None = None

    @property
    def next_transition(self) -> RouteTransition | None:
        """Integration-friendly alias; there is never more than one."""
        return self.transition


@dataclass(frozen=True)
class NavigatorRouteDecision:
    action: RouteAction
    reason: str
    target: str | None = None
    route_transitions: int | None = None
    snapshot_id: str | None = None


def normalize_location_name(value: object) -> str:
    """Normalize human/JS Russian labels without inventing an identity."""
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower().replace("ё", "е")
    text = re.sub(r"[\W_]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _location(raw: object, *, fallback_name: str | None = None) -> LocationRef | None:
    if isinstance(raw, str):
        return LocationRef(raw)
    if not isinstance(raw, Mapping):
        return None
    name = _text(raw.get("name") or raw.get("label") or raw.get("title") or fallback_name)
    aliases = raw.get("aliases", raw.get("names", ()))
    if isinstance(aliases, str):
        aliases = (aliases,)
    if not isinstance(aliases, Sequence):
        aliases = ()
    if not name and aliases:
        name = _text(aliases[0])
    identity = _text(raw.get("id") or raw.get("locationId") or raw.get("location_id") or raw.get("url") or raw.get("href"))
    if not name and identity:
        name = identity
    if not name:
        return None
    return LocationRef(
        name=name,
        id=_text(raw.get("id") or raw.get("locationId") or raw.get("location_id")),
        url=_text(raw.get("url") or raw.get("href")),
        aliases=tuple(str(item).strip() for item in aliases if str(item).strip()),
    )


def _stale(snapshot: Mapping[str, Any], max_age_s: float) -> bool:
    if snapshot.get("stale") is True or snapshot.get("fresh") is False:
        return True
    age = snapshot.get("age_s", snapshot.get("snapshot_age_s"))
    if age is not None:
        try:
            return float(age) > max_age_s
        except (TypeError, ValueError):
            return True
    captured = snapshot.get("captured_at", snapshot.get("generated_at"))
    if captured:
        try:
            parsed = datetime.fromisoformat(str(captured).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return time.time() - parsed.timestamp() > max_age_s
        except (TypeError, ValueError):
            return True
    return True


class RoutePlanner:
    def __init__(self, *, max_depth: int = 32, max_snapshot_age_s: float = 15.0) -> None:
        self.max_depth = max_depth
        self.max_snapshot_age_s = max_snapshot_age_s

    def plan(self, snapshot: Mapping[str, Any], desired: object | None = None) -> RouteDecision:
        if not isinstance(snapshot, Mapping):
            return RouteDecision(RouteAction.STOP_UNSAFE, None, None, "invalid_snapshot", None)
        snapshot_id = _text(snapshot.get("snapshot_id") or snapshot.get("version"))
        if not snapshot_id:
            return RouteDecision(RouteAction.REFRESH, None, None, "snapshot_identity_missing", None)
        if _stale(snapshot, self.max_snapshot_age_s):
            return RouteDecision(RouteAction.REFRESH, None, None, "stale_snapshot", snapshot_id)
        if self.max_depth < 0:
            return RouteDecision(RouteAction.STOP_UNSAFE, None, None, "invalid_depth_limit", snapshot_id)

        current_raw = snapshot.get("current", snapshot.get("currentLocation"))
        desired_raw = desired if desired is not None else snapshot.get("desired", snapshot.get("target"))
        current = _location(current_raw)
        target = _location(desired_raw)
        if current is None or target is None or not current.confirmed or not target.name:
            return RouteDecision(RouteAction.STOP_UNSAFE, None, None, "unconfirmed_location", snapshot_id)

        locations = self._locations(snapshot)
        current = self._resolve(current, locations)
        targets = self._matches(target, locations)
        if current is None:
            return RouteDecision(RouteAction.STOP_UNSAFE, None, None, "current_location_ambiguous_or_unknown", snapshot_id)
        if len(targets) != 1:
            reason = "target_ambiguous" if len(targets) > 1 else "target_unknown"
            return RouteDecision(RouteAction.STOP_UNSAFE, None, None, reason, snapshot_id)
        target = targets[0]
        if not target.confirmed:
            return RouteDecision(RouteAction.STOP_UNSAFE, None, None, "target_unconfirmed", snapshot_id)
        if self._same(current, target):
            return RouteDecision(RouteAction.ARRIVED, target, None, "already_at_destination", snapshot_id)

        edges = self._edges(snapshot, locations)
        if edges is None:
            return RouteDecision(RouteAction.STOP_UNSAFE, target, None, "graph_ambiguous_or_invalid", snapshot_id)
        transition = self._bfs(current, target, edges)
        if transition is None:
            return RouteDecision(RouteAction.STOP_UNSAFE, target, None, "no_safe_route", snapshot_id)
        return RouteDecision(RouteAction.MOVE, transition.destination, transition, "next_bfs_transition", snapshot_id)

    @staticmethod
    def _same(left: LocationRef, right: LocationRef) -> bool:
        return bool((left.id and right.id and left.id == right.id) or (left.url and right.url and left.url == right.url))

    def _locations(self, snapshot: Mapping[str, Any]) -> list[LocationRef]:
        data = snapshot.get("map", snapshot)
        raw = data.get("locations", data.get("nodes", ())) if isinstance(data, Mapping) else ()
        result: list[LocationRef] = []
        if isinstance(raw, Mapping):
            raw = [dict(value, name=key) if isinstance(value, Mapping) else {"name": key, "id": value} for key, value in raw.items()]
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            for item in raw:
                location = _location(item)
                if location:
                    result.append(location)
        for candidate in (snapshot.get("current", snapshot.get("currentLocation")), snapshot.get("desired", snapshot.get("target"))):
            location = _location(candidate)
            if location and not any(
                self._same(location, existing)
                or any(normalize_location_name(name) == normalize_location_name(other) for name in location.names for other in existing.names)
                for existing in result
            ):
                result.append(location)
        return result

    def _matches(self, query: LocationRef, locations: list[LocationRef]) -> list[LocationRef]:
        qids = {value for value in (query.id, query.url) if value}
        query_tokens = qids or {query.name}
        matches = [
            item
            for item in locations
            if (query_tokens & {value for value in (item.id, item.url) if value})
            or any(normalize_location_name(name) == normalize_location_name(query.name) for name in item.names)
        ]
        return matches

    def _resolve(self, query: LocationRef, locations: list[LocationRef]) -> LocationRef | None:
        matches = self._matches(query, locations)
        return matches[0] if len(matches) == 1 and matches[0].confirmed else None

    def _edges(self, snapshot: Mapping[str, Any], locations: list[LocationRef]) -> list[RouteTransition] | None:
        data = snapshot.get("map", snapshot)
        raw = data.get("edges", data.get("transitions", ())) if isinstance(data, Mapping) else ()
        if not raw:
            compass = snapshot.get("compass", {})
            raw = compass.get("neighbors", ()) if isinstance(compass, Mapping) else ()
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            return None
        transitions: list[RouteTransition] = []
        for item in raw:
            if not isinstance(item, Mapping):
                return None
            source_raw = item.get("from", item.get("source"))
            destination_raw = item.get("to", item.get("destination"))
            # Compass neighbor records commonly omit ``from`` and describe
            # only the adjacent destination; the current node is then the
            # source of that one observed transition.
            if source_raw is None:
                source_raw = snapshot.get("current", snapshot.get("currentLocation"))
            if destination_raw is None and "neighbors" in (snapshot.get("compass") or {}):
                destination_raw = item
            source = self._resolve(_location(source_raw) or LocationRef(""), locations)
            destination = self._resolve(_location(destination_raw) or LocationRef(""), locations)
            if source is None or destination is None or not destination.confirmed:
                return None
            transitions.append(RouteTransition(source, destination, _text(item.get("direction"))))
        return transitions

    def _bfs(self, current: LocationRef, target: LocationRef, edges: list[RouteTransition]) -> RouteTransition | None:
        adjacency: dict[str, list[RouteTransition]] = {}
        for edge in edges:
            key = edge.source.id or edge.source.url
            if key:
                adjacency.setdefault(key, []).append(edge)
        start = current.id or current.url
        goal = target.id or target.url
        if not start or not goal:
            return None
        queue = deque([(start, 0, None)])
        seen = {start}
        while queue:
            node, depth, first = queue.popleft()
            if depth >= self.max_depth:
                continue
            for edge in adjacency.get(node, ()):
                destination_key = edge.destination.id or edge.destination.url
                if not destination_key or destination_key in seen:
                    continue
                candidate = edge if first is None else first
                if destination_key == goal:
                    return candidate
                seen.add(destination_key)
                queue.append((destination_key, depth + 1, candidate))
        return None


def plan_route(snapshot: Mapping[str, Any], desired: object | None = None, *, max_depth: int = 32, max_snapshot_age_s: float = 15.0) -> RouteDecision:
    """Functional integration entry point; returns exactly one next transition."""
    return RoutePlanner(max_depth=max_depth, max_snapshot_age_s=max_snapshot_age_s).plan(snapshot, desired)


def validate_navigator_route(
    snapshot: Mapping[str, Any],
    expected_target: str,
    *,
    max_transitions: int = 50,
    max_snapshot_age_s: float = 15.0,
    now: Callable[[], float] = time.time,
) -> NavigatorRouteDecision:
    """Validate one game-navigator observation before submitting ``Дойти``."""
    if not isinstance(snapshot, Mapping):
        return NavigatorRouteDecision(RouteAction.STOP_UNSAFE, "invalid_navigator_snapshot")
    snapshot_id = _text(snapshot.get("snapshotId") or snapshot.get("snapshot_id"))
    target = _text(snapshot.get("target"))
    if not snapshot_id or not _text(snapshot.get("href")):
        return NavigatorRouteDecision(RouteAction.REFRESH, "navigator_snapshot_identity_missing", target, snapshot_id=snapshot_id)
    generated = snapshot.get("generatedAt", snapshot.get("generated_at"))
    generated_at: float | None = None
    if isinstance(generated, (int, float)) and not isinstance(generated, bool):
        generated_at = float(generated)
    elif isinstance(generated, str) and generated.strip():
        try:
            parsed = datetime.fromisoformat(generated.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            generated_at = parsed.timestamp()
        except ValueError:
            generated_at = None
    if generated_at is None or not isinstance(max_snapshot_age_s, (int, float)) or max_snapshot_age_s <= 0:
        return NavigatorRouteDecision(RouteAction.REFRESH, "navigator_snapshot_time_missing", target, snapshot_id=snapshot_id)
    age = now() - generated_at
    if age < 0 or age > max_snapshot_age_s:
        return NavigatorRouteDecision(RouteAction.REFRESH, "navigator_snapshot_stale", target, snapshot_id=snapshot_id)
    if not _text(expected_target):
        return NavigatorRouteDecision(RouteAction.STOP_UNSAFE, "navigator_target_mismatch", target, snapshot_id=snapshot_id)
    if not target:
        return NavigatorRouteDecision(RouteAction.REFRESH, "navigator_target_selection_pending", target, snapshot_id=snapshot_id)
    if normalize_location_name(target) != normalize_location_name(expected_target):
        return NavigatorRouteDecision(RouteAction.STOP_UNSAFE, "navigator_target_mismatch", target, snapshot_id=snapshot_id)
    current_location = snapshot.get("currentLocation")
    if current_location is True:
        return NavigatorRouteDecision(RouteAction.ARRIVED, "navigator_target_current_location", target, 0, snapshot_id)
    if current_location is not False:
        return NavigatorRouteDecision(RouteAction.STOP_UNSAFE, "navigator_current_location_unknown", target, snapshot_id=snapshot_id)
    visible_buttons = snapshot.get("visibleGoButtonCount")
    transitions = snapshot.get("routeTransitions")
    route_length_observed = (
        isinstance(transitions, int)
        and not isinstance(transitions, bool)
        and transitions > 0
    )
    if (
        snapshot.get("hasRoute") is False
        and isinstance(visible_buttons, int)
        and not isinstance(visible_buttons, bool)
        and visible_buttons >= 0
    ):
        return NavigatorRouteDecision(
            RouteAction.REFRESH,
            "navigator_route_render_pending",
            target,
            transitions if route_length_observed else None,
            snapshot_id,
        )
    if (
        snapshot.get("hasRoute") is not True
        or not isinstance(visible_buttons, int)
        or isinstance(visible_buttons, bool)
        or visible_buttons != 1
    ):
        return NavigatorRouteDecision(RouteAction.STOP_UNSAFE, "navigator_route_ambiguous", target, snapshot_id=snapshot_id)
    if (
        not route_length_observed
        or not isinstance(max_transitions, int)
        or isinstance(max_transitions, bool)
        or max_transitions <= 0
        or transitions > max_transitions
    ):
        return NavigatorRouteDecision(
            RouteAction.STOP_UNSAFE,
            "navigator_route_length_invalid",
            target,
            transitions if isinstance(transitions, int) else None,
            snapshot_id,
        )
    return NavigatorRouteDecision(RouteAction.MOVE, "navigator_route_confirmed", target, transitions, snapshot_id)
