from src.antibot_cv.automation.quest_route_binding import (
    QuestRouteBindingEvidence,
    RouteIdentity,
    validate_quest_route_binding,
    route_binding_evidence,
)
from types import SimpleNamespace


def test_accept_requires_one_exact_giver() -> None:
    evidence = QuestRouteBindingEvidence(
        kind="quest_accept",
        target="Порт Барбуса",
        director_present=True,
        phase="ROUTE",
        pending=RouteIdentity("31", "Поручение", "Порт Барбуса", ("Лоцман",)),
        reference=RouteIdentity(
            "31",
            "Поручение",
            "Порт Барбуса",
            ("Лоцман", "Староста"),
        ),
    )

    assert validate_quest_route_binding(evidence) == "accept_giver_ambiguous"


def test_dialogue_requires_exact_lease_fingerprint_and_plan() -> None:
    evidence = QuestRouteBindingEvidence(
        kind="quest_dialogue",
        target="Туманные луга",
        director_present=True,
        phase="ROUTE",
        pending=RouteIdentity(
            "91",
            "Разговор",
            "Туманные луга",
            fingerprint="fresh",
        ),
        lease=RouteIdentity("91", "Разговор", fingerprint="stale"),
        plan_quest_id="91",
        plan_kind="npc_dialogue_or_handoff",
    )

    assert validate_quest_route_binding(evidence) == "dialogue_identity_mismatch"


def test_turn_in_exact_binding_passes() -> None:
    objective = RouteIdentity(
        "17",
        "Доставка",
        "Город Барбус",
        ("Квартирмейстер",),
        "complete-fingerprint",
    )
    evidence = QuestRouteBindingEvidence(
        kind="quest_turn_in",
        target="Город Барбус",
        director_present=True,
        phase="ROUTE",
        pending=objective,
        lease=RouteIdentity("17", "Доставка", fingerprint="complete-fingerprint"),
        accepted=RouteIdentity(
            "17",
            "Доставка",
            "Город Барбус",
            ("Квартирмейстер",),
        ),
    )

    assert validate_quest_route_binding(evidence) is None


def test_location_route_requires_director_owned_single_target() -> None:
    evidence = QuestRouteBindingEvidence(
        kind="quest_location",
        target="Дикий предел",
        director_present=True,
        pending=RouteIdentity("44", "Охота", fingerprint="objective-fingerprint"),
        lease=RouteIdentity("44", "Охота", fingerprint="objective-fingerprint"),
        active_quest_id="44",
        route_link_label="Дикий предел",
        navigator_label="Дикий предел",
        target_routes=("Дикий предел", "Запасной путь"),
        route_locations=("Дикий предел",),
    )

    assert validate_quest_route_binding(evidence) == "quest_location_identity_mismatch"


def test_nonquest_route_does_not_require_quest_evidence() -> None:
    evidence = QuestRouteBindingEvidence(
        kind="interrupted_route_resume",
        target="Порт Барбуса",
        director_present=False,
    )

    assert validate_quest_route_binding(evidence) is None


def test_route_binding_tolerates_chain_without_ordered_handoff_cursor() -> None:
    director = SimpleNamespace(
        chain=SimpleNamespace(lease=None), pending_accept=None,
        active_route_plan=None, active_objective=None,
    )

    evidence = route_binding_evidence(
        "quest_dialogue", "Туманные луга", director=director,
        intake_pending=None, dialogue_pending=None, turn_in_pending=None,
        active_quest_id=None, route_link_label=None,
        target_routes_by_monster={}, route_locations=(),
    )

    assert validate_quest_route_binding(evidence) == "lease_missing"
