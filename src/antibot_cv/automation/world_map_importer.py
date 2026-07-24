"""Parse the game's public world-map manifests into registry evidence."""

from __future__ import annotations

from dataclasses import dataclass
import re
import xml.etree.ElementTree as ET
from typing import Mapping


@dataclass(frozen=True)
class WorldMapDataset:
    locations: Mapping[str, str]
    edges: Mapping[str, tuple[str, ...]]
    npc_names: Mapping[str, tuple[str, ...]]


def parse_world_map(
    *, compass_xml: str, area_xml_by_source: Mapping[str, str]
) -> WorldMapDataset:
    locations: dict[str, str] = {}
    npc_names: dict[str, set[str]] = {}
    for source, payload in sorted(area_xml_by_source.items()):
        root = ET.fromstring(payload)
        if root.tag != "areas":
            raise ValueError(f"invalid area map root: {source}")
        for location in root.findall(".//location"):
            location_id = _positive_id(location.get("id"))
            title = _text(location.findtext("title"))
            if not location_id or not title:
                continue
            previous = locations.get(location_id)
            if previous is not None and previous.casefold() != title.casefold():
                raise ValueError(f"conflicting map location identity: {location_id}")
            locations[location_id] = title
            names = npc_names.setdefault(location_id, set())
            for obj in location.findall("object"):
                if obj.get("id") != "chel_gray":
                    continue
                names.update(
                    name for item in obj.findall("item")
                    if (name := _text(item.text))
                )

    # compass_conf.xml intentionally contains several top-level sections.
    compass = ET.fromstring(f"<root>{compass_xml}</root>")
    edges: dict[str, set[str]] = {}
    for location in compass.findall(".//locations/loc"):
        source_id = _positive_id(location.get("id"))
        if not source_id:
            continue
        neighbours = edges.setdefault(source_id, set())
        for candidate in re.findall(r"\d+", location.get("verges") or ""):
            target_id = _positive_id(candidate)
            if target_id and target_id != source_id:
                neighbours.add(target_id)

    return WorldMapDataset(
        locations=dict(sorted(locations.items(), key=lambda item: int(item[0]))),
        edges={
            key: tuple(sorted(values, key=int))
            for key, values in sorted(edges.items(), key=lambda item: int(item[0]))
        },
        npc_names={
            key: tuple(sorted(values))
            for key, values in sorted(npc_names.items(), key=lambda item: int(item[0]))
            if values
        },
    )


def merge_world_map(data: dict[str, object], dataset: WorldMapDataset) -> None:
    locations = data.setdefault("locations", {})
    edges = data.setdefault("edges", {})
    if not isinstance(locations, dict) or not isinstance(edges, dict):
        raise ValueError("invalid world registry")
    for location_id, name in dataset.locations.items():
        locations[location_id] = {"id": location_id, "name": name}
    for source_id, targets in dataset.edges.items():
        outgoing = edges.setdefault(source_id, {})
        if not isinstance(outgoing, dict):
            raise ValueError(f"invalid edge registry: {source_id}")
        for target_id in targets:
            existing_location = locations.get(target_id)
            existing_name = (
                existing_location.get("name")
                if isinstance(existing_location, dict) else ""
            )
            target_name = dataset.locations.get(target_id) or str(existing_name or "")
            outgoing[target_id] = {"to": target_id, "name": target_name}
    data["mapNpcs"] = {
        location_id: {
            "locationId": location_id,
            "locationName": dataset.locations[location_id],
            "names": list(names),
        }
        for location_id, names in dataset.npc_names.items()
    }
    data["mapSources"] = {
        "compass": "images/swf/compass_conf.xml",
        "world": "images/data/areas/world_conf.xml",
    }


def _positive_id(value: object) -> str:
    text = str(value or "").strip()
    return text if text.isascii() and text.isdecimal() and int(text) > 0 else ""


def _text(value: object) -> str:
    return " ".join(str(value or "").split())[:300]
