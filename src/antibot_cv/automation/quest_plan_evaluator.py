"""Pure, deterministic evaluation of semantic quest plans.

The evaluator does not execute actions and does not mutate a plan or evidence.
It deliberately fails closed when supplied evidence cannot be bound uniquely to
the exact semantic requirement that it purports to satisfy.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from typing import Any, Iterable, Mapping

from .quest_evidence import EvidenceEnvelope, EvidenceSourceKind, FactKind
from .quest_plan_model import (
    Acquire,
    AllOf,
    AnyOf,
    AreaObject,
    CombatDrop,
    Gathering,
    InteractNpc,
    Kill,
    Purchase,
    QuestLeaf,
    QuestNode,
    QuestPlan,
    Sequence,
    TurnIn,
    Visit,
)


EVALUATOR_CAPABILITY = "quest_plan_evaluator_v1"


class EvaluationStatus(str, Enum):
    SATISFIED = "satisfied"
    ACTIONABLE = "actionable"
    BLOCKED = "blocked"
    UNSAFE = "unsafe"
    UNKNOWN = "unknown"


class DispatchState(str, Enum):
    SHADOW_ONLY = "shadow_only"


@dataclass(frozen=True, slots=True)
class EvaluationContext:
    client_id: str
    profile_id: str
    tab_id: str
    causal_baseline: str
    min_revision: int
    now: float | None = None

    def __post_init__(self) -> None:
        for name in ("client_id", "profile_id", "tab_id", "causal_baseline"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
            object.__setattr__(self, name, value.strip())
        if isinstance(self.min_revision, bool) or not isinstance(self.min_revision, int) or self.min_revision < 0:
            raise ValueError("min_revision must be a non-negative integer")
        if self.now is not None:
            value = float(self.now)
            if value < 0 or value != value or value in (float("inf"), float("-inf")):
                raise ValueError("now must be a finite non-negative number")
            object.__setattr__(self, "now", value)


@dataclass(frozen=True, slots=True)
class RequirementEvaluation:
    requirement_id: str
    step_fingerprint: str
    status: EvaluationStatus
    reason: str
    evidence_fingerprints: tuple[str, ...] = ()
    evidence_revision: int | None = None


@dataclass(frozen=True, slots=True)
class PlanEvaluation:
    status: EvaluationStatus
    next_atom: QuestLeaf | None
    requirements: tuple[RequirementEvaluation, ...]
    consumed_evidence_fingerprints: tuple[str, ...]
    reason: str = ""
    reissue_allowed: bool = True

    @property
    def root_status(self) -> EvaluationStatus:
        return self.status


@dataclass(frozen=True, slots=True)
class EvaluationCursor:
    """Serializable shadow cursor; it never changes evaluator inputs."""

    plan_fingerprint: str
    next_requirement_id: str | None
    evidence_fingerprints: tuple[str, ...]
    evidence_revision: int
    staged_requirement_id: str | None = None
    dispatch_state: DispatchState = DispatchState.SHADOW_ONLY
    capability_version: str = EVALUATOR_CAPABILITY

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "capability_version": self.capability_version,
            "plan_fingerprint": self.plan_fingerprint,
            "next_requirement_id": self.next_requirement_id,
            "evidence_fingerprints": list(self.evidence_fingerprints),
            "evidence_revision": self.evidence_revision,
            "staged_requirement_id": self.staged_requirement_id,
            "dispatch_state": self.dispatch_state.value,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "EvaluationCursor":
        if not isinstance(raw, Mapping) or raw.get("schema") != 1:
            raise ValueError("unsupported evaluation cursor schema")
        if raw.get("capability_version") != EVALUATOR_CAPABILITY:
            raise ValueError("unsupported evaluation cursor capability")
        plan_fingerprint = raw.get("plan_fingerprint")
        next_requirement_id = raw.get("next_requirement_id")
        fingerprints = raw.get("evidence_fingerprints")
        revision = raw.get("evidence_revision")
        staged_requirement_id = raw.get("staged_requirement_id")
        if not isinstance(plan_fingerprint, str) or not plan_fingerprint.strip():
            raise ValueError("invalid cursor plan fingerprint")
        if next_requirement_id is not None and (
            not isinstance(next_requirement_id, str) or not next_requirement_id.strip()
        ):
            raise ValueError("invalid cursor next requirement")
        if not isinstance(fingerprints, list) or any(
            not isinstance(item, str) or not item.strip() for item in fingerprints
        ) or len(fingerprints) != len(set(fingerprints)):
            raise ValueError("invalid cursor evidence fingerprints")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise ValueError("invalid cursor evidence revision")
        if staged_requirement_id is not None and (
            not isinstance(staged_requirement_id, str) or not staged_requirement_id.strip()
        ):
            raise ValueError("invalid cursor staged requirement")
        try:
            dispatch_state = DispatchState(raw.get("dispatch_state"))
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid cursor dispatch state") from exc
        return cls(
            plan_fingerprint, next_requirement_id, tuple(fingerprints), revision,
            staged_requirement_id, dispatch_state,
        )

    @classmethod
    def from_json(cls, value: str) -> "EvaluationCursor":
        raw = json.loads(value)
        if not isinstance(raw, dict):
            raise ValueError("evaluation cursor JSON root must be an object")
        return cls.from_dict(raw)


# Capability names intentionally mirror the semantic operation/source values.
def _capability(leaf: QuestLeaf) -> str:
    if isinstance(leaf, Kill):
        return "kill"
    if isinstance(leaf, Acquire):
        return leaf.source.kind.value
    if isinstance(leaf, Visit):
        return "visit"
    if isinstance(leaf, InteractNpc):
        return "interact_npc"
    return "turn_in"


def _expected(leaf: QuestLeaf) -> tuple[EvidenceSourceKind, FactKind, str, int | bool]:
    if isinstance(leaf, Kill):
        return EvidenceSourceKind.COMBAT, FactKind.COUNT_AT_LEAST, leaf.target, leaf.count
    if isinstance(leaf, Acquire):
        # Acquisition route authorizes an action; authoritative inventory proves
        # the resulting item requirement independently of that route.
        return EvidenceSourceKind.INVENTORY, FactKind.COUNT_AT_LEAST, leaf.item, leaf.count
    if isinstance(leaf, Visit):
        return EvidenceSourceKind.LOCATION, FactKind.VISITED, leaf.location, True
    if isinstance(leaf, InteractNpc):
        return EvidenceSourceKind.NPC_DIALOG, FactKind.INTERACTED, leaf.npc, True
    return EvidenceSourceKind.TURN_IN, FactKind.TURNED_IN, leaf.npc, True


def _leaves(node: QuestNode) -> tuple[QuestLeaf, ...]:
    if isinstance(node, (Sequence, AllOf, AnyOf)):
        return tuple(leaf for child in node.nodes for leaf in _leaves(child))
    return (node,)


def _leaf_evaluation(
    plan: QuestPlan,
    leaf: QuestLeaf,
    evidence: tuple[EvidenceEnvelope, ...],
    capabilities: frozenset[str],
    context: EvaluationContext | None,
    now: float | None,
) -> RequirementEvaluation:
    matching = [item for item in evidence if item.requirement_id == leaf.requirement_id]
    if len(matching) > 1:
        # Competing claims are ambiguous before inspecting their asserted fact.
        return RequirementEvaluation(leaf.requirement_id, leaf.step_id, EvaluationStatus.UNSAFE, "ambiguous_evidence")
    if isinstance(leaf, InteractNpc) and leaf.opaque:
        if matching:
            return RequirementEvaluation(
                leaf.requirement_id, leaf.step_id, EvaluationStatus.UNSAFE,
                "opaque_requirement_evidence_forbidden",
            )
        return RequirementEvaluation(
            leaf.requirement_id, leaf.step_id, EvaluationStatus.UNKNOWN,
            "opaque_requirement_unknown",
        )
    exact: list[EvidenceEnvelope] = []
    expected_source, expected_fact, subject, required = _expected(leaf)
    for item in matching:
        if (
            item.quest_id != plan.quest_id
            or item.quest_title != plan.quest_title
            or item.plan_fingerprint != plan.fingerprint
            or item.step_fingerprint != leaf.step_id
            or item.source_kind is not expected_source
            or item.fact.kind is not expected_fact
            or item.fact.subject != subject
            or item.fact.required != required
            or item.validation_errors(now=now)
            or context is None
            or item.client_id != context.client_id
            or item.profile_id != context.profile_id
            or item.tab_id != context.tab_id
            or item.causal_baseline != context.causal_baseline
            or item.revision < context.min_revision
        ):
            return RequirementEvaluation(leaf.requirement_id, leaf.step_id, EvaluationStatus.UNSAFE, "invalid_evidence")
        exact.append(item)
    if exact:
        fingerprint = exact[0].fingerprint
        return RequirementEvaluation(
            leaf.requirement_id, leaf.step_id, EvaluationStatus.SATISFIED,
            "evidence_satisfied", (fingerprint,), exact[0].revision,
        )
    capability = _capability(leaf)
    if capability not in capabilities:
        return RequirementEvaluation(leaf.requirement_id, leaf.step_id, EvaluationStatus.BLOCKED, "blocked_capability")
    return RequirementEvaluation(leaf.requirement_id, leaf.step_id, EvaluationStatus.ACTIONABLE, "evidence_required")


def evaluate_quest_plan(
    plan: QuestPlan,
    evidence: Iterable[EvidenceEnvelope] = (),
    *,
    capabilities: Iterable[str] = (),
    context: EvaluationContext | None = None,
    now: float | None = None,
    cursor: EvaluationCursor | None = None,
) -> PlanEvaluation:
    """Evaluate *plan* without side effects or branch guessing."""

    if not isinstance(plan, QuestPlan):
        raise TypeError("plan must be a QuestPlan")
    items = tuple(evidence)
    if any(not isinstance(item, EvidenceEnvelope) for item in items):
        raise TypeError("evidence must contain EvidenceEnvelope values")
    if context is not None and not isinstance(context, EvaluationContext):
        raise TypeError("context must be an EvaluationContext")
    if cursor is not None and not isinstance(cursor, EvaluationCursor):
        raise TypeError("cursor must be an EvaluationCursor")
    effective_now = now if now is not None else (None if context is None else context.now)
    capability_set = frozenset(capabilities)
    if any(not isinstance(item, str) or not item.strip() for item in capability_set):
        raise ValueError("capabilities must be non-empty strings")

    leaves = _leaves(plan.graph.root)
    known_ids = {leaf.requirement_id for leaf in leaves}
    foreign = any(
        item.requirement_id not in known_ids
        or item.quest_id != plan.quest_id
        or item.quest_title != plan.quest_title
        or item.plan_fingerprint != plan.fingerprint
        for item in items
    )
    missing_context = bool(items) and context is None
    evaluations = tuple(
        _leaf_evaluation(plan, leaf, items, capability_set, context, effective_now) for leaf in leaves
    )
    if foreign or missing_context:
        return PlanEvaluation(EvaluationStatus.UNSAFE, None, evaluations, (), "foreign_or_unbound_evidence")

    status, next_atom, reason = _evaluate_node(plan.graph.root, evaluations, leaves)
    consumed = tuple(sorted(
        fingerprint
        for evaluation in evaluations
        for fingerprint in evaluation.evidence_fingerprints
    ))
    result = PlanEvaluation(status, next_atom, evaluations, consumed, reason)
    if cursor is None:
        return result
    expected_next = None if next_atom is None else next_atom.requirement_id
    current_revision = max(
        (item.evidence_revision for item in evaluations if item.evidence_revision is not None),
        default=0,
    )
    if (
        cursor.plan_fingerprint != plan.fingerprint
        or not set(cursor.evidence_fingerprints).issubset(consumed)
        or cursor.evidence_revision != current_revision
        or cursor.next_requirement_id != expected_next
        or cursor.staged_requirement_id not in {None, expected_next}
    ):
        return PlanEvaluation(
            EvaluationStatus.UNSAFE, None, evaluations, consumed,
            "cursor_resume_mismatch", False,
        )
    return PlanEvaluation(status, next_atom, evaluations, consumed, "cursor_shadow_resume", False)


def _evaluate_node(
    node: QuestNode,
    evaluations: tuple[RequirementEvaluation, ...],
    leaves: tuple[QuestLeaf, ...],
) -> tuple[EvaluationStatus, QuestLeaf | None, str]:
    by_id = {item.requirement_id: item for item in evaluations}
    leaf_by_id = {leaf.requirement_id: leaf for leaf in leaves}
    if not isinstance(node, (Sequence, AllOf, AnyOf)):
        item = by_id[node.requirement_id]
        return item.status, node if item.status is EvaluationStatus.ACTIONABLE else None, item.reason

    children = [_evaluate_node(child, evaluations, leaves) for child in node.nodes]
    statuses = [item[0] for item in children]
    if EvaluationStatus.UNSAFE in statuses:
        return EvaluationStatus.UNSAFE, None, "unsafe_requirement"
    if isinstance(node, AnyOf):
        if EvaluationStatus.SATISFIED in statuses:
            return EvaluationStatus.SATISFIED, None, "alternative_satisfied"
        # FAIL_CLOSED: multiple semantic alternatives are never guessed.
        return EvaluationStatus.UNKNOWN, None, "fail_closed_alternative"
    if isinstance(node, Sequence):
        prior_max_revision: int | None = None
        first_pending: tuple[EvaluationStatus, QuestLeaf | None, str] | None = None
        for child, (child_status, child_next, child_reason) in zip(node.nodes, children):
            revisions = _node_revisions(child, by_id)
            if revisions and first_pending is not None:
                return EvaluationStatus.UNSAFE, None, "out_of_order_evidence"
            if revisions and prior_max_revision is not None and min(revisions) <= prior_max_revision:
                return EvaluationStatus.UNSAFE, None, "noncausal_sequence_evidence"
            if child_status is EvaluationStatus.SATISFIED:
                if revisions:
                    prior_max_revision = max(revisions)
                continue
            if first_pending is None:
                first_pending = (child_status, child_next, child_reason)
        if first_pending is not None:
            return first_pending
        return EvaluationStatus.SATISFIED, None, "sequence_satisfied"
    if all(status is EvaluationStatus.SATISFIED for status in statuses):
        return EvaluationStatus.SATISFIED, None, "all_requirements_satisfied"
    if EvaluationStatus.BLOCKED in statuses:
        return EvaluationStatus.BLOCKED, None, "blocked_requirement"
    if EvaluationStatus.UNKNOWN in statuses:
        return EvaluationStatus.UNKNOWN, None, "unknown_requirement"
    actionable = next(
        (leaf_by_id[leaf.requirement_id] for leaf in _leaves(node) if by_id[leaf.requirement_id].status is EvaluationStatus.ACTIONABLE),
        None,
    )
    return EvaluationStatus.ACTIONABLE, actionable, "actionable_requirement"


def _node_revisions(
    node: QuestNode, by_id: Mapping[str, RequirementEvaluation]
) -> tuple[int, ...]:
    return tuple(
        evaluation.evidence_revision
        for leaf in _leaves(node)
        if (evaluation := by_id[leaf.requirement_id]).evidence_revision is not None
    )


def cursor_from_evaluation(
    plan: QuestPlan,
    evaluation: PlanEvaluation,
    evidence_revision: int | None = None,
) -> EvaluationCursor:
    if evidence_revision is None:
        evidence_revision = max(
            (
                item.evidence_revision
                for item in evaluation.requirements
                if item.evidence_revision is not None
            ),
            default=0,
        )
    if isinstance(evidence_revision, bool) or not isinstance(evidence_revision, int) or evidence_revision < 0:
        raise ValueError("evidence_revision must be a non-negative integer")
    return EvaluationCursor(
        plan.fingerprint,
        None if evaluation.next_atom is None else evaluation.next_atom.requirement_id,
        evaluation.consumed_evidence_fingerprints,
        evidence_revision,
        None if evaluation.next_atom is None else evaluation.next_atom.requirement_id,
    )


Evaluation = PlanEvaluation
Status = EvaluationStatus

__all__ = [
    "DispatchState", "EVALUATOR_CAPABILITY", "Evaluation", "EvaluationContext", "EvaluationCursor", "EvaluationStatus",
    "PlanEvaluation", "RequirementEvaluation", "Status", "cursor_from_evaluation",
    "evaluate_quest_plan",
]
