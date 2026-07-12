from __future__ import annotations

from src.antibot_cv.automation.route_planner import (
    RouteAction,
    RoutePlanner,
    plan_route,
    validate_navigator_route,
)


def snapshot(**overrides):
    value = {
        "snapshot_id": "s-1",
        "age_s": 0,
        "current": {"name": "Длань Рода", "id": "a", "url": "/map/a"},
        "desired": {"name": "Прокалённое плато"},
        "map": {
            "locations": [
                {"name": "Длань Рода", "id": "a", "url": "/map/a"},
                {"name": "Дикий предел", "id": "b", "url": "/map/b"},
                {"name": "Прокаленное плато", "id": "c", "url": "/map/c"},
            ],
            "edges": [
                {"from": "a", "to": "b", "direction": "east"},
                {"from": "b", "to": "c", "direction": "north"},
            ],
        },
        "compass": {"selected": "east"},
    }
    value.update(overrides)
    return value


def test_bfs_returns_only_first_verified_transition_and_destination():
    decision = plan_route(snapshot())
    assert decision.action is RouteAction.MOVE
    assert decision.destination.id == "b"
    assert decision.transition.destination.id == "b"
    assert decision.transition.direction == "east"
    assert decision.reason == "next_bfs_transition"


def test_arrived_uses_confirmed_id_even_with_name_normalization():
    value = snapshot(desired={"name": "длань рода"})
    decision = plan_route(value)
    assert decision.action is RouteAction.ARRIVED
    assert decision.transition is None


def test_ambiguous_name_fails_closed():
    value = snapshot()
    value["map"]["locations"].append({"name": "Прокалённое плато", "id": "other", "url": "/map/other"})
    assert plan_route(value).reason == "target_ambiguous"


def test_stale_and_missing_edge_are_not_moves():
    assert plan_route(snapshot(stale=True)).action is RouteAction.REFRESH
    value = snapshot()
    value["map"]["edges"] = []
    decision = plan_route(value)
    assert decision.action is RouteAction.STOP_UNSAFE
    assert decision.reason == "no_safe_route"


def test_cycles_and_depth_limit_terminate_fail_closed():
    value = snapshot()
    value["map"]["edges"] = [
        {"from": "a", "to": "b"},
        {"from": "b", "to": "a"},
    ]
    assert RoutePlanner(max_depth=1).plan(value).reason == "no_safe_route"


def test_unconfirmed_or_invalid_graph_is_unsafe():
    value = snapshot(current={"name": "Длань Рода"})
    assert plan_route(value).reason == "unconfirmed_location"
    value = snapshot()
    value["map"]["edges"] = [{"from": "a", "to": "unknown"}]
    assert plan_route(value).reason == "graph_ambiguous_or_invalid"


def test_graph_snapshot_without_age_is_refreshed() -> None:
    value = snapshot()
    value.pop("age_s")
    assert plan_route(value).action is RouteAction.REFRESH


def test_navigator_route_requires_fresh_exact_unambiguous_observation() -> None:
    observed = {
        "snapshotId": "nav-1",
        "generatedAt": 1000.0,
        "href": "https://3kingdoms.ru/navigator.php?name=x",
        "target": "Дикий предел",
        "currentLocation": False,
        "hasRoute": True,
        "visibleGoButtonCount": 1,
        "routeTransitions": 2,
    }
    decision = validate_navigator_route(observed, "дикий предел", now=lambda: 1001.0)
    assert decision.action is RouteAction.MOVE
    assert decision.route_transitions == 2
    assert decision.snapshot_id == "nav-1"

    assert validate_navigator_route({**observed, "target": "Дикий предел II"}, "Дикий предел", now=lambda: 1001.0).reason == "navigator_target_mismatch"
    assert validate_navigator_route({**observed, "visibleGoButtonCount": 2}, "Дикий предел", now=lambda: 1001.0).reason == "navigator_route_ambiguous"
    assert validate_navigator_route({**observed, "routeTransitions": 51}, "Дикий предел", max_transitions=50, now=lambda: 1001.0).reason == "navigator_route_length_invalid"
    assert validate_navigator_route(observed, "Дикий предел", now=lambda: 1020.0).action is RouteAction.REFRESH


def test_navigator_current_location_is_arrived_without_go() -> None:
    observed = {
        "snapshotId": "nav-2",
        "generatedAt": 1000.0,
        "href": "https://3kingdoms.ru/navigator.php?name=x",
        "target": "Дикий предел",
        "currentLocation": True,
        "hasRoute": False,
        "visibleGoButtonCount": 0,
        "routeTransitions": None,
    }
    assert validate_navigator_route(observed, "Дикий предел", now=lambda: 1001.0).action is RouteAction.ARRIVED
