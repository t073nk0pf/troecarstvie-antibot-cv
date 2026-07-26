"""Curated, source-verified dialogue puzzle sequences without runtime network access."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
from urllib.parse import quote_plus


@dataclass(frozen=True)
class PuzzleRecipeSource:
    url: str
    claimed_sequence: tuple[int, ...]


KATUR_RECIPE_SOURCES = (
    PuzzleRecipeSource("https://3k.ucoz.org/forum/2-47-1", (3, 1, 2, 5, 4)),
    PuzzleRecipeSource("https://3kingdoms.ru/user_info.php?nick=pac-man", (3, 1, 2, 5, 4)),
)


_VALUE_BY_DRUM = {1: 3, 2: 1, 3: 2, 4: 5, 5: 4}
_SELECT_REF_TO_DRUM = {4190: 1, 4198: 2, 4206: 3, 4214: 4, 4222: 5}
_ORDINAL = {1: "первой", 2: "второй", 3: "третьей", 4: "четвертой", 5: "пятой"}
_PUZZLE_CONTROL_PATTERN = re.compile(
    r"^\*(?:нажать|крутить|установить|вернуть|попытаться)\b.*\*$"
)


def quest_solution_search_queries(quest_title: str) -> tuple[str, ...]:
    """Build bounded manual-research queries; runtime never performs I/O."""

    title = " ".join(str(quest_title or "").split())
    if not title or len(title) > 220:
        return ()
    encoded = quote_plus(f'"{title}" код последовательность')
    return (
        f"https://www.google.com/search?q={encoded}+site%3A3kingdoms.ru",
        f"https://www.google.com/search?q={encoded}+site%3A3k.ucoz.org",
    )


def recipe_has_independent_consensus(sources: Sequence[PuzzleRecipeSource]) -> bool:
    """Require the same sequence on at least two independently hosted sources."""

    hosts_by_sequence: dict[tuple[int, ...], set[str]] = {}
    for source in sources:
        match = re.match(r"https://([^/]+)/", source.url)
        if match and source.claimed_sequence:
            hosts_by_sequence.setdefault(source.claimed_sequence, set()).add(match.group(1).casefold())
    return any(len(hosts) >= 2 for hosts in hosts_by_sequence.values())


def select_verified_puzzle_action(
    quest_id: str,
    actions: Sequence[Mapping[str, object]],
    *,
    href: str,
    completed_drums: tuple[int, ...],
) -> Mapping[str, object] | None:
    if (
        quest_id != "282"
        or not recipe_has_independent_consensus(KATUR_RECIPE_SOURCES)
        or any(item not in _VALUE_BY_DRUM for item in completed_drums)
    ):
        return None
    normalized = tuple((action, _text(action)) for action in actions if isinstance(action, Mapping))
    selected = _selected_drum(href)
    if selected is not None:
        expected = f"*установить значение барабанчика на {_VALUE_BY_DRUM[selected]}*"
        return _unique(normalized, expected, puzzle_completed_drum=selected)
    # Quest 282 opens with the first drum already at the documented value 3;
    # the page therefore exposes selectors only for drums 2..5.  Treat that
    # exact, complete screen signature as evidence of the implicit first value.
    implicit = _implicit_completed_drums(normalized, completed_drums)
    effective_completed = (*completed_drums, *(item for item in implicit if item not in completed_drums))
    remaining = tuple(item for item in _VALUE_BY_DRUM if item not in effective_completed)
    if remaining:
        drum = remaining[0]
        expected = f"*крутить барабанчик на {_ORDINAL[drum]} сверху скобе*"
        return _unique(
            normalized,
            expected,
            puzzle_selected_drum=drum,
            puzzle_implicit_completed_drums=implicit,
        )
    return _unique(normalized, "*попытаться открыть книгу*", puzzle_open=True)


def unsupported_puzzle_signature(
    quest_id: str,
    actions: Sequence[Mapping[str, object]],
) -> bool:
    """Recognize a choice grid as a puzzle without guessing its solution.

    A normal dialogue branch can expose several prose replies.  Puzzle pages
    instead expose a larger set of imperative, asterisk-wrapped controls.  We
    only classify a strong signature so ordinary ambiguous dialogue remains a
    fail-closed branch decision.
    """

    if quest_id == "282":
        return False
    texts = tuple(
        _text(action)
        for action in actions
        if isinstance(action, Mapping)
    )
    controls = {text for text in texts if _PUZZLE_CONTROL_PATTERN.fullmatch(text)}
    has_mechanism = any(
        marker in text
        for text in controls
        for marker in ("кнопк", "барабанчик", "замок", "сундук", "книг")
    )
    return len(texts) >= 4 and len(controls) >= 3 and has_mechanism


def _selected_drum(href: str) -> int | None:
    match = re.search(r"(?:[?&])ref=(\d+)(?:[&#]|$)", str(href or ""))
    return _SELECT_REF_TO_DRUM.get(int(match.group(1))) if match else None


def _implicit_completed_drums(
    actions: tuple[tuple[Mapping[str, object], str], ...],
    completed_drums: tuple[int, ...],
) -> tuple[int, ...]:
    if completed_drums:
        return ()
    texts = {text for _, text in actions}
    expected = {
        f"*крутить барабанчик на {_ORDINAL[drum]} сверху скобе*"
        for drum in (2, 3, 4, 5)
    }
    expected.add("*попытаться открыть книгу*")
    return (1,) if expected.issubset(texts) and not any("первой сверху" in text for text in texts) else ()


def _text(action: Mapping[str, object]) -> str:
    return " ".join(str(action.get("text") or "").casefold().replace("ё", "е").split())


def _unique(
    actions: tuple[tuple[Mapping[str, object], str], ...],
    expected: str,
    **metadata: object,
) -> Mapping[str, object] | None:
    matches = tuple(action for action, text in actions if text == expected)
    return {**matches[0], **metadata} if len(matches) == 1 else None
