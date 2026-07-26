from src.antibot_cv.automation.quest_route_binding import (
    QuestRouteBindingEvidence,
    RouteIdentity,
    validate_quest_route_binding,
    route_binding_evidence,
)
from src.antibot_cv.automation.navigation_runtime import NavigationRuntimeMixin
from src.antibot_cv.automation.quest_runtime import QuestRuntimeMixin
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


def test_area_object_requires_exact_pending_lease_and_sealed_authority() -> None:
    evidence = QuestRouteBindingEvidence(
        kind="quest_area_object",
        target="Пристанище трёх ветров",
        director_present=True,
        phase="route",
        pending=RouteIdentity("304", "Цветочная болезнь", "Пристанище трёх ветров"),
        lease=RouteIdentity("304", "Цветочная болезнь", fingerprint="lease-fingerprint"),
        authority_present=True,
        authority_complete=True,
        authority_plan_fingerprint="plan-fingerprint",
        authority_lease_fingerprint="lease-fingerprint",
    )

    assert validate_quest_route_binding(evidence) is None
    assert validate_quest_route_binding(
        QuestRouteBindingEvidence(
            **{
                **evidence.__dict__,
                "authority_lease_fingerprint": "foreign-lease",
            }
        )
    ) == "area_object_authority_mismatch"


def test_area_object_adapter_binds_pending_record_and_authority() -> None:
    director = SimpleNamespace(
        chain=SimpleNamespace(
            lease=SimpleNamespace(
                quest_id="304",
                quest_title="Цветочная болезнь",
                current_fingerprint="lease-fingerprint",
            )
        )
    )
    pending = SimpleNamespace(
        phase=SimpleNamespace(value="route"),
        plan=SimpleNamespace(quest_id="304", quest_title="Цветочная болезнь"),
        requirement=SimpleNamespace(location="Пристанище трёх ветров"),
    )
    authority = SimpleNamespace(
        complete=True,
        plan_fingerprint="plan-fingerprint",
        lease_fingerprint="lease-fingerprint",
    )

    evidence = route_binding_evidence(
        "quest_area_object", "Пристанище трёх ветров", director=director,
        intake_pending=None, dialogue_pending=None, turn_in_pending=None,
        active_quest_id=None, route_link_label=None,
        target_routes_by_monster={}, route_locations=(),
        area_object_pending=pending, active_catalog_authority=authority,
    )

    assert validate_quest_route_binding(evidence) is None


def test_area_object_arrival_mismatch_stops_before_phase_mutation_or_action() -> None:
    class Runtime:
        _route_arrival_binding_error = QuestRuntimeMixin._route_arrival_binding_error

        def _stop_leveling_unsafe(self, reason: str) -> bool:
            self.stopped_reason = reason
            return True

        def _on_quest_area_object_route_arrived(self, reason: str) -> bool:
            self.area_arrival_calls.append(reason)
            self._quest_area_objects.pending.phase = SimpleNamespace(value="observe")
            return True

    runtime = Runtime()
    pending = SimpleNamespace(
        phase=SimpleNamespace(value="route"),
        plan=SimpleNamespace(quest_id="304", quest_title="Цветочная болезнь"),
        requirement=SimpleNamespace(location="Пристанище трёх ветров"),
    )
    runtime._quest_area_objects = SimpleNamespace(pending=pending)
    runtime._quest_director = SimpleNamespace(
        chain=SimpleNamespace(
            lease=SimpleNamespace(
                quest_id="304",
                quest_title="Цветочная болезнь",
                current_fingerprint="lease-fingerprint",
            )
        )
    )
    runtime._quest_active_catalog_authority = SimpleNamespace(
        complete=True,
        plan_fingerprint="plan-fingerprint",
        lease_fingerprint="foreign-lease",
    )
    runtime._quest_intake = SimpleNamespace(pending=None)
    runtime._quest_dialogue = SimpleNamespace(pending=None)
    runtime._quest_turn_in = SimpleNamespace(pending=None)
    runtime._active_quest_id = None
    runtime._quest_route_link_label = None
    runtime._quest_target_routes = {}
    runtime._quest_route_locations = ()
    runtime._route_recovery_kind = "quest_area_object"
    runtime._route_destination_name = "Пристанище трёх ветров"
    runtime._navigator_target_name = None
    runtime._route_resume_target_name = None
    runtime.area_arrival_calls = []
    runtime.stopped_reason = None

    assert NavigationRuntimeMixin._finish_route_arrival(runtime, "route_arrived") is True
    assert runtime.stopped_reason == "route_arrival_binding:area_object_authority_mismatch"
    assert pending.phase.value == "route"
    assert runtime.area_arrival_calls == []


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
