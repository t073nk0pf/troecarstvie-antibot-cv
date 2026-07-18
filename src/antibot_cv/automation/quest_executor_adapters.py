"""Pure capability adapters for the semantic quest executor protocol.

These adapters only turn an already-bound semantic leaf into a typed intent.
They do not parse quest text, execute actions, or decide whole-quest completion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from .action_registry import action_domain
from .quest_evidence import EvidenceEnvelope, EvidenceSourceKind
from .quest_execution_model import MutationIntent, RouteKind, RouteLease
from .quest_plan_model import (
    Acquire,
    AreaObject,
    CombatDrop,
    Gathering,
    InteractNpc,
    Kill,
    QuestLeaf,
    TurnIn,
    Visit,
)


class ExecutorContextError(ValueError):
    """Raised when intent authority is absent, stale, or foreign."""


class EvidenceReconciliationError(ValueError):
    """Raised when evidence is not authoritative for the exact requirement."""


@dataclass(frozen=True, slots=True)
class ExecutorContext:
    quest_id: str
    plan_fingerprint: str
    actor_id: str
    client_id: str
    profile_id: str
    tab_id: str
    revision: str
    now: float
    max_snapshot_age_s: float
    route_lease: RouteLease
    cycle_id: int = 0
    battle_id: int | None = None
    dry_run: bool = True
    action_type: str | None = None
    action_metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    executor_id: str
    requirement_id: str
    step_fingerprint: str
    evidence_fingerprint: str
    revision: int
    satisfied: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "executor_id": self.executor_id,
            "requirement_id": self.requirement_id,
            "step_fingerprint": self.step_fingerprint,
            "evidence_fingerprint": self.evidence_fingerprint,
            "revision": self.revision,
            "satisfied": self.satisfied,
        }


class _CapabilityExecutor:
    executor_id = ""
    route_kind: RouteKind
    action_type = ""
    evidence_sources: frozenset[EvidenceSourceKind]
    allowed_action_types: frozenset[str]

    def __init__(self) -> None:
        self._results: dict[str, ReconciliationResult] = {}
        self._bindings: dict[str, dict[str, str]] = {}

    def can_execute(self, atom: QuestLeaf) -> bool:
        raise NotImplementedError

    def _metadata(self, atom: QuestLeaf) -> Mapping[str, object]:
        raise NotImplementedError

    def _route_kind(self, atom: QuestLeaf) -> RouteKind:
        return self.route_kind

    def plan_intent(self, atom: QuestLeaf, *, context: object | None = None) -> MutationIntent:
        if not self.can_execute(atom):
            raise ExecutorContextError(f"{self.executor_id} cannot execute this atom")
        if not isinstance(context, ExecutorContext):
            raise ExecutorContextError("typed executor context is required")
        if not isinstance(context.plan_fingerprint, str) or not context.plan_fingerprint.strip():
            raise ExecutorContextError("plan fingerprint is required")
        lease = context.route_lease
        if lease.kind is not self._route_kind(atom):
            raise ExecutorContextError("route lease kind mismatch")
        reason = lease.coherence_reason(
            now=context.now,
            actor_id=context.actor_id,
            client_id=context.client_id,
            profile_id=context.profile_id,
            tab_id=context.tab_id,
            quest_id=context.quest_id,
            step_fingerprint=atom.step_id,
            revision=context.revision,
            max_snapshot_age_s=context.max_snapshot_age_s,
        )
        if reason is not None:
            raise ExecutorContextError(reason)
        if context.action_type is None or action_domain(context.action_type) is None:
            raise ExecutorContextError("registered legacy executor action is required")
        if context.action_type not in self.allowed_action_types:
            raise ExecutorContextError("legacy executor action is not owned by capability phase")
        action_type = context.action_type
        metadata = {
            **dict(context.action_metadata),
            "executor_id": self.executor_id,
            "quest_id": context.quest_id,
            "plan_fingerprint": context.plan_fingerprint,
            **self._metadata(atom),
        }
        binding = {
            "quest_id": context.quest_id,
            "plan_fingerprint": context.plan_fingerprint,
            "step_fingerprint": atom.step_id,
            "client_id": context.client_id,
            "profile_id": context.profile_id,
            "tab_id": context.tab_id,
        }
        intent = MutationIntent(
            action_type=action_type,
            requirement_id=atom.requirement_id,
            step_fingerprint=atom.step_id,
            idempotency_key=f"{self.executor_id}:{lease.fingerprint}:{atom.requirement_id}",
            route_lease=lease,
            cycle_id=context.cycle_id,
            battle_id=context.battle_id,
            dry_run=context.dry_run,
            metadata=metadata,
        )
        self._bindings[atom.requirement_id] = binding
        return intent

    def reconcile(
        self, atom: QuestLeaf, *, evidence: object, context: object | None = None,
    ) -> ReconciliationResult:
        if not self.can_execute(atom):
            raise EvidenceReconciliationError("executor does not own requirement")
        if not isinstance(evidence, EvidenceEnvelope):
            raise EvidenceReconciliationError("typed evidence envelope is required")
        if evidence.requirement_id != atom.requirement_id or evidence.step_fingerprint != atom.step_id:
            raise EvidenceReconciliationError("evidence requirement binding mismatch")
        binding = self._bindings.get(atom.requirement_id)
        if binding is None:
            if not isinstance(context, ExecutorContext):
                raise EvidenceReconciliationError("requirement has no planned binding")
            binding = {
                "quest_id": context.quest_id,
                "plan_fingerprint": context.plan_fingerprint,
                "step_fingerprint": atom.step_id,
                "client_id": context.client_id,
                "profile_id": context.profile_id,
                "tab_id": context.tab_id,
            }
        observed_binding = {
            "quest_id": evidence.quest_id,
            "plan_fingerprint": evidence.plan_fingerprint,
            "step_fingerprint": evidence.step_fingerprint,
            "client_id": evidence.client_id,
            "profile_id": evidence.profile_id,
            "tab_id": evidence.tab_id,
        }
        if observed_binding != binding:
            raise EvidenceReconciliationError("foreign evidence binding")
        if evidence.source_kind not in self.evidence_sources:
            raise EvidenceReconciliationError("evidence source is not authoritative for capability")
        errors = evidence.validation_errors()
        if errors:
            raise EvidenceReconciliationError(",".join(errors))
        result = ReconciliationResult(
            executor_id=self.executor_id,
            requirement_id=atom.requirement_id,
            step_fingerprint=atom.step_id,
            evidence_fingerprint=evidence.fingerprint,
            revision=evidence.revision,
            satisfied=True,
        )
        previous = self._results.get(atom.requirement_id)
        if previous is not None and evidence.revision < previous.revision:
            raise EvidenceReconciliationError("evidence revision regressed")
        self._results[atom.requirement_id] = result
        self._bindings[atom.requirement_id] = binding
        return result

    def checkpoint(self) -> Mapping[str, object]:
        return {
            "schema_version": 1,
            "executor_id": self.executor_id,
            "bindings": {key: dict(value) for key, value in sorted(self._bindings.items())},
            "requirements": {
                key: result.to_dict() for key, result in sorted(self._results.items())
            },
        }

    def restore(self, payload: Mapping[str, object]) -> None:
        if not isinstance(payload, Mapping) or payload.get("schema_version") != 1:
            raise ValueError("unsupported executor checkpoint")
        if payload.get("executor_id") != self.executor_id:
            raise ValueError("foreign executor checkpoint")
        raw_results = payload.get("requirements")
        raw_bindings = payload.get("bindings")
        if set(payload) != {"schema_version", "executor_id", "bindings", "requirements"}:
            raise ValueError("checkpoint has unknown or missing keys")
        if not isinstance(raw_results, Mapping) or not isinstance(raw_bindings, Mapping):
            raise ValueError("checkpoint requirements must be a mapping")
        restored_bindings: dict[str, dict[str, str]] = {}
        binding_keys = {
            "quest_id", "plan_fingerprint", "step_fingerprint", "client_id", "profile_id", "tab_id"
        }
        for key, raw in raw_bindings.items():
            if not isinstance(key, str) or not isinstance(raw, Mapping) or set(raw) != binding_keys:
                raise ValueError("malformed executor binding")
            binding = dict(raw)
            if not all(isinstance(value, str) and value.strip() for value in binding.values()):
                raise ValueError("malformed executor binding")
            restored_bindings[key] = binding  # type: ignore[assignment]
        restored: dict[str, ReconciliationResult] = {}
        result_keys = {
            "executor_id", "requirement_id", "step_fingerprint", "evidence_fingerprint",
            "revision", "satisfied",
        }
        for key, raw in raw_results.items():
            if not isinstance(key, str) or not isinstance(raw, Mapping) or set(raw) != result_keys:
                raise ValueError("malformed executor checkpoint")
            try:
                revision = raw["revision"]
                if isinstance(revision, bool) or not isinstance(revision, int):
                    raise ValueError("revision must be an integer")
                result = ReconciliationResult(
                    executor_id=raw["executor_id"],  # type: ignore[arg-type]
                    requirement_id=raw["requirement_id"],  # type: ignore[arg-type]
                    step_fingerprint=raw["step_fingerprint"],  # type: ignore[arg-type]
                    evidence_fingerprint=raw["evidence_fingerprint"],  # type: ignore[arg-type]
                    revision=revision,
                    satisfied=raw["satisfied"],  # type: ignore[arg-type]
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("malformed executor checkpoint") from exc
            if (
                result.executor_id != self.executor_id
                or result.requirement_id != key
                or not result.step_fingerprint
                or not result.evidence_fingerprint
                or result.revision < 0
                or result.satisfied is not True
            ):
                raise ValueError("malformed executor checkpoint")
            restored[key] = result
        if not set(restored).issubset(restored_bindings):
            raise ValueError("checkpoint result lacks binding")
        self._bindings = restored_bindings
        self._results = restored


class KillExecutor(_CapabilityExecutor):
    executor_id = "quest.kill"
    route_kind = RouteKind.QUEST_LOCATION
    action_type = "quest_combat"
    evidence_sources = frozenset({EvidenceSourceKind.COMBAT})
    allowed_action_types = frozenset({
        "open_hunt", "attack_visible_target", "open_location_navigator",
        "navigator_select_target", "navigator_go", "location_route_step",
    })

    def can_execute(self, atom: QuestLeaf) -> bool:
        return isinstance(atom, Kill)

    def _metadata(self, atom: QuestLeaf) -> Mapping[str, object]:
        assert isinstance(atom, Kill)
        return {"target": atom.target, "count": atom.count}


class CombatDropExecutor(_CapabilityExecutor):
    executor_id = "quest.acquire.combat_drop"
    route_kind = RouteKind.QUEST_LOCATION
    action_type = "quest_combat_drop"
    evidence_sources = frozenset({EvidenceSourceKind.INVENTORY, EvidenceSourceKind.COMBAT})
    allowed_action_types = KillExecutor.allowed_action_types

    def can_execute(self, atom: QuestLeaf) -> bool:
        return isinstance(atom, Acquire) and isinstance(atom.source, CombatDrop)

    def _metadata(self, atom: QuestLeaf) -> Mapping[str, object]:
        assert isinstance(atom, Acquire) and isinstance(atom.source, CombatDrop)
        return {"item": atom.item, "count": atom.count, "target": atom.source.target}


class AreaObjectExecutor(_CapabilityExecutor):
    executor_id = "quest.acquire.area_object"
    route_kind = RouteKind.QUEST_AREA_OBJECT
    action_type = "quest_area_object"
    evidence_sources = frozenset({EvidenceSourceKind.INVENTORY, EvidenceSourceKind.AREA_OBJECT})
    allowed_action_types = frozenset({
        "open_location_navigator", "navigator_select_target", "navigator_go",
        "location_route_step", "area_object_snapshot", "inspect_area_object",
    })

    def can_execute(self, atom: QuestLeaf) -> bool:
        return isinstance(atom, Acquire) and isinstance(atom.source, AreaObject)

    def _metadata(self, atom: QuestLeaf) -> Mapping[str, object]:
        assert isinstance(atom, Acquire) and isinstance(atom.source, AreaObject)
        return {"item": atom.item, "count": atom.count, "object_name": atom.source.object_name}


class GatheringExecutor(_CapabilityExecutor):
    executor_id = "quest.acquire.gathering"
    route_kind = RouteKind.QUEST_LOCATION
    action_type = "quest_gathering"
    evidence_sources = frozenset({EvidenceSourceKind.INVENTORY, EvidenceSourceKind.GATHERING})
    allowed_action_types = frozenset({
        "open_location_navigator", "navigator_select_target", "navigator_go",
        "location_route_step",
    })

    def can_execute(self, atom: QuestLeaf) -> bool:
        return isinstance(atom, Acquire) and isinstance(atom.source, Gathering)

    def _metadata(self, atom: QuestLeaf) -> Mapping[str, object]:
        assert isinstance(atom, Acquire) and isinstance(atom.source, Gathering)
        return {"item": atom.item, "count": atom.count, "resource": atom.source.resource}


class VisitExecutor(_CapabilityExecutor):
    executor_id = "quest.visit"
    route_kind = RouteKind.QUEST_LOCATION
    action_type = "quest_visit"
    evidence_sources = frozenset({EvidenceSourceKind.LOCATION})
    allowed_action_types = frozenset({
        "open_location_navigator", "navigator_select_target", "navigator_go",
        "location_route_step",
    })

    def can_execute(self, atom: QuestLeaf) -> bool:
        return isinstance(atom, Visit)

    def _metadata(self, atom: QuestLeaf) -> Mapping[str, object]:
        assert isinstance(atom, Visit)
        return {"location": atom.location}


class NpcInteractionExecutor(_CapabilityExecutor):
    executor_id = "quest.interact_npc"
    action_type = "quest_interact_npc"
    evidence_sources = frozenset({EvidenceSourceKind.NPC_DIALOG})
    allowed_action_types = frozenset({
        "open_location_navigator", "navigator_select_target", "navigator_go",
        "location_route_step", "open_exact_npc", "npc_quest_action",
    })

    def can_execute(self, atom: QuestLeaf) -> bool:
        return isinstance(atom, InteractNpc)

    def _route_kind(self, atom: QuestLeaf) -> RouteKind:
        return (
            RouteKind.QUEST_ORDERED_HANDOFF
            if isinstance(atom, InteractNpc) and atom.request is not None
            else RouteKind.QUEST_DIALOGUE
        )

    def _metadata(self, atom: QuestLeaf) -> Mapping[str, object]:
        assert isinstance(atom, InteractNpc)
        return {"npc": atom.npc, "request": atom.request, "opaque": atom.opaque}


class TurnInExecutor(_CapabilityExecutor):
    executor_id = "quest.turn_in"
    route_kind = RouteKind.QUEST_TURN_IN
    action_type = "quest_turn_in"
    evidence_sources = frozenset({EvidenceSourceKind.TURN_IN})
    allowed_action_types = NpcInteractionExecutor.allowed_action_types

    def can_execute(self, atom: QuestLeaf) -> bool:
        return isinstance(atom, TurnIn)

    def _metadata(self, atom: QuestLeaf) -> Mapping[str, object]:
        assert isinstance(atom, TurnIn)
        return {"npc": atom.npc}


def default_quest_executors() -> tuple[_CapabilityExecutor, ...]:
    """Return exclusive executors; Purchase intentionally has no capability."""

    return (
        KillExecutor(), CombatDropExecutor(), AreaObjectExecutor(), GatheringExecutor(),
        VisitExecutor(), NpcInteractionExecutor(), TurnInExecutor(),
    )


__all__ = [
    "AreaObjectExecutor", "CombatDropExecutor", "EvidenceReconciliationError",
    "ExecutorContext", "ExecutorContextError", "GatheringExecutor", "KillExecutor",
    "NpcInteractionExecutor", "ReconciliationResult", "TurnInExecutor",
    "VisitExecutor", "default_quest_executors",
]
