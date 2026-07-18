"""Action-free Stage 3 facade for gradual semantic quest-engine rollout.

The facade is deliberately a domain boundary: it compiles and evaluates an
already-observed active quest, but never imports a controller/runtime or emits
an action.  Allowlisted quests fail closed once semantic authority is enabled;
an unsafe semantic result must not fall through to the legacy engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .quest_active_catalog import ActiveQuestEntry
from .quest_compiler import QuestCompileStatus, compile_quest_plan
from .quest_evidence import EvidenceEnvelope
from .quest_plan_evaluator import (
    EvaluationContext,
    EvaluationStatus,
    evaluate_quest_plan,
)
from .quest_plan_model import QuestLeaf
from .quest_shadow_comparator import ShadowComparison, compare_shadow_decision
from .quest_shadow_legacy_adapter import LegacyDecisionEnvelope


SEMANTIC_VERTICAL_SLICE_QUEST_IDS = frozenset({"280", "304"})


class QuestSemanticDisposition(str, Enum):
    """Whether the caller should retain or yield quest-decision authority."""

    NOT_HANDLED = "not_handled"
    SHADOW = "shadow"
    AUTHORITATIVE = "authoritative"


@dataclass(frozen=True, slots=True)
class QuestSemanticRuntimeOutcome:
    disposition: QuestSemanticDisposition
    quest_id: str
    status: EvaluationStatus | None = None
    next_atom: QuestLeaf | None = None
    reason: str = ""
    comparison: ShadowComparison | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.disposition, QuestSemanticDisposition):
            raise TypeError("disposition must be a QuestSemanticDisposition")
        if not isinstance(self.quest_id, str):
            raise TypeError("quest_id must be a string")
        if self.disposition is QuestSemanticDisposition.AUTHORITATIVE:
            if not isinstance(self.status, EvaluationStatus):
                raise ValueError("authoritative outcome requires an evaluation status")
            if self.comparison is not None:
                raise ValueError("authoritative outcome cannot contain a shadow comparison")
        elif self.status is not None or self.next_atom is not None:
            raise ValueError("non-authoritative outcome cannot expose an executable atom")
        if self.disposition is QuestSemanticDisposition.SHADOW:
            if not isinstance(self.comparison, ShadowComparison):
                raise ValueError("shadow outcome requires a comparison record")
        elif self.comparison is not None:
            raise ValueError("comparison is only valid for shadow outcomes")

    @property
    def legacy_fallback_allowed(self) -> bool:
        """True only when semantic decision authority was never acquired."""

        return self.disposition is not QuestSemanticDisposition.AUTHORITATIVE


def evaluate_semantic_quest_runtime(
    quest_engine_mode: str,
    entry: ActiveQuestEntry,
    evidence: tuple[EvidenceEnvelope, ...],
    context: EvaluationContext,
    capabilities: Iterable[str],
    legacy_decision: LegacyDecisionEnvelope,
) -> QuestSemanticRuntimeOutcome:
    """Return a pure rollout decision for one already-normalized observation."""

    if quest_engine_mode not in {"legacy", "shadow", "q280_q304"}:
        raise ValueError("unsupported quest engine mode")
    if not isinstance(entry, ActiveQuestEntry):
        raise TypeError("entry must be an ActiveQuestEntry")
    if not isinstance(evidence, tuple) or any(
        not isinstance(item, EvidenceEnvelope) for item in evidence
    ):
        raise TypeError("evidence must be a tuple of EvidenceEnvelope values")
    if not isinstance(context, EvaluationContext):
        raise TypeError("context must be an EvaluationContext")
    if not isinstance(legacy_decision, LegacyDecisionEnvelope):
        raise TypeError("legacy_decision must be normalized")

    if quest_engine_mode == "legacy":
        return _not_handled(entry.id, "legacy_mode")

    if quest_engine_mode == "shadow":
        comparison = compare_shadow_decision(
            entry,
            legacy_decision,
            evidence=evidence,
            capabilities=capabilities,
            context=context,
        )
        return QuestSemanticRuntimeOutcome(
            QuestSemanticDisposition.SHADOW,
            entry.id,
            reason=comparison.code,
            comparison=comparison,
        )

    if entry.id not in SEMANTIC_VERTICAL_SLICE_QUEST_IDS:
        return _not_handled(entry.id, "quest_not_allowlisted")

    compiled = compile_quest_plan(entry)
    if compiled.status is not QuestCompileStatus.READY or compiled.plan is None:
        status = (
            EvaluationStatus.UNSAFE
            if compiled.status is QuestCompileStatus.UNSAFE
            else EvaluationStatus.UNKNOWN
        )
        diagnostic = compiled.diagnostics[0] if compiled.diagnostics else "compile_failed"
        return QuestSemanticRuntimeOutcome(
            QuestSemanticDisposition.AUTHORITATIVE,
            entry.id,
            status=status,
            reason=f"compile_{compiled.status.value}:{diagnostic}",
        )

    evaluation = evaluate_quest_plan(
        compiled.plan,
        evidence,
        capabilities=capabilities,
        context=context,
    )
    return QuestSemanticRuntimeOutcome(
        QuestSemanticDisposition.AUTHORITATIVE,
        entry.id,
        status=evaluation.status,
        next_atom=evaluation.next_atom,
        reason=evaluation.reason,
    )


def _not_handled(quest_id: str, reason: str) -> QuestSemanticRuntimeOutcome:
    return QuestSemanticRuntimeOutcome(
        QuestSemanticDisposition.NOT_HANDLED,
        quest_id,
        reason=reason,
    )


__all__ = [
    "SEMANTIC_VERTICAL_SLICE_QUEST_IDS",
    "QuestSemanticDisposition",
    "QuestSemanticRuntimeOutcome",
    "evaluate_semantic_quest_runtime",
]
