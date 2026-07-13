from __future__ import annotations

import json
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
)
from src.antibot_cv.automation.runtime_constants import (
    ATTACK_RETRY_DELAY_MS,
    HUNT_RETRY_DELAY_MS,
    JS_BATTLE_PROBE_INTERVAL_MS,
)
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
from src.antibot_cv.detection.resources import ResourceStatus
from src.antibot_cv.viewport.coordinates import Rect


class CombatRuntimeMixin:
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
            return self._next_live_combat_slot()
        if decision.intent is CombatIntent.USE_SKILL and decision.skill is not None:
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
        items_enabled = item_config.enabled
        item_allow_names = {
            BattleItemKind.HEALTH: tuple(item_config.health_names) if items_enabled else (),
            BattleItemKind.PROWESS: tuple(item_config.prowess_names) if items_enabled else (),
            BattleItemKind.DAMAGE_BOOST: tuple(item_config.damage_boost_names) if items_enabled else (),
        }
        item_allow_slots = {
            BattleItemKind.HEALTH: tuple(item_config.health_slots) if items_enabled else (),
            BattleItemKind.PROWESS: tuple(item_config.prowess_slots) if items_enabled else (),
            BattleItemKind.DAMAGE_BOOST: tuple(item_config.damage_boost_slots) if items_enabled else (),
        }
        items: list[PolicyBattleItem] = []
        raw_items = battle_snapshot.get("items")
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
                    or any(_normalize_phrase(name) == _normalize_phrase(allowed) for allowed in item_allow_names[kind])
                ]
                if len(matched_kinds) != 1:
                    continue
                raw_quantity = _optional_int(raw.get("quantity"))
                ready = raw.get("ready") is True and raw.get("disabled") is not True
                cooldown = _optional_float(raw.get("cooldown"))
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

        hp = int(round(max(0.0, min(100.0, health_percent)) * 100))
        prowess = int(round(max(0.0, min(100.0, prowess_percent)) * 100))
        battle_id = int(self.session.battle_id or 0)
        policy = CombatPolicy(
            skill_slot_allowlist=tuple(allowed_slots),
            item_name_allowlist=item_allow_names,
            item_slot_allowlist=item_allow_slots,
            hp_threshold=max(0.0, min(1.0, item_config.health_use_when_below_percent / 100)),
            prowess_threshold=max(0.0, min(1.0, item_config.prowess_use_when_below_percent / 100)),
            damage_boost_enabled=item_config.enabled and item_config.damage_boost_enabled,
            allow_zero_prowess_slot=allow_zero,
        )
        return policy.decide(
            PolicyBattleSnapshot(
                active=battle_snapshot.get("hasFight") if isinstance(battle_snapshot.get("hasFight"), bool) else None,
                finished=battle_snapshot.get("finished") if isinstance(battle_snapshot.get("finished"), bool) else None,
                turn=1 if battle_snapshot.get("myTurn") is True else 0,
                resources=PolicyBattleResources(hp, 10000, prowess, 10000),
                skills=tuple(abilities),
                items=tuple(items),
                damage_boost_active=self._battle_item_recovery_counts.get((battle_id, "damage_boost"), 0) > 0,
            )
        )

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
        if self.config.dry_run or not config.enabled or self.session.battle_id is None:
            return False
        kind = kind_override or self._battle_item_recovery_kind(status)
        if kind is None:
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
        max_uses = max(1, int(config.max_uses_per_battle))
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
                self._stop_leveling_unsafe("battle_item_result_ambiguous")
            return False
        self._battle_item_recovery_counts[key] = self._battle_item_recovery_counts.get(key, 0) + 1
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
        interval_ms = max(250, int(self.config.leveling.snapshot_interval_ms))
        if not force and self._last_state_snapshot_monotonic is not None:
            elapsed_ms = (now - self._last_state_snapshot_monotonic) * 1000
            if elapsed_ms < interval_ms:
                return self._state_snapshot_cache
        self._last_state_snapshot_monotonic = now
        try:
            from src.antibot_cv.automation.browser_injector import global_browser_injector

            result = global_browser_injector().execute(
                "state_snapshot",
                {"include": ["player", "location", "deathRevive", "battle", "hunt", "quests", "shopInventory"]},
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
        if isinstance(death_data, dict) and death_data.get("resurrectionNoticeAvailable") is True:
            request = ActionRequest(
                "close_resurrection_notice",
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                dry_run=self.config.dry_run,
                metadata={
                    "verify_delay_ms": 250,
                    "reason": "post_revive_confirmation",
                    "snapshot_id": self._current_state_snapshot_id,
                },
            )
            if not self.action_executor.execute(request):
                return self._stop_leveling_unsafe("resurrection_notice_close_failed")
            return True
        return self._handle_leveling_death(death_data)

    def _handle_battle_wait(self, frame: np.ndarray) -> None:
        if not self.config.dry_run and self._sync_battle_from_injector(frame):
            return
        if not self.config.dry_run:
            self._handle_live_failed_visible_attack(frame)
            return
        result = self.battle_detector.detect(frame)
        for signal in result.signals:
            self.logger.log_event(
                "battle_signal",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                detector_id=signal.signal_id,
                detector_confidence=signal.confidence,
                frame_hash=self._last_frame_hash,
            )
        if not result.detected:
            if self._click_attack_if_available(frame):
                return
            self._retry_target_click_if_due(frame)
            return
        self.session.mark_battle_detected()
        self.logger.log_event(
            "battle_detected",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            detector_confidence=result.confidence,
            frame_hash=self._last_frame_hash,
        )
        self.latencies.start("battle_to_ability4")
        self._safe_transition(GameState.BATTLE_ACTIVE)
        self._selected_target = None
        self._last_attack_click_monotonic = None
        self._last_combat_click_monotonic = None
        self._combat_slot_sequence_index = 0
        self._use_ability4(frame, result.panel_bbox)

    def _click_attack_if_available(self, frame: np.ndarray) -> bool:
        if not self.session.can_click_attack():
            return False
        if self._last_attack_click_monotonic is not None:
            elapsed_ms = (time.monotonic() - self._last_attack_click_monotonic) * 1000
            if elapsed_ms < ATTACK_RETRY_DELAY_MS:
                return False
        result = self.attack_detector.detect(frame)
        if not result.detected or result.center is None:
            return False
        attack_attempt = self.session.attack_click_count()
        point = _attack_click_point(result, attack_attempt)
        request = ActionRequest(
            "click_attack",
            frame_point=point,
            screen_point=self.mapper.frame_to_pynput(point),
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={
                "attack_click_attempt": attack_attempt,
                "detector_confidence": result.confidence,
                "frame_hash": self._last_frame_hash,
            },
        )
        self.logger.log_event(
            "attack_detected",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            attack_click_attempt=attack_attempt,
            detector_id="attack_button",
            detector_confidence=result.confidence,
            x_frame=point.x,
            y_frame=point.y,
            button_x_frame=None if result.bbox is None else result.bbox.x,
            button_y_frame=None if result.bbox is None else result.bbox.y,
            button_width=None if result.bbox is None else result.bbox.width,
            button_height=None if result.bbox is None else result.bbox.height,
            frame_hash=self._last_frame_hash,
        )
        if self.action_executor.execute(request):
            self.logger.log_event(
                "attack_clicked",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                attack_click_attempt=attack_attempt,
                detector_id="attack_button",
                detector_confidence=result.confidence,
                x_frame=point.x,
                y_frame=point.y,
                frame_hash=self._last_frame_hash,
            )
            self._last_attack_click_monotonic = time.monotonic()
            return True
        return False

    def _handle_battle_active(self, frame: np.ndarray, result: object | None = None) -> None:
        if not self.config.dry_run:
            self._use_ability4(frame, None)
            return
        battle_result = result if result is not None else self.battle_detector.detect(frame)
        if not getattr(battle_result, "detected", False):
            battle_end = self.battle_end_detector.detect(frame)
            if battle_end.detected:
                self._safe_transition(GameState.WAIT_BATTLE_END, reason="battle_end_without_ability")
                self._handle_battle_end(frame, battle_end)
            return
        self._use_ability4(frame, battle_result.panel_bbox)

    def _sync_battle_from_injector(self, frame: np.ndarray, *, force_probe: bool = False) -> bool:
        snapshot = self._battle_snapshot_via_injector(force=force_probe)
        return self._sync_battle_from_snapshot(frame, snapshot)

    def _sync_battle_from_snapshot(self, frame: np.ndarray, snapshot: dict[str, object] | None) -> bool:
        if not self._injector_snapshot_has_active_battle(snapshot):
            return False
        if self.session.battle_id is None:
            self.session.new_battle()
        self.session.mark_battle_detected()
        self.logger.log_event(
            "battle_detected",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            detector_id="browser_injector",
            detector_confidence=1.0,
            fight_path=snapshot.get("fightPath"),
            fight_href=snapshot.get("fightHref"),
            frame_hash=self._last_frame_hash,
        )
        self.latencies.start("battle_to_ability4")
        if self.state_machine.state != GameState.BATTLE_ACTIVE:
            if not self._sync_transition(GameState.BATTLE_ACTIVE, "js_battle_detected"):
                return False
        self._selected_target = None
        self._last_attack_click_monotonic = None
        self._last_combat_click_monotonic = None
        self._combat_slot_sequence_index = 0
        self._use_ability4(frame, None)
        return True

    def _battle_snapshot_via_injector(self, *, force: bool = False) -> dict[str, object] | None:
        if (
            self.config.dry_run
            or not self.browser_client_id
            or not isinstance(self.action_executor.sink, LiveMacActionSink)
        ):
            return None
        now = time.monotonic()
        if not force and self._last_js_battle_probe_monotonic is not None:
            elapsed_ms = (now - self._last_js_battle_probe_monotonic) * 1000
            if elapsed_ms < JS_BATTLE_PROBE_INTERVAL_MS:
                return self._last_battle_snapshot_cache
        self._last_js_battle_probe_monotonic = now
        try:
            from src.antibot_cv.automation.browser_injector import global_browser_injector

            result = global_browser_injector().execute(
                "battle_snapshot",
                timeout_s=2.5,
                client_id=self.browser_client_id,
            )
        except Exception as exc:
            self.logger.log_event(
                "js_battle_probe_failed",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                reason=str(exc),
                frame_hash=self._last_frame_hash,
            )
            return None
        if not result.ok:
            self.logger.log_event(
                "js_battle_probe_failed",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                reason=result.message,
                frame_hash=self._last_frame_hash,
            )
            return None
        try:
            parsed = json.loads(result.message)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        self._last_battle_snapshot_cache = parsed
        self._last_battle_snapshot_success_monotonic = time.monotonic()
        self._record_battle_outcome(parsed)
        return parsed

    def _record_battle_outcome(self, snapshot: dict[str, object] | None) -> None:
        if not isinstance(snapshot, dict) or snapshot.get("finished") is not True:
            return
        outcome = str(snapshot.get("outcome") or "unknown").strip().lower()
        if outcome not in {"victory", "defeat", "unknown"}:
            outcome = "unknown"
        self.session.mark_battle_outcome(outcome)
        self.logger.log_event(
            "battle_outcome_observed",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            outcome=outcome,
            evidence=snapshot.get("outcomeEvidence"),
        )

    def _injector_snapshot_has_active_battle(self, snapshot: dict[str, object] | None) -> bool:
        if not snapshot:
            return False
        abilities = snapshot.get("abilities")
        has_combat_ability = (
            isinstance(abilities, list)
            and any(isinstance(ability, dict) and isinstance(ability.get("id"), int) and ability.get("id") < 0 for ability in abilities)
        )
        return bool(snapshot.get("hasFight") and snapshot.get("useSkillAvailable") and has_combat_ability)

    def _injector_snapshot_has_inactive_battle(self, snapshot: dict[str, object] | None) -> bool:
        return isinstance(snapshot, dict) and snapshot.get("hasFight") is False

    def _retry_target_click_if_due(self, frame: np.ndarray) -> None:
        if self._selected_target is None:
            return
        max_attempts = 1 + len(self.config.target.click_retry_offsets)
        if self._target_click_attempts >= max_attempts:
            return
        if self._last_target_click_monotonic is None:
            return
        elapsed_ms = (time.monotonic() - self._last_target_click_monotonic) * 1000
        if elapsed_ms < self.config.target.click_retry_delay_ms:
            return
        if not self._refresh_selected_target(frame):
            self.logger.log_event(
                "target_reacquire_missed",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                target_id=self._selected_target.target_id,
                target_click_attempt=self._target_click_attempts,
                frame_hash=self._last_frame_hash,
            )
            return
        self.logger.log_event(
            "target_click_retry",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            target_id=self._selected_target.target_id,
            target_click_attempt=self._target_click_attempts,
            frame_hash=self._last_frame_hash,
        )
        self._click_selected_target()

    def _refresh_selected_target(self, frame: np.ndarray) -> bool:
        if self._selected_target is None:
            return False
        previous = self._selected_target
        candidates = self.target_locator.locate(frame)
        if not candidates:
            return False
        tracks = self.tracker.update(candidates)
        same_target = [candidate for candidate in candidates if candidate.target_id == previous.target_id]
        candidates_to_rank = same_target or candidates
        best = min(
            candidates_to_rank,
            key=lambda candidate: (
                (candidate.label_center.x - previous.label_center.x) ** 2
                + (candidate.label_center.y - previous.label_center.y) ** 2,
                -candidate.confidence,
            ),
        )
        self._selected_target = best
        matched_track = next(
            (
                track
                for track in tracks
                if track.target_id == best.target_id
                and track.label_center.x == best.label_center.x
                and track.label_center.y == best.label_center.y
            ),
            None,
        )
        self.logger.log_event(
            "target_reacquired",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            detector_confidence=best.confidence,
            target_id=best.target_id,
            track_id=None if matched_track is None else matched_track.track_id,
            x_frame=best.interaction_point.x,
            y_frame=best.interaction_point.y,
            target_click_attempt=self._target_click_attempts,
            frame_hash=self._last_frame_hash,
        )
        return True

    def _use_ability4(self, frame: np.ndarray, panel_bbox: Rect | None) -> None:
        if self.state_machine.state != GameState.BATTLE_ACTIVE or not self.session.can_use_ability4():
            return
        if not self.config.dry_run:
            slot_index = self._live_combat_slot_for_current_resources(frame)
            if slot_index is None:
                return
            if not self._use_skill_slot_via_injector("click_ability_4", slot_index, "ability4_js_intended"):
                fallback_slot = max(0, int(self.config.combat.low_resource_fallback_slot_index))
                if (
                    slot_index != fallback_slot
                    and self._live_zero_fallback_allowed(frame)
                    and self._use_skill_slot_via_injector(
                        "click_ability_4", fallback_slot, "ability4_js_fallback_intended"
                    )
                ):
                    slot_index = fallback_slot
                else:
                    return
            latency = self.latencies.finish("battle_to_ability4")
            self.logger.log_event(
                "latency",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                latency_type="battle_to_ability4",
                latency_ms=latency,
                reaction_latency_ms=latency,
            )
            self._safe_transition(GameState.ABILITY_4_USED)
            self._safe_transition(GameState.WAIT_BATTLE_END)
            return
        slot = self.ability_detector.slot4(frame, panel_bbox)
        if slot is None:
            self.logger.log_event("error", state=self.state_machine.state.value, reason="slot4_not_found")
            return
        ready_ok = slot.ready or self.config.dry_run or not self.config.ability4.require_ready_confirmation
        if slot.confidence < self.config.ability4.slot_confidence_threshold or not ready_ok:
            self.logger.log_event(
                "action_blocked",
                state=self.state_machine.state.value,
                action_type="click_ability_4",
                battle_id=self.session.battle_id,
                block_reason="ability4_not_ready",
            )
            return
        screen_point = self.mapper.frame_to_pynput(slot.center)
        request = ActionRequest(
            "click_ability_4",
            frame_point=slot.center,
            screen_point=screen_point,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={
                "detector_confidence": slot.confidence,
                "frame_hash": self._last_frame_hash,
                "pre_click_delay_ms": self.config.ability4.pre_click_delay_ms,
                "click_hold_ms": self.config.ability4.click_hold_ms,
            },
        )
        self.logger.log_event(
            "ability4_intended",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            x_frame=slot.center.x,
            y_frame=slot.center.y,
            detector_confidence=slot.confidence,
            frame_hash=self._last_frame_hash,
        )
        if self.action_executor.execute(request):
            latency = self.latencies.finish("battle_to_ability4")
            self.logger.log_event(
                "latency",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                latency_type="battle_to_ability4",
                latency_ms=latency,
                reaction_latency_ms=latency,
            )
            self._safe_transition(GameState.ABILITY_4_USED)
            self._safe_transition(GameState.WAIT_BATTLE_END)

    def _configured_live_combat_slots(self) -> tuple[int, ...]:
        sequence = tuple(slot for slot in self.config.combat.slot_sequence if slot >= 0)
        if sequence:
            return sequence
        return (max(0, int(self.config.combat.slot_index or 1) - 1),)

    def _live_zero_fallback_allowed(self, frame: np.ndarray) -> bool:
        if not self.config.combat.low_resource_fallback_enabled:
            return False
        status = self._detect_current_resources(frame)
        prowess = status.prowess.percent
        threshold = max(0.0, float(self.config.combat.low_resource_fallback_percent))
        return prowess is not None and prowess <= threshold

    def _next_live_combat_slot(self) -> int:
        sequence = self._configured_live_combat_slots()
        slot = sequence[self._combat_slot_sequence_index % len(sequence)]
        self._combat_slot_sequence_index += 1
        return slot

    def _use_skill_slot_via_injector(self, action_type: str, slot_index: int, event_type: str) -> bool:
        if action_type == "click_combat_slot":
            pre_click_delay_ms = self.config.combat.pre_click_delay_ms
            click_hold_ms = self.config.combat.click_hold_ms
        else:
            pre_click_delay_ms = self.config.ability4.pre_click_delay_ms
            click_hold_ms = self.config.ability4.click_hold_ms
        request = ActionRequest(
            action_type,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={
                "use_js_skill": True,
                "skill_slot": slot_index,
                "slot_index": slot_index,
                "frame_hash": self._last_frame_hash,
                "pre_click_delay_ms": pre_click_delay_ms,
                "click_hold_ms": click_hold_ms,
            },
        )
        self.logger.log_event(
            event_type,
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            action_type=action_type,
            js_slot=slot_index,
            frame_hash=self._last_frame_hash,
        )
        return self.action_executor.execute(request)

    def _handle_battle_end(self, frame: np.ndarray, result: object | None = None) -> None:
        if not self.config.dry_run:
            self._handle_live_battle_end(frame)
            return
        result = result if result is not None else self.battle_end_detector.detect(frame)
        if not result.detected:
            if not self.config.dry_run:
                battle_snapshot = self._battle_snapshot_via_injector(force=True)
                if self._injector_snapshot_has_active_battle(battle_snapshot):
                    self._continue_battle_combat(frame, None)
                    return
                if self._injector_snapshot_has_inactive_battle(battle_snapshot):
                    visible_snapshot = self._visible_hunt_snapshot_via_injector()
                    if self._live_snapshot_on_hunt_page(visible_snapshot):
                        self.logger.log_event(
                            "hunt_return_confirmed",
                            state=self.state_machine.state.value,
                            cycle_id=self.session.cycle_id,
                            battle_id=self.session.battle_id,
                            reason="battle_end_js_hunt_page",
                            frame_hash=self._last_frame_hash,
                        )
                        self._complete_or_mark_incomplete_after_hunt_return(frame, reason="battle_end_js_hunt_page")
                        return
                    if self._live_snapshot_on_fight_page(visible_snapshot):
                        self._log_live_page_wait(
                            "battle_end_inactive_fight_page",
                            visible_snapshot,
                            reason="inactive_fight_page",
                        )
                        if self._exit_battle_via_injector("inactive_fight_page"):
                            return
            battle = self.battle_detector.detect(frame)
            if battle.detected:
                self._continue_battle_combat(frame, battle.panel_bbox)
                return
            if self._hunt_return_confirmed(frame):
                self.logger.log_event(
                    "hunt_return_confirmed",
                    state=self.state_machine.state.value,
                    cycle_id=self.session.cycle_id,
                    battle_id=self.session.battle_id,
                    reason="battle_end_skipped",
                    frame_hash=self._last_frame_hash,
                )
                self._complete_or_mark_incomplete_after_hunt_return(frame, reason="battle_end_skipped")
            return
        self.logger.log_event(
            "battle_end_detected",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            detector_id="battle_end",
            detector_confidence=result.confidence,
            frame_hash=self._last_frame_hash,
        )
        if self.config.dry_run:
            self.session.mark_battle_outcome("victory")
        if self.state_machine.state == GameState.WAIT_BATTLE_END:
            self.latencies.start("battle_end_to_exit")
            self._safe_transition(GameState.BATTLE_END_DETECTED)
        if not self.session.can_click_exit():
            return
        if result.exit_button_bbox is None and self.config.dry_run:
            return
        center = None if result.exit_button_bbox is None else result.exit_button_bbox.center
        self.logger.log_event(
            "exit_detected",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            detector_confidence=result.confidence,
            x_frame=None if center is None else center.x,
            y_frame=None if center is None else center.y,
        )
        request = ActionRequest(
            "click_exit",
            frame_point=center,
            screen_point=None if center is None else self.mapper.frame_to_pynput(center),
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={
                "detector_confidence": result.confidence,
                "frame_hash": self._last_frame_hash,
                "use_js_open_hunt": not self.config.dry_run,
            },
        )
        if self.action_executor.execute(request):
            latency = self.latencies.finish("battle_end_to_exit")
            self.logger.log_event("latency", latency_type="battle_end_to_exit", latency_ms=latency, reaction_latency_ms=latency)
            self._safe_transition(GameState.EXIT_BATTLE)
            self._safe_transition(GameState.STATISTICS_WAIT)

    def _exit_battle_via_injector(self, reason: str) -> bool:
        if self.session.battle_id is None or not self.session.can_click_exit():
            return False
        self.logger.log_event(
            "exit_detected",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            detector_confidence=1.0,
            reason=reason,
            frame_hash=self._last_frame_hash,
        )
        request = ActionRequest(
            "click_exit",
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={
                "detector_confidence": 1.0,
                "frame_hash": self._last_frame_hash,
                "use_js_open_hunt": not self.config.dry_run,
                "reason": reason,
            },
        )
        if not self.action_executor.execute(request):
            return False
        self.latencies.finish("battle_end_to_exit")
        if self.state_machine.state == GameState.WAIT_BATTLE_END:
            self._safe_transition(GameState.BATTLE_END_DETECTED, reason=reason)
        if self.state_machine.state == GameState.BATTLE_END_DETECTED:
            self._safe_transition(GameState.EXIT_BATTLE, reason=reason)
        if self.state_machine.state == GameState.EXIT_BATTLE:
            self._safe_transition(GameState.STATISTICS_WAIT, reason=reason)
        return True

    def _handle_live_battle_end(self, frame: np.ndarray) -> None:
        battle_snapshot = self._battle_snapshot_via_injector(force=True)
        if self._injector_snapshot_has_active_battle(battle_snapshot):
            self._continue_battle_combat(frame, None)
            return
        visible_snapshot = self._visible_hunt_snapshot_via_injector()
        if self._live_snapshot_on_hunt_page(visible_snapshot):
            self._log_live_page_wait("hunt_return_confirmed", visible_snapshot, reason="live_battle_end_hunt_page")
            self._complete_or_mark_incomplete_after_hunt_return(frame, reason="live_battle_end_hunt_page")
            return
        if self._live_snapshot_on_fight_page(visible_snapshot):
            self._log_live_page_wait("battle_end_inactive_fight_page", visible_snapshot, reason="live_battle_end_fight_page")
            self._exit_battle_via_injector("live_battle_end_fight_page")

    def _continue_battle_combat(self, frame: np.ndarray, panel_bbox: Rect | None) -> bool:
        if not self.config.combat.enabled or self.session.battle_id is None:
            return False
        if self._last_combat_click_monotonic is not None:
            elapsed_ms = (time.monotonic() - self._last_combat_click_monotonic) * 1000
            if elapsed_ms < self.config.combat.click_interval_ms:
                return False
        slot_index = max(1, int(self.config.combat.slot_index or 4))
        if not self.config.dry_run:
            slot_index = self._live_combat_slot_for_current_resources(frame)
            if slot_index is None:
                return False
            if self._use_skill_slot_via_injector("click_combat_slot", slot_index, "combat_slot_js_intended"):
                self._last_combat_click_monotonic = time.monotonic()
                return True
            fallback_slot = max(0, int(self.config.combat.low_resource_fallback_slot_index))
            if (
                slot_index != fallback_slot
                and self._live_zero_fallback_allowed(frame)
                and self._use_skill_slot_via_injector(
                    "click_combat_slot", fallback_slot, "combat_slot_js_fallback_intended"
                )
            ):
                self._last_combat_click_monotonic = time.monotonic()
                return True
            return False
        slots = self.ability_detector.detect_slots(frame, panel_bbox)
        if len(slots) < slot_index:
            self.logger.log_event(
                "combat_slot_missing",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                slot_index=slot_index,
                frame_hash=self._last_frame_hash,
            )
            return False
        slot = slots[slot_index - 1]
        ready_ok = slot.ready or self.config.dry_run or not self.config.combat.require_ready_confirmation
        if slot.confidence < self.config.combat.slot_confidence_threshold or not ready_ok:
            self.logger.log_event(
                "action_blocked",
                state=self.state_machine.state.value,
                action_type="click_combat_slot",
                battle_id=self.session.battle_id,
                block_reason="combat_slot_not_ready",
                slot_index=slot.index,
                detector_confidence=slot.confidence,
            )
            return False
        request = ActionRequest(
            "click_combat_slot",
            frame_point=slot.center,
            screen_point=self.mapper.frame_to_pynput(slot.center),
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={
                "slot_index": slot.index,
                "detector_confidence": slot.confidence,
                "frame_hash": self._last_frame_hash,
                "pre_click_delay_ms": self.config.combat.pre_click_delay_ms,
                "click_hold_ms": self.config.combat.click_hold_ms,
            },
        )
        self.logger.log_event(
            "combat_slot_intended",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            slot_index=slot.index,
            x_frame=slot.center.x,
            y_frame=slot.center.y,
            detector_confidence=slot.confidence,
            frame_hash=self._last_frame_hash,
        )
        if self.action_executor.execute(request):
            self._last_combat_click_monotonic = time.monotonic()
            return True
        return False

    def _handle_statistics(self, frame: np.ndarray, result: object | None = None) -> None:
        if not self.config.dry_run:
            self._handle_live_statistics_wait(frame)
            return
        result = result if result is not None else self.statistics_detector.detect(frame)
        if not result.detected:
            if self._hunt_return_confirmed(frame):
                self.logger.log_event(
                    "hunt_return_confirmed",
                    state=self.state_machine.state.value,
                    cycle_id=self.session.cycle_id,
                    battle_id=self.session.battle_id,
                    reason="statistics_skipped",
                    frame_hash=self._last_frame_hash,
                )
                self._complete_or_mark_incomplete_after_hunt_return(frame, reason="statistics_skipped")
            elif _game_shell_present(frame):
                self._open_hunt_from_game_shell(frame)
                self._state_entered_monotonic = time.monotonic()
            return
        self.logger.log_event(
            "statistics_detected",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            detector_id="statistics",
            detector_confidence=result.confidence,
            frame_hash=self._last_frame_hash,
        )
        if self.state_machine.state == GameState.STATISTICS_WAIT:
            self.latencies.start("statistics_to_hunt")
            self._safe_transition(GameState.STATISTICS_DETECTED)
        self._click_hunt_from_statistics(result, transition_to_cooldown=True)

    def _click_hunt_from_statistics(self, result: object, *, transition_to_cooldown: bool) -> bool:
        if not self.session.can_click_hunt():
            return False
        if result.hunt_button_bbox is None and self.config.dry_run:
            return False
        center = None if result.hunt_button_bbox is None else result.hunt_button_bbox.center
        self.logger.log_event(
            "hunt_detected",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            detector_confidence=result.confidence,
            x_frame=None if center is None else center.x,
            y_frame=None if center is None else center.y,
        )
        request = ActionRequest(
            "click_hunt",
            frame_point=center,
            screen_point=None if center is None else self.mapper.frame_to_pynput(center),
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={
                "click_count": 2,
                "detector_confidence": result.confidence,
                "frame_hash": self._last_frame_hash,
                "use_js_open_hunt": not self.config.dry_run,
            },
        )
        if self.action_executor.execute(request):
            self._last_hunt_click_monotonic = time.monotonic()
            if transition_to_cooldown:
                latency = self.latencies.finish("statistics_to_hunt")
                self.logger.log_event("latency", latency_type="statistics_to_hunt", latency_ms=latency, reaction_latency_ms=latency)
                self._safe_transition(GameState.RETURN_TO_HUNT)
                self._safe_transition(GameState.COOLDOWN)
            return True
        return False

    def _handle_cooldown(self, frame: np.ndarray) -> None:
        if not self.config.dry_run:
            self._handle_live_cooldown(frame)
            return
        if self.battle_detector.detect(frame).detected or self.battle_end_detector.detect(frame).detected:
            return
        statistics = self.statistics_detector.detect(frame)
        if statistics.detected:
            if self._hunt_retry_due() and statistics.hunt_button_bbox is not None and self.session.can_click_hunt():
                self.logger.log_event(
                    "hunt_return_retry",
                    state=self.state_machine.state.value,
                    cycle_id=self.session.cycle_id,
                    battle_id=self.session.battle_id,
                    hunt_click_attempt=self.session.hunt_click_count(),
                    detector_confidence=statistics.confidence,
                    frame_hash=self._last_frame_hash,
                )
                self._click_hunt_from_statistics(statistics, transition_to_cooldown=False)
            return
        if not self._hunt_return_confirmed(frame):
            if _game_shell_present(frame):
                self._open_hunt_from_game_shell(frame)
            else:
                self._recover_unknown_screen(frame)
            self.logger.log_event(
                "hunt_return_waiting",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                frame_hash=self._last_frame_hash,
            )
            return
        self._complete_or_mark_incomplete_after_hunt_return(frame, reason="hunt_return_confirmed")

    def _handle_live_statistics_wait(self, frame: np.ndarray) -> None:
        visible_snapshot = self._visible_hunt_snapshot_via_injector()
        if self._live_snapshot_on_hunt_page(visible_snapshot):
            self._log_live_page_wait("hunt_return_confirmed", visible_snapshot, reason="live_statistics_hunt_page")
            self._complete_or_mark_incomplete_after_hunt_return(frame, reason="live_statistics_hunt_page")
            return
        if self._live_snapshot_on_fight_page(visible_snapshot):
            battle_snapshot = self._battle_snapshot_via_injector(force=True)
            if self._injector_snapshot_has_active_battle(battle_snapshot):
                self._continue_battle_combat(frame, None)
                return
            self._log_live_page_wait("battle_end_inactive_fight_page", visible_snapshot, reason="live_statistics_fight_page")
            self._exit_battle_via_injector("live_statistics_fight_page")
            return
        if self._live_snapshot_on_area_error(visible_snapshot):
            self._log_live_page_wait("live_area_error_waiting", visible_snapshot, reason="live_statistics_area_error")
            return
        self._open_hunt_from_game_shell(frame)

    def _handle_live_cooldown(self, frame: np.ndarray) -> bool:
        visible_snapshot = self._visible_hunt_snapshot_via_injector()
        if self._live_snapshot_on_hunt_page(visible_snapshot):
            self._log_live_page_wait("hunt_return_confirmed", visible_snapshot, reason="live_cooldown_hunt_page")
            self._complete_or_mark_incomplete_after_hunt_return(frame, reason="live_cooldown_hunt_page")
            return True
        if self._live_snapshot_on_fight_page(visible_snapshot):
            battle_snapshot = self._battle_snapshot_via_injector(force=True)
            if self._injector_snapshot_has_active_battle(battle_snapshot):
                self._continue_battle_combat(frame, None)
            else:
                self._exit_battle_via_injector("live_cooldown_fight_page")
            return True
        self._open_hunt_from_game_shell(frame)
        return False

    def _complete_or_mark_incomplete_after_hunt_return(self, frame: np.ndarray, *, reason: str) -> None:
        self._last_hunt_click_monotonic = None
        if self._route_resume_target_name:
            resume_target = self._route_resume_target_name
            self._route_resume_target_name = None
            self.session.reset_cycle_attempt()
            self._reset_location_context()
            if not _same_location_name(self.current_location_name, resume_target):
                if self.state_machine.state is not GameState.LOCATION_SEARCH:
                    self._safe_transition(GameState.LOCATION_SEARCH, reason="route_battle_interruption_resolved")
                self._start_location_route(
                    resume_target,
                    kind="post_battle_route_resume",
                    reason="post_battle_route_resume",
                )
                return
        if not self.session.can_complete_cycle(require_victory=not self.config.dry_run):
            self.logger.log_event(
                "cycle_incomplete",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                battle_id=self.session.battle_id,
                reason=reason,
                battles_detected=self.session.battles_detected,
                ability4_actions=self.session.ability4_actions,
                combat_actions=self.session.combat_actions,
                battle_outcome=self.session.cycle_battle_outcome,
                frame_hash=self._last_frame_hash,
            )
            self.session.mark_incomplete_cycle()
            self.session.mark_recovery()
            self._reset_location_context()
            self._safe_transition(GameState.LOCATION_SEARCH, reason="cycle_incomplete")
            return
        completed_cycle_id = self.session.cycle_id
        if self.session.completed_cycles + 1 < self.session.requested_cycles:
            self._use_item_recovery_after_cycle(frame, reason=reason)
        self.session.complete_cycle()
        self.logger.log_event(
            "cycle_completed",
            state=self.state_machine.state.value,
            cycle_id=completed_cycle_id,
            completed_cycles=self.session.completed_cycles,
            reason=reason,
            frame_hash=self._last_frame_hash,
        )
        if self.session.completed_cycles >= self.session.requested_cycles:
            self.logger.log_event(
                "session_stopped",
                state=self.state_machine.state.value,
                cycle_id=self.session.cycle_id,
                reason="max_cycles",
            )
            self._safe_transition(GameState.STOPPED, reason="max_cycles")
        elif self.state_machine.state != GameState.LOCATION_SEARCH and self.state_machine.can_transition(GameState.LOCATION_SEARCH):
            self._safe_transition(GameState.LOCATION_SEARCH, reason=reason)
            if not self._resources_allow_search(frame):
                return
        elif not self._resources_allow_search(frame):
            return
        else:
            self._safe_transition(GameState.LOCATION_SEARCH, reason=reason)


    def _hunt_retry_due(self) -> bool:
        if self._last_hunt_click_monotonic is None:
            return True
        elapsed_ms = (time.monotonic() - self._last_hunt_click_monotonic) * 1000
        return elapsed_ms >= HUNT_RETRY_DELAY_MS

    def _hunt_return_confirmed(self, frame: np.ndarray) -> bool:
        return _location_map_present(frame, self.config.viewport.direction_pad_roi)
