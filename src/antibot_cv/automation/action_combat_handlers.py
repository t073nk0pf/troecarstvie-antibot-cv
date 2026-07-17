"""Physical combat action handlers for the live sink."""

from src.antibot_cv.automation import action_live_sink as live

ACTION_TYPES = frozenset({"attack_visible_target", "click_ability_4", "click_combat_slot", "use_battle_item", "revive_free", "close_resurrection_notice", "click_exit", "click_hunt", "open_hunt"})

json = live.json
time = live.time
skill_mutation_payload = live.skill_mutation_payload
open_location_navigator_fallback = live.open_location_navigator_fallback
parse_catalog_navigation_outcome = live.parse_catalog_navigation_outcome
npc_open_command_payload = live.npc_open_command_payload
parse_npc_open_outcome = live.parse_npc_open_outcome
parse_npc_quest_action_outcome = live.parse_npc_quest_action_outcome
refresh_resource_source_after_use = live.refresh_resource_source_after_use
resource_percent_from_open_result = live.resource_percent_from_open_result
wait_resource_percent_after_use = live.wait_resource_percent_after_use
_compact_injector_message = live._compact_injector_message
_parse_injector_dict = live._parse_injector_dict
_compact_recovery_result = live._compact_recovery_result
_copy_request = live._copy_request
_log_action = live._log_action

def global_browser_injector():
    return live.global_browser_injector()


