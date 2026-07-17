#!/usr/bin/env python3
"""Build read-only 3kingdoms navigator catalogs from the official public XML."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path


BASE_URL = "https://3kingdoms.ru/"
COMPASS_PATH = "images/swf/compass_conf.xml"
WORLD_PATH = "images/data/areas/world_conf.xml"
LEVEL_RE = re.compile(r"^(.*?)\s*\[(\d+)\]\s*$")


def fetch_text(path: str) -> str:
    with urllib.request.urlopen(BASE_URL + path, timeout=30) as response:
        return response.read().decode("utf-8-sig")


def md_escape(value: str) -> str:
    return " ".join(value.split()).replace("|", "\\|")


def sort_key(value: str) -> tuple[str, str]:
    return (value.casefold(), value)


def parse_world() -> tuple[dict[str, dict], list[str]]:
    world = ET.fromstring(fetch_text(WORLD_PATH))
    files = list(dict.fromkeys(node.attrib["src"] for node in world.findall(".//file")))
    locations: dict[str, dict] = {}

    for source in files:
        root = ET.fromstring(fetch_text(source))
        area_title = (root.findtext(".//area/title") or Path(source).stem).strip()
        for node in root.findall(".//location"):
            location_id = node.attrib.get("id")
            title = (node.findtext("title") or "").strip()
            if not location_id or not title:
                continue
            record = locations.setdefault(
                location_id,
                {
                    "id": location_id,
                    "names": set(),
                    "areas": set(),
                    "sources": set(),
                    "objects": [],
                },
            )
            record["names"].add(title)
            record["areas"].add(area_title)
            record["sources"].add(source)
            for obj in node.findall("./object"):
                record["objects"].append(
                    {
                        "type": obj.attrib.get("id", "object"),
                        "race": obj.attrib.get("race"),
                        "inner_pos": obj.attrib.get("inner_pos"),
                        "title": (obj.findtext("title") or "").strip(),
                        "text": (obj.findtext("text") or "").strip(),
                        "items": [
                            (item.text or "").strip()
                            for item in obj.findall("item")
                            if (item.text or "").strip()
                        ],
                    }
                )
    return locations, files


def parse_graph(race: str) -> tuple[set[str], set[tuple[str, str]], set[str]]:
    root = ET.fromstring("<root>" + fetch_text(COMPASS_PATH) + "</root>")
    records = [
        node
        for node in root.findall("./locations/loc")
        if node.attrib.get("race") in (None, race)
    ]
    nodes = {node.attrib["id"] for node in records}
    edges: set[tuple[str, str]] = set()
    raw_references: set[str] = set()
    for destination in records:
        destination_id = destination.attrib["id"]
        for raw_pair in destination.attrib.get("verges", "").split("|"):
            pair = raw_pair.split(",")
            raw_references.update(item for item in pair if item and item != "null")
            if len(pair) != 2:
                continue
            for source_id in pair:
                if source_id and source_id != "null" and source_id in nodes:
                    edges.add((source_id, destination_id))
    unresolved = raw_references - nodes
    return nodes, edges, unresolved


def location_name(locations: dict[str, dict], location_id: str) -> str:
    names = sorted(locations.get(location_id, {}).get("names", []), key=sort_key)
    return " / ".join(names) if names else f"ID {location_id}"


def locations_markdown(locations: dict[str, dict], checked: str) -> str:
    graphs = {race: parse_graph(race) for race in ("1", "2")}
    lines = [
        "# Локации и переходы «Троецарствия»",
        "",
        f"Сформировано: {checked}. Источники: [compass_conf.xml](https://3kingdoms.ru/images/swf/compass_conf.xml) и XML-карты из [world_conf.xml](https://3kingdoms.ru/images/data/areas/world_conf.xml).",
        "",
        "Переходы восстановлены по фактическому алгоритму `compass.js`: `source → destination`, если `source` встречается в двухэлементной паре `destination.verges`. Одноэлементные записи код игнорирует. Это статическая конфигурация; фактическая доступность и условия требуют LIVE-проверки.",
        "",
    ]
    for race, label in (("1", "Артания (`race=1`)"), ("2", "Куявия (`race=2`)")):
        nodes, edges, unresolved = graphs[race]
        adjacency: dict[str, set[str]] = collections.defaultdict(set)
        for source, destination in edges:
            adjacency[source].add(destination)
        undirected = {tuple(sorted(edge)) for edge in edges}
        asymmetric = {edge for edge in edges if (edge[1], edge[0]) not in edges}
        lines.extend(
            [
                f"## {label}",
                "",
                f"Узлов в конфигурации: {len(nodes)}. Ориентированных переходов: {len(edges)}. Пар без учёта направления: {len(undirected)}. Асимметричных переходов: {len(asymmetric)}. Сырых ссылок на внешние ID: {len(unresolved)}.",
                "",
                "Асимметричные: " + ("; ".join(f"`{a} → {b}`" for a, b in sorted(asymmetric)) or "нет") + ". Сырые внешние ID: " + (", ".join(f"`{item}`" for item in sorted(unresolved)) or "нет") + ".",
                "",
                "| ID | Локация | Исходящие переходы по `compass.js` | Региональные XML |",
                "|---:|---|---|---|",
            ]
        )
        for location_id in sorted(nodes, key=lambda item: (sort_key(location_name(locations, item)), int(item))):
            neighbors = sorted(adjacency.get(location_id, []), key=lambda item: sort_key(location_name(locations, item)))
            neighbor_text = "; ".join(
                f"{md_escape(location_name(locations, item))} (`{item}`)"
                for item in neighbors
            ) or "—"
            sources = sorted(locations.get(location_id, {}).get("sources", []), key=sort_key)
            source_text = ", ".join(f"`{Path(source).name}`" for source in sources) or "—"
            lines.append(
                f"| `{location_id}` | {md_escape(location_name(locations, location_id))} | {neighbor_text} | {source_text} |"
            )
        lines.append("")

    lines.extend(
        [
            "## Именованные локации вне графа",
            "",
            "XML мира может содержать локации, которых нет в выбранной фракционной проекции навигатора.",
            "",
            "| ID | Локация | Региональные XML |",
            "|---:|---|---|",
        ]
    )
    graph_ids = graphs["1"][0] | graphs["2"][0]
    for location_id in sorted(set(locations) - graph_ids, key=lambda item: sort_key(location_name(locations, item))):
        sources = sorted(locations[location_id]["sources"], key=sort_key)
        lines.append(
            f"| `{location_id}` | {md_escape(location_name(locations, location_id))} | "
            + ", ".join(f"`{Path(source).name}`" for source in sources)
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def entity_index(locations: dict[str, dict], object_type: str) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = collections.defaultdict(list)
    for location_id, location in locations.items():
        for obj in location["objects"]:
            if obj["type"] != object_type:
                continue
            for raw_name in obj["items"]:
                match = LEVEL_RE.match(raw_name)
                name = match.group(1).strip() if match else raw_name
                level = int(match.group(2)) if match else None
                result[name].append(
                    {
                        "location_id": location_id,
                        "location": location_name(locations, location_id),
                        "level": level,
                        "raw": raw_name,
                    }
                )
    return result


def resources_markdown(locations: dict[str, dict], checked: str) -> str:
    resource_types = {
        "farm_floor": "Растения",
        "farm_rock": "Камни",
        "farm_fish": "Рыба",
    }
    lines = [
        "# Ресурсы «Троецарствия»",
        "",
        f"Сформировано: {checked}. Источник: региональные XML навигатора из [world_conf.xml](https://3kingdoms.ru/images/data/areas/world_conf.xml).",
        "",
        "Число в квадратных скобках сохранено как нейтральное значение `[]`; его точная игровая семантика для ресурсов кодом навигатора не определяется.",
        "",
    ]
    for object_type, title in resource_types.items():
        index = entity_index(locations, object_type)
        placements = sum(len(items) for items in index.values())
        lines.extend(
            [
                f"## {title}",
                "",
                f"Уникальных названий: {len(index)}. Размещений по локациям: {placements}.",
                "",
                "| Ресурс | Значение `[]` | Локации |",
                "|---|---:|---|",
            ]
        )
        for name in sorted(index, key=sort_key):
            entries = index[name]
            levels = sorted({entry["level"] for entry in entries if entry["level"] is not None})
            locations_text = "; ".join(
                f"{md_escape(entry['location'])} (`{entry['location_id']}`)"
                for entry in sorted(entries, key=lambda item: sort_key(item["location"]))
            )
            lines.append(
                f"| {md_escape(name)} | {', '.join(map(str, levels)) if levels else '—'} | {locations_text} |"
            )
        lines.append("")
    return "\n".join(lines)


def monsters_markdown(locations: dict[str, dict], checked: str) -> str:
    index = entity_index(locations, "monster")
    boss_index = entity_index(locations, "boss")
    for name, entries in boss_index.items():
        index[name].extend(entries)
    placements = sum(len(items) for items in index.values())
    lines = [
        "# Монстры для охоты «Троецарствия»",
        "",
        f"Сформировано: {checked}. Источник: блоки `object id=monster` и потенциальные `object id=boss` в XML навигатора из [world_conf.xml](https://3kingdoms.ru/images/data/areas/world_conf.xml). В текущем наборе блоки `boss` отсутствуют.",
        "",
        f"Уникальных названий: {len(index)}. Размещений по локациям: {placements}.",
        "",
        "Число в квадратных скобках интерпретируется как отображаемый уровень монстра. Каталог отражает конфигурацию навигатора, но не гарантирует текущее появление, доступность или безопасность охоты.",
        "",
        "| Монстр | Уровень | Локации |",
        "|---|---:|---|",
    ]
    for name in sorted(index, key=sort_key):
        entries = index[name]
        levels = sorted({entry["level"] for entry in entries if entry["level"] is not None})
        locations_text = "; ".join(
            f"{md_escape(entry['location'])} (`{entry['location_id']}`)"
            for entry in sorted(entries, key=lambda item: sort_key(item["location"]))
        )
        lines.append(
            f"| {md_escape(name)} | {', '.join(map(str, levels)) if levels else '—'} | {locations_text} |"
        )
    lines.append("")
    return "\n".join(lines)


def instances_markdown(locations: dict[str, dict], checked: str) -> str:
    records = []
    for location_id, location in locations.items():
        for obj in location["objects"]:
            if obj["type"] == "star_2":
                records.append(
                    {
                        "name": obj["title"] or "Без названия",
                        "location": location_name(locations, location_id),
                        "location_id": location_id,
                        "race": obj["race"],
                        "inner_pos": obj["inner_pos"],
                        "conditions": obj["items"],
                        "description": obj["text"],
                    }
                )
    records.sort(key=lambda item: (sort_key(item["name"]), sort_key(item["location"])))
    lines = [
        "# Инстансы «Троецарствия»",
        "",
        f"Сформировано: {checked}. Источник: блоки `object id=star_2` в XML навигатора из [world_conf.xml](https://3kingdoms.ru/images/data/areas/world_conf.xml).",
        "",
        f"Размещений входов/карточек: {len(records)}. Одинаковый инстанс может иметь несколько входов или фракционных вариантов. В текущих XML: {sum(1 for item in records if item['race'] == '1')} для `race=1`, {sum(1 for item in records if item['race'] == '2')} для `race=2`.",
        "",
        "| Инстанс | Race | Локация входа | Позиция | Условия/период | Описание |",
        "|---|---:|---|---|---|---|",
    ]
    for record in records:
        conditions = "; ".join(md_escape(item) for item in record["conditions"]) or "—"
        description = md_escape(record["description"]) or "—"
        lines.append(
            f"| {md_escape(record['name'])} | {record['race'] or '—'} | {md_escape(record['location'])} (`{record['location_id']}`) | `{record['inner_pos'] or '—'}` | {conditions} | {description} |"
        )
    lines.extend(
        [
            "",
            "## Ограничение",
            "",
            "Это каталог объектов-инстансов, показанных навигатором. Он может не включать временные события, скрытые входы или режимы, которые сервер добавляет динамически.",
            "",
        ]
    )
    return "\n".join(lines)


def npcs_markdown(locations: dict[str, dict], checked: str) -> str:
    marker_labels = {
        "chel_gray": "Артания / серый маркер",
        "chel_green": "Куявия / зелёный маркер",
        "chel_orange": "Барбуссия / оранжевый маркер",
        "chel_blue": "синий маркер",
        "chel_red": "красный маркер",
    }
    rows = []
    unique_names = set()
    for location_id, location in locations.items():
        for obj in location["objects"]:
            if not obj["type"].startswith("chel_"):
                continue
            for raw_name in obj["items"]:
                unique_names.add(raw_name)
                rows.append(
                    {
                        "name": raw_name,
                        "location_id": location_id,
                        "location": location_name(locations, location_id),
                        "marker": obj["type"],
                        "inner_pos": obj["inner_pos"],
                        "sources": sorted(location["sources"], key=sort_key),
                    }
                )
    rows.sort(key=lambda item: (sort_key(item["location"]), sort_key(item["name"])))
    lines = [
        "# NPC по локациям «Троецарствия»",
        "",
        f"Сформировано: {checked}. Источник: блоки `object id=chel_*` в региональных XML навигатора из [world_conf.xml](https://3kingdoms.ru/images/data/areas/world_conf.xml).",
        "",
        f"Уникальных имён: {len(unique_names)}. Размещений NPC по локациям: {len(rows)}.",
        "",
        "Одинаковое имя может встречаться в нескольких локациях. Тип `chel_*` сохранён как визуальный/фракционный маркер исходного XML и не считается стабильным публичным API.",
        "",
        "| Локация | ID | NPC | Маркер XML | Позиция | Источник |",
        "|---|---:|---|---|---|---|",
    ]
    for row in rows:
        source_text = ", ".join(f"`{Path(source).name}`" for source in row["sources"])
        marker = marker_labels.get(row["marker"], row["marker"])
        lines.append(
            f"| {md_escape(row['location'])} | `{row['location_id']}` | {md_escape(row['name'])} | "
            f"{md_escape(marker)} (`{row['marker']}`) | `{row['inner_pos'] or '—'}` | {source_text} |"
        )
    lines.extend(
        [
            "",
            "## Ограничение",
            "",
            "Каталог отражает статические XML навигатора. Сезонные, временные, квестовые или динамически добавленные NPC могут отсутствовать, а фактическое присутствие требует LIVE-проверки.",
            "",
        ]
    )
    return "\n".join(lines)


def npc_catalog_json(locations: dict[str, dict], checked: str) -> str:
    """Build a machine-readable canonical NPC-to-parent-location directory.

    The public map XML does not expose runtime area-object or NPC-instance IDs.
    Those identities are deliberately left null and must be learned from fresh
    area/NPC snapshots instead of being guessed from coordinates or ordering.
    """

    records = []
    for location_id, location in locations.items():
        names = sorted(location.get("names", ()), key=sort_key)
        parent_name = names[0] if names else f"ID {location_id}"
        for obj in location["objects"]:
            if not obj["type"].startswith("chel_"):
                continue
            for raw_name in obj["items"]:
                records.append(
                    {
                        "canonicalName": raw_name,
                        "locationId": location_id,
                        "locationName": parent_name,
                        "locationAliases": names,
                        "marker": obj["type"],
                        "innerPosition": obj["inner_pos"],
                        "areaObjectId": None,
                        "npcInstanceId": None,
                        "proxyNames": [],
                        "sources": sorted(Path(source).name for source in location["sources"]),
                    }
                )
    records.sort(
        key=lambda item: (
            sort_key(item["canonicalName"]),
            sort_key(item["locationName"]),
            int(item["locationId"]),
        )
    )
    payload = {
        "schemaVersion": 1,
        "generatedAt": checked,
        "sources": {
            "world": BASE_URL + WORLD_PATH,
            "scope": "public regional map XML object[id^=chel_]",
        },
        "identityContract": {
            "canonicalName": "map XML display name; not a click identity",
            "locationId": "parent world location identity",
            "areaObjectId": "runtime-only .b-control-area NPC/proxy data-id",
            "npcInstanceId": "runtime-only npc.php npc_id when observed",
            "proxyNames": "runtime aliases such as Дом ... or Палатка ...",
        },
        "npcs": records,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("docs/3kingdoms"))
    parser.add_argument(
        "--only",
        choices=("all", "npc"),
        default="all",
        help="Build every catalogue or only the NPC markdown/JSON pair.",
    )
    args = parser.parse_args()
    locations, _ = parse_world()
    checked = dt.date.today().isoformat()
    outputs = {
        "LOCATIONS_AND_TRANSITIONS.md": locations_markdown(locations, checked),
        "RESOURCES.md": resources_markdown(locations, checked),
        "HUNT_MONSTERS.md": monsters_markdown(locations, checked),
        "INSTANCES.md": instances_markdown(locations, checked),
        "NPCS_BY_LOCATION.md": npcs_markdown(locations, checked),
        "NPC_CATALOG.json": npc_catalog_json(locations, checked),
    }
    if args.only == "npc":
        outputs = {
            name: content
            for name, content in outputs.items()
            if name in {"NPCS_BY_LOCATION.md", "NPC_CATALOG.json"}
        }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for filename, content in outputs.items():
        (args.output_dir / filename).write_text(content, encoding="utf-8")
        print(f"wrote {args.output_dir / filename}")


if __name__ == "__main__":
    main()
