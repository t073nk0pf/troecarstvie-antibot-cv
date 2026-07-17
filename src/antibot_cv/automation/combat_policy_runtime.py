from __future__ import annotations

import json
import random
import time

import numpy as np

from src.antibot_cv.automation.actions import ActionRequest, LiveMacActionSink
from src.antibot_cv.automation.combat_policy import (
    AvailableSkill as PolicySkill,
    BattleItem as PolicyBattleItem,
    BattleItemKind,
    BattleResources as PolicyBattleResources,
    BattleSnapshot as PolicyBattleSnapshot,
    CombatIntent,
    CombatPolicy,
    CombatDecision as PolicyCombatDecision,
)
from src.antibot_cv.automation.combat_skill_mutation import (
    bind_skill_mutation,
    refresh_skill_mutation_binding,
)
from src.antibot_cv.automation.runtime_constants import (
    ATTACK_RETRY_DELAY_MS,
    HUNT_RETRY_DELAY_MS,
    JS_BATTLE_PROBE_INTERVAL_MS,
)
from src.antibot_cv.automation.spellbook_combat_adapter import decide_current_spellbook_action
from src.antibot_cv.automation.runtime_helpers import (
    attack_click_point as _attack_click_point,
    game_shell_present as _game_shell_present,
    location_map_present as _location_map_present,
    normalize_phrase as _normalize_phrase,
    optional_float as _optional_float,
    optional_int as _optional_int,
    same_location_name as _same_location_name,
)
from src.antibot_cv.automation.state_machine import GameState
from src.antibot_cv.automation.state_snapshot_profiles import sections_for_state
from src.antibot_cv.detection.resources import ResourceStatus
from src.antibot_cv.viewport.coordinates import Rect


