"""Pure canonical/runtime NPC directory used by quest routing."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import json
from pathlib import Path
import re
from typing import Mapping

from src.antibot_cv.automation.runtime_helpers import same_location_name


class NpcResolutionStatus(str, Enum):
    RESOLVED = "resolved"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class NpcDirectoryEntry:
    canonical_name: str
    location_id: str
    location_name: str
    location_aliases: tuple[str, ...] = ()
    proxy_names: tuple[str, ...] = ()
    area_object_id: str | None = None
    npc_instance_id: str | None = None


@dataclass(frozen=True)
class NpcResolution:
    status: NpcResolutionStatus
    entries: tuple[NpcDirectoryEntry, ...]
    reason: str

    @property
    def entry(self) -> NpcDirectoryEntry | None:
        return self.entries[0] if self.status is NpcResolutionStatus.RESOLVED else None


class QuestNpcDirectory:
    """Merge public canonical placements with safely observed click identities."""

    def __init__(self, entries: tuple[NpcDirectoryEntry, ...] = ()) -> None:
        self.entries = entries

    @classmethod
    def from_files(
        cls,
        catalog_path: str | Path,
        runtime_registry_path: str | Path | None = None,
    ) -> "QuestNpcDirectory":
        raw = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
        entries = _parse_catalog(raw)
        if runtime_registry_path is not None and Path(runtime_registry_path).exists():
            registry = json.loads(Path(runtime_registry_path).read_text(encoding="utf-8"))
            entries = _merge_runtime(entries, registry)
        return cls(entries)

    def resolve(
        self,
        expected_name: object,
        *,
        location_id: object = None,
        location_name: object = None,
    ) -> NpcResolution:
        query = _text(expected_name, 180)
        wanted_id = _identity(location_id)
        wanted_location = _text(location_name, 220)
        if not query:
            return NpcResolution(NpcResolutionStatus.NOT_FOUND, (), "npc_query_invalid")
        candidates = tuple(
            entry for entry in self.entries
            if (not wanted_id or entry.location_id == wanted_id)
            and (
                not wanted_location
                or same_location_name(entry.location_name, wanted_location)
                or any(same_location_name(alias, wanted_location) for alias in entry.location_aliases)
            )
        )
        exact = tuple(entry for entry in candidates if _signature(entry.canonical_name) == _signature(query))
        if len(exact) == 1:
            return NpcResolution(NpcResolutionStatus.RESOLVED, exact, "canonical_signature")
        if len(exact) > 1:
            return NpcResolution(NpcResolutionStatus.AMBIGUOUS, exact, "canonical_signature_ambiguous")
        proper = _signature(query)
        last = proper[-1] if proper else ""
        surname_matches = tuple(
            entry for entry in candidates
            if len(last) >= 4 and last in _signature(entry.canonical_name)
        )
        if len(surname_matches) == 1:
            return NpcResolution(NpcResolutionStatus.RESOLVED, surname_matches, "proper_name_stem")
        if len(surname_matches) > 1:
            return NpcResolution(NpcResolutionStatus.AMBIGUOUS, surname_matches, "proper_name_stem_ambiguous")
        return NpcResolution(NpcResolutionStatus.NOT_FOUND, (), "npc_not_in_directory")


def _parse_catalog(raw: object) -> tuple[NpcDirectoryEntry, ...]:
    if not isinstance(raw, Mapping) or raw.get("schemaVersion") != 1:
        raise ValueError("unsupported NPC catalogue schema")
    records = raw.get("npcs")
    if not isinstance(records, list):
        raise ValueError("NPC catalogue records are missing")
    entries = []
    for record in records:
        if not isinstance(record, Mapping):
            raise ValueError("NPC catalogue record is invalid")
        name = _text(record.get("canonicalName"), 180)
        location_id = _identity(record.get("locationId"))
        location_name = _text(record.get("locationName"), 220)
        aliases = _string_tuple(record.get("locationAliases"), 220)
        if not name or not location_id or not location_name:
            raise ValueError("NPC catalogue identity is incomplete")
        entries.append(NpcDirectoryEntry(name, location_id, location_name, aliases))
    return tuple(entries)


def _merge_runtime(
    entries: tuple[NpcDirectoryEntry, ...], raw: object
) -> tuple[NpcDirectoryEntry, ...]:
    if not isinstance(raw, Mapping) or raw.get("schemaVersion") != 1:
        return entries
    observed = raw.get("npcs")
    if not isinstance(observed, Mapping):
        return entries
    result = list(entries)
    for value in observed.values():
        if not isinstance(value, Mapping):
            continue
        location_id = _identity(value.get("locationId"))
        proxy_name = _text(value.get("name"), 180)
        area_id = _identity(value.get("areaObjectId"))
        instance_id = _identity(value.get("npcInstanceId")) or None
        if not location_id or not proxy_name or not area_id:
            continue
        proxy_signature = _signature(proxy_name)
        matches = [
            index for index, entry in enumerate(result)
            if entry.location_id == location_id
            and _signature(entry.canonical_name)
            and _signature(entry.canonical_name)[-1] in proxy_signature
        ]
        if len(matches) != 1:
            continue
        index = matches[0]
        entry = result[index]
        result[index] = replace(
            entry,
            proxy_names=tuple(dict.fromkeys((*entry.proxy_names, proxy_name))),
            area_object_id=area_id,
            npc_instance_id=instance_id or entry.npc_instance_id,
        )
    return tuple(result)


def _signature(value: object) -> tuple[str, ...]:
    return tuple(_stem(token) for token in re.findall(r"[a-zа-я0-9]+", str(value or "").casefold().replace("ё", "е")))


def _stem(token: str) -> str:
    if len(token) <= 3:
        return token
    for suffix in ("иями", "ями", "ами", "его", "ого", "ему", "ому", "ыми", "ими", "ую", "юю", "ая", "яя", "ов", "ев", "ом", "ем", "ой", "ей", "ы", "и", "а", "я", "у", "ю", "е", "ь"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[: -len(suffix)]
    return token


def _identity(value: object) -> str:
    text = str(value or "").strip()
    return text if text.isdecimal() and int(text) >= 0 else ""


def _text(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else ""


def _string_tuple(value: object, limit: int) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(text for item in value if (text := _text(item, limit)))
