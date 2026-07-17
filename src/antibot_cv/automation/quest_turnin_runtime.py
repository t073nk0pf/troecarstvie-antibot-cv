"""Pure fail-closed policy for a bounded completed-quest turn-in.

The runtime consumes validated domain observations and emits action requests;
it never parses a page or performs an action itself.  Terminal completion is
accepted only after a fresh, complete active catalogue no longer contains the
same pinned quest.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from types import MappingProxyType

from src.antibot_cv.automation.quest_active_catalog import ActiveQuestEntry
from src.antibot_cv.automation.quest_chain_runtime import QuestChainLease
from src.antibot_cv.automation.quest_director_policy import QuestRef
from src.antibot_cv.automation.quest_giver import resolve_unique_giver
from src.antibot_cv.automation.quest_objective_runtime import quest_step_fingerprint
from src.antibot_cv.automation.quest_objective_router import (
    ObjectiveRouteKind,
    ObjectiveRouteStatus,
    classify_objective,
)


class QuestTurnInPhase(str, Enum):
    ROUTE = "ROUTE"
    NPC_LOOKUP = "NPC_LOOKUP"
    NPC_DIALOG = "NPC_DIALOG"
    VERIFY_ACTIVE = "VERIFY_ACTIVE"


class QuestTurnInIntent(str, Enum):
    OPEN_NPC = "OPEN_NPC"
    OPEN_QUEST = "OPEN_QUEST"
    ANSWER_DIALOG = "ANSWER_DIALOG"
    COMPLETE_QUEST = "COMPLETE_QUEST"


class QuestTurnInError(ValueError):
    def __init__(self, unsafe_reason: str, message: str) -> None:
        super().__init__(message)
        self.unsafe_reason = unsafe_reason


@dataclass(frozen=True)
class QuestTurnInObjective:
    quest_id: str
    quest_title: str
    giver_name: str
    location: str
    completed_fingerprint: str


@dataclass(frozen=True)
class PendingQuestTurnIn:
    objective: QuestTurnInObjective
    phase: QuestTurnInPhase
    location_id: str | None = None
    npc_id: str | None = None
    npc_name: str | None = None
    quest_opened: bool = False
    dialog_steps: int = 0
    last_answer_ref: str | None = None
    completion_after_revision: int | None = None


@dataclass(frozen=True)
class QuestTurnInDecision:
    intent: QuestTurnInIntent
    action_type: str
    action_metadata: Mapping[str, object]
    action_failure_reason: str
    quest_id: str
    snapshot_id: str


class QuestTurnInRuntime:
    """Own one bounded turn-in transaction without executing side effects."""

    def __init__(self) -> None:
        self.pending: PendingQuestTurnIn | None = None
        self._pending_decision: QuestTurnInDecision | None = None

    def begin(
        self,
        entry: ActiveQuestEntry,
        *,
        lease: QuestChainLease,
        quest_ref: QuestRef,
        already_at_location: bool,
        terminal_collection_confirmed: bool = False,
    ) -> PendingQuestTurnIn:
        if self.pending is not None:
            raise RuntimeError("quest turn-in is already in progress")
        objective = _turn_in_objective(
            entry,
            lease=lease,
            quest_ref=quest_ref,
            terminal_collection_confirmed=terminal_collection_confirmed,
        )
        self.pending = PendingQuestTurnIn(
            objective,
            QuestTurnInPhase.NPC_LOOKUP if already_at_location else QuestTurnInPhase.ROUTE,
        )
        self._pending_decision = None
        return self.pending

    def mark_route_arrived(self) -> PendingQuestTurnIn:
        pending = self._require(QuestTurnInPhase.ROUTE)
        self.pending = replace(pending, phase=QuestTurnInPhase.NPC_LOOKUP)
        self._pending_decision = None
        return self.pending

    def decide_area_npc(self, snapshot: object) -> QuestTurnInDecision:
        pending = self._require(QuestTurnInPhase.NPC_LOOKUP)
        self._pending_decision = None
        if not isinstance(snapshot, dict) or snapshot.get("ok") is not True or snapshot.get("truncated") is not False:
            raise QuestTurnInError("turn_in_npc_snapshot_invalid", "area NPC snapshot is not authoritative")
        snapshot_id = _text(snapshot.get("snapshotId"), 120)
        location = snapshot.get("location")
        location_id = _text(location.get("id"), 80) if isinstance(location, dict) else ""
        location_name = _text(location.get("name"), 220) if isinstance(location, dict) else ""
        if not snapshot_id.startswith("area-npcs-") or not location_id:
            raise QuestTurnInError("turn_in_npc_snapshot_invalid", "area NPC snapshot identity is missing")
        if not location_name or _normalize(location_name) != _normalize(pending.objective.location):
            raise QuestTurnInError("turn_in_location_mismatch", "area snapshot is from another location")
        try:
            match = resolve_unique_giver(pending.objective.giver_name, snapshot.get("items"))
        except ValueError as exc:
            raise QuestTurnInError("turn_in_npc_missing_or_ambiguous", str(exc)) from exc
        npc_id = _text(match.get("dataId"), 80)
        npc_name = _text(match.get("name"), 180)
        if not npc_id.isdecimal() or int(npc_id) < 0 or not npc_name:
            raise QuestTurnInError("turn_in_npc_identity_invalid", "matched NPC identity is invalid")
        self.pending = replace(pending, location_id=location_id, npc_id=npc_id, npc_name=npc_name)
        return self._remember(QuestTurnInDecision(
            QuestTurnInIntent.OPEN_NPC,
            "open_exact_npc",
            _metadata(expected_snapshot_id=snapshot_id, expected_location_id=location_id, npc_id=npc_id,
                      expected_name=npc_name, expected_dialog_name=pending.objective.giver_name,
                      quest_id=pending.objective.quest_id),
            "quest_turn_in_npc_open_failed", pending.objective.quest_id, snapshot_id,
        ))

    def decide_dialog(self, snapshot: object, *, max_steps: int = 20) -> QuestTurnInDecision:
        pending = self._require(QuestTurnInPhase.NPC_DIALOG)
        self._pending_decision = None
        if not isinstance(max_steps, int) or isinstance(max_steps, bool) or max_steps <= 0:
            raise ValueError("max_steps must be a positive integer")
        if (not isinstance(snapshot, dict) or snapshot.get("ok") is not True
                or snapshot.get("truncated") is not False or snapshot.get("identityMatches") is not True):
            raise QuestTurnInError("turn_in_dialog_snapshot_invalid", "NPC dialogue snapshot is not authoritative")
        snapshot_id = _text(snapshot.get("snapshotId"), 120)
        if not snapshot_id.startswith("npc-dialog-") or not pending.npc_id:
            raise QuestTurnInError("turn_in_dialog_snapshot_invalid", "NPC dialogue identity is missing")
        common = dict(expected_snapshot_id=snapshot_id, npc_id=pending.npc_id,
                      quest_id=pending.objective.quest_id, expected_title=pending.objective.quest_title)
        answers = _actions(snapshot.get("dialogActions"), quest_id=pending.objective.quest_id,
                           action="answer", npc_id=pending.npc_id)
        completions = _actions(snapshot.get("doneActions"), quest_id=pending.objective.quest_id,
                               action="done", npc_id=pending.npc_id)
        effective_pending = pending
        inferred_already_open = False
        if not pending.quest_opened:
            opens = _actions(snapshot.get("questActions"), quest_id=pending.objective.quest_id, action="open")
            if len(opens) == 1:
                if answers or completions:
                    raise QuestTurnInError(
                        "turn_in_action_ambiguous",
                        "dialogue exposes open and progression actions together",
                    )
                title = _text(opens[0].get("title"), 220)
                if not title:
                    raise QuestTurnInError("turn_in_open_invalid", "quest open title is missing")
                return self._remember(QuestTurnInDecision(
                    QuestTurnInIntent.OPEN_QUEST, "npc_quest_action",
                    _metadata(**{**common, "expected_title": title}, action="open"),
                    "quest_turn_in_open_failed", pending.objective.quest_id, snapshot_id,
                ))
            if len(opens) > 1 or len(answers) > 1 or len(completions) > 1 or (answers and completions):
                raise QuestTurnInError("turn_in_open_missing_or_ambiguous", "quest open action is missing or ambiguous")
            if len(answers) + len(completions) != 1:
                raise QuestTurnInError("turn_in_open_missing_or_ambiguous", "quest open action is missing or ambiguous")
            # The NPC can open a dialogue directly after the area click.  A
            # single snapshot-bound continuation is stronger evidence than a
            # missing intermediate "Далее" card, so continue without ever
            # mutating persisted pending state until the action is accepted.
            effective_pending = replace(pending, quest_opened=True)
            inferred_already_open = True
        if len(answers) > 1 or len(completions) > 1 or (answers and completions):
            raise QuestTurnInError("turn_in_action_ambiguous", "dialogue exposes ambiguous turn-in actions")
        if effective_pending.dialog_steps >= max_steps:
            raise QuestTurnInError("turn_in_step_limit_exceeded", "turn-in dialogue step limit exceeded")
        if answers:
            ref, text = _text(answers[0].get("ref"), 80), _text(answers[0].get("text"), 1200)
            if ref == effective_pending.last_answer_ref:
                # The browser can expose the just-submitted reply during the
                # next controller pass before it renders the final `done`.
                # This is a causal-settle wait, not an invalid reply.
                raise QuestTurnInError(
                    "turn_in_action_not_advanced",
                    "dialogue still exposes the previously submitted answer",
                )
            if not ref.isdecimal() or int(ref) <= 0 or not text:
                raise QuestTurnInError("turn_in_answer_invalid", "dialogue answer identity is invalid or stale")
            return self._remember(QuestTurnInDecision(
                QuestTurnInIntent.ANSWER_DIALOG, "npc_quest_action",
                _metadata(**common, action="answer", expected_ref=ref, expected_text=text,
                          inferred_already_open=inferred_already_open),
                "quest_turn_in_answer_failed", pending.objective.quest_id, snapshot_id,
            ))
        if completions:
            point_id, text = _text(completions[0].get("pointId"), 80), _text(completions[0].get("text"), 1200)
            if not point_id.isdecimal() or int(point_id) <= 0 or not text:
                raise QuestTurnInError("turn_in_completion_invalid", "completion identity is invalid")
            return self._remember(QuestTurnInDecision(
                QuestTurnInIntent.COMPLETE_QUEST, "npc_quest_action",
                _metadata(**common, action="done", expected_point_id=point_id, expected_text=text,
                          inferred_already_open=inferred_already_open),
                "quest_turn_in_completion_failed", pending.objective.quest_id, snapshot_id,
            ))
        raise QuestTurnInError("turn_in_action_missing", "turn-in progression action is missing")

    def acknowledge(
        self,
        decision: QuestTurnInDecision,
        *,
        active_catalog_revision: int | None = None,
    ) -> PendingQuestTurnIn:
        if decision is not self._pending_decision or self.pending is None:
            raise RuntimeError("quest turn-in decision is stale")
        pending = self.pending
        if decision.intent is QuestTurnInIntent.OPEN_NPC:
            updated = replace(pending, phase=QuestTurnInPhase.NPC_DIALOG)
        elif decision.intent is QuestTurnInIntent.OPEN_QUEST:
            updated = replace(pending, quest_opened=True)
        elif decision.intent is QuestTurnInIntent.ANSWER_DIALOG:
            updated = replace(pending, dialog_steps=pending.dialog_steps + 1,
                              last_answer_ref=str(decision.action_metadata["expected_ref"]),
                              quest_opened=True if decision.action_metadata.get("inferred_already_open") is True else pending.quest_opened)
        elif decision.intent is QuestTurnInIntent.COMPLETE_QUEST:
            if (
                not isinstance(active_catalog_revision, int)
                or isinstance(active_catalog_revision, bool)
                or active_catalog_revision < 0
            ):
                raise ValueError("completion acknowledgement requires a non-negative active catalogue revision")
            updated = replace(pending, phase=QuestTurnInPhase.VERIFY_ACTIVE,
                              dialog_steps=pending.dialog_steps + 1,
                              completion_after_revision=active_catalog_revision,
                              quest_opened=True if decision.action_metadata.get("inferred_already_open") is True else pending.quest_opened)
        else:  # pragma: no cover
            raise RuntimeError("unsupported quest turn-in decision")
        self.pending, self._pending_decision = updated, None
        return updated

    def verify_terminal(
        self,
        entries: Sequence[ActiveQuestEntry],
        *,
        catalog_complete: bool,
        catalog_revision: int,
    ) -> str:
        pending = self._validate_fresh_active_catalog(
            catalog_complete=catalog_complete,
            catalog_revision=catalog_revision,
        )
        if any(entry.id == pending.objective.quest_id for entry in entries):
            raise QuestTurnInError("turn_in_quest_still_active", "completed quest remains active")
        quest_id = pending.objective.quest_id
        self.pending, self._pending_decision = None, None
        return quest_id

    def verify_continuation(
        self,
        entries: Sequence[ActiveQuestEntry],
        *,
        catalog_complete: bool,
        catalog_revision: int,
    ) -> ActiveQuestEntry:
        """Accept only a fresh, uniquely advanced step of the same quest chain."""

        pending = self._validate_fresh_active_catalog(
            catalog_complete=catalog_complete,
            catalog_revision=catalog_revision,
        )
        matches = [entry for entry in entries if entry.id == pending.objective.quest_id]
        if len(matches) != 1 or matches[0].title != pending.objective.quest_title:
            raise QuestTurnInError(
                "turn_in_continuation_identity_invalid",
                "continued quest identity is missing, duplicated, or changed",
            )
        fingerprint, reason = quest_step_fingerprint(matches[0])
        if fingerprint is None:
            raise QuestTurnInError(
                "turn_in_continuation_fingerprint_invalid",
                reason or "continued quest fingerprint is invalid",
            )
        if fingerprint == pending.objective.completed_fingerprint:
            raise QuestTurnInError(
                "turn_in_step_not_advanced",
                "completed quest remains on the same step after turn-in",
            )
        return matches[0]

    def confirm_continuation(self) -> None:
        self._require(QuestTurnInPhase.VERIFY_ACTIVE)
        self.pending, self._pending_decision = None, None

    def _validate_fresh_active_catalog(
        self,
        *,
        catalog_complete: bool,
        catalog_revision: int,
    ) -> PendingQuestTurnIn:
        pending = self._require(QuestTurnInPhase.VERIFY_ACTIVE)
        if catalog_complete is not True:
            raise QuestTurnInError(
                "turn_in_active_catalog_incomplete",
                "turn-in verification requires a complete catalogue",
            )
        if (
            not isinstance(catalog_revision, int)
            or isinstance(catalog_revision, bool)
            or pending.completion_after_revision is None
            or catalog_revision <= pending.completion_after_revision
        ):
            raise QuestTurnInError(
                "turn_in_active_catalog_stale",
                "turn-in verification requires a newer active catalogue revision",
            )
        return pending

    def _remember(self, decision: QuestTurnInDecision) -> QuestTurnInDecision:
        self._pending_decision = decision
        return decision

    def _require(self, phase: QuestTurnInPhase) -> PendingQuestTurnIn:
        if self.pending is None or self.pending.phase is not phase:
            raise RuntimeError(f"quest turn-in phase must be {phase.value}")
        return self.pending


def _turn_in_objective(
    entry: ActiveQuestEntry,
    *,
    lease: QuestChainLease,
    quest_ref: QuestRef,
    terminal_collection_confirmed: bool = False,
) -> QuestTurnInObjective:
    if entry.id != lease.quest_id or entry.title != lease.quest_title or entry.id != quest_ref.id or entry.title != quest_ref.title:
        raise QuestTurnInError("turn_in_identity_mismatch", "turn-in identities do not agree")
    progress = entry.data.get("progress")
    progress_complete = isinstance(progress, Mapping) and progress.get("complete") is True
    route_plan = classify_objective(entry)
    explicit_turn_in = (
        route_plan.status is ObjectiveRouteStatus.READY
        and route_plan.kind is ObjectiveRouteKind.TURN_IN
    )
    if not terminal_collection_confirmed and not progress_complete and not explicit_turn_in:
        raise QuestTurnInError("turn_in_objective_not_complete", "quest objective is not confirmed complete")
    fingerprint, reason = quest_step_fingerprint(entry)
    if fingerprint is None or fingerprint != lease.current_fingerprint:
        raise QuestTurnInError("turn_in_fingerprint_mismatch", reason or "completed fingerprint does not match lease")
    if len(quest_ref.giver_names) != 1 or not quest_ref.location:
        raise QuestTurnInError("turn_in_route_missing_or_ambiguous", "turn-in requires one saved giver and location")
    return QuestTurnInObjective(entry.id, entry.title, quest_ref.giver_names[0], quest_ref.location, fingerprint)


def _actions(raw: object, *, quest_id: str, action: str, npc_id: str | None = None) -> tuple[dict[str, object], ...]:
    if not isinstance(raw, list):
        raise QuestTurnInError("turn_in_action_collection_invalid", "dialogue action collection is invalid")
    result = []
    for item in raw:
        if not isinstance(item, dict):
            raise QuestTurnInError("turn_in_action_collection_invalid", "dialogue action item is invalid")
        item_quest_id = str(item.get("questId") or "")
        if not item_quest_id.isdecimal() or int(item_quest_id) <= 0:
            raise QuestTurnInError("turn_in_action_collection_invalid", "dialogue action quest identity is invalid")
        if item_quest_id != quest_id:
            continue
        if item.get("action") != action:
            raise QuestTurnInError("turn_in_action_collection_invalid", "relevant dialogue action type is invalid")
        if item.get("visible") is not True or item.get("disabled") is not False:
            raise QuestTurnInError("turn_in_action_collection_invalid", "relevant dialogue action state is invalid")
        if npc_id is not None:
            item_npc_id = str(item.get("npcId") or "")
            if not item_npc_id.isdecimal() or int(item_npc_id) < 0:
                raise QuestTurnInError("turn_in_action_collection_invalid", "relevant dialogue NPC identity is invalid")
            if item_npc_id != npc_id:
                raise QuestTurnInError("turn_in_action_collection_invalid", "relevant dialogue NPC identity mismatches")
        result.append(item)
    return tuple(result)


def _metadata(**values: object) -> Mapping[str, object]:
    return MappingProxyType(values)


def _text(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else ""


def _normalize(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").split())
