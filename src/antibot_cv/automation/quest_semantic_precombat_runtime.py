from __future__ import annotations

import time

from src.antibot_cv.automation.actions import ActionRequest
from src.antibot_cv.automation.quest_compiler import QuestCompileStatus, compile_quest_plan
from src.antibot_cv.automation.quest_combat_binding import (
    QuestCombatAdmission,
    QuestCombatAdmissionStatus,
    bind_semantic_combat_target,
)
from src.antibot_cv.automation.quest_inventory_evidence import (
    QuestInventoryEvidenceStatus,
    build_quest_inventory_evidence,
)
from src.antibot_cv.automation.quest_inventory_guard import evaluate_quest_inventory
from src.antibot_cv.automation.quest_plan_evaluator import EvaluationContext, EvaluationStatus
from src.antibot_cv.automation.quest_plan_model import Acquire, CombatDrop, Kill, iter_requirements
from src.antibot_cv.automation.quest_semantic_runtime import (
    QuestSemanticDisposition,
    evaluate_semantic_quest_runtime,
)
from src.antibot_cv.automation.quest_shadow_legacy_adapter import normalize_legacy_decision


class SemanticQuestPrecombatRuntimeMixin:
    """Semantic quest admission at the pre-combat boundary.

    The host runtime supplies the legacy fallback, refresh handoff, safety stop,
    and the controller-owned observations.  This domain mixin only compiles and
    evaluates the semantic quest decision before a combat mutation is allowed.
    """

    def _set_semantic_combat_admission(
        self,
        status: QuestCombatAdmissionStatus,
        reason: str,
        binding=None,
    ) -> None:
        self._quest_semantic_precombat_attack_allowed = (
            status is QuestCombatAdmissionStatus.ACTIONABLE and binding is not None
        )
        self._quest_semantic_combat_binding = binding
        self._quest_semantic_combat_admission = QuestCombatAdmission(status, reason, binding)

    def _quest_inventory_allows_attack(self) -> bool:
        mode = getattr(getattr(self.config, "leveling", None), "quest_engine_mode", "legacy")
        if mode == "legacy":
            return self._legacy_quest_inventory_allows_attack()

        director = getattr(self, "_quest_director", None)
        objective = director.active_objective if director is not None else None
        if (
            objective is None
            or not director.active_snapshot_fresh
            or not director.active_catalog.complete
        ):
            self._set_semantic_combat_admission(
                QuestCombatAdmissionStatus.WAIT,
                "semantic_authority_unavailable",
            )
            if mode == "shadow":
                return self._legacy_quest_inventory_allows_attack()
            return False
        if mode == "q280_q304" and objective.quest_id not in {"280", "304"}:
            self._set_semantic_combat_admission(
                QuestCombatAdmissionStatus.WAIT,
                "explicit_legacy_quest_path",
            )
            return self._legacy_quest_inventory_allows_attack()
        entries = tuple(
            entry for entry in director.active_catalog.result
            if entry.id == objective.quest_id and entry.title == objective.quest_title
        )
        if len(entries) != 1:
            if mode == "shadow":
                self._set_semantic_combat_admission(
                    QuestCombatAdmissionStatus.WAIT,
                    "semantic_active_entry_missing_or_ambiguous",
                )
                return self._legacy_quest_inventory_allows_attack()
            self._stop_leveling_unsafe("quest_semantic_active_entry_missing_or_ambiguous")
            return False
        entry = entries[0]
        compiled = compile_quest_plan(entry)
        if compiled.status is not QuestCompileStatus.READY or compiled.plan is None:
            if mode == "shadow":
                self._set_semantic_combat_admission(
                    QuestCombatAdmissionStatus.WAIT,
                    f"semantic_compile:{compiled.status.value}",
                )
                return self._legacy_quest_inventory_allows_attack()
            self._stop_leveling_unsafe(f"quest_semantic_compile:{compiled.status.value}")
            return False
        plan = compiled.plan
        authority = getattr(self, "_quest_active_catalog_authority", None)
        if mode != "shadow" and authority is None:
            self._set_semantic_combat_admission(
                QuestCombatAdmissionStatus.WAIT,
                "semantic_catalog_authority_missing",
            )
            return False
        authority_token = (
            getattr(authority, "causal_baseline", None),
            getattr(authority, "revision", None),
            objective.fingerprint,
            plan.fingerprint,
        )
        if getattr(self, "_quest_semantic_combat_authority_token", None) not in {None, authority_token}:
            self._set_semantic_combat_admission(
                QuestCombatAdmissionStatus.WAIT,
                "semantic_authority_changed",
            )
        requirements = tuple(
            requirement for requirement in iter_requirements(plan.graph.root)
            if isinstance(requirement, Acquire)
        )
        last_fingerprint = getattr(self, "_quest_inventory_checked_fingerprint", None)
        last_cycle = getattr(self, "_quest_inventory_checked_cycle", None)
        current_cycle = self.session.completed_cycles
        inspection_due = (
            last_fingerprint != objective.fingerprint
            or last_cycle is None
            or current_cycle - last_cycle >= 1
            or getattr(self, "_quest_semantic_combat_authority_token", None) != authority_token
        )
        if not inspection_due:
            cached = getattr(self, "_quest_semantic_precombat_attack_allowed", None)
            return True if cached is None else bool(cached)
        sink = self.action_executor.sink
        if hasattr(sink, "last_quest_inventory_snapshot"):
            sink.last_quest_inventory_snapshot = None
        metadata = {
            "quest_id": objective.quest_id,
            "quest_title": objective.quest_title,
            "objective_fingerprint": objective.fingerprint,
            "names": [item.item for item in requirements],
            "inventory_open_delay_ms": self.config.item_recovery.inventory_open_delay_ms,
            "reason": "quest_precombat_inventory_guard",
        }
        if authority is not None:
            metadata.update({
                "causal_baseline": str(getattr(authority, "causal_baseline", "") or ""),
                "minimum_revision": getattr(authority, "revision", None),
            })
        request = ActionRequest(
            "inspect_quest_inventory",
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata=metadata,
        )
        if not self.action_executor.execute(request):
            self._stop_leveling_unsafe("quest_inventory_inspection_failed")
            return False
        self._quest_inventory_navigation_owned = True
        self._selected_target = None
        self._current_target = None
        result = evaluate_quest_inventory(
            objective,
            getattr(sink, "last_quest_inventory_snapshot", None),
        )
        self._quest_inventory_checked_fingerprint = objective.fingerprint
        self._quest_inventory_checked_cycle = current_cycle
        self.logger.log_event(
            "quest_inventory_guard_decision",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            quest_id=objective.quest_id,
            quest_title=objective.quest_title,
            objective_fingerprint=objective.fingerprint,
            confirmed=result.confirmed,
            complete=result.complete,
            requirements=[
                {"name": item.name, "required": item.required}
                for item in result.requirements
            ],
            collected=[{"name": name, "count": count} for name, count in result.collected],
            reason=result.reason,
        )
        legacy = normalize_legacy_decision({
            "quest_id": objective.quest_id,
            "status": "satisfied" if result.confirmed and result.complete else (
                "actionable" if result.confirmed else "unsafe"
            ),
            **({
                "atom_kind": "acquire",
                "target": requirements[0].item,
            } if result.confirmed and not result.complete and requirements else {}),
            "reason": result.reason,
        })

        snapshot = getattr(sink, "last_quest_inventory_snapshot", None)
        observation = getattr(
            getattr(self, "_quest_active_catalog_authority_accumulator", None),
            "observation", None,
        ) if authority is not None else None
        evidence_result = None
        context = None
        if observation is not None:
            evidence_guard = type(result)(
                result.confirmed,
                result.complete,
                result.requirements,
                tuple((name, count) for name, count in result.collected if count > 0),
                result.reason,
            )
            context = EvaluationContext(
                observation.client_id,
                observation.profile_id,
                observation.tab_id,
                observation.causal_baseline,
                director.active_catalog.revision,
                now=time.time(),
            )
            evidence_result = build_quest_inventory_evidence(
                plan,
                snapshot,
                evidence_guard,
                client_id=context.client_id,
                profile_id=context.profile_id,
                tab_id=context.tab_id,
                causal_baseline=context.causal_baseline,
                now=context.now,
            )
        if mode == "shadow":
            evidence = (
                evidence_result.envelopes
                if evidence_result is not None
                and evidence_result.status is QuestInventoryEvidenceStatus.READY
                else ()
            )
            shadow_context = context or EvaluationContext(
                "shadow-client", "shadow-profile", "shadow-tab", "shadow-baseline", 0,
                now=time.time(),
            )
            outcome = evaluate_semantic_quest_runtime(
                mode, entry, evidence, shadow_context,
                ("kill", "combat_drop", "area_object", "gathering", "purchase", "visit", "interact_npc", "turn_in"),
                legacy,
            )
            comparison = outcome.comparison
            self.logger.log_event(
                "quest_semantic_precombat_shadow",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                quest_id=entry.id,
                comparison_code=comparison.code if comparison is not None else outcome.reason,
                comparison_fingerprint=comparison.fingerprint if comparison is not None else None,
            )
            return self._legacy_quest_inventory_allows_attack_after_inspection(result, objective, current_cycle)

        if context is None or evidence_result is None:
            self._stop_leveling_unsafe("quest_semantic_inventory_context_missing")
            return False
        if evidence_result.status is not QuestInventoryEvidenceStatus.READY:
            self._stop_leveling_unsafe(f"quest_semantic_inventory:{evidence_result.reason}")
            return False
        outcome = evaluate_semantic_quest_runtime(
            mode, entry, evidence_result.envelopes, context,
            ("kill", "combat_drop", "area_object", "gathering", "purchase", "visit", "interact_npc", "turn_in"),
            legacy,
        )
        if outcome.disposition is not QuestSemanticDisposition.AUTHORITATIVE:
            return self._legacy_quest_inventory_allows_attack_after_inspection(result, objective, current_cycle)
        previous_evidence = tuple(getattr(self, "_quest_semantic_evidence", ()))
        inventory_requirement_ids = {
            item.requirement_id for item in requirements
        }
        self._quest_semantic_evidence = (
            *(item for item in previous_evidence
              if item.requirement_id not in inventory_requirement_ids),
            *evidence_result.envelopes,
        )
        self._quest_semantic_evaluation_context = context
        self._quest_semantic_plan = plan
        self._quest_semantic_combat_authority_token = authority_token
        atom = outcome.next_atom
        if outcome.status is EvaluationStatus.ACTIONABLE and (
            isinstance(atom, Kill)
            or isinstance(atom, Acquire) and isinstance(atom.source, CombatDrop)
        ):
            combat_target = atom.target if isinstance(atom, Kill) else atom.source.target
            legacy_objective_target = getattr(objective, "monster", None)
            legacy_monster = getattr(legacy_objective_target, "name", None)
            legacy_names = (
                (str(legacy_monster).strip(),)
                if isinstance(legacy_monster, str) and legacy_monster.strip()
                else tuple(getattr(self, "_quest_target_names", ()))
            )
            binding = bind_semantic_combat_target(
                combat_target,
                requirement_id=atom.requirement_id,
                plan_fingerprint=plan.fingerprint,
                legacy_names=legacy_names,
                legacy_specs=(),
            )
            if binding is None:
                self._set_semantic_combat_admission(
                    QuestCombatAdmissionStatus.BLOCKED_UNSAFE,
                    "quest_semantic_combat_target_mismatch",
                )
                self._stop_leveling_unsafe("quest_semantic_combat_target_mismatch")
                return False
            self._quest_target_names = (binding.target,)
            target_level = getattr(legacy_objective_target, "level", None)
            self._quest_target_specs = (
                ((binding.target, target_level),)
                if isinstance(target_level, int) and not isinstance(target_level, bool) and target_level > 0
                else ()
            )
            self._set_semantic_combat_admission(
                QuestCombatAdmissionStatus.ACTIONABLE,
                "semantic_combat_target_bound",
                binding,
            )
            return False
        self._set_semantic_combat_admission(
            QuestCombatAdmissionStatus.WAIT,
            f"semantic_next_atom:{outcome.status.value}",
        )
        if outcome.status is EvaluationStatus.ACTIONABLE:
            self._quest_target_names = ()
            self._quest_target_levels = ()
            self._quest_target_specs = ()
            self._next_quest_refresh_cycle = current_cycle
            self._maybe_start_quest_refresh()
            return False
        self._stop_leveling_unsafe(f"quest_semantic_precombat:{outcome.status.value}:{outcome.reason}")
        return False
