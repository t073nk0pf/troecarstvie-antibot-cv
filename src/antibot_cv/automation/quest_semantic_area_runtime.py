"""Fail-closed semantic admission helpers for quest area-object execution."""

from __future__ import annotations

import time

from .area_object_activity import area_object_progress
from .gathering_activity_runtime import GatheringRequirement
from .quest_compiler import QuestCompileStatus, compile_quest_plan
from .quest_inventory_evidence import (
    QuestInventoryEvidenceStatus,
    build_quest_inventory_evidence,
    merge_authoritative_inventory_evidence,
    quest_inventory_item_matches,
)
from .quest_inventory_guard import QuestInventoryGuardResult, evaluate_quest_inventory
from .quest_plan_evaluator import EvaluationContext, EvaluationStatus, evaluate_quest_plan
from .quest_plan_model import Acquire, AreaObject


class QuestSemanticAreaRuntimeMixin:
    """Domain logic used by the refresh orchestrator; emits no actions."""

    def _record_quest_area_inventory_evidence(self, area_plan, snapshot, items) -> bool:
        if self.config.leveling.quest_engine_mode != "q280_q304" or area_plan.quest_id != "304":
            return True
        resolved = self._semantic_area_plan(area_plan)
        if resolved is None:
            return False
        semantic_plan, objective, authority = resolved
        if (
            authority.complete is not True
            or authority.plan_fingerprint != semantic_plan.fingerprint
            or snapshot.get("clientId") != authority.client_id
            or snapshot.get("profileId") != authority.profile_id
            or str(snapshot.get("tabId") or "") != authority.tab_id
            or snapshot.get("causalBaseline") != authority.causal_baseline
        ):
            return False
        revision = snapshot.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            return False
        now = time.time()
        previous_context = getattr(self, "_quest_semantic_evaluation_context", None)
        if previous_context is not None and (
            previous_context.client_id != authority.client_id
            or previous_context.profile_id != authority.profile_id
            or previous_context.tab_id != authority.tab_id
            or previous_context.causal_baseline != authority.causal_baseline
        ):
            return False
        guard = evaluate_quest_inventory(objective, snapshot)
        if not guard.confirmed:
            return False
        progress = area_object_progress(area_plan, items)
        collected = dict(guard.collected)
        collected.update((name, count) for name, count in progress.collected if count > 0)
        combined_guard = QuestInventoryGuardResult(
            True, progress.complete and guard.complete,
            tuple(GatheringRequirement(name, count) for name, count in collected.items()),
            tuple(collected.items()), "quest_semantic_area_inventory_guard",
        )
        adapted = build_quest_inventory_evidence(
            semantic_plan, snapshot, combined_guard,
            client_id=authority.client_id, profile_id=authority.profile_id,
            tab_id=authority.tab_id, causal_baseline=authority.causal_baseline,
            freshness_seconds=max(1.0, self.config.leveling.snapshot_stale_timeout_ms / 1000),
            now=now,
        )
        if adapted.status is not QuestInventoryEvidenceStatus.READY:
            return False
        existing = tuple(getattr(self, "_quest_semantic_evidence", ()))
        try:
            merged = merge_authoritative_inventory_evidence(
                existing, adapted.envelopes, plan=semantic_plan,
            )
        except (TypeError, ValueError):
            return False
        min_revision = min((item.revision for item in merged), default=revision)
        self._quest_semantic_evidence = merged
        self._quest_semantic_evaluation_context = EvaluationContext(
            authority.client_id, authority.profile_id, authority.tab_id,
            authority.causal_baseline, min_revision, now,
        )
        return True

    def _quest_area_semantic_binding_valid(self, area_plan) -> bool:
        if self.config.leveling.quest_engine_mode != "q280_q304" or area_plan.quest_id != "304":
            return True
        resolved = self._semantic_area_plan(area_plan)
        if resolved is None:
            return False
        semantic_plan, _objective, authority = resolved
        context = getattr(self, "_quest_semantic_evaluation_context", None)
        evidence = tuple(getattr(self, "_quest_semantic_evidence", ()))
        lease = getattr(getattr(self._quest_director, "chain", None), "lease", None)
        if not (
            isinstance(context, EvaluationContext) and authority.complete is True
            and authority.plan_fingerprint == semantic_plan.fingerprint
            and lease is not None and lease.current_fingerprint
            and lease.current_fingerprint == authority.lease_fingerprint
            and context.client_id == authority.client_id
            and context.profile_id == authority.profile_id
            and context.tab_id == authority.tab_id
            and context.causal_baseline == authority.causal_baseline
            and authority.generated_at <= context.now
            and context.now - authority.generated_at <= authority.max_age_seconds
        ):
            return False
        pending = self._quest_area_objects.pending
        if pending is None:
            return False
        evaluation = evaluate_quest_plan(
            semantic_plan, evidence,
            capabilities=("combat_drop", "area_object", "turn_in"), context=context,
        )
        atom = evaluation.next_atom
        return bool(
            evaluation.status is EvaluationStatus.ACTIONABLE
            and isinstance(atom, Acquire) and isinstance(atom.source, AreaObject)
            and atom.requirement_id == self._pending_area_requirement_id(semantic_plan, pending)
            and quest_inventory_item_matches(
                pending.requirement.resource_name, atom.item,
            )
            and atom.count == pending.requirement.required
            and quest_inventory_item_matches(
                pending.requirement.resource_name, atom.source.object_name,
            )
        )

    def _semantic_area_plan(self, area_plan):
        director = self._quest_director
        authority = getattr(self, "_quest_active_catalog_authority", None)
        objective = None if director is None else director.active_objective
        if (
            director is None or objective is None or authority is None
            or objective.quest_id != area_plan.quest_id
            or objective.quest_title != area_plan.quest_title
        ):
            return None
        entries = tuple(
            entry for entry in director.active_catalog.result
            if entry.id == area_plan.quest_id and entry.title == area_plan.quest_title
        )
        if len(entries) != 1:
            return None
        compiled = compile_quest_plan(entries[0])
        if compiled.status is not QuestCompileStatus.READY or compiled.plan is None:
            return None
        return compiled.plan, objective, authority

    @staticmethod
    def _pending_area_requirement_id(semantic_plan, pending):
        matches = tuple(
            leaf for leaf in _requirements(semantic_plan.graph.root)
            if isinstance(leaf, Acquire) and isinstance(leaf.source, AreaObject)
            and quest_inventory_item_matches(pending.requirement.resource_name, leaf.item)
            and leaf.count == pending.requirement.required
            and quest_inventory_item_matches(
                pending.requirement.resource_name, leaf.source.object_name,
            )
        )
        return matches[0].requirement_id if len(matches) == 1 else None


def _requirements(node):
    from .quest_plan_model import iter_requirements
    return iter_requirements(node)


__all__ = ["QuestSemanticAreaRuntimeMixin"]
