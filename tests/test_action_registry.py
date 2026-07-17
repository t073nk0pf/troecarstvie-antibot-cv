from src.antibot_cv.automation.action_registry import DOMAIN_ACTIONS, action_domain


def test_action_registry_has_disjoint_domain_ownership() -> None:
    registered: set[str] = set()
    for action_types in DOMAIN_ACTIONS.values():
        assert registered.isdisjoint(action_types)
        registered.update(action_types)


def test_action_registry_routes_representative_actions() -> None:
    assert action_domain("attack_visible_target") == "combat"
    assert action_domain("location_route_step") == "navigation"
    assert action_domain("npc_quest_action") == "quest"
    assert action_domain("use_recovery_items") == "resource"
    assert action_domain("unknown_action") is None
