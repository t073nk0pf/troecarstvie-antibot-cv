"""Physical navigation action handlers for the live sink."""

from src.antibot_cv.automation import action_live_sink as live

ACTION_TYPES = frozenset({"enter_instance", "open_area", "open_location_navigator", "navigator_select_target", "navigator_go", "location_route_step", "viewport_move", "open_url", "browser_back"})

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
_route_confirmation_reason = live._route_confirmation_reason

def global_browser_injector():
    return live.global_browser_injector()


def handle_action(self, request):
    if request.action_type == 'enter_instance':
        injector = global_browser_injector()
        metadata = dict(request.metadata or {})
        expected_name = str(metadata.get('expected_name') or '').strip()
        expected_snapshot_id = str(metadata.get('expected_snapshot_id') or '').strip()
        if not expected_name or not expected_snapshot_id:
            _log_action(self.logger, 'action_blocked', request, block_reason='instance_identity_missing')
            return False
        result = self._execute_injector(injector, 'enter_instance', {'expectedName': expected_name, 'expectedSnapshotId': expected_snapshot_id, 'navigationDelayMs': max(25, min(250, int(metadata.get('navigation_delay_ms') or 75)))}, timeout_s=3.5)
        parsed = _parse_injector_dict(result.message)
        submitted = bool(result.ok and isinstance(parsed, dict) and (parsed.get('message') == 'instance_entry_submitted') and (parsed.get('submitted') is True))
        logged_request = _copy_request(request, metadata={**metadata, 'injector_message': _compact_injector_message(result.message), 'injector_client_id': result.client_id})
        if submitted:
            _log_action(self.logger, 'instance_entry_requested', logged_request, dry_run=False)
            return True
        _log_action(self.logger, 'action_blocked', logged_request, block_reason=f'injector_enter_instance_failed:{_compact_injector_message(result.message)}')
        return False

    if request.action_type == 'open_area':
        result = self._execute_injector(global_browser_injector(), 'open_area', {'commandTimeoutMs': 4000}, timeout_s=5.0)
        result_metadata = {**dict(request.metadata or {}), 'injector_message': _compact_injector_message(result.message), 'injector_client_id': result.client_id}
        logged_request = _copy_request(request, metadata=result_metadata)
        try:
            candidate = json.loads(result.message)
            parsed = candidate if isinstance(candidate, dict) else None
        except json.JSONDecodeError:
            parsed = None
        if result.ok and parsed is not None and (parsed.get('message') in {'area_opened', 'area_already_open'}):
            _log_action(self.logger, 'open_area_requested', logged_request, dry_run=False)
            return True
        if parsed is not None and parsed.get('message') == 'area_open_unconfirmed':
            time.sleep(0.5)
            confirmation = self._execute_injector(global_browser_injector(), 'location_route_snapshot', {}, timeout_s=2.5)
            try:
                confirmation_candidate = json.loads(confirmation.message)
                confirmation_parsed = confirmation_candidate if isinstance(confirmation_candidate, dict) else None
            except json.JSONDecodeError:
                confirmation_parsed = None
            location = confirmation_parsed.get('location') if isinstance(confirmation_parsed, dict) else None
            if confirmation.ok and isinstance(confirmation_parsed, dict) and (confirmation_parsed.get('message') == 'location_route_snapshot') and (confirmation_parsed.get('pageKind') == 'area') and isinstance(location, dict) and bool(str(location.get('id') or '').strip()) and bool(str(location.get('semanticName') or '').strip()):
                confirmed_metadata = {**result_metadata, 'area_confirmation': 'location_snapshot_after_delayed_navigation', 'area_confirmation_message': _compact_injector_message(confirmation.message), 'area_confirmation_client_id': confirmation.client_id}
                _log_action(self.logger, 'open_area_requested', _copy_request(request, metadata=confirmed_metadata), dry_run=False)
                return True
        _log_action(self.logger, 'action_blocked', logged_request, block_reason=f'injector_open_area_failed:{_compact_injector_message(result.message)}')
        return False

    if request.action_type == 'open_location_navigator':
        metadata = dict(request.metadata or {})
        result = self._execute_injector(global_browser_injector(), 'open_location_navigator', {}, timeout_s=2.5)
        result_metadata = {**metadata, 'injector_message': _compact_injector_message(result.message), 'injector_client_id': result.client_id}
        logged_request = _copy_request(request, metadata=result_metadata)
        if result.ok:
            _log_action(self.logger, 'location_navigator_opened', logged_request, dry_run=False)
            return True
        _log_action(self.logger, 'action_blocked', logged_request, block_reason=f'injector_open_location_navigator_failed:{_compact_injector_message(result.message)}')
        return False

    if request.action_type == 'navigator_select_target':
        metadata = dict(request.metadata or {})
        navigator_client_id = str(metadata.get('navigator_client_id') or '')
        target = str(metadata.get('target') or '').strip()
        if not navigator_client_id or not target:
            reason = 'navigator_client_missing' if not navigator_client_id else 'navigator_target_missing'
            _log_action(self.logger, 'action_blocked', request, block_reason=reason)
            return False
        injector = global_browser_injector()
        search_delay_ms = max(250, min(15000, int(metadata.get('search_delay_ms', 250) or 250)))
        route_delay_ms = max(100, min(8000, int(metadata.get('route_delay_ms', 350) or 350)))
        command_timeout_ms = max(2350, min(25000, max(search_delay_ms + route_delay_ms + 2000, int(metadata.get('command_timeout_ms', 0) or 0))))
        payload = {'target': target, 'kind': str(metadata.get('target_kind') or 'location'), 'searchDelayMs': search_delay_ms, 'routeDelayMs': route_delay_ms, 'commandTimeoutMs': command_timeout_ms}

        def select_target_once():
            return self._execute_injector(injector, 'navigator_select_target', payload, timeout_s=command_timeout_ms / 1000 + 0.5, client_id_override=navigator_client_id)
        result = select_target_once()
        parsed: dict[str, object] | None = None
        try:
            candidate = json.loads(result.message)
            parsed = candidate if isinstance(candidate, dict) else None
        except json.JSONDecodeError:
            parsed = None
        initial_message = _compact_injector_message(result.message)
        retryable_unique_result = parsed is not None and parsed.get('message') == 'navigator_target_missing_in_section' and (parsed.get('exactCandidateCount') == 1) and (parsed.get('candidateCount') == 0)
        route_snapshot = parsed.get('after') if parsed is not None else None
        retryable_route_result = parsed is not None and parsed.get('message') == 'navigator_route_not_ready' and isinstance(route_snapshot, dict) and (str(route_snapshot.get('target') or '').strip().casefold() == target.casefold())
        if (retryable_unique_result or retryable_route_result) and int(metadata.get('retry_limit', 1) or 0) > 0:
            retry_delay_ms = max(0, min(2000, int(metadata.get('retry_delay_ms', 500) or 0)))
            retry_request = _copy_request(request, metadata={**metadata, 'injector_message': initial_message, 'injector_client_id': result.client_id, 'retry_delay_ms': retry_delay_ms, 'retry_limit': 1})
            _log_action(self.logger, 'navigator_target_selection_retry', retry_request, dry_run=False)
            if retry_delay_ms:
                time.sleep(retry_delay_ms / 1000)
            if retryable_unique_result:
                result = select_target_once()
            else:
                result = self._execute_injector(injector, 'navigator_snapshot', {}, timeout_s=2.5, client_id_override=navigator_client_id)
            try:
                candidate = json.loads(result.message)
                parsed = candidate if isinstance(candidate, dict) else None
            except json.JSONDecodeError:
                parsed = None
        result_metadata = {**metadata, 'injector_message': _compact_injector_message(result.message), 'injector_client_id': result.client_id}
        if initial_message != result_metadata['injector_message']:
            result_metadata['initial_injector_message'] = initial_message
        logged_request = _copy_request(request, metadata=result_metadata)
        route_snapshot_confirmed = result.ok and parsed is not None and (parsed.get('message') == 'navigator_snapshot') and (str(parsed.get('target') or '').strip().casefold() == target.casefold()) and (parsed.get('currentLocation') is True or parsed.get('hasRoute') is True or (isinstance(parsed.get('routeTransitions'), int) and (not isinstance(parsed.get('routeTransitions'), bool)) and (int(parsed['routeTransitions']) > 0)))
        if result.ok and parsed is not None and (parsed.get('message') in {'navigator_target_selected', 'navigator_target_already_selected'} or route_snapshot_confirmed):
            _log_action(self.logger, 'navigator_target_selected', logged_request, dry_run=False)
            return True
        _log_action(self.logger, 'action_blocked', logged_request, block_reason=f'injector_navigator_select_target_failed:{_compact_injector_message(result.message)}')
        return False

    if request.action_type == 'navigator_go':
        metadata = dict(request.metadata or {})
        navigator_client_id = str(metadata.get('navigator_client_id') or '')
        if not navigator_client_id:
            _log_action(self.logger, 'action_blocked', request, block_reason='navigator_client_missing')
            return False
        injector = global_browser_injector()
        expected_transitions = metadata.get('route_transitions')
        parent_route_before = None
        parent_route_before_result = None
        if self.browser_client_id and isinstance(expected_transitions, int) and (not isinstance(expected_transitions, bool)) and (expected_transitions > 0):
            parent_route_before_result = self._execute_injector(injector, 'location_route_snapshot', {}, timeout_s=2.5, client_id_override=self.browser_client_id)
            parent_route_before = _parse_injector_dict(parent_route_before_result.message)
        result = self._execute_injector(injector, 'navigator_go', {'expectedTarget': metadata.get('target', '')}, timeout_s=3.0, client_id_override=navigator_client_id)
        result_metadata = {**metadata, 'injector_message': _compact_injector_message(result.message), 'injector_client_id': result.client_id}
        logged_request = _copy_request(request, metadata=result_metadata)
        parsed: dict[str, object] | None = None
        try:
            candidate = json.loads(result.message)
            parsed = candidate if isinstance(candidate, dict) else None
        except json.JSONDecodeError:
            parsed = None
        if not result.ok and result.message == 'injector_ack_timeout' and self.browser_client_id:
            confirmation = None
            confirmation_parsed = None
            route_confirmation_reason = 'parent_route_after_unconfirmed'
            for attempt in range(20):
                confirmation = self._execute_injector(injector, 'location_route_snapshot', {}, timeout_s=2.5, client_id_override=self.browser_client_id)
                confirmation_parsed = _parse_injector_dict(confirmation.message) if confirmation.ok else None
                route_confirmation_reason = _route_confirmation_reason(parent_route_before, confirmation_parsed, expected_transitions)
                if route_confirmation_reason != 'parent_route_after_unconfirmed':
                    break
                if attempt < 19:
                    time.sleep(0.2)
            assert confirmation is not None
            if route_confirmation_reason in {'confirmed_changed_connected_route', 'confirmed_connected_route_after_unconfirmed_before'}:
                confirmed_metadata = {**result_metadata, 'route_confirmation': 'parent_route_snapshot_after_ack_timeout', 'route_confirmation_before_message': _compact_injector_message(parent_route_before_result.message if parent_route_before_result is not None else ''), 'route_confirmation_message': _compact_injector_message(confirmation.message), 'route_confirmation_client_id': confirmation.client_id}
                _log_action(self.logger, 'navigator_go_requested', _copy_request(request, metadata=confirmed_metadata), dry_run=False)
                return True
            result_metadata = {**result_metadata, 'route_confirmation_rejected': route_confirmation_reason, 'route_confirmation_before_message': _compact_injector_message(parent_route_before_result.message if parent_route_before_result is not None else ''), 'route_confirmation_message': _compact_injector_message(confirmation.message), 'route_confirmation_client_id': confirmation.client_id}
            logged_request = _copy_request(request, metadata=result_metadata)
        if result.ok and parsed is not None and (parsed.get('submitted') is True or parsed.get('message') == 'navigator_already_at_target'):
            _log_action(self.logger, 'navigator_go_requested', logged_request, dry_run=False)
            return True
        _log_action(self.logger, 'action_blocked', logged_request, block_reason=f'injector_navigator_go_failed:{_compact_injector_message(result.message)}')
        return False

    if request.action_type == 'location_route_step':
        metadata = dict(request.metadata or {})
        result = self._execute_injector(global_browser_injector(), 'location_route_step', {'expectedCurrentLocationId': metadata.get('expected_current_location_id', ''), 'navigationDelayMs': metadata.get('navigation_delay_ms', 75)}, timeout_s=3.0)
        result_metadata = {**metadata, 'injector_message': _compact_injector_message(result.message), 'injector_client_id': result.client_id}
        logged_request = _copy_request(request, metadata=result_metadata)
        parsed: dict[str, object] | None = None
        try:
            candidate = json.loads(result.message)
            parsed = candidate if isinstance(candidate, dict) else None
        except json.JSONDecodeError:
            parsed = None
        if result.ok and parsed is not None and (parsed.get('submitted') is True):
            _log_action(self.logger, 'location_route_step_submitted', logged_request, dry_run=False)
            return True
        _log_action(self.logger, 'action_blocked', logged_request, block_reason=f'injector_location_route_step_failed:{_compact_injector_message(result.message)}')
        return False

    if request.action_type == 'viewport_move':
        metadata = dict(request.metadata or {})
        if metadata.get('use_js_hunt_direction'):
            direction = request.scan_direction or str(metadata.get('scan_direction') or '')
            result = self._execute_injector(global_browser_injector(), 'hunt_move_direction', {'direction': direction, 'margin': int(metadata.get('margin', 35) or 0)}, timeout_s=2.5)
            result_metadata = dict(metadata)
            result_metadata['injector_message'] = _compact_injector_message(result.message)
            result_metadata['injector_client_id'] = result.client_id
            if result.ok:
                _log_action(self.logger, 'viewport_move_js_hunt_direction', _copy_request(request, metadata=result_metadata), dry_run=False)
                return True
            _log_action(self.logger, 'action_blocked', _copy_request(request, metadata=result_metadata), block_reason=f'injector_hunt_move_direction_failed:{_compact_injector_message(result.message)}')
            return False

    if request.action_type == 'open_url':
        _log_action(self.logger, 'action_blocked', request, block_reason='live_js_only_unsupported_action:open_url')
        return False

    if request.action_type == 'browser_back':
        _log_action(self.logger, 'action_blocked', request, block_reason='live_js_only_unsupported_action:browser_back')
        return False
    return None
