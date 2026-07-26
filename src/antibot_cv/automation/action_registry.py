"""Domain registry for live action dispatch.

The registry is deliberately data-only: the physical mutation boundary remains
the live sink, while this module makes ownership explicit and testable.
"""

from src.antibot_cv.automation import (
    action_combat_handlers,
    action_navigation_handlers,
    action_quest_handlers,
    action_resource_handlers,
)

DOMAIN_ACTIONS = {
    "combat": action_combat_handlers.ACTION_TYPES,
    "navigation": action_navigation_handlers.ACTION_TYPES,
    "quest": action_quest_handlers.ACTION_TYPES,
    "resource": action_resource_handlers.ACTION_TYPES,
}


def action_domain(action_type: str) -> str | None:
    matches = [domain for domain, actions in DOMAIN_ACTIONS.items() if action_type in actions]
    if len(matches) > 1:
        raise RuntimeError(f"action registered in multiple domains: {action_type}")
    return matches[0] if matches else None
