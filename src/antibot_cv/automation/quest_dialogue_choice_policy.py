"""Conservative selection of a progress-oriented quest dialogue reply."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re


_POSITIVE_PATTERNS = (
    (re.compile(r"\b(?:сдела(?:ю|ем)|выполн(?:ю|им)|помогу|поможем|добуду|добудем|"
                r"принесу|принесем|найду|найдем|проведу|проведем|продолжу|продолжим|"
                r"отправлюсь|отправимся|возьмусь|возьмемся)\b"), 4),
    (re.compile(r"\b(?:я\s+)?(?:соглас(?:ен|на|ны)|берусь|принимаю)\b"), 4),
    (re.compile(r"\b(?:я\s+)?готов(?:а|ы)?\s+(?:\w+\s+){0,2}"
                r"(?:помочь|сделать|выполнить|искать|найти|отправиться|провести)\b"), 4),
    (re.compile(r"\bгде\s+мне\s+(?:искать|найти|добыть|взять)\b"), 3),
    (re.compile(r"\bкуда\s+мне\s+(?:идти|отправиться|нести)\b"), 3),
    (re.compile(r"\b(?:сказывай|рассказывай)\b"), 1),
    (re.compile(r"\b(?:в\s+моих\s+силах|как\s+можно\s+скорее)\b"), 1),
)
_DIRECT_DENY_PATTERNS = (
    re.compile(r"\bне\s+(?:соглас\w*|сдела\w*|выполн\w*|помог\w*|помож\w*|"
               r"возьм\w*|готов\w*|буду|стану|хочу|желаю|могу|ведаю|знаю|умею|"
               r"подготовлен\w*|жрец)\b"),
    re.compile(r"\b(?:никогда|отказ\w*|прощай|проваливай|убирайся|замолчи)\b"),
    re.compile(r"\b(?:до\s+свидания|не\s+сейчас|не\s+твое\s+дело|сам\s+сделай)\b"),
    re.compile(r"\b(?:иначе\b.{0,80}\b(?:мертвец\w*|убью|погиб\w*)|угрожа\w*)\b"),
)
_DIRECT_NO_PATTERN = re.compile(r"^нет\b")
_PROMISE_REMINDER_PATTERN = re.compile(
    r"\bобещал\w*\b.{0,100}\b(?:нехорошо|нельзя|не\s+стоит)\b.{0,80}\bотказ\w*"
)
_UNCERTAINTY_PATTERNS = (
    re.compile(r"\bне\s+думаю\b"),
    re.compile(r"\bсомнева\w*\b"),
    re.compile(r"\bвряд\s+ли\b"),
    re.compile(r"\bможет\s+быть\b"),
    re.compile(r"\bнеужели\b"),
    re.compile(r"\bне\s+уверен\w*\b"),
)
_SAFE_GREETING_CONTINUATION_PATTERNS = (
    re.compile(r"\b(?:хочу|желаю)\s+(?:я\s+)?поприветствовать\b"),
    re.compile(r"\bрад\s+(?:я\s+)?служить\s+во\s+благо\b"),
)
_SAFE_ATONEMENT_CONTINUATION_PATTERN = re.compile(
    r"\bчто\s+могу\s+сделать\b.{0,120}\b(?:искупить|загладить)\s+вин\w*\b"
)
_HESITATION_PATTERNS = (
    re.compile(r"\bбоюсь\b"),
    re.compile(r"\b(?:почему|зачем|разве|что\s+ты)\b"),
)
_SAFE_ACTION_REQUEST_QUESTION = re.compile(
    r"^(?:где\s+мне\s+(?:искать|найти|добыть|взять)|"
    r"куда\s+мне\s+(?:идти|отправиться|нести))\b[^?!.]*\?\s*$"
)
_MIN_POSITIVE_EVIDENCE = 3
_MIN_NET_SCORE = 2
_MIN_SAFE_MARGIN = 2
_NEGATION_WINDOW_TOKENS = 3
_TOKEN_PATTERN = re.compile(r"[a-zа-я0-9]+", re.IGNORECASE)


@dataclass(frozen=True)
class _ChoiceScore:
    net: int
    positive: int
    negative: int
    disqualified: bool


def select_progress_dialogue_action(
    actions: Sequence[Mapping[str, object]],
) -> Mapping[str, object] | None:
    """Return one safely bound progress reply when the visible choice is decisive.

    Different text answers can advance different quest branches.  The policy
    therefore refuses ties, unknown alternatives, explicit denials, and
    duplicate DOM controls that the bridge could not mutate uniquely.
    """

    distinct: dict[tuple[str, str], Mapping[str, object]] = {}
    for action in actions:
        if not isinstance(action, Mapping):
            return None
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
        identity = (ref, text)
        if identity in distinct:
            return None
        distinct[identity] = action
    candidates = tuple(distinct.values())
    if not candidates:
        return None
    if len(candidates) == 1:
        sole_score = _score(candidates[0])
        if sole_score.disqualified:
            return None
        if sole_score.negative > 0 and sole_score.positive < _MIN_POSITIVE_EVIDENCE:
            return None
        return candidates[0]

    scored = tuple((_score(candidate), candidate) for candidate in candidates)
    positive_candidates = tuple(
        (choice_score, candidate)
        for choice_score, candidate in scored
        if not choice_score.disqualified
        and choice_score.positive >= _MIN_POSITIVE_EVIDENCE
        and choice_score.net >= _MIN_NET_SCORE
    )
    if len(positive_candidates) != 1:
        return None
    best_score, best = positive_candidates[0]
    for runner_score, runner in scored:
        if runner is best:
            continue
        if (
            best_score.net - runner_score.net < _MIN_SAFE_MARGIN
            or (
                not runner_score.disqualified
                and (runner_score.negative <= 0 or runner_score.net >= 0)
            )
        ):
            return None
    return best


def select_exploratory_dialogue_action(
    actions: Sequence[Mapping[str, object]],
    *,
    attempted_refs: Sequence[str] = (),
) -> Mapping[str, object] | None:
    """Select a bounded dialogue continuation, avoiding refusals and repeats.

    The semantic selector remains the first choice.  When it cannot rank a
    branch, deterministic source order provides bounded exploration.  Explicit
    denials, uncertain commitments and malformed controls remain excluded.
    """

    attempted = {str(value).strip() for value in attempted_refs}
    preferred = select_progress_dialogue_action(actions)
    if preferred is not None and str(preferred.get("ref") or "").strip() not in attempted:
        return preferred
    distinct: dict[tuple[str, str], Mapping[str, object]] = {}
    for action in actions:
        if not isinstance(action, Mapping):
            return None
        ref = str(action.get("ref") or "").strip()
        text = str(action.get("text") or "").strip()
        if (
            not ref.isdecimal() or int(ref) <= 0 or len(ref) > 80
            or not text or len(text) > 1200
            or (ref, text) in distinct
        ):
            return None
        distinct[(ref, text)] = action
    for (ref, _), action in distinct.items():
        if ref in attempted:
            continue
        if not _exploration_disqualified(action):
            return action
    return None


def _exploration_disqualified(candidate: Mapping[str, object]) -> bool:
    """Keep explicit refusals out while allowing neutral story questions.

    The strict scorer decides which branch is semantically preferred.  When
    no branch wins, bounded exploration must still advance ordinary dialogue
    questions; treating uncertainty as a hard veto previously abandoned the
    entire quest and switched to an unrelated combat objective.
    """

    normalized = " ".join(
        str(candidate.get("text") or "").casefold().replace("ё", "е").split()
    )
    promise_reminder = _PROMISE_REMINDER_PATTERN.search(normalized) is not None
    return (
        (
            any(pattern.search(normalized) for pattern in _DIRECT_DENY_PATTERNS)
            or _DIRECT_NO_PATTERN.search(normalized) is not None
        )
        and not promise_reminder
        and _SAFE_ATONEMENT_CONTINUATION_PATTERN.search(normalized) is None
    )


def _score(candidate: Mapping[str, object]) -> _ChoiceScore:
    normalized = " ".join(
        str(candidate.get("text") or "").casefold().replace("ё", "е").split()
    )
    promise_reminder = _PROMISE_REMINDER_PATTERN.search(normalized) is not None
    direct_deny = (
        (
            any(pattern.search(normalized) for pattern in _DIRECT_DENY_PATTERNS)
            or _DIRECT_NO_PATTERN.search(normalized) is not None
        )
        and not promise_reminder
    )
    safe_greeting_continuation = all(
        pattern.search(normalized)
        for pattern in _SAFE_GREETING_CONTINUATION_PATTERNS
    )
    safe_atonement_continuation = (
        _SAFE_ATONEMENT_CONTINUATION_PATTERN.search(normalized) is not None
    )
    uncertain = (
        any(pattern.search(normalized) for pattern in _UNCERTAINTY_PATTERNS)
        and not safe_greeting_continuation
        and not safe_atonement_continuation
    )
    positive, negated_positive = _positive_evidence(normalized)
    if promise_reminder:
        positive += 4
    if safe_greeting_continuation:
        positive += 3
    if safe_atonement_continuation:
        positive += 3
    unsafe_commitment_question = (
        positive >= _MIN_POSITIVE_EVIDENCE
        and "?" in normalized
        and not safe_greeting_continuation
        and not safe_atonement_continuation
        and _SAFE_ACTION_REQUEST_QUESTION.fullmatch(normalized) is None
    )
    disqualified = (
        (direct_deny and not safe_atonement_continuation)
        or uncertain
        or (negated_positive and not safe_atonement_continuation)
        or unsafe_commitment_question
    )
    negative = sum(2 for pattern in _HESITATION_PATTERNS if pattern.search(normalized))
    if "?" in normalized:
        negative += 1
    if disqualified:
        negative += 100
    return _ChoiceScore(positive - negative, positive, negative, disqualified)


def _positive_evidence(normalized: str) -> tuple[int, bool]:
    """Score lexical commitments and veto any match governed by nearby ``не``."""

    total = 0
    for pattern, weight in _POSITIVE_PATTERNS:
        matches = tuple(pattern.finditer(normalized))
        if not matches:
            continue
        for match in matches:
            preceding_tokens = _TOKEN_PATTERN.findall(normalized[: match.start()])
            if "не" in preceding_tokens[-_NEGATION_WINDOW_TOKENS:]:
                return total, True
        total += weight
    return total, False
