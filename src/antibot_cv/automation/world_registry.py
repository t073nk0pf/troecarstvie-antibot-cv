from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class WorldRegistry:
    """Persistent identities learned from structured game snapshots."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.data: dict[str, Any] = {
            "schemaVersion": 1,
            "locations": {},
            "edges": {},
            "npcs": {},
            "instances": {},
        }
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        candidate = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(candidate, dict) or candidate.get("schemaVersion") != 1:
            raise ValueError("unsupported world registry schema")
        candidate.setdefault("edges", {})
        for section in ("locations", "edges", "npcs", "instances"):
            if not isinstance(candidate.get(section), dict):
                raise ValueError(f"invalid world registry section: {section}")
        self.data = candidate

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

    def observe_location(self, location_id: object, name: object) -> bool:
        identity = _identity(location_id)
        title = _text(name)
        if not identity or not title:
            return False
        locations = self.data["locations"]
        before = locations.get(identity)
        locations[identity] = {"id": identity, "name": title}
        return before != locations[identity]

    def observe_route(self, snapshot: dict[str, Any]) -> bool:
        changed = False
        location = snapshot.get("location")
        if isinstance(location, dict):
            changed |= self.observe_location(location.get("id"), location.get("semanticName"))
        transition = snapshot.get("nextTransition")
        if isinstance(transition, dict):
            changed |= self.observe_location(transition.get("locId"), transition.get("name"))
            current_id = _identity(snapshot.get("currentLocationId")) or (
                _identity(location.get("id")) if isinstance(location, dict) else ""
            )
            next_id = _identity(transition.get("locId"))
            if current_id and next_id:
                edges = self.data["edges"].setdefault(current_id, {})
                edge = {"to": next_id, "name": _text(transition.get("name"))}
                if edges.get(next_id) != edge:
                    edges[next_id] = edge
                    changed = True
        if changed:
            self.save()
        return changed

    def shortest_path(self, start_id: object, target_id: object) -> tuple[str, ...] | None:
        start = _identity(start_id)
        target = _identity(target_id)
        if not start or not target:
            return None
        if start == target:
            return ()
        queue: list[tuple[str, tuple[str, ...]]] = [(start, ())]
        visited = {start}
        while queue:
            current, path = queue.pop(0)
            neighbours = self.data["edges"].get(current, {})
            if not isinstance(neighbours, dict):
                continue
            for neighbour in neighbours:
                if neighbour in visited:
                    continue
                candidate = (*path, neighbour)
                if neighbour == target:
                    return candidate
                visited.add(neighbour)
                queue.append((neighbour, candidate))
        return None

    def observe_area_npcs(self, snapshot: dict[str, Any]) -> bool:
        location = snapshot.get("location")
        if not isinstance(location, dict):
            return False
        location_id = _identity(location.get("id"))
        changed = self.observe_location(location_id, location.get("name"))
        if not location_id:
            return changed
        for item in snapshot.get("items") or []:
            if not isinstance(item, dict):
                continue
            object_id = _identity(item.get("dataId"))
            name = _text(item.get("name"))
            if not object_id or not name:
                continue
            key = f"{location_id}:{object_id}"
            value = {
                "areaObjectId": object_id,
                "locationId": location_id,
                "name": name,
            }
            if self.data["npcs"].get(key) != value:
                self.data["npcs"][key] = value
                changed = True
        if changed:
            self.save()
        return changed

    def observe_npc_dialog(self, snapshot: dict[str, Any]) -> bool:
        area_object_id = _identity(snapshot.get("npcId"))
        if not area_object_id:
            return False
        instance_ids = {
            _identity(action.get("npcInstanceId"))
            for action in (snapshot.get("questActions") or []) + (snapshot.get("dialogActions") or [])
            if isinstance(action, dict)
        }
        instance_ids.discard("")
        changed = False
        for value in self.data["npcs"].values():
            if not isinstance(value, dict) or value.get("areaObjectId") != area_object_id:
                continue
            if instance_ids:
                npc_instance_id = sorted(instance_ids, key=int)[0]
                if value.get("npcInstanceId") != npc_instance_id:
                    value["npcInstanceId"] = npc_instance_id
                    changed = True
        if changed:
            self.save()
        return changed

    def observe_instances(self, snapshot: dict[str, Any]) -> bool:
        location = snapshot.get("location")
        location_id = _identity(location.get("id")) if isinstance(location, dict) else ""
        changed = False
        if isinstance(location, dict):
            changed |= self.observe_location(location_id, location.get("name"))
        for item in snapshot.get("items") or []:
            if not isinstance(item, dict):
                continue
            instance_id = _identity(item.get("id"))
            name = _text(item.get("name"))
            if not instance_id or not name or not location_id:
                continue
            key = f"{location_id}:{instance_id}"
            value = {
                "entranceObjectId": instance_id,
                "locationId": location_id,
                "name": name,
                "href": _text(item.get("href")),
            }
            if self.data["instances"].get(key) != value:
                self.data["instances"][key] = value
                changed = True
        if changed:
            self.save()
        return changed


def _identity(value: object) -> str:
    text = str(value or "").strip()
    return text if text.isdecimal() and int(text) >= 0 else ""


def _text(value: object) -> str:
    return " ".join(str(value or "").split())[:300]