def handle_action(self, request):
    if request.action_type == 'attack_visible_target':
        injector = global_browser_injector()
        metadata = dict(request.metadata or {})
        payload = {'confirmed': int(metadata.get('confirmed', 1) or 0), 'margin': int(metadata.get('margin', 35) or 0)}
        names = metadata.get('names')
        if isinstance(names, list):
            payload['names'] = names
        allowed_levels = metadata.get('allowed_levels')
        if isinstance(allowed_levels, list):
            payload['allowedLevels'] = allowed_levels
        allowed_bot_ids = metadata.get('allowed_bot_ids')
        if isinstance(allowed_bot_ids, list):
            payload['allowedBotIds'] = allowed_bot_ids
        target_specs = metadata.get('target_specs')
        if isinstance(target_specs, list):
            payload['targetSpecs'] = target_specs
        payload['verifyTimeoutMs'] = 3500
        payload['commandTimeoutMs'] = 6500
        result = self._execute_injector(injector, 'attack_visible_bot', payload, timeout_s=max(0.1, float(metadata.get('timeout_s', 7.0) or 7.0)))
        result_metadata = dict(metadata)
        result_metadata['injector_message'] = _compact_injector_message(result.message)
        result_metadata['injector_client_id'] = result.client_id
        if result.ok:
            try:
                parsed = json.loads(result.message)
                target = parsed.get('target') if isinstance(parsed, dict) else None
                if isinstance(target, dict):
                    result_metadata['bot_id'] = target.get('botId')
                    result_metadata['target_name'] = target.get('name')
                    result_metadata['target_level'] = target.get('level')
                    result_metadata['target_level_source'] = target.get('levelSource')
                    result_metadata['target_world_x'] = target.get('x')
                    result_metadata['target_world_y'] = target.get('y')
                    result_metadata['target_screen_x'] = target.get('screenX')
                    result_metadata['target_screen_y'] = target.get('screenY')
            except Exception:
                pass
            logged_request = _copy_request(request, metadata=result_metadata)
            _log_action(self.logger, 'attack_visible_target_requested', logged_request, dry_run=False)
            return True
        logged_request = _copy_request(request, metadata=result_metadata)
        _log_action(self.logger, 'action_blocked', logged_request, block_reason=f'injector_attack_visible_failed:{_compact_injector_message(result.message)}')
        return False

    if request.action_type in {'click_ability_4', 'click_combat_slot'}:
        metadata = dict(request.metadata or {})
        if metadata.get('use_js_skill'):
            raw_slot = metadata.get('skill_slot', metadata.get('slot_index', 4))
            slot = int(raw_slot) if raw_slot is not None else 4
            payload = skill_mutation_payload(metadata, slot)
            if payload is None:
                _log_action(self.logger, 'action_blocked', request, block_reason='skill_mutation_binding_missing')
                return False
            result = self._execute_injector(global_browser_injector(), 'use_skill_slot', payload, timeout_s=max(0.1, float(metadata.get('timeout_s', 4.5) or 4.5)))
            result_metadata = dict(metadata)
            result_metadata['injector_message'] = _compact_injector_message(result.message)
            result_metadata['injector_client_id'] = result.client_id
            result_metadata['skill_slot'] = slot
            if result.ok:
                try:
                    parsed = json.loads(result.message)
                    if isinstance(parsed, dict):
                        result_metadata['injector_result_message'] = parsed.get('message')
                        result_metadata['fight_path'] = parsed.get('fightPath')
                        result_metadata['fight_href'] = parsed.get('fightHref')
                        ability = parsed.get('ability')
                        if isinstance(ability, dict):
                            result_metadata['ability_id'] = ability.get('id')
                            result_metadata['ability_name'] = ability.get('name')
                            result_metadata['ability_slot'] = ability.get('slot')
                except json.JSONDecodeError:
                    pass
                event_type = f'{request.action_type}_js'
                _log_action(self.logger, event_type, _copy_request(request, metadata=result_metadata), dry_run=False)
                return True
            _log_action(self.logger, 'action_blocked', _copy_request(request, metadata=result_metadata), block_reason=f'injector_use_skill_failed:{_compact_injector_message(result.message)}')
            return False

    if request.action_type == 'use_battle_item':
        metadata = dict(request.metadata or {})
        payload: dict[str, object] = {'kind': str(metadata.get('kind') or ''), 'slots': metadata.get('slots', []), 'names': metadata.get('names', [])}
        if 'item_id' in metadata:
            payload['itemId'] = metadata.get('item_id')
        if 'pre_click_delay_ms' in metadata:
            payload['preClickDelayMs'] = metadata.get('pre_click_delay_ms')
        if 'click_hold_ms' in metadata:
            payload['clickHoldMs'] = metadata.get('click_hold_ms')
        result = self._execute_injector(global_browser_injector(), 'use_battle_item', payload, timeout_s=2.5)
        result_metadata = dict(metadata)
        result_metadata['injector_message'] = _compact_injector_message(result.message)
        result_metadata['injector_client_id'] = result.client_id
        parsed: dict[str, object] | None = None
        try:
            candidate = json.loads(result.message)
            parsed = candidate if isinstance(candidate, dict) else None
        except json.JSONDecodeError:
            parsed = None
        if parsed is not None:
            result_metadata['injector_result_message'] = parsed.get('message')
            result_metadata['fight_path'] = parsed.get('fightPath')
            result_metadata['fight_href'] = parsed.get('fightHref')
            item = parsed.get('item')
            if isinstance(item, dict):
                result_metadata['item_id'] = item.get('id')
                result_metadata['item_name'] = item.get('name')
                result_metadata['item_slot'] = item.get('slot')
            result_metadata['method'] = parsed.get('method')
            result_metadata['slot'] = parsed.get('slot')
            result_metadata['evidence'] = parsed.get('evidence')
        if result.ok:
            _log_action(self.logger, 'use_battle_item_js', _copy_request(request, metadata=result_metadata), dry_run=False)
            return True
        if parsed is not None and parsed.get('message') == 'battle_item_use_ambiguous':
            self.last_ambiguous_action = 'use_battle_item'
            _log_action(self.logger, 'use_battle_item_ambiguous', _copy_request(request, metadata=result_metadata), dry_run=False)
            return False
        _log_action(self.logger, 'action_blocked', _copy_request(request, metadata=result_metadata), block_reason=f'injector_use_battle_item_failed:{_compact_injector_message(result.message)}')
        return False

    if request.action_type == 'revive_free':
        metadata = dict(request.metadata or {})
        result = self._execute_injector(global_browser_injector(), 'revive_free', {'expectedCharacter': metadata.get('expected_character', ''), 'verifyDelayMs': metadata.get('verify_delay_ms', 1000)}, timeout_s=4.0)
        result_metadata = dict(metadata)
        result_metadata['injector_message'] = _compact_injector_message(result.message)
        result_metadata['injector_client_id'] = result.client_id
        parsed: dict[str, object] | None = None
        try:
            candidate = json.loads(result.message)
            parsed = candidate if isinstance(candidate, dict) else None
        except json.JSONDecodeError:
            parsed = None
        if parsed is not None:
            result_metadata['injector_result_message'] = parsed.get('message')
            result_metadata['submitted'] = parsed.get('submitted')
            result_metadata['confirmed'] = parsed.get('confirmed')
            result_metadata['option'] = parsed.get('option')
        if result.ok and parsed is not None and (parsed.get('submitted') is True):
            _log_action(self.logger, 'revive_free_submitted', _copy_request(request, metadata=result_metadata), dry_run=False)
            return True
        _log_action(self.logger, 'action_blocked', _copy_request(request, metadata=result_metadata), block_reason=f'injector_revive_free_failed:{_compact_injector_message(result.message)}')
        return False

    if request.action_type == 'close_resurrection_notice':
        metadata = dict(request.metadata or {})
        result = self._execute_injector(global_browser_injector(), 'close_resurrection_notice', {'verifyDelayMs': metadata.get('verify_delay_ms', 250)}, timeout_s=2.5)
        result_metadata = dict(metadata)
        result_metadata['injector_message'] = _compact_injector_message(result.message)
        result_metadata['injector_client_id'] = result.client_id
        parsed: dict[str, object] | None = None
        try:
            candidate = json.loads(result.message)
            parsed = candidate if isinstance(candidate, dict) else None
        except json.JSONDecodeError:
            parsed = None
        if parsed is not None:
            result_metadata['injector_result_message'] = parsed.get('message')
            result_metadata['closed'] = parsed.get('closed')
            result_metadata['confirmed'] = parsed.get('confirmed')
        if result.ok and parsed is not None and (parsed.get('confirmed') is True):
            _log_action(self.logger, 'resurrection_notice_closed', _copy_request(request, metadata=result_metadata), dry_run=False)
            return True
        _log_action(self.logger, 'action_blocked', _copy_request(request, metadata=result_metadata), block_reason=f'injector_close_resurrection_notice_failed:{_compact_injector_message(result.message)}')
        return False

    if request.action_type in {'click_exit', 'click_hunt'}:
        metadata = dict(request.metadata or {})
        if metadata.get('use_js_open_hunt'):
            result = self._execute_injector(global_browser_injector(), 'open_hunt', {'verifyTimeoutMs': 2000, 'commandTimeoutMs': 5000}, timeout_s=5.5)
            result_metadata = dict(metadata)
            result_metadata['injector_message'] = _compact_injector_message(result.message)
            result_metadata['injector_client_id'] = result.client_id
            request = _copy_request(request, metadata=result_metadata)
            if result.ok:
                _log_action(self.logger, f'{request.action_type}_js_open_hunt', request, dry_run=False)
                return True
            if request.screen_point is None:
                _log_action(self.logger, 'action_blocked', request, block_reason=f'injector_open_hunt_failed:{_compact_injector_message(result.message)}')
                return False

    if request.action_type == 'open_hunt':
        injector = global_browser_injector()
        result = self._execute_injector(injector, 'open_hunt', {'verifyTimeoutMs': 2000, 'commandTimeoutMs': 5000}, timeout_s=5.5)
        if result.ok:
            metadata = dict(request.metadata or {})
            metadata['injector_message'] = _compact_injector_message(result.message)
            metadata['injector_client_id'] = result.client_id
            request = _copy_request(request, metadata=metadata)
            _log_action(self.logger, 'open_hunt_requested', request, dry_run=False)
            return True
        _log_action(self.logger, 'action_blocked', request, block_reason=f'injector_open_hunt_failed:{_compact_injector_message(result.message)}')
        return False
    return None
