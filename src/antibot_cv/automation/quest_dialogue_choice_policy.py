"""Conservative selection of a progress-oriented quest dialogue reply."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


_REFUSAL_MARKERS = (
    "не могу",
    "не буду",
    "не стану",
    "отказыва",
    "прощай",
    "до свидан",
    "задержаться",
    "не сейчас",
)


def select_progress_dialogue_action(
    actions: Sequence[Mapping[str, object]],
) -> Mapping[str, object] | None:
    """Return the sole non-refusal reply when the visible choice is decisive.

    Different text answers can advance different quest branches.  The policy
    therefore refuses ties and unknown alternatives.  Exact duplicate DOM
    controls are treated as one action because they share the same server
    reference and text.
    """

    distinct: dict[tuple[str, str], Mapping[str, object]] = {}
    for action in actions:
        raw_ref = action.get("ref")
        raw_text = action.get("text")
        if not isinstance(raw_ref, str) or not isinstance(raw_text, str):
            return None
        ref = raw_ref.strip()
        text = raw_text.strip()
        if (
            not ref.isdecimal()
            or int(ref) <= 0
            or len(ref) > 80
            or not text
            or len(text) > 1200
        ):
            return None
        distinct.setdefault((ref, text), action)
    candidates = tuple(distinct.values())
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) < 2:
        return None

    def score(candidate: Mapping[str, object]) -> int:
        normalized = " ".join(str(candidate.get("text") or "").casefold().split())
        return -sum(marker in normalized for marker in _REFUSAL_MARKERS)

    ranked = sorted(((score(candidate), candidate) for candidate in candidates), key=lambda entry: entry[0], reverse=True)
    best_score, best = ranked[0]
    runner_up_score, _ = ranked[1]
    return best if best_score >= 0 and best_score > runner_up_score else None
