"""Physical quest action handlers for the live sink."""

from src.antibot_cv.automation import action_live_sink as live

ACTION_TYPES = frozenset({"open_quests", "open_quest_catalog", "open_active_quest_page", "open_exact_npc", "npc_quest_action", "open_quest_navigator"})

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
    if request.action_type == 'open_quests':
        result = self._execute_injector(global_browser_injector(), 'open_quests', {'verifyTimeoutMs': 2000, 'commandTimeoutMs': 5000}, timeout_s=5.5)
        metadata = dict(request.metadata or {})
        metadata['injector_message'] = _compact_injector_message(result.message)
        metadata['injector_client_id'] = result.client_id
        logged_request = _copy_request(request, metadata=metadata)
        if result.ok:
            _log_action(self.logger, 'open_quests_requested', logged_request, dry_run=False)
            return True
        _log_action(self.logger, 'action_blocked', logged_request, block_reason=f'injector_open_quests_failed:{_compact_injector_message(result.message)}')
        return False

    if request.action_type == 'open_quest_catalog':
        metadata = dict(request.metadata or {})
        raw_page = metadata.get('page', 0)
        page = int(raw_page) if isinstance(raw_page, int) and (not isinstance(raw_page, bool)) else -1
        if page < 0 or page > 100:
            _log_action(self.logger, 'action_blocked', request, block_reason='quest_catalog_page_invalid')
            return False
        result = self._execute_injector(global_browser_injector(), 'open_quest_catalog', {'page': page, 'verifyTimeoutMs': 5000, 'commandTimeoutMs': 8000}, timeout_s=8.5)
        outcome = parse_catalog_navigation_outcome(result_ok=bool(result.ok), message=result.message, client_id=result.client_id, destination=f'/user_quest.php?mode=avail&page={page}')
        self.last_catalog_navigation_outcome = outcome
        result_metadata = {**metadata, 'page': page, 'catalog_navigation_outcome': outcome.status.value, 'injector_message': outcome.reason, 'injector_client_id': outcome.client_id}
        logged_request = _copy_request(request, metadata=result_metadata)
        if outcome.dispatched:
            _log_action(self.logger, 'open_quest_catalog_requested', logged_request, dry_run=False)
            return True
        _log_action(self.logger, 'action_blocked', logged_request, block_reason=f'injector_open_quest_catalog_failed:{outcome.reason}')
        return False

    if request.action_type == 'open_active_quest_page':
        metadata = dict(request.metadata or {})
        raw_page = metadata.get('page', 0)
        page = int(raw_page) if isinstance(raw_page, int) and (not isinstance(raw_page, bool)) else -1
        if page < 0 or page > 100:
            _log_action(self.logger, 'action_blocked', request, block_reason='quest_active_page_invalid')
            return False
        result = self._execute_injector(global_browser_injector(), 'open_active_quest_page', {'page': page, 'verifyTimeoutMs': 5000, 'commandTimeoutMs': 7000}, timeout_s=7.5)
        outcome = parse_catalog_navigation_outcome(result_ok=bool(result.ok), message=result.message, client_id=result.client_id, destination=f'/user_quest.php?mode=started&page={page}')
        self.last_active_catalog_navigation_outcome = outcome
        result_metadata = {**metadata, 'page': page, 'active_catalog_navigation_outcome': outcome.status.value, 'injector_message': outcome.reason, 'injector_client_id': outcome.client_id}
        logged_request = _copy_request(request, metadata=result_metadata)
        if outcome.dispatched:
            _log_action(self.logger, 'open_active_quest_page_requested', logged_request, dry_run=False)
            return True
        _log_action(self.logger, 'action_blocked', logged_request, block_reason=f'injector_open_active_quest_page_failed:{outcome.reason}')
        return False

    if request.action_type == 'open_exact_npc':
        metadata = dict(request.metadata or {})
        payload = npc_open_command_payload(metadata)
        self.last_npc_open_outcome = None
        if payload is None:
            _log_action(self.logger, 'action_blocked', request, block_reason='npc_identity_invalid')
            return False
        result = self._execute_injector(global_browser_injector(), 'open_exact_npc', payload, timeout_s=6.0)
        result_metadata = {**metadata, 'injector_message': _compact_injector_message(result.message), 'injector_client_id': result.client_id}
        logged_request = _copy_request(request, metadata=result_metadata)
        outcome = parse_npc_open_outcome(result_ok=result.ok, message=result.message, client_id=result.client_id)
        self.last_npc_open_outcome = outcome
        if outcome.dispatched:
            _log_action(self.logger, 'exact_npc_open_requested', logged_request, dry_run=False)
            return True
        _log_action(self.logger, 'action_blocked', logged_request, block_reason=f'injector_open_exact_npc_failed:{_compact_injector_message(result.message)}')
        return False

    if request.action_type == 'npc_quest_action':
        metadata = dict(request.metadata or {})
        self.last_npc_quest_action_outcome = None
        expected_snapshot_id = str(metadata.get('expected_snapshot_id') or '').strip()
        npc_id = str(metadata.get('npc_id') or '').strip()
        expected_npc_name = str(metadata.get('expected_name') or metadata.get('giver_name') or '').strip()
        quest_id = str(metadata.get('quest_id') or '').strip()
        expected_title = str(metadata.get('expected_title') or '').strip()
        action = str(metadata.get('action') or '').strip().lower()
        expected_ref = str(metadata.get('expected_ref') or '').strip()
        expected_point_id = str(metadata.get('expected_point_id') or '').strip()
        expected_text = str(metadata.get('expected_text') or '').strip()
        valid = expected_snapshot_id.startswith('npc-dialog-') and 0 < len(expected_snapshot_id) <= 120 and npc_id.isdecimal() and (int(npc_id) >= 0) and len(expected_npc_name) <= 180 and quest_id.isdecimal() and (int(quest_id) > 0) and (action == 'done' or 0 < len(expected_title) <= 220) and (action in {'open', 'answer', 'accept', 'done'}) and (action == 'open' or (0 < len(expected_text) <= 1200 and (action == 'accept' or (action == 'done' and expected_point_id.isdecimal() and (int(expected_point_id) > 0)) or (expected_ref.isdecimal() and int(expected_ref) > 0))))
        if not valid:
            _log_action(self.logger, 'action_blocked', request, block_reason='npc_quest_action_invalid')
            return False
        result = self._execute_injector(global_browser_injector(), 'npc_quest_action', {'expectedSnapshotId': expected_snapshot_id, 'npcId': npc_id, 'expectedName': expected_npc_name or None, 'questId': quest_id, 'expectedTitle': expected_title, 'action': action, 'expectedRef': expected_ref if action == 'answer' else None, 'expectedPointId': expected_point_id if action == 'done' else None, 'expectedText': expected_text if action in {'answer', 'accept', 'done'} else None}, timeout_s=3.0)
        result_metadata = {**metadata, 'injector_message': _compact_injector_message(result.message), 'injector_client_id': result.client_id}
        logged_request = _copy_request(request, metadata=result_metadata)
        outcome = parse_npc_quest_action_outcome(result_ok=result.ok, message=result.message, client_id=result.client_id)
        self.last_npc_quest_action_outcome = outcome
        if outcome.dispatched:
            _log_action(self.logger, 'npc_quest_action_submitted', logged_request, dry_run=False)
            return True
        _log_action(self.logger, 'action_blocked', logged_request, block_reason=f'injector_npc_quest_action_failed:{_compact_injector_message(result.message)}')
        return False

    if request.action_type == 'open_quest_navigator':
        metadata = dict(request.metadata or {})
        injector = global_browser_injector()
        result = self._execute_injector(injector, 'open_quest_navigator', {'target': metadata.get('target', ''), **({'linkLabel': metadata.get('link_label')} if metadata.get('link_label') else {}), **({'expectedQuestId': metadata.get('quest_id')} if metadata.get('quest_id') else {})}, timeout_s=2.5)
        result_metadata = {**metadata, 'injector_message': _compact_injector_message(result.message), 'injector_client_id': result.client_id}
        logged_request = _copy_request(request, metadata=result_metadata)
        if result.ok:
            _log_action(self.logger, 'quest_navigator_opened', logged_request, dry_run=False)
            return True
        fallback = open_location_navigator_fallback(result, metadata=result_metadata, execute=lambda: self._execute_injector(injector, 'open_location_navigator', {}, timeout_s=2.5), compact_message=_compact_injector_message)
        if fallback is not None:
            fallback_request = _copy_request(request, metadata=fallback.metadata)
            if fallback.ok:
                _log_action(self.logger, 'quest_navigator_opened', fallback_request, dry_run=False)
            else:
                _log_action(self.logger, 'action_blocked', fallback_request, block_reason=fallback.block_reason)
            return fallback.ok
        _log_action(self.logger, 'action_blocked', logged_request, block_reason=f'injector_open_quest_navigator_failed:{_compact_injector_message(result.message)}')
        return False
    return None
