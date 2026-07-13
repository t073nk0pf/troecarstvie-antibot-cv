"""Pure, fail-closed execution policy for active travel-to-NPC quest steps."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum
import re
from types import MappingProxyType

from src.antibot_cv.automation.quest_active_catalog import ActiveQuestEntry
from src.antibot_cv.automation.quest_giver import resolve_unique_giver
from src.antibot_cv.automation.quest_objective_runtime import quest_step_fingerprint


class QuestDialoguePhase(str, Enum):
    ROUTE = "ROUTE"
    NPC_LOOKUP = "NPC_LOOKUP"
    NPC_DIALOG = "NPC_DIALOG"
    VERIFY_ACTIVE = "VERIFY_ACTIVE"


class QuestDialogueIntent(str, Enum):
    OPEN_NPC = "OPEN_NPC"
    OPEN_QUEST = "OPEN_QUEST"
    ANSWER_DIALOG = "ANSWER_DIALOG"
    COMPLETE_STEP = "COMPLETE_STEP"


class QuestDialogueError(ValueError):
    """A stable fail-closed parsing or decision failure."""

    def __init__(self, unsafe_reason: str, message: str) -> None:
        super().__init__(message)
        self.unsafe_reason = unsafe_reason


@dataclass(frozen=True)
class QuestDialogueObjective:
    quest_id: str
    quest_title: str
    objective: str
    npc_query: str
    location: str
    fingerprint: str


@dataclass(frozen=True)
class PendingQuestDialogue:
    objective: QuestDialogueObjective
    phase: QuestDialoguePhase
    location_id: str | None = None
    npc_id: str | None = None
    npc_name: str | None = None
    area_snapshot_id: str | None = None
    quest_opened: bool = False
    dialog_steps: int = 0


@dataclass(frozen=True)
class QuestDialogueDecision:
    intent: QuestDialogueIntent
    action_type: str
    action_metadata: Mapping[str, object]
    action_failure_reason: str
    quest_id: str
    snapshot_id: str


def parse_dialogue_objective(entry: ActiveQuestEntry) -> QuestDialogueObjective:
    """Parse a single-location Russian ``go to NPC`` active quest step.

    The parser deliberately accepts only the shape evidenced by the active
    quest page: one navigation target that is also named as a location in the
    objective, and an NPC phrase between ``к`` and ``в <location>``.
    """

    if not isinstance(entry, ActiveQuestEntry):
        raise QuestDialogueError("dialogue_entry_invalid", "active quest entry is invalid")
    fingerprint, reason = quest_step_fingerprint(entry)
    if fingerprint is None:
        raise QuestDialogueError(f"dialogue_{reason}", "active quest fingerprint is unsafe")
    raw_objective = entry.data.get("objective")
    navigation = entry.data.get("navigation")
    if not isinstance(raw_objective, str):  # Also guarded by the fingerprint.
        raise QuestDialogueError("dialogue_objective_missing", "quest objective is missing")
    if not isinstance(navigation, (tuple, list)) or len(navigation) != 1:
        raise QuestDialogueError(
            "dialogue_location_missing_or_ambiguous",
            "dialogue step must expose exactly one navigation location",
        )
    raw_location = navigation[0]
    if not isinstance(raw_location, Mapping):
        raise QuestDialogueError("dialogue_location_invalid", "navigation location is invalid")
    target = _bounded_text(raw_location.get("target"), max_length=220)
    label = _bounded_text(raw_location.get("text"), max_length=220)
    if not target or not label or _normalized(target) != _normalized(label):
        raise QuestDialogueError(
            "dialogue_location_missing_or_ambiguous",
            "dialogue navigation does not identify one exact location",
        )

    # Require the exact navigation label to occur after the Russian locative
    # preposition. This rejects NPC/building targets such as ``Дом Франка``.
    npc_match = re.search(
        rf"(?:^|\s)к\s+(.+?)\s+в\s+{re.escape(label)}(?=\s|[.,!?;:]|$)",
        raw_objective,
        flags=re.IGNORECASE,
    )
    if npc_match is None:
        raise QuestDialogueError(
            "dialogue_npc_missing_or_ambiguous",
            "quest objective does not identify an NPC before the navigation location",
        )
    npc_query = " ".join(npc_match.group(1).split())
    if not npc_query or len(npc_query) > 180 or len(re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", npc_query)) < 1:
        raise QuestDialogueError("dialogue_npc_missing_or_ambiguous", "quest NPC query is invalid")
    return QuestDialogueObjective(
        quest_id=entry.id,
        quest_title=entry.title,
        objective=raw_objective,
        npc_query=npc_query,
        location=label,
        fingerprint=fingerprint,
    )


class QuestDialogueRuntime:
    """Own state and safe decisions for one active dialogue step."""

    def __init__(self) -> None:
        self.pending: PendingQuestDialogue | None = None
        self._pending_decision: QuestDialogueDecision | None = None

    def begin(
        self, entry: ActiveQuestEntry, *, already_at_location: bool
    ) -> PendingQuestDialogue:
        if self.pending is not None:
            raise RuntimeError("quest dialogue is already in progress")
        objective = parse_dialogue_objective(entry)
        self.pending = PendingQuestDialogue(
            objective=objective,
            phase=(
                QuestDialoguePhase.NPC_LOOKUP
                if already_at_location
                else QuestDialoguePhase.ROUTE
            ),
        )
        self._pending_decision = None
        return self.pending

    def mark_route_arrived(self) -> PendingQuestDialogue:
        pending = self._require(QuestDialoguePhase.ROUTE)
        self._pending_decision = None
        self.pending = replace(pending, phase=QuestDialoguePhase.NPC_LOOKUP)
        return self.pending

    def decide_area_npc(self, snapshot: object) -> QuestDialogueDecision:
        pending = self._require(QuestDialoguePhase.NPC_LOOKUP)
        self._pending_decision = None
        if not isinstance(snapshot, dict) or snapshot.get("ok") is not True:
            raise QuestDialogueError("dialogue_npc_snapshot_invalid", "area NPC snapshot is unavailable")
        if snapshot.get("truncated") is not False:
            raise QuestDialogueError("dialogue_npc_snapshot_invalid", "area NPC snapshot is truncated")
        snapshot_id = _bounded_text(snapshot.get("snapshotId"), max_length=120)
        location = snapshot.get("location")
        location_id = _bounded_text(location.get("id"), max_length=80) if isinstance(location, dict) else ""
        location_name = _bounded_text(location.get("name"), max_length=220) if isinstance(location, dict) else ""
        if not snapshot_id.startswith("area-npcs-") or not location_id:
            raise QuestDialogueError("dialogue_npc_snapshot_invalid", "area NPC snapshot identity is missing")
        if location_name and _normalized(location_name) != _normalized(pending.objective.location):
            raise QuestDialogueError("dialogue_location_mismatch", "area snapshot is from another location")
        try:
            match = resolve_unique_giver(pending.objective.npc_query, snapshot.get("items"))
        except ValueError as exc:
            raise QuestDialogueError("dialogue_npc_missing_or_ambiguous", str(exc)) from exc
        npc_id = _bounded_text(match.get("dataId"), max_length=80)
        npc_name = _bounded_text(match.get("name"), max_length=180)
        if not npc_id.isdecimal() or int(npc_id) <= 0 or not npc_name:
            raise QuestDialogueError("dialogue_npc_identity_invalid", "matched NPC identity is invalid")
        self.pending = replace(
            pending,
            location_id=location_id,
            npc_id=npc_id,
            npc_name=npc_name,
            area_snapshot_id=snapshot_id,
        )
        return self._remember(
            QuestDialogueDecision(
                intent=QuestDialogueIntent.OPEN_NPC,
                action_type="open_exact_npc",
                action_metadata=_metadata(
                    expected_snapshot_id=snapshot_id,
                    expected_location_id=location_id,
                    npc_id=npc_id,
                    expected_name=npc_name,
                    expected_dialog_name=npc_name,
                    quest_id=pending.objective.quest_id,
                ),
                action_failure_reason="quest_dialogue_npc_open_failed",
                quest_id=pending.objective.quest_id,
                snapshot_id=snapshot_id,
            )
        )

    def decide_dialog(self, snapshot: object, *, max_steps: int = 20) -> QuestDialogueDecision:
        pending = self._require(QuestDialoguePhase.NPC_DIALOG)
        self._pending_decision = None
        if not isinstance(max_steps, int) or isinstance(max_steps, bool) or max_steps <= 0:
            raise ValueError("max_steps must be a positive integer")
        if (
            not isinstance(snapshot, dict)
            or snapshot.get("ok") is not True
            or snapshot.get("truncated") is not False
            or snapshot.get("identityMatches") is not True
        ):
            raise QuestDialogueError("dialogue_snapshot_invalid", "NPC dialogue snapshot is not authoritative")
        snapshot_id = _bounded_text(snapshot.get("snapshotId"), max_length=120)
        if not snapshot_id.startswith("npc-dialog-") or not pending.npc_id:
            raise QuestDialogueError("dialogue_snapshot_invalid", "NPC dialogue identity is missing")
        common = {
            "expected_snapshot_id": snapshot_id,
            "npc_id": pending.npc_id,
            "quest_id": pending.objective.quest_id,
            "expected_title": pending.objective.quest_title,
        }
        if not pending.quest_opened:
            opens = _actions(
                snapshot.get("questActions"),
                quest_id=pending.objective.quest_id,
                action="open",
                title=pending.objective.quest_title,
            )
            if len(opens) != 1:
                raise QuestDialogueError("dialogue_open_missing_or_ambiguous", "quest open action is missing or ambiguous")
            return self._remember(
                QuestDialogueDecision(
                    QuestDialogueIntent.OPEN_QUEST,
                    "npc_quest_action",
                    _metadata(**common, action="open"),
                    "quest_dialogue_open_failed",
                    pending.objective.quest_id,
                    snapshot_id,
                )
            )

        answers = _actions(
            snapshot.get("dialogActions"),
            quest_id=pending.objective.quest_id,
            npc_id=pending.npc_id,
            action="answer",
        )
        completions = _actions(
            snapshot.get("doneActions"),
            quest_id=pending.objective.quest_id,
            npc_id=pending.npc_id,
            action="done",
        )
        if len(answers) > 1 or len(completions) > 1 or (answers and completions):
            raise QuestDialogueError("dialogue_action_ambiguous", "dialogue exposes ambiguous progression actions")
        if pending.dialog_steps >= max_steps:
            raise QuestDialogueError("dialogue_step_limit_exceeded", "quest dialogue step limit exceeded")
        if len(answers) == 1:
            expected_ref = _bounded_text(answers[0].get("ref"), max_length=80)
            expected_text = _bounded_text(answers[0].get("text"), max_length=1200)
            if not expected_ref.isdecimal() or int(expected_ref) <= 0 or not expected_text:
                raise QuestDialogueError("dialogue_answer_invalid", "dialogue answer identity is invalid")
            return self._remember(
                QuestDialogueDecision(
                    QuestDialogueIntent.ANSWER_DIALOG,
                    "npc_quest_action",
                    _metadata(**common, action="answer", expected_ref=expected_ref, expected_text=expected_text),
                    "quest_dialogue_answer_failed",
                    pending.objective.quest_id,
                    snapshot_id,
                )
            )
        if len(completions) == 1:
            point_id = _bounded_text(completions[0].get("pointId"), max_length=80)
            expected_text = _bounded_text(completions[0].get("text"), max_length=1200)
            if not point_id.isdecimal() or int(point_id) <= 0 or not expected_text:
                raise QuestDialogueError("dialogue_completion_invalid", "dialogue completion identity is invalid")
            return self._remember(
                QuestDialogueDecision(
                    QuestDialogueIntent.COMPLETE_STEP,
                    "npc_quest_action",
                    _metadata(**common, action="done", expected_point_id=point_id, expected_text=expected_text),
                    "quest_dialogue_completion_failed",
                    pending.objective.quest_id,
                    snapshot_id,
                )
            )
        raise QuestDialogueError("dialogue_action_missing", "dialogue progression action is missing")

    def acknowledge(self, decision: QuestDialogueDecision) -> PendingQuestDialogue:
        if decision is not self._pending_decision or self.pending is None:
            raise RuntimeError("quest dialogue decision is stale")
        if decision.quest_id != self.pending.objective.quest_id:
            raise RuntimeError("quest dialogue decision belongs to another quest")
        pending = self.pending
        if decision.intent is QuestDialogueIntent.OPEN_NPC:
            if not pending.npc_id:
                raise RuntimeError("quest NPC was not observed")
            updated = replace(pending, phase=QuestDialoguePhase.NPC_DIALOG)
        elif decision.intent is QuestDialogueIntent.OPEN_QUEST:
            updated = replace(pending, quest_opened=True)
        elif decision.intent is QuestDialogueIntent.ANSWER_DIALOG:
            updated = replace(pending, dialog_steps=pending.dialog_steps + 1)
        elif decision.intent is QuestDialogueIntent.COMPLETE_STEP:
            updated = replace(
                pending,
                phase=QuestDialoguePhase.VERIFY_ACTIVE,
                dialog_steps=pending.dialog_steps + 1,
            )
        else:  # pragma: no cover
            raise RuntimeError("unsupported quest dialogue decision")
        self.pending = updated
        self._pending_decision = None
        return updated

    def finish_verified(self, *, quest_id: str, previous_fingerprint: str) -> None:
        pending = self._require(QuestDialoguePhase.VERIFY_ACTIVE)
        if quest_id != pending.objective.quest_id or previous_fingerprint != pending.objective.fingerprint:
            raise RuntimeError("active refresh verification does not match the dialogue step")
        self.pending = None
        self._pending_decision = None

    def _remember(self, decision: QuestDialogueDecision) -> QuestDialogueDecision:
        self._pending_decision = decision
        return decision

    def _require(self, phase: QuestDialoguePhase) -> PendingQuestDialogue:
        if self.pending is None or self.pending.phase is not phase:
            raise RuntimeError(f"quest dialogue phase must be {phase.value}")
        return self.pending


def _actions(
    raw: object,
    *,
    quest_id: str,
    action: str,
    npc_id: str | None = None,
    title: str | None = None,
) -> tuple[dict[str, object], ...]:
    if not isinstance(raw, list):
        return ()
    matches: list[dict[str, object]] = []
    for candidate in raw:
        if not isinstance(candidate, dict):
            continue
        if (
            str(candidate.get("questId") or "") != quest_id
            or candidate.get("action") != action
            or candidate.get("visible") is not True
            or candidate.get("disabled") is not False
        ):
            continue
        if npc_id is not None and str(candidate.get("npcId") or "") != npc_id:
            continue
        if title is not None and _normalized(candidate.get("title")) != _normalized(title):
            continue
        matches.append(candidate)
    return tuple(matches)


def _metadata(**values: object) -> Mapping[str, object]:
    return MappingProxyType(dict(values))


def _normalized(value: object) -> str:
    return " ".join(str(value or "").casefold().replace("ё", "е").split())


def _bounded_text(value: object, *, max_length: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= max_length else ""
