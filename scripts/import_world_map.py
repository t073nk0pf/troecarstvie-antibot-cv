#!/usr/bin/env python3
"""Merge the official game-map manifests into the local world registry."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from urllib.request import urlopen
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.antibot_cv.automation.world_map_importer import merge_world_map, parse_world_map

ORIGIN = "https://3kingdoms.ru/"
WORLD_SOURCE = "images/data/areas/world_conf.xml"
COMPASS_SOURCE = "images/swf/compass_conf.xml"


def fetch(path: str) -> str:
    with urlopen(ORIGIN + path, timeout=15) as response:
        return response.read().decode("utf-8-sig")


def main() -> int:
    world_xml = fetch(WORLD_SOURCE)
    world = ET.fromstring(world_xml)
    sources = tuple(dict.fromkeys(
        item.get("src") for item in world.findall(".//file") if item.get("src")
    ))
    dataset = parse_world_map(
        compass_xml=fetch(COMPASS_SOURCE),
        area_xml_by_source={source: fetch(source) for source in sources},
    )
    path = ROOT / "config/world_registry.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    merge_world_map(data, dataset)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "locations": len(dataset.locations),
        "edges": sum(len(values) for values in dataset.edges.values()),
        "npcNames": sum(len(values) for values in dataset.npc_names.values()),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
