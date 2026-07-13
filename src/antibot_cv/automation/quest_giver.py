"""Conservative Russian quest-giver name resolution against area NPCs."""

from __future__ import annotations

import re


def resolve_unique_giver(expected: str, items: object) -> dict[str, object]:
    """Return one actionable NPC matching exact text or deterministic word stems."""

    if not isinstance(items, list):
        raise ValueError("area NPC items are missing")
    candidates = [item for item in items if isinstance(item, dict) and item.get("actionable") is True]
    exact = [item for item in candidates if _normalized(str(item.get("name") or "")) == _normalized(expected)]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise ValueError("quest giver NPC is ambiguous")
    expected_signature = _stem_signature(expected)
    if not expected_signature:
        raise ValueError("quest giver name is invalid")
    morphological = [
        item
        for item in candidates
        if _stem_signature(str(item.get("name") or "")) == expected_signature
    ]
    if len(morphological) == 1:
        return morphological[0]
    if len(morphological) > 1:
        raise ValueError("quest giver NPC is ambiguous")
    # Some locations expose a building/proxy (for example ``Дом Франка``)
    # instead of the catalogue role (``Хранитель леса Франк``).  The final
    # proper-name stem is safe only when it identifies exactly one actionable
    # area object; otherwise remain fail-closed.
    proper_name_stem = expected_signature[-1]
    proxy_matches = [
        item
        for item in candidates
        if len(proper_name_stem) >= 4
        and proper_name_stem in _stem_signature(str(item.get("name") or ""))
    ]
    if len(proxy_matches) != 1:
        raise ValueError("quest giver NPC is missing or ambiguous")
    return proxy_matches[0]


def _normalized(value: str) -> str:
    return " ".join(_tokens(value))


def _stem_signature(value: str) -> tuple[str, ...]:
    return tuple(_stem(token) for token in _tokens(value))


def _tokens(value: str) -> list[str]:
    return re.findall(r"[a-zа-я0-9]+", str(value or "").casefold().replace("ё", "е"))


def _stem(token: str) -> str:
    if len(token) <= 3:
        return token
    for suffix in (
        "иями",
        "ями",
        "ами",
        "его",
        "ого",
        "ему",
        "ому",
        "ыми",
        "ими",
        "ую",
        "юю",
        "ая",
        "яя",
        "ов",
        "ев",
        "ом",
        "ем",
        "ой",
        "ей",
        "ы",
        "и",
        "а",
        "я",
        "у",
        "ю",
        "е",
        "ь",
    ):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[: -len(suffix)]
    return token
