from __future__ import annotations


def route_snapshot_fingerprint(
    snapshot: object,
) -> tuple[str, str, tuple[str, ...], str] | None:
    if not isinstance(snapshot, dict):
        return None
    if snapshot.get("message") != "location_route_snapshot" or snapshot.get("pageKind") != "area":
        return None
    current_location_id = str(snapshot.get("currentLocationId") or "").strip()
    target_location_id = str(snapshot.get("targetLocationId") or "").strip()
    raw_path = snapshot.get("foundPath")
    if not current_location_id or not target_location_id or not isinstance(raw_path, list):
        return None
    found_path = tuple(str(item or "").strip() for item in raw_path)
    if any(not item for item in found_path):
        return None
    transition = snapshot.get("nextTransition")
    next_location_id = (
        str(transition.get("locId") or "").strip() if isinstance(transition, dict) else ""
    )
    return current_location_id, target_location_id, found_path, next_location_id


def route_confirmation_reason(
    before: object,
    after: object,
    expected_transitions: object,
) -> str:
    if (
        not isinstance(expected_transitions, int)
        or isinstance(expected_transitions, bool)
        or expected_transitions <= 0
    ):
        return "expected_transition_count_invalid"
    after_fingerprint = route_snapshot_fingerprint(after)
    if after_fingerprint is None:
        return "parent_route_after_unconfirmed"
    current_location_id, target_location_id, found_path, next_location_id = after_fingerprint
    if target_location_id == "0":
        return "parent_route_target_missing"
    if current_location_id == target_location_id:
        return "parent_route_already_at_target"
    if len(found_path) != expected_transitions:
        return "parent_route_transition_count_mismatch"
    if not found_path or found_path[-1] != target_location_id:
        return "parent_route_destination_disconnected"
    if next_location_id != found_path[0]:
        return "parent_route_next_transition_disconnected"
    before_fingerprint = route_snapshot_fingerprint(before)
    if before_fingerprint is None:
        return "confirmed_connected_route_after_unconfirmed_before"
    if after_fingerprint == before_fingerprint:
        return "parent_route_unchanged_after_go"
    return "confirmed_changed_connected_route"
