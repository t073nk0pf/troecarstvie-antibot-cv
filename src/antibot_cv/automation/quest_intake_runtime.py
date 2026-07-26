"""Action-free state, validation, and decisions for quest acceptance."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum
from types import MappingProxyType

from src.antibot_cv.automation.quest_director_policy import QuestRef
from src.antibot_cv.automation.quest_dialogue_choice_policy import (
    select_exploratory_dialogue_action,
    select_progress_dialogue_action,
)
from src.antibot_cv.automation.quest_dialogue_recipe import DialogueRecipeCursor
from src.antibot_cv.automation.quest_giver import resolve_unique_giver
from src.antibot_cv.automation.quest_catalog_navigation import snapshot_epoch_seconds
from src.antibot_cv.automation.quest_npc_open_navigation import PendingNpcOpen
from src.antibot_cv.automation.quest_npc_action_journal import dialog_semantic_fingerprint


class QuestAcceptPhase(str, Enum):
    ROUTE = "ROUTE"
    NPC_LOOKUP = "NPC_LOOKUP"
    NPC_DIALOG = "NPC_DIALOG"
    VERIFY_STARTED = "VERIFY_STARTED"


class QuestIntakeIntent(str, Enum):
    OPEN_NPC = "OPEN_NPC"
    OPEN_QUEST = "OPEN_QUEST"
    ANSWER_DIALOG = "ANSWER_DIALOG"
    ACCEPT_QUEST = "ACCEPT_QUEST"
    COMPLETE_QUEST = "COMPLETE_QUEST"


class QuestIntakeDecisionError(ValueError):
    """A fail-closed intake decision failure with a stable controller reason."""

    def __init__(self, unsafe_reason: str, message: str) -> None:
        super().__init__(message)
        self.unsafe_reason = unsafe_reason


@dataclass(frozen=True)
class QuestIntakeDecision:
    """An action-free, snapshot-bound request for the orchestrator to execute."""

    intent: QuestIntakeIntent
    action_type: str
    action_metadata: Mapping[str, object]
    action_failure_reason: str
    quest_id: str
    snapshot_id: str
    observed_headers: tuple[str, ...] = ()
    observed_actions: tuple[str, ...] = ()


@dataclass(frozen=True)
class PendingQuestAccept:
    quest_id: str
    title: str
    location: str
    giver_name: str
    phase: QuestAcceptPhase
    location_id: str | None = None
    npc_id: str | None = None
    route_ref: str | None = None
    npc_name: str | None = None
    area_snapshot_id: str | None = None
    area_generated_at: float | None = None
    quest_opened: bool = False
    dialog_steps: int = 0
    accept_submitted: bool = False


class QuestIntakeRuntime:
    """Own quest-intake rules while an orchestrator only performs actions."""

    def __init__(self) -> None:
        self.pending: PendingQuestAccept | None = None
        self._pending_ref: QuestRef | None = None
        self._pending_decision: QuestIntakeDecision | None = None
        self._dialogue_recipe = DialogueRecipeCursor()

    def begin(self, quest: QuestRef, *, already_at_location: bool) -> PendingQuestAccept:
        if self.pending is not None:
            raise RuntimeError("quest acceptance is already in progress")
        if not quest.id.isdecimal() or int(quest.id) <= 0:
            raise ValueError("quest id must be a positive integer")
        if not quest.title.strip() or not quest.location or not quest.location.strip():
            raise ValueError("quest acceptance requires title and location")
        if len(quest.giver_names) != 1 or not quest.giver_names[0].strip():
            raise ValueError("quest acceptance requires exactly one giver")
        self._pending_decision = None
        self._dialogue_recipe.reset()
        self._pending_ref = quest
        self.pending = PendingQuestAccept(
            quest_id=quest.id,
            title=quest.title.strip(),
            location=quest.location.strip(),
            giver_name=quest.giver_names[0].strip(),
            phase=QuestAcceptPhase.NPC_LOOKUP if already_at_location else QuestAcceptPhase.ROUTE,
        )
        return self.pending

    def mark_route_arrived(self) -> PendingQuestAccept:
        pending = self._require(QuestAcceptPhase.ROUTE)
        self._pending_decision = None
        self.pending = replace(pending, phase=QuestAcceptPhase.NPC_LOOKUP)
        return self.pending

    def decide_area_npc(self, snapshot: object) -> QuestIntakeDecision:
        """Validate one area snapshot and bind an exact NPC-open intent to it."""

        self._pending_decision = None
        try:
            observed = self.observe_area_npcs(snapshot)
        except (RuntimeError, ValueError) as exc:
            if isinstance(exc, QuestIntakeDecisionError):
                raise
            raise QuestIntakeDecisionError(
                f"quest_accept_npc_snapshot:{exc}",
                str(exc),
            ) from exc
        if not observed.location_id or not observed.npc_id or not observed.route_ref or not observed.npc_name or not observed.area_snapshot_id or observed.area_generated_at is None:
            raise QuestIntakeDecisionError(
                "quest_accept_npc_snapshot:quest giver NPC was not observed",
                "quest giver NPC was not observed",
            )
        decision = QuestIntakeDecision(
            intent=QuestIntakeIntent.OPEN_NPC,
            action_type="open_exact_npc",
            action_metadata=_immutable_metadata(
                expected_snapshot_id=observed.area_snapshot_id,
                expected_location_id=observed.location_id,
                npc_id=observed.npc_id,
                expected_route_ref=observed.route_ref,
                expected_name=observed.npc_name,
                expected_dialog_name=observed.giver_name,
                quest_id=observed.quest_id,
                quest_title=observed.title,
            ),
            action_failure_reason="quest_accept_npc_open_failed",
            quest_id=observed.quest_id,
            snapshot_id=observed.area_snapshot_id,
        )
        self._pending_decision = decision
        return decision

    def observe_area_npcs(self, snapshot: object) -> PendingQuestAccept:
        """Validate and retain exact NPC identity without performing an action."""

        pending = self._require(QuestAcceptPhase.NPC_LOOKUP)
        if not isinstance(snapshot, dict) or snapshot.get("ok") is not True:
            raise ValueError("area NPC snapshot is unavailable")
        if snapshot.get("truncated") is not False:
            raise ValueError("area NPC snapshot is truncated or unconfirmed")
        snapshot_id = _bounded_text(snapshot.get("snapshotId"), max_length=120)
        if not snapshot_id.startswith("area-npcs-"):
            raise ValueError("area NPC snapshot identity is missing")
        generated_at = snapshot_epoch_seconds(snapshot.get("generatedAt"))
        if generated_at is None:
            raise ValueError("area NPC snapshot timestamp is missing")
        location = snapshot.get("location")
        location_id = (
            _bounded_text(location.get("id"), max_length=80)
            if isinstance(location, dict)
            else ""
        )
        if not location_id:
            raise ValueError("area NPC location identity is missing")
        raw_items = snapshot.get("items")
        if not isinstance(raw_items, list):
            raise ValueError("area NPC items are missing")
        match = resolve_unique_giver(pending.giver_name, raw_items)
        npc_id = _bounded_text(match.get("dataId"), max_length=80)
        if not npc_id.isdecimal() or int(npc_id) < 0:
            raise ValueError("quest giver NPC identity is invalid")
        npc_name = _bounded_text(match.get("name"), max_length=180)
        route_ref = _bounded_text(match.get("routeRef"), max_length=40)
        if not route_ref.isascii() or not route_ref.isdecimal() or int(route_ref) <= 0:
            raise ValueError("quest giver NPC route identity is invalid")
        if not npc_name or len(pending.giver_name) > 180:
            raise ValueError("quest giver NPC name is invalid")
        self.pending = replace(
            pending,
            location_id=location_id,
            npc_id=npc_id,
            route_ref=route_ref,
            npc_name=npc_name,
            area_snapshot_id=snapshot_id,
            area_generated_at=generated_at,
        )
        return self.pending

    def decide_dialog(self, snapshot: object, *, max_steps: int = 64) -> QuestIntakeDecision:
        """Choose the sole safe open, answer, or accept action from a dialog snapshot."""

        self._pending_decision = None
        pending = self._require(QuestAcceptPhase.NPC_DIALOG)
        if (
            not isinstance(snapshot, dict)
            or snapshot.get("ok") is not True
            or snapshot.get("truncated") is not False
        ):
            raise QuestIntakeDecisionError(
                "quest_accept_dialog_snapshot_invalid",
                "NPC dialog snapshot is unavailable, truncated, or unconfirmed",
            )
        if snapshot.get("identityMatches") is not True:
            raise QuestIntakeDecisionError(
                "quest_accept_dialog_identity_mismatch",
                "NPC dialog identity does not match the pending giver",
            )
        snapshot_id = _bounded_text(snapshot.get("snapshotId"), max_length=120)
        if not snapshot_id.startswith("npc-dialog-"):
            raise QuestIntakeDecisionError(
                "quest_accept_dialog_snapshot_invalid",
                "NPC dialog snapshot identity is missing",
            )
        if not pending.npc_id or not pending.npc_id.isdecimal() or int(pending.npc_id) < 0:
            raise QuestIntakeDecisionError(
                "quest_accept_dialog_identity_mismatch",
                "pending NPC identity is invalid",
            )

        headers = _observed_texts(snapshot.get("headers"))
        observed_actions = _observed_action_texts(snapshot.get("actions"))
        common = {
            "expected_snapshot_id": snapshot_id,
            "expected_generated_at": snapshot.get("generatedAt"),
            "source_semantic_fingerprint": dialog_semantic_fingerprint(snapshot),
            "expected_href": snapshot.get("href"),
            "giver_name": pending.giver_name,
            "npc_id": pending.npc_id,
            "quest_id": pending.quest_id,
            "expected_title": pending.title,
            "quest_opened": pending.quest_opened,
            "dialog_steps": pending.dialog_steps,
        }
        if not pending.quest_opened:
            candidates = _matching_actions(
                snapshot.get("questActions"),
                quest_id=pending.quest_id,
                action="open",
                title=pending.title,
            )
            if len(candidates) != 1:
                raise QuestIntakeDecisionError(
                    "quest_accept_open_action_missing_or_ambiguous",
                    "quest open action is missing or ambiguous",
                )
            return self._remember_decision(
                QuestIntakeDecision(
                    intent=QuestIntakeIntent.OPEN_QUEST,
                    action_type="npc_quest_action",
                    action_metadata=_immutable_metadata(**common, action="open"),
                    action_failure_reason="quest_accept_open_action_failed",
                    quest_id=pending.quest_id,
                    snapshot_id=snapshot_id,
                    observed_headers=headers,
                    observed_actions=observed_actions,
                )
            )

        dialog_actions = _matching_actions(
            snapshot.get("dialogActions"),
            quest_id=pending.quest_id,
            npc_id=pending.npc_id,
            action="answer",
        )
        selected_dialog_action = self._dialogue_recipe.select(
            quest_id=pending.quest_id, title=pending.title, actions=dialog_actions,
        )
        recipe_role = self._dialogue_recipe.pending_role
        if selected_dialog_action is None:
            selected_dialog_action = select_progress_dialogue_action(dialog_actions)
        if selected_dialog_action is None and len(dialog_actions) == 1:
            # A sole exact control is not a branch choice.  Advancing it is the
            # only way to continue observing the quest, even when its prose
            # resembles a refusal out of context.
            selected_dialog_action = dialog_actions[0]
        elif selected_dialog_action is None and len(dialog_actions) > 1:
            # Prefer the semantic winner above.  If several non-refusal story
            # or puzzle continuations remain, bounded exploration advances the
            # first exact transport-bound option instead of abandoning the
            # whole quest.  Durable action reconciliation still prevents an
            # unobserved mutation from being reissued.
            selected_dialog_action = select_exploratory_dialogue_action(dialog_actions)
        if selected_dialog_action is not None:
            if pending.dialog_steps >= max_steps and recipe_role is None:
                raise QuestIntakeDecisionError(
                    "quest_accept_dialog_step_limit_exceeded",
                    "quest dialogue step limit exceeded",
                )
            candidate = selected_dialog_action
            expected_ref = _bounded_text(candidate.get("ref"), max_length=80)
            expected_text = _bounded_text(candidate.get("text"), max_length=1200)
            if not expected_ref.isdecimal() or int(expected_ref) <= 0 or not expected_text:
                raise QuestIntakeDecisionError(
                    "quest_accept_dialog_action_missing_or_ambiguous",
                    "quest dialog answer identity is invalid",
                )
            return self._remember_decision(
                QuestIntakeDecision(
                    intent=QuestIntakeIntent.ANSWER_DIALOG,
                    action_type="npc_quest_action",
                    action_metadata=_immutable_metadata(
                        **common,
                        action="answer",
                        expected_ref=expected_ref,
                        expected_text=expected_text,
                        recipe_role=recipe_role,
                    ),
                    action_failure_reason="quest_accept_dialog_action_failed",
                    quest_id=pending.quest_id,
                    snapshot_id=snapshot_id,
                    observed_headers=headers,
                    observed_actions=observed_actions,
                )
            )
        if dialog_actions:
            raise QuestIntakeDecisionError(
                "quest_accept_dialog_action_ambiguous",
                "quest dialog answer action is ambiguous",
            )

        title_present = sum(
            1 for header in headers if _normalized_title(header) == _normalized_title(pending.title)
        ) == 1
        objective_present = any("\u0432\u0430\u0448\u0430 \u0446\u0435\u043b\u044c:" in text.casefold() for text in observed_actions)
        done_actions = _matching_actions(
            snapshot.get("doneActions"),
            quest_id=pending.quest_id,
            npc_id=pending.npc_id,
            action="done",
        )
        exact_terminal_done = len(done_actions) == 1
        if not title_present or (not objective_present and not exact_terminal_done):
            raise QuestIntakeDecisionError(
                "quest_accept_terminal_evidence_missing",
                "quest terminal detail evidence is missing",
            )
        accept_actions = _matching_actions(
            snapshot.get("acceptActions"),
            quest_id=pending.quest_id,
            npc_id=pending.npc_id,
            action="accept",
        )
        if len(accept_actions) != 1:
            raise QuestIntakeDecisionError(
                "quest_accept_submit_action_missing_or_ambiguous",
                "quest accept action is missing or ambiguous",
            )
        expected_text = _bounded_text(accept_actions[0].get("text"), max_length=1200)
        if not expected_text:
            raise QuestIntakeDecisionError(
                "quest_accept_submit_action_missing_or_ambiguous",
                "quest accept action text is missing",
            )
        terminal_action = done_actions[0] if exact_terminal_done else accept_actions[0]
        terminal_intent = (
            QuestIntakeIntent.COMPLETE_QUEST
            if exact_terminal_done else QuestIntakeIntent.ACCEPT_QUEST
        )
        terminal_kind = "done" if exact_terminal_done else "accept"
        return self._remember_decision(
            QuestIntakeDecision(
                intent=terminal_intent,
                action_type="npc_quest_action",
                action_metadata=_immutable_metadata(
                    **common,
                    action=terminal_kind,
                    expected_point_id=terminal_action.get("pointId"),
                    expected_text=expected_text,
                ),
                action_failure_reason="quest_accept_submit_action_failed",
                quest_id=pending.quest_id,
                snapshot_id=snapshot_id,
                observed_headers=headers,
                observed_actions=observed_actions,
            )
        )

    def acknowledge(self, decision: QuestIntakeDecision) -> PendingQuestAccept:
        """Advance state only after the orchestrator reports action success."""

        if decision is not self._pending_decision:
            raise RuntimeError("quest intake decision is stale or was not issued by this runtime")
        pending = self.pending
        if pending is None or decision.quest_id != pending.quest_id:
            raise RuntimeError("quest intake decision does not match pending quest")
        if decision.intent is QuestIntakeIntent.OPEN_NPC:
            updated = self.mark_npc_opened()
        elif decision.intent is QuestIntakeIntent.OPEN_QUEST:
            updated = self.mark_quest_opened()
        elif decision.intent is QuestIntakeIntent.ANSWER_DIALOG:
            updated = self.mark_dialog_answer_settled()
        elif decision.intent in {
            QuestIntakeIntent.ACCEPT_QUEST, QuestIntakeIntent.COMPLETE_QUEST,
        }:
            updated = self.mark_accept_submitted()
        else:  # pragma: no cover - exhaustive guard for future enum additions.
            raise RuntimeError("unsupported quest intake decision")
        self._pending_decision = None
        return updated

    def mark_dialog_answer_settled(self) -> PendingQuestAccept:
        """Advance an acknowledged answer and its optional recipe cursor."""

        updated = self.mark_dialog_answered()
        self._dialogue_recipe.acknowledge()
        return updated

    def mark_npc_opened(self) -> PendingQuestAccept:
        pending = self._require(QuestAcceptPhase.NPC_LOOKUP)
        if not pending.location_id or not pending.npc_id or not pending.npc_name or not pending.area_snapshot_id or pending.area_generated_at is None:
            raise RuntimeError("quest giver NPC was not observed")
        self._pending_decision = None
        self.pending = replace(pending, phase=QuestAcceptPhase.NPC_DIALOG)
        return self.pending

    def restore_npc_open(
        self, staged: PendingNpcOpen, *, confirmed: bool,
    ) -> PendingQuestAccept:
        """Restore the bounded pre- or post-settlement NPC-open phase."""

        if self.pending is not None or self._pending_ref is not None:
            raise RuntimeError("quest acceptance is already in progress")
        quest = QuestRef(
            id=staged.quest_id,
            title=staged.quest_title,
            accept_ref=staged.quest_accept_ref,
            location=staged.location_name,
            giver_names=(staged.giver_name,),
            catalog_page=staged.quest_catalog_page,
        )
        self._pending_ref = quest
        self._pending_decision = None
        self.pending = PendingQuestAccept(
            quest_id=staged.quest_id,
            title=staged.quest_title,
            location=staged.location_name,
            giver_name=staged.giver_name,
            phase=QuestAcceptPhase.NPC_DIALOG if confirmed else QuestAcceptPhase.NPC_LOOKUP,
            location_id=staged.location_id,
            npc_id=staged.npc_id,
            npc_name=staged.npc_name,
            area_snapshot_id=staged.area_snapshot_id,
            area_generated_at=staged.area_generated_at,
            quest_opened=staged.quest_opened,
            dialog_steps=staged.dialog_steps,
        )
        return self.pending

    def mark_quest_opened(self) -> PendingQuestAccept:
        pending = self._require(QuestAcceptPhase.NPC_DIALOG)
        if pending.quest_opened:
            raise RuntimeError("quest detail is already open")
        self._pending_decision = None
        self.pending = replace(pending, quest_opened=True)
        return self.pending

    def mark_dialog_answered(self, *, max_steps: int = 64) -> PendingQuestAccept:
        pending = self._require(QuestAcceptPhase.NPC_DIALOG)
        if not pending.quest_opened:
            raise RuntimeError("quest detail is not open")
        if pending.dialog_steps >= max_steps:
            raise RuntimeError("quest dialogue step limit exceeded")
        self._pending_decision = None
        self.pending = replace(pending, dialog_steps=pending.dialog_steps + 1)
        return self.pending

    def mark_accept_submitted(self) -> PendingQuestAccept:
        pending = self._require(QuestAcceptPhase.NPC_DIALOG)
        if not pending.quest_opened:
            raise RuntimeError("quest acceptance detail was not opened")
        self._pending_decision = None
        self.pending = replace(
            pending,
            phase=QuestAcceptPhase.VERIFY_STARTED,
            accept_submitted=True,
        )
        return self.pending

    def finish(self, quest_id: str) -> None:
        pending = self._require(QuestAcceptPhase.VERIFY_STARTED)
        if not pending.accept_submitted or pending.quest_id != quest_id:
            raise RuntimeError("quest acceptance verification does not match pending quest")
        self._pending_decision = None
        self._pending_ref = None
        self.pending = None

    def recover_confirmed_active(self, expected_ref: QuestRef) -> None:
        """Clear a restored local intake after durable active-catalog confirmation."""

        if not self.matches_pending(expected_ref):
            raise RuntimeError("confirmed active quest does not match pending intake")
        self._pending_decision = None
        self._pending_ref = None
        self.pending = None

    def abandon(self, expected_ref: QuestRef) -> None:
        """Clear one exact unconfirmed intake locally without performing an action."""

        if not self.matches_pending(expected_ref):
            raise RuntimeError("abandoned quest does not match pending intake")
        self._pending_decision = None
        self._pending_ref = None
        self.pending = None

    def matches_pending(self, expected_ref: QuestRef) -> bool:
        """Prevalidate an exact local intake without changing its state."""

        pending = self.pending
        return bool(
            pending is not None
            and self._pending_ref == expected_ref
            and pending.quest_id == expected_ref.id
            and pending.title == expected_ref.title
            and pending.location == expected_ref.location
            and (pending.giver_name,) == expected_ref.giver_names
            and pending.phase not in {QuestAcceptPhase.VERIFY_STARTED}
            and not pending.accept_submitted
        )

    def _remember_decision(self, decision: QuestIntakeDecision) -> QuestIntakeDecision:
        self._pending_decision = decision
        return decision

    def _require(self, phase: QuestAcceptPhase) -> PendingQuestAccept:
        if self.pending is None or self.pending.phase is not phase:
            raise RuntimeError(f"quest acceptance phase must be {phase.value}")
        return self.pending


def _normalized_title(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def _bounded_text(value: object, *, max_length: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= max_length else ""


def _observed_texts(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(str(value or "") for value in raw)


def _observed_action_texts(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(
        str(action.get("containerText") or "")
        for action in raw
        if isinstance(action, dict)
    )


def _matching_actions(
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
        if title is not None and _normalized_title(candidate.get("title")) != _normalized_title(title):
            continue
        matches.append(candidate)
    return tuple(matches)


def _immutable_metadata(**values: object) -> Mapping[str, object]:
    return MappingProxyType(dict(values))
