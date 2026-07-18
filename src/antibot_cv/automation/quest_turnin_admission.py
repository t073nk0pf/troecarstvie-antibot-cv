"""Pure fail-closed admission gate for semantic quest turn-in.

The gate binds a turn-in atom to one complete active-catalogue observation.  It
does not derive references, dispatch intents, or import an execution runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math

from .quest_active_catalog import ActiveQuestEntry
from .quest_chain_state import QuestChainLease
from .quest_director_policy import QuestRef
from .quest_plan_evaluator import EvaluationContext, EvaluationStatus, PlanEvaluation
from .quest_plan_model import QuestPlan, TurnIn, iter_requirements


class TurnInAdmissionStatus(str, Enum):
    ALLOW = "allow"
    BLOCKED = "blocked"
    UNSAFE = "unsafe"


@dataclass(frozen=True, slots=True)
class ActiveCatalogAuthority:
    """Identity and freshness envelope for one complete active catalogue."""

    entries: tuple[ActiveQuestEntry, ...]
    revision: int
    snapshot_id: str
    generated_at: float
    max_age_seconds: float
    client_id: str
    profile_id: str
    tab_id: str
    causal_baseline: str
    plan_fingerprint: str
    lease_fingerprint: str
    causal_ancestors: tuple[str, ...] = ()
    complete: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "entries", tuple(self.entries))
        object.__setattr__(self, "causal_ancestors", tuple(self.causal_ancestors))


@dataclass(frozen=True, slots=True)
class TurnInAdmissionToken:
    quest_id: str
    quest_title: str
    plan_fingerprint: str
    turn_in_requirement_id: str
    lease_fingerprint: str
    catalog_snapshot_id: str
    catalog_revision: int
    client_id: str
    profile_id: str
    tab_id: str
    causal_baseline: str


@dataclass(frozen=True, slots=True)
class TurnInAdmissionDecision:
    status: TurnInAdmissionStatus
    reason: str
    token: TurnInAdmissionToken | None = None

    @property
    def allowed(self) -> bool:
        return self.status is TurnInAdmissionStatus.ALLOW


def admit_turn_in(
    plan: QuestPlan,
    evaluation: PlanEvaluation,
    lease: QuestChainLease,
    entry: ActiveQuestEntry,
    quest_ref: QuestRef,
    context: EvaluationContext,
    authority: ActiveCatalogAuthority,
    *,
    now: float,
) -> TurnInAdmissionDecision:
    """Return an immutable authorization token only for one exact turn-in."""

    if not _typed_inputs(plan, evaluation, lease, entry, quest_ref, context, authority):
        return _unsafe("invalid_input_type")
    if not _valid_authority_shape(authority, now):
        return _unsafe("invalid_catalog_authority")
    if not authority.complete:
        return _blocked("catalog_incomplete")
    if authority.generated_at > now or now - authority.generated_at > authority.max_age_seconds:
        return _blocked("catalog_stale")
    if (
        authority.client_id != context.client_id
        or authority.profile_id != context.profile_id
        or authority.tab_id != context.tab_id
    ):
        return _unsafe("foreign_catalog_authority")
    if (
        context.causal_baseline != authority.causal_baseline
        and context.causal_baseline not in authority.causal_ancestors
    ):
        return _unsafe("foreign_catalog_authority")
    if authority.revision < context.min_revision:
        return _blocked("catalog_before_evaluation_baseline")

    matches = tuple(
        candidate for candidate in authority.entries
        if candidate.id == plan.quest_id and candidate.title == plan.quest_title
    )
    identity_collisions = tuple(
        candidate for candidate in authority.entries
        if candidate.id == plan.quest_id or candidate.title == plan.quest_title
    )
    if len(matches) != 1 or len(identity_collisions) != 1 or entry != matches[0]:
        return _unsafe("ambiguous_or_missing_catalog_identity")
    if entry.id != plan.quest_id or entry.title != plan.quest_title:
        return _unsafe("entry_plan_identity_mismatch")

    if lease.quest_id != plan.quest_id or lease.quest_title != plan.quest_title:
        return _unsafe("lease_plan_identity_mismatch")
    if authority.plan_fingerprint != plan.fingerprint:
        return _unsafe("plan_fingerprint_mismatch")
    if (
        not lease.current_fingerprint
        or authority.lease_fingerprint != lease.current_fingerprint
        or lease.turn_in_ref_fingerprint != lease.current_fingerprint
    ):
        return _unsafe("lease_fingerprint_mismatch")
    if lease.accepted_ref != quest_ref:
        return _unsafe("quest_ref_lease_mismatch")
    if (
        quest_ref.id != plan.quest_id
        or quest_ref.title != plan.quest_title
        or not quest_ref.location
        or len(quest_ref.giver_names) != 1
        or not quest_ref.giver_names[0].strip()
    ):
        return _unsafe("quest_ref_not_unique")

    leaves = tuple(iter_requirements(plan.graph.root))
    turn_ins = tuple(leaf for leaf in leaves if isinstance(leaf, TurnIn))
    if len(turn_ins) != 1:
        return _unsafe("turn_in_not_unique")
    turn_in = turn_ins[0]
    if _name_key(turn_in.npc) != _name_key(quest_ref.giver_names[0]):
        return _unsafe("turn_in_npc_mismatch")
    if evaluation.status is not EvaluationStatus.ACTIONABLE or evaluation.next_atom != turn_in:
        return _blocked("turn_in_not_next_actionable_atom")

    by_id = {item.requirement_id: item for item in evaluation.requirements}
    if len(by_id) != len(evaluation.requirements) or set(by_id) != {leaf.requirement_id for leaf in leaves}:
        return _unsafe("evaluation_requirement_mismatch")
    if any(by_id[leaf.requirement_id].step_fingerprint != leaf.step_id for leaf in leaves):
        return _unsafe("evaluation_step_fingerprint_mismatch")
    if by_id[turn_in.requirement_id].status is not EvaluationStatus.ACTIONABLE:
        return _blocked("turn_in_requirement_not_actionable")
    prerequisites = tuple(by_id[leaf.requirement_id] for leaf in leaves if not isinstance(leaf, TurnIn))
    if any(item.status is not EvaluationStatus.SATISFIED for item in prerequisites):
        return _blocked("prerequisites_not_satisfied")
    if any(item.evidence_revision is None for item in prerequisites):
        return _unsafe("prerequisite_revision_missing")
    newest_prerequisite = max(
        (item.evidence_revision for item in prerequisites if item.evidence_revision is not None),
        default=-1,
    )
    if authority.revision <= newest_prerequisite:
        return _blocked("catalog_not_newer_than_prerequisites")
    if authority.revision < lease.selected_revision:
        return _blocked("catalog_older_than_lease")

    token = TurnInAdmissionToken(
        plan.quest_id, plan.quest_title, plan.fingerprint, turn_in.requirement_id,
        lease.current_fingerprint, authority.snapshot_id, authority.revision,
        context.client_id, context.profile_id, context.tab_id, context.causal_baseline,
    )
    return TurnInAdmissionDecision(TurnInAdmissionStatus.ALLOW, "turn_in_admitted", token)


def _typed_inputs(*values: object) -> bool:
    expected = (
        QuestPlan, PlanEvaluation, QuestChainLease, ActiveQuestEntry,
        QuestRef, EvaluationContext, ActiveCatalogAuthority,
    )
    return all(isinstance(value, kind) for value, kind in zip(values, expected, strict=True))


def _valid_authority_shape(authority: ActiveCatalogAuthority, now: float) -> bool:
    strings = (
        authority.snapshot_id, authority.client_id, authority.profile_id, authority.tab_id,
        authority.causal_baseline, authority.plan_fingerprint, authority.lease_fingerprint,
    )
    return (
        all(isinstance(item, ActiveQuestEntry) for item in authority.entries)
        and isinstance(authority.complete, bool)
        and isinstance(authority.revision, int) and not isinstance(authority.revision, bool)
        and authority.revision >= 0
        and all(isinstance(item, str) and item.strip() for item in strings)
        and all(isinstance(item, str) and item.strip() for item in authority.causal_ancestors)
        and len(set(authority.causal_ancestors)) == len(authority.causal_ancestors)
        and _finite_non_negative(authority.generated_at)
        and _finite_non_negative(authority.max_age_seconds)
        and authority.max_age_seconds > 0
        and _finite_non_negative(now)
    )


def _finite_non_negative(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def _name_key(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").split())


def _blocked(reason: str) -> TurnInAdmissionDecision:
    return TurnInAdmissionDecision(TurnInAdmissionStatus.BLOCKED, reason)


def _unsafe(reason: str) -> TurnInAdmissionDecision:
    return TurnInAdmissionDecision(TurnInAdmissionStatus.UNSAFE, reason)


__all__ = [
    "ActiveCatalogAuthority", "TurnInAdmissionDecision", "TurnInAdmissionStatus",
    "TurnInAdmissionToken", "admit_turn_in",
]
