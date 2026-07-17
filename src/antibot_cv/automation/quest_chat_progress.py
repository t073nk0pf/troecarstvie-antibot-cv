"""Conservative chat evidence for refreshing an authoritative quest step.

Chat is only a hint that the server may have advanced a collection objective.
It never completes a step.  A caller must refresh the full active catalogue and
let the usual fingerprint comparison decide what happened.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import re
from typing import Mapping, Sequence

from src.antibot_cv.automation.quest_active_catalog import ActiveQuestEntry
from src.antibot_cv.automation.quest_objective_runtime import quest_step_fingerprint


_COLLECTION_MESSAGE = re.compile(
    r"^вы\s+набрали\s+(?:достаточное|необходимое)\s+количество\s+(.+?)\s*[.!]?$",
    re.IGNORECASE,
)
_ITEM_RECEIPT_MESSAGE = re.compile(
    r"^получено\s*:\s*(.+?)\s+([1-9]\d*)\s*шт\.?$",
    re.IGNORECASE,
)
_WORD = re.compile(r"[а-яa-z0-9]+", re.IGNORECASE)
_ENDINGS = tuple(
    sorted(
        {
            "иями", "ями", "ами", "ого", "его", "ому", "ему", "иях",
            "ах", "ях", "ам", "ям", "ов", "ев", "ей", "ых", "их", "ые", "ие", "ой", "ый",
            "ий", "ая", "яя", "ую", "юю", "ом", "ем", "ым", "им",
            "а", "я", "у", "ю", "ы", "и", "е", "о", "ь", "й",
        },
        key=len,
        reverse=True,
    )
)


@dataclass(frozen=True)
class AuthoritativeQuestStep:
    quest_id: str
    quest_title: str
    fingerprint: str
    objective_text: str


@dataclass(frozen=True)
class QuestChatProgressEvidence:
    event_id: str
    resource: str
    normalized_resource: str
    quest_id: str
    quest_title: str
    fingerprint: str
    terminal_collection: bool = False


@dataclass(frozen=True)
class PendingQuestChatRefresh:
    evidence: QuestChatProgressEvidence
    trigger_revision: int
    trigger_snapshot_id: str
    started_monotonic: float
    deadline_monotonic: float
    attempts: int = 0
    dedicated_refresh_started: bool = False
    next_retry_monotonic: float = 0.0


class QuestChatReconcileState(str, Enum):
    NOT_NEWER = "not_newer"
    SAME_STEP = "same_step"
    ADVANCED = "advanced"
    REMOVED = "removed"
    UNSAFE = "unsafe"


@dataclass(frozen=True)
class QuestChatReconcileResult:
    state: QuestChatReconcileState
    reason: str
    observed_fingerprint: str | None = None


def reconcile_chat_refresh(
    pending: PendingQuestChatRefresh,
    entries: Sequence[ActiveQuestEntry],
    *,
    completed_revision: int,
) -> QuestChatReconcileResult:
    """Reconcile evidence only against a strictly newer complete catalogue."""

    if completed_revision <= pending.trigger_revision:
        return QuestChatReconcileResult(
            QuestChatReconcileState.NOT_NEWER,
            "active_catalog_revision_not_newer_than_chat_trigger",
        )
    if not pending.dedicated_refresh_started:
        return QuestChatReconcileResult(
            QuestChatReconcileState.NOT_NEWER,
            "completed_refresh_predates_chat_trigger",
        )
    matches = [entry for entry in entries if entry.id == pending.evidence.quest_id]
    if not matches:
        return QuestChatReconcileResult(
            QuestChatReconcileState.REMOVED,
            "quest_removed_after_chat_progress_without_terminal_proof",
        )
    if len(matches) != 1 or matches[0].title != pending.evidence.quest_title:
        return QuestChatReconcileResult(
            QuestChatReconcileState.UNSAFE,
            "quest_identity_missing_or_ambiguous_after_chat_progress",
        )
    fingerprint, reason = quest_step_fingerprint(matches[0])
    if fingerprint is None:
        return QuestChatReconcileResult(
            QuestChatReconcileState.UNSAFE,
            reason or "quest_fingerprint_invalid_after_chat_progress",
        )
    if fingerprint == pending.evidence.fingerprint:
        return QuestChatReconcileResult(
            QuestChatReconcileState.SAME_STEP,
            "quest_step_unchanged_after_chat_progress",
            fingerprint,
        )
    return QuestChatReconcileResult(
        QuestChatReconcileState.ADVANCED,
        "quest_step_advanced_after_chat_progress",
        fingerprint,
    )


class QuestChatProgressTracker:
    """Validate, age-check, and deduplicate collection progress hints."""

    def __init__(self, *, max_age_s: float = 8.0, max_seen: int = 256) -> None:
        if max_age_s <= 0 or max_seen <= 0:
            raise ValueError("chat progress bounds must be positive")
        self.max_age_s = float(max_age_s)
        self.max_seen = int(max_seen)
        self._seen_order: deque[tuple[str, str, str]] = deque()
        self._seen: set[tuple[str, str, str]] = set()

    def observe(
        self,
        section: object,
        *,
        snapshot_generated_at: object,
        step: AuthoritativeQuestStep | None,
        now: datetime | None = None,
    ) -> QuestChatProgressEvidence | None:
        if step is None or not _valid_step(step):
            return None
        if not isinstance(section, Mapping) or section.get("status") != "available":
            return None
        data = section.get("data")
        if (
            not isinstance(data, Mapping)
            or data.get("loadStatus") != "loaded"
            or data.get("truncated") is not False
        ):
            return None
        observations = data.get("observations")
        if not isinstance(observations, list) or len(observations) > 40:
            return None
        snapshot_time = _timestamp(snapshot_generated_at)
        current_time = now or datetime.now(timezone.utc)
        if snapshot_time is None or abs((current_time - snapshot_time).total_seconds()) > self.max_age_s:
            return None

        matches: list[QuestChatProgressEvidence] = []
        for observation in observations:
            evidence = self._validate_observation(
                observation,
                snapshot_time=snapshot_time,
                step=step,
            )
            if evidence is not None:
                matches.append(evidence)
        # Multiple new matching lines in one snapshot are ambiguous.  The next
        # authoritative catalogue refresh is intentionally not guessed from them.
        if len(matches) != 1:
            return None
        evidence = matches[0]
        key = (step.quest_id, step.fingerprint, evidence.normalized_resource)
        if key in self._seen:
            return None
        self._seen.add(key)
        self._seen_order.append(key)
        while len(self._seen_order) > self.max_seen:
            self._seen.discard(self._seen_order.popleft())
        return evidence

    def _validate_observation(
        self,
        observation: object,
        *,
        snapshot_time: datetime,
        step: AuthoritativeQuestStep,
    ) -> QuestChatProgressEvidence | None:
        if not isinstance(observation, Mapping) or observation.get("isNew") is not True:
            return None
        event_id = _bounded_text(observation.get("eventId"), 200)
        text = _bounded_text(observation.get("text"), 500)
        claimed_resource = _bounded_text(observation.get("resource"), 220)
        observed_at = _timestamp(observation.get("observedAt"))
        if not event_id or not text or not claimed_resource or observed_at is None:
            return None
        age = (snapshot_time - observed_at).total_seconds()
        if age < -1.0 or age > self.max_age_s:
            return None
        resource = collection_resource(text)
        if resource is None or normalize_phrase(resource) != normalize_phrase(claimed_resource):
            return None
        if not resource_matches_objective(resource, step.objective_text):
            return None
        return QuestChatProgressEvidence(
            event_id,
            resource,
            normalize_phrase(resource),
            step.quest_id,
            step.quest_title,
            step.fingerprint,
            collection_completes_step(resource, step.objective_text),
        )


def collection_resource(text: object) -> str | None:
    value = _bounded_text(text, 500)
    match = _COLLECTION_MESSAGE.fullmatch(value)
    if match is None:
        match = _ITEM_RECEIPT_MESSAGE.fullmatch(value)
    if match is None:
        return None
    resource = re.sub(r"\s+", " ", match.group(1)).strip(" .!\t\r\n")
    return resource if 2 <= len(resource) <= 220 and _WORD.search(resource) else None


def normalize_phrase(value: object) -> str:
    return " ".join(_WORD.findall(_bounded_text(value, 1000).casefold().replace("ё", "е")))


def resource_matches_objective(resource: object, objective: object) -> bool:
    resource_tokens = tuple(_stem(token) for token in _WORD.findall(normalize_phrase(resource)))
    objective_tokens = tuple(_stem(token) for token in _WORD.findall(normalize_phrase(objective)))
    if not resource_tokens or any(len(token) < 3 for token in resource_tokens):
        return False
    width = len(resource_tokens)
    occurrences = sum(
        objective_tokens[index : index + width] == resource_tokens
        for index in range(max(0, len(objective_tokens) - width + 1))
    )
    if occurrences == 1:
        return True
    if occurrences > 1:
        return False

    # Server chat commonly joins the trophy and its source into one phrase
    # (for example, "жал Шершней-мстителей"), while the quest body
    # mentions the monster and "5 жал" in separate clauses.  Match that
    # representation semantically.  The leading token is the trophy noun and
    # must be unique in the objective; the remaining provenance/monster tokens
    # only need to be present because quest prose commonly repeats the target.
    distinct_resource_tokens = set(resource_tokens)
    return (
        objective_tokens.count(resource_tokens[0]) == 1
        and all(token in objective_tokens for token in distinct_resource_tokens)
    )


def collection_completes_step(resource: object, objective: object) -> bool:
    """Whether one exact collection signal is followed by an explicit return."""

    text = _bounded_text(objective, 1000)
    if not resource_matches_objective(resource, text):
        return False
    normalized = normalize_phrase(text)
    return bool(
        re.search(r"\b(?:возвращайтесь|вернитесь|возвратитесь)\s+к\b", normalized)
        and len(
            re.findall(
                r"\b(?:получите|добудьте|соберите|наберите|принесите)\b",
                normalized,
            )
        )
        == 1
    )


def _stem(token: str) -> str:
    if len(token) < 4:
        return token
    for ending in _ENDINGS:
        if token.endswith(ending) and len(token) - len(ending) >= 3:
            return token[: -len(ending)].rstrip("ьй")
    return token.rstrip("ьй")


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or len(value) > 80:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _bounded_text(value: object, limit: int) -> str:
    return value.strip() if isinstance(value, str) and 0 < len(value) <= limit else ""


def _valid_step(step: AuthoritativeQuestStep) -> bool:
    return bool(
        step.quest_id.isdecimal()
        and int(step.quest_id) > 0
        and step.quest_title.strip()
        and step.fingerprint.strip()
        and step.objective_text.strip()
    )
