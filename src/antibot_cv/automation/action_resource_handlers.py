"""Physical resource action handlers for the live sink."""

from src.antibot_cv.automation import action_live_sink as live

ACTION_TYPES = frozenset({"inspect_quest_inventory", "area_object_snapshot", "inspect_area_object", "use_recovery_items"})

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
    if request.action_type in {"area_object_snapshot", "inspect_area_object"}:
        self.last_area_object_result = None
        command = request.action_type
        result = self._execute_injector(
            global_browser_injector(), command, dict(request.metadata or {}), timeout_s=5.0
        )
        parsed = _parse_injector_dict(result.message)
        if result.ok and isinstance(parsed, dict):
            self.last_area_object_result = parsed
        _log_action(
            self.logger,
            command,
            request,
            area_object_ok=bool(result.ok and isinstance(parsed, dict)),
            area_object_message=_compact_injector_message(result.message),
            area_object_client_id=result.client_id,
        )
        return bool(result.ok and isinstance(parsed, dict))
    if request.action_type == "inspect_quest_inventory":
        metadata = dict(request.metadata or {})
        self.last_quest_inventory_snapshot = None
        injector = global_browser_injector()
        result = self._execute_injector(
            injector,
            "inventory_snapshot",
            {
                "open": True,
                "category": "quest",
                "categoryWaitMs": 3500,
                "questCategoryLoadDelayMs": max(
                    0, int(metadata.get("quest_category_load_delay_ms", 1500) or 1500)
                ),
                "names": metadata.get("names", []),
                "inventoryOpenDelayMs": max(
                    0, int(metadata.get("inventory_open_delay_ms", 1500) or 1500)
                ),
            },
            timeout_s=max(10.0, float(metadata.get("timeout_s", 10.0) or 10.0)),
        )
        parsed = _parse_injector_dict(result.message)
        if result.ok and parsed is not None:
            self.last_quest_inventory_snapshot = parsed
        inventory_observation = {}
        if isinstance(parsed, dict):
            raw_items = parsed.get("items")
            if isinstance(raw_items, list):
                inventory_observation = {
                    "inventory_category": parsed.get("category"),
                    "inventory_category_confirmed": parsed.get("categoryConfirmed"),
                    "inventory_item_count": parsed.get("itemCount"),
                    "inventory_items": [
                        {
                            "name": item.get("artAltTitle") or item.get("name"),
                            "count": item.get("count"),
                            "kind": item.get("artAltKind"),
                            "path": item.get("path"),
                        }
                        for item in raw_items[:40]
                        if isinstance(item, dict)
                    ],
                }
        _log_action(
            self.logger,
            "quest_inventory_inspected",
            request,
            inventory_client_id=result.client_id,
            inventory_ok=result.ok,
            inventory_message=_compact_injector_message(result.message),
            **inventory_observation,
        )
        return bool(result.ok and parsed is not None)
    if request.action_type == 'use_recovery_items':
        metadata = dict(request.metadata or {})
        timeout_s = float(metadata.get('timeout_s', 6.0) or 6.0)
        threshold = float(metadata.get('use_when_below_percent', 90) or 90)
        max_uses_per_resource = max(1, int(metadata.get('max_uses_per_resource', 1) or 1))
        inventory_open_delay_ms = max(0, int(metadata.get('inventory_open_delay_ms', 1500) or 1500))
        open_timeout_s = max(0.1, timeout_s, inventory_open_delay_ms / 1000 + 5.5)
        between_items_delay_s = max(0.0, int(metadata.get('between_items_delay_ms', 0) or 0) / 1000)
        confirm_delay_s = max(0.0, int(metadata.get('confirm_delay_ms', 700) or 0) / 1000)
        injector = global_browser_injector()
        result_metadata = dict(metadata)
        attempts: list[dict[str, object]] = []
        resource_results: list[dict[str, object]] = []
        kinds = (('health', 'health_names', 'health_restore_percent', 'healthPercent', 'health_use_when_below_percent'), ('prowess', 'prowess_names', 'prowess_restore_percent', 'prowessPercent', 'prowess_use_when_below_percent'))
        for kind, names_key, restore_key, percent_key, threshold_key in kinds:
            kind_threshold = float(metadata.get(threshold_key, threshold) or threshold)
            restore_percent = max(0.0, float(metadata.get(restore_key, 0) or 0))
            kind_ok = False
            kind_reason = 'not_attempted'
            last_percent: float | None = None
            for use_index in range(max_uses_per_resource):
                open_started_at = time.monotonic()
                open_result = self._execute_injector(injector, 'open_recovery_item', {'kind': kind, 'names': metadata.get(names_key, []), 'useWhenBelowPercent': kind_threshold, 'useIfResourcesMissing': bool(metadata.get('use_if_resources_missing', False)), 'forceUse': bool(metadata.get('force_use', False)), 'inventoryOpenDelayMs': inventory_open_delay_ms}, timeout_s=open_timeout_s)
                open_elapsed_ms = int((time.monotonic() - open_started_at) * 1000)
                attempt: dict[str, object] = {'kind': kind, 'use_index': use_index + 1, 'open_ok': open_result.ok, 'open_message': _compact_injector_message(open_result.message), 'open_client_id': open_result.client_id, 'open_elapsed_ms': open_elapsed_ms, 'open_timeout_s': open_timeout_s}
                try:
                    parsed = json.loads(open_result.message)
                    if isinstance(parsed, dict):
                        attempt['open_result'] = _compact_recovery_result(parsed)
                        if parsed.get('message') == 'recovery_item_not_needed':
                            percent = parsed.get('percent')
                            if not isinstance(percent, bool) and percent is not None:
                                try:
                                    last_percent = float(percent)
                                except (TypeError, ValueError):
                                    last_percent = None
                            attempts.append(attempt)
                            kind_ok = True
                            kind_reason = 'not_needed'
                            break
                        if parsed.get('message') != 'recovery_item_clicked':
                            attempts.append(attempt)
                            kind_reason = str(parsed.get('message') or 'open_failed')
                            break
                        percent_before = resource_percent_from_open_result(parsed, percent_key)
                        if percent_before is not None:
                            attempt['percent_before'] = percent_before
                        if restore_percent > 0:
                            attempt['restore_percent'] = restore_percent
                        if parsed.get('requiresConfirm') is False:
                            attempt['confirm_skipped'] = 'not_required'
                            if between_items_delay_s:
                                time.sleep(between_items_delay_s)
                            refresh_result = refresh_resource_source_after_use(injector, client_id=self.browser_client_id)
                            if refresh_result is not None:
                                attempt['resource_refresh_ok'] = refresh_result.ok
                                attempt['resource_refresh_message'] = _compact_injector_message(refresh_result.message)
                            percent_after = wait_resource_percent_after_use(injector, percent_key, percent_before, client_id=self.browser_client_id)
                            if percent_after is not None:
                                last_percent = percent_after
                                attempt['percent_after'] = percent_after
                                attempt['threshold'] = kind_threshold
                            attempts.append(attempt)
                            if percent_after is not None and percent_after >= kind_threshold:
                                kind_ok = True
                                kind_reason = 'threshold_reached'
                                break
                            if use_index + 1 >= max_uses_per_resource:
                                kind_reason = 'max_uses_reached'
                                break
                            continue
                except json.JSONDecodeError:
                    attempts.append(attempt)
                    kind_reason = 'open_result_json_error'
                    break
                if confirm_delay_s:
                    time.sleep(confirm_delay_s)
                confirm_started_at = time.monotonic()
                confirm_result = self._execute_injector(injector, 'confirm_action_form', {}, timeout_s=max(0.1, timeout_s))
                attempt['confirm_elapsed_ms'] = int((time.monotonic() - confirm_started_at) * 1000)
                attempt['confirm_ok'] = confirm_result.ok
                attempt['confirm_message'] = _compact_injector_message(confirm_result.message)
                attempt['confirm_client_id'] = confirm_result.client_id
                if confirm_result.ok:
                    if between_items_delay_s:
                        time.sleep(between_items_delay_s)
                    refresh_result = refresh_resource_source_after_use(injector, client_id=self.browser_client_id)
                    if refresh_result is not None:
                        attempt['resource_refresh_ok'] = refresh_result.ok
                        attempt['resource_refresh_message'] = _compact_injector_message(refresh_result.message)
                    percent_after = wait_resource_percent_after_use(injector, percent_key, attempt.get('percent_before'), client_id=self.browser_client_id)
                    if percent_after is not None:
                        last_percent = percent_after
                        attempt['percent_after'] = percent_after
                        attempt['threshold'] = kind_threshold
                attempts.append(attempt)
                if attempt.get('percent_after') is not None and float(attempt['percent_after']) >= kind_threshold:
                    kind_ok = True
                    kind_reason = 'threshold_reached'
                    break
                if not confirm_result.ok or use_index + 1 >= max_uses_per_resource:
                    kind_reason = 'confirm_failed' if not confirm_result.ok else 'max_uses_reached'
                    break
            resource_results.append({'kind': kind, 'ok': kind_ok, 'reason': kind_reason, 'threshold': kind_threshold, 'last_percent': last_percent})
        overall_ok = all((bool(item['ok']) for item in resource_results))
        if bool(metadata.get('open_hunt_after', True)):
            hunt_result = self._execute_injector(injector, 'open_hunt', {'verifyTimeoutMs': 2000, 'commandTimeoutMs': 5000}, timeout_s=5.5)
            result_metadata['open_hunt_ok'] = hunt_result.ok
            result_metadata['open_hunt_message'] = _compact_injector_message(hunt_result.message)
            result_metadata['open_hunt_client_id'] = hunt_result.client_id
        result_metadata['recovery_item_attempts'] = attempts
        result_metadata['recovery_resource_results'] = resource_results
        if overall_ok:
            _log_action(self.logger, 'recovery_items_js', _copy_request(request, metadata=result_metadata), dry_run=False)
            return True
        _log_action(self.logger, 'action_blocked', _copy_request(request, metadata=result_metadata), block_reason='injector_recovery_items_failed')
        return False
    return None