class CombatPolicyRuntimeMixin:
    """Select combat actions and enforce resource and death safety policy."""

    def _live_combat_slot_for_current_resources(self, frame: np.ndarray) -> int | None:
        if not self.config.resources.enabled:
            return self._next_live_combat_slot()
        status = self._detect_current_resources(frame)
        missing = self._resource_missing(status)
        if missing:
            self._log_resource_status("combat_resources_missing", status, missing=missing)
            return self._next_live_combat_slot()
        battle_snapshot = self._last_battle_snapshot_cache
        last_battle = self._last_battle_snapshot_success_monotonic
        if battle_snapshot is None or last_battle is None or time.monotonic() - last_battle > 2.0:
            battle_snapshot = self._battle_snapshot_via_injector(force=True)
        if not isinstance(battle_snapshot, dict):
            self.logger.log_event(
                "combat_policy_skipped",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                reason="battle_snapshot_unavailable",
            )
            bound_name = str(
                self._bound_character_name or self.current_character_name or ""
            ).strip().casefold()
            if bound_name == "v3g45":
                decision = self._combat_policy_decision(status, {})
                if decision.intent is CombatIntent.STOP_UNSAFE:
                    self._stop_leveling_unsafe(f"combat_policy:{decision.reason}")
                return None
            return self._legacy_live_combat_slot(frame, status)
        decision = self._combat_policy_decision(status, battle_snapshot)
        self.logger.log_event(
            "combat_policy_decision",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            intent=decision.intent.value,
            reason=decision.reason,
            skill_slot=None if decision.skill is None else decision.skill.slot,
            item_name=None if decision.item is None else decision.item.name,
            item_slot=None if decision.item is None else decision.item.slot,
        )
        if decision.intent is CombatIntent.USE_ITEM and decision.item is not None:
            kind = decision.item.kind.value if isinstance(decision.item.kind, BattleItemKind) else str(decision.item.kind)
            if self._try_battle_item_recovery(
                frame,
                status,
                kind_override=kind,
                selected_slot=decision.item.slot,
                selected_name=decision.item.name,
            ):
                return None
            if self.state_machine.state == GameState.STOPPED:
                return None
            return None
        if decision.intent is CombatIntent.USE_SKILL and decision.skill is not None:
            self._pending_skill_expected_damage = float(decision.skill.damage)
            self._combat_slot_sequence_index += 1
            return decision.skill.slot
        if decision.intent is CombatIntent.STOP_UNSAFE:
            self._stop_leveling_unsafe(f"combat_policy:{decision.reason}")
        return None

    def _legacy_live_combat_slot(self, frame: np.ndarray, status: ResourceStatus) -> int | None:
        """Compatibility path; every resulting JS action still verifies its postcondition."""
        if self._try_battle_item_recovery(frame, status):
            return None
        low = self._resource_gaps(status, recover=False)
        if not low:
            self._log_resource_status("combat_resources_ok", status)
            return self._next_live_combat_slot()
        prowess = status.prowess.percent
        fallback_threshold = max(0.0, float(self.config.combat.low_resource_fallback_percent))
        if (
            self.config.resources.wait_in_battle_when_low
            and self.config.combat.low_resource_fallback_enabled
            and prowess is not None
            and prowess <= fallback_threshold
        ):
            fallback_slot = max(0, int(self.config.combat.low_resource_fallback_slot_index))
            self._log_resource_status(
                "combat_low_resource_fallback",
                status,
                low_resources=low,
                fallback_slot=fallback_slot,
                force=True,
            )
            return fallback_slot
        self._log_resource_status("combat_low_resource_continuing", status, low_resources=low, force=True)
        return self._next_live_combat_slot()

    def _combat_policy_decision(
        self,
        status: ResourceStatus,
        battle_snapshot: dict[str, object],
    ) -> object:
        health_percent = status.health.percent
        prowess_percent = status.prowess.percent
        if health_percent is None or prowess_percent is None:
            return CombatPolicy().decide(
                PolicyBattleSnapshot(
                    active=None,
                    finished=None,
                    turn=0,
                    resources=PolicyBattleResources(None, None, None, None),
                )
            )
        sequence = self._configured_live_combat_slots()
        start = self._combat_slot_sequence_index % len(sequence)
        rotated = sequence[start:] + sequence[:start]
        allowed_slots = list(dict.fromkeys(rotated))
        abilities: list[PolicySkill] = []
        raw_abilities = battle_snapshot.get("abilities")
        if isinstance(raw_abilities, list):
            for raw in raw_abilities:
                if not isinstance(raw, dict):
                    continue
                ability_id = _optional_int(raw.get("id"))
                if ability_id is None or ability_id >= 0:
                    continue
                slot = _optional_int(raw.get("slot"))
                if slot is None or slot not in allowed_slots:
                    continue
                raw_ready = raw.get("ready")
                raw_cooldown = _optional_float(raw.get("cooldown"))
                ready = raw_ready is True or (
                    not self.config.combat.require_ready_confirmation
                    and raw.get("disabled") is not True
                    and (raw_cooldown is None or raw_cooldown == 0)
                )
                priority = len(rotated) - rotated.index(slot)
                abilities.append(
                    PolicySkill(
                        name=str(raw.get("name") or f"slot:{slot}"),
                        slot=slot,
                        damage=float(priority),
                        ready=ready,
                        cooldown=0 if ready and raw_cooldown is None else raw_cooldown,
                    )
                )
        if not abilities and battle_snapshot.get("useSkillAvailable") is True and not self.config.combat.require_ready_confirmation:
            slot = rotated[0]
            abilities.append(PolicySkill(f"slot:{slot}", slot, 1.0, True, 0))
        fallback_threshold = max(0.0, float(self.config.combat.low_resource_fallback_percent))
        fallback_slot = max(0, int(self.config.combat.low_resource_fallback_slot_index))
        allow_zero = (
            self.config.combat.low_resource_fallback_enabled
            and prowess_percent <= fallback_threshold
        )
        if allow_zero and all(skill.slot != fallback_slot for skill in abilities):
            abilities.append(PolicySkill("zero_resource_fallback", fallback_slot, 1000.0, True, 0))
        if allow_zero and fallback_slot not in allowed_slots:
            allowed_slots.append(fallback_slot)

        item_config = self.config.battle_item_recovery
        resource_items_enabled = item_config.enabled
        damage_boost_enabled = item_config.damage_boost_enabled
        item_allow_names = {
            BattleItemKind.HEALTH: tuple(item_config.health_names) if resource_items_enabled else (),
            BattleItemKind.PROWESS: tuple(item_config.prowess_names) if resource_items_enabled else (),
            BattleItemKind.DAMAGE_BOOST: tuple(item_config.damage_boost_names) if damage_boost_enabled else (),
        }
        item_allow_slots = {
            BattleItemKind.HEALTH: tuple(item_config.health_slots) if resource_items_enabled else (),
            BattleItemKind.PROWESS: tuple(item_config.prowess_slots) if resource_items_enabled else (),
            BattleItemKind.DAMAGE_BOOST: tuple(item_config.damage_boost_slots) if damage_boost_enabled else (),
        }
        items: list[PolicyBattleItem] = []
        raw_items = battle_snapshot.get("items")
        item_readiness: dict[tuple[int, str], tuple[bool, float | None]] = {}
        if isinstance(raw_abilities, list):
            for raw in raw_abilities:
                if not isinstance(raw, dict):
                    continue
                ability_id = _optional_int(raw.get("id"))
                slot = _optional_int(raw.get("slot"))
                name = str(raw.get("name") or "").strip()
                if ability_id is None or ability_id <= 0 or slot is None or not name:
                    continue
                ready = raw.get("ready") is True and raw.get("disabled") is not True
                cooldown = _optional_float(raw.get("cooldown"))
                if ready and cooldown is None:
                    cooldown = 0
                item_readiness[(slot, _normalize_phrase(name))] = (ready, cooldown)
        if isinstance(raw_items, list):
            for raw in raw_items:
                if not isinstance(raw, dict):
                    continue
                slot = _optional_int(raw.get("slot"))
                name = str(raw.get("name") or "").strip()
                if slot is None or not name:
                    continue
                matched_kinds = [
                    kind
                    for kind in BattleItemKind
                    if slot in item_allow_slots[kind]
                    or any(
                        _normalize_phrase(allowed) in _normalize_phrase(name)
                        for allowed in item_allow_names[kind]
                        if _normalize_phrase(allowed)
                    )
                ]
                if len(matched_kinds) != 1:
                    continue
                raw_quantity = _optional_int(raw.get("quantity"))
                readiness = item_readiness.get((slot, _normalize_phrase(name)))
                ready = (
                    readiness[0]
                    if readiness is not None
                    else raw.get("ready") is True and raw.get("disabled") is not True
                )
                cooldown = readiness[1] if readiness is not None else _optional_float(raw.get("cooldown"))
                if ready and cooldown is None:
                    cooldown = 0
                items.append(
                    PolicyBattleItem(
                        name=name,
                        slot=slot,
                        kind=matched_kinds[0],
                        count=raw_quantity if raw_quantity is not None else (1 if ready else 0),
                        ready=ready,
                        cooldown=cooldown,
                    )
                )

        effective_item_allow_names = {
            kind: tuple(dict.fromkeys((*item_allow_names[kind], *(item.name for item in items if item.kind is kind))))
            for kind in BattleItemKind
        }

        hp = int(round(max(0.0, min(100.0, health_percent)) * 100))
        prowess = int(round(max(0.0, min(100.0, prowess_percent)) * 100))
        battle_id = int(self.session.battle_id or 0)
        policy = CombatPolicy(
            skill_slot_allowlist=tuple(allowed_slots),
            item_name_allowlist=effective_item_allow_names,
            item_slot_allowlist=item_allow_slots,
            hp_threshold=max(0.0, min(1.0, item_config.health_use_when_below_percent / 100)),
            prowess_threshold=max(0.0, min(1.0, item_config.prowess_use_when_below_percent / 100)),
            damage_boost_enabled=damage_boost_enabled,
            damage_boost_use_chance_percent=item_config.damage_boost_use_chance_percent,
            allow_zero_prowess_slot=allow_zero,
        )
        generic_decision = policy.decide(
            PolicyBattleSnapshot(
                active=battle_snapshot.get("hasFight") if isinstance(battle_snapshot.get("hasFight"), bool) else None,
                finished=battle_snapshot.get("finished") if isinstance(battle_snapshot.get("finished"), bool) else None,
                turn=1 if battle_snapshot.get("myTurn") is True else 0,
                resources=PolicyBattleResources(hp, 10000, prowess, 10000),
                skills=tuple(abilities),
                items=tuple(items),
                damage_boost_active=self._damage_boost_armed_battle_id == battle_id,
                damage_boost_roll=random.random(),
            )
        )
        self._spellbook_battle_state.begin(self.session.battle_id)
        spellbook = decide_current_spellbook_action(
            battle_snapshot,
            prowess=int(round(prowess_percent)),
            anti_spam_blocked_slots=frozenset(
                self._spellbook_battle_state.confirmed_setup_slots
                | self._spellbook_battle_state.blocked_setup_slots
            ),
            character_name=self._bound_character_name or self.current_character_name,
        )
        spellbook = self._spellbook_battle_state.bound(spellbook)
        if spellbook.matched and spellbook.decision is not None:
            if generic_decision.intent is CombatIntent.USE_ITEM:
                self._spellbook_battle_state.pending_slot = None
                selected_decision = generic_decision
            elif (
                spellbook.decision.intent is CombatIntent.WAIT
                and spellbook.decision.reason == "no_explicit_ready_action"
            ):
                # The spellbook profile has no ready action according to its
                # advisory cooldown evidence.  Preserve the established fast
                # combat behavior: submit the next allowlisted strike and let
                # the game reject it if it is still unavailable.
                self._spellbook_battle_state.pending_slot = None
                selected_decision = generic_decision
            else:
                selected_decision = spellbook.decision
        else:
            selected_decision = generic_decision
        self._pending_skill_mutation_binding = bind_skill_mutation(
            battle_snapshot,
            selected_decision,
        )
        if (
            selected_decision.intent is CombatIntent.USE_SKILL
            and self._pending_skill_mutation_binding is None
            and not self.config.dry_run
        ):
            self._spellbook_battle_state.pending_slot = None
            return PolicyCombatDecision(
                CombatIntent.STOP_UNSAFE,
                "skill_mutation_binding_missing_or_unsafe",
            )
        return selected_decision

    def _try_battle_item_recovery(
        self,
        frame: np.ndarray,
        status: ResourceStatus,
        *,
        kind_override: str | None = None,
        selected_slot: int | None = None,
        selected_name: str | None = None,
    ) -> bool:
        config = self.config.battle_item_recovery
        if self.config.dry_run or self.session.battle_id is None:
            return False
        kind = kind_override or self._battle_item_recovery_kind(status)
        if kind is None:
            return False
        if kind in {"health", "prowess"} and not config.enabled:
            return False
        if kind == "health":
            slots = tuple(config.health_slots)
            names = tuple(config.health_names)
            threshold: float | None = config.health_use_when_below_percent
        elif kind == "prowess":
            slots = tuple(config.prowess_slots)
            names = tuple(config.prowess_names)
            threshold = config.prowess_use_when_below_percent
        elif kind == "damage_boost" and config.damage_boost_enabled:
            slots = tuple(config.damage_boost_slots)
            names = tuple(config.damage_boost_names)
            threshold = None
        else:
            return False
        if selected_slot is not None:
            slots = (selected_slot,)
        if selected_name:
            names = (selected_name,)
        if not slots and not names:
            self.logger.log_event(
                "battle_item_recovery_skipped",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                kind=kind,
                reason="no_slots_or_names",
                health_percent=status.health.percent,
                prowess_percent=status.prowess.percent,
                frame_hash=self._last_frame_hash,
            )
            return False
        now = time.monotonic()
        cooldown_ms = max(0, int(config.cooldown_ms))
        if self._last_battle_item_recovery_monotonic is not None:
            elapsed_ms = (now - self._last_battle_item_recovery_monotonic) * 1000
            if elapsed_ms < cooldown_ms:
                return False
        key = (int(self.session.battle_id), kind)
        # The configured cap protects emergency HP/prowess consumption.  A
        # damage sphere is a per-strike prefix and must not be reduced to one
        # use by the popup's "банок за бой" limit.  Keep a high bounded
        # guard for corrupted/repeating battle state.
        max_uses = 100 if kind == "damage_boost" else max(1, int(config.max_uses_per_battle))
        if self._battle_item_recovery_counts.get(key, 0) >= max_uses:
            return False
        self.logger.log_event(
            "battle_item_recovery_intended",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            kind=kind,
            slots=list(slots),
            names=list(names),
            threshold=threshold,
            health_percent=status.health.percent,
            prowess_percent=status.prowess.percent,
            frame_hash=self._last_frame_hash,
        )
        request = ActionRequest(
            "use_battle_item",
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={
                "kind": kind,
                "slots": list(slots),
                "names": list(names),
                "threshold": threshold,
                "health_percent": status.health.percent,
                "prowess_percent": status.prowess.percent,
                "frame_hash": self._last_frame_hash,
                "pre_click_delay_ms": config.pre_click_delay_ms,
                "click_hold_ms": config.click_hold_ms,
            },
        )
        if not self.action_executor.execute(request):
            self._last_battle_item_recovery_monotonic = now
            if getattr(self.action_executor.sink, "last_ambiguous_action", None) == "use_battle_item":
                # A damage sphere is a best-effort prefix for a strike.  It is
                # normal for the game to reject a repeat while its effect is
                # active; do not freeze the battle on an inconclusive second
                # request.  Arm exactly the next selected strike, which will
                # clear this marker on completion.  HP/prowess potions retain
                # their strict confirmation requirement.
                if kind == "damage_boost":
                    self._damage_boost_armed_battle_id = int(self.session.battle_id)
                    self.logger.log_event(
                        "battle_damage_boost_unconfirmed_continuing",
                        state=self.state_machine.state.value,
                        cycle_id=self.session.cycle_id,
                        battle_id=self.session.battle_id,
                        item_slot=selected_slot,
                        item_name=selected_name,
                    )
                    return True
                self._stop_leveling_unsafe("battle_item_result_ambiguous")
            return False
        self._battle_item_recovery_counts[key] = self._battle_item_recovery_counts.get(key, 0) + 1
        if kind == "damage_boost":
            self._damage_boost_armed_battle_id = int(self.session.battle_id)
        self._last_battle_item_recovery_monotonic = now
        return True

    def _battle_item_recovery_kind(self, status: ResourceStatus) -> str | None:
        config = self.config.battle_item_recovery
        if status.health.percent is not None and status.health.percent <= float(config.health_use_when_below_percent):
            return "health"
        if status.prowess.percent is not None and status.prowess.percent <= float(config.prowess_use_when_below_percent):
            return "prowess"
        return None

    def _state_snapshot_via_injector(self, *, force: bool = False) -> dict[str, object] | None:
        now = time.monotonic()
        include = sections_for_state(self.state_machine.state, getattr(self, "current_page_kind", None))
        interval_ms = max(250, int(self.config.leveling.snapshot_interval_ms))
        if not force and self._last_state_snapshot_monotonic is not None:
            elapsed_ms = (now - self._last_state_snapshot_monotonic) * 1000
            if elapsed_ms < interval_ms and getattr(self, "_state_snapshot_cache_sections", None) == include:
                return self._state_snapshot_cache
        self._last_state_snapshot_monotonic = now
        try:
            from src.antibot_cv.automation.browser_injector import global_browser_injector

            result = global_browser_injector().execute(
                "state_snapshot",
                {"include": list(include)},
                timeout_s=2.5,
                client_id=self.browser_client_id,
            )
        except Exception as exc:
            self.logger.log_event(
                "state_js_snapshot_failed",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                reason=str(exc),
                frame_hash=self._last_frame_hash,
            )
            return self._state_snapshot_cache
        if not result.ok:
            self.logger.log_event(
                "state_js_snapshot_failed",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                reason=result.message,
                injector_client_id=result.client_id,
                frame_hash=self._last_frame_hash,
            )
            return self._state_snapshot_cache
        try:
            payload = json.loads(result.message)
        except json.JSONDecodeError:
            return self._state_snapshot_cache
        if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
            return self._state_snapshot_cache
        self._state_snapshot_cache = payload
        self._state_snapshot_cache_sections = include
        self._last_state_snapshot_success_monotonic = time.monotonic()
        self._last_state_snapshot_client_id = result.client_id or self.browser_client_id
        return payload

    def _observe_death_guard(self, *, force: bool = False) -> bool:
        if self.config.dry_run:
            return False
        snapshot = self._state_snapshot_via_injector(force=True) if force else self._state_snapshot_via_injector()
        if not isinstance(snapshot, dict):
            return self.state_machine.state in {GameState.DEAD, GameState.REVIVE_PENDING}
        self._current_state_snapshot_id = str(snapshot.get("snapshotId") or "") or None
        sections = snapshot.get("sections")
        if not isinstance(sections, dict):
            return self.state_machine.state in {GameState.DEAD, GameState.REVIVE_PENDING}
        player_section = sections.get("player")
        player = player_section.get("data") if isinstance(player_section, dict) else None
        if isinstance(player, dict):
            name = str(player.get("name") or "").strip()
            level = _optional_int(player.get("level"))
            xp_percent = _optional_float(player.get("xpPercent"))
            if name:
                if self._bind_or_reject_character(name):
                    return True
                self.current_character_name = name
            if level is not None:
                self.current_level = level
            if xp_percent is not None:
                self.current_xp_percent = xp_percent
        death_section = sections.get("deathRevive")
        death_data = death_section.get("data") if isinstance(death_section, dict) else None
        location_section = sections.get("location")
        location = location_section.get("data") if isinstance(location_section, dict) else None
        self._update_location_tracking(location, death_data, player)
        if (
            isinstance(death_data, dict)
            and death_data.get("dead") is False
            and death_data.get("resurrectionNoticeAvailable") is True
        ):
            self._log_recovery_phase("revive_confirmed", reason="resurrection_notice_available")
            request = ActionRequest(
                "close_resurrection_notice",
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                dry_run=self.config.dry_run,
                metadata={
                    "verify_delay_ms": 250,
                    "reason": "post_revive_confirmation",
                    "snapshot_id": self._current_state_snapshot_id,
                    "recovery_id": self._active_recovery_id,
                },
            )
            if not self.action_executor.execute(request):
                return self._stop_leveling_unsafe("resurrection_notice_close_failed")
            self._log_recovery_phase("notice_closed", reason="resurrection_notice_closed")
            return True
        return self._handle_leveling_death(death_data)
