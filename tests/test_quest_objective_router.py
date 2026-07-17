from types import MappingProxyType

import pytest

from src.antibot_cv.automation.quest_active_catalog import ActiveQuestEntry
from src.antibot_cv.automation.quest_director_policy import QuestDirectorIntent
from src.antibot_cv.automation.quest_director_runtime import QuestDirectorRuntime
from src.antibot_cv.automation.quest_objective_router import (
    ObjectiveRouteKind,
    ObjectiveRouteStatus,
    classify_objective,
)


def entry(
    objective: str,
    *,
    kind: str = "unknown",
    navigation: tuple[tuple[str, str], ...] = (("Туманные луга", "Туманные луга"),),
    progress: object = None,
) -> ActiveQuestEntry:
    data = MappingProxyType(
        {
            "id": "31",
            "title": "Поиски Рокоша",
            "status": "active",
            "objective": objective,
            "objectiveKind": kind,
            "navigation": tuple(
                MappingProxyType({"text": text, "target": target})
                for text, target in navigation
            ),
            "progress": progress,
        }
    )
    return ActiveQuestEntry("31", "Поиски Рокоша", data)


def test_dative_npc_handoff_without_preposition_is_supported() -> None:
    plan = classify_objective(entry(
        "Отправляйтесь ремесленнику Сулемайту на Прокалённое плато и отдайте ему полученную книгу.",
        kind="unknown",
        navigation=(("Прокалённое плато", "Прокалённое плато"),),
    ))

    assert plan.status is ObjectiveRouteStatus.READY
    assert plan.kind is ObjectiveRouteKind.NPC_DIALOGUE_OR_HANDOFF


@pytest.mark.parametrize(
    ("objective", "kind", "navigation", "expected"),
    [
        (
            "Убейте 5 Гигантских ос.",
            "combat",
            (("Гигантская оса", "Гигантская оса [5]"),),
            ObjectiveRouteKind.PURE_KILL,
        ),
        (
            "Добудьте 3 осиных крыла с Гигантских ос.",
            "collect",
            (("Гигантская оса", "Гигантская оса [5]"),),
            ObjectiveRouteKind.COMBAT_DROP,
        ),
        (
            "Поговорите с волхвом Алстардом.",
            "dialogue",
            (("Прокалённое плато", "Прокалённое плато"),),
            ObjectiveRouteKind.NPC_DIALOGUE_OR_HANDOFF,
        ),
        (
            "Отправляйтесь в Туманные луга.",
            "travel",
            (("Туманные луга", "Туманные луга"),),
            ObjectiveRouteKind.LOCATION_VISIT,
        ),
        (
            "Купите 3 панциря у торговца Богдана.",
            "unknown",
            (("Городская площадь", "Городская площадь"),),
            ObjectiveRouteKind.NPC_PURCHASE,
        ),
    ],
)
def test_classifies_conservative_single_step_shapes(objective, kind, navigation, expected) -> None:
    plan = classify_objective(entry(objective, kind=kind, navigation=navigation))

    assert plan.status is ObjectiveRouteStatus.READY
    assert plan.kind is expected
    assert [item.kind for item in plan.unmet_requirements] == [expected]


def test_gathering_keeps_multiple_resources_in_authoritative_order() -> None:
    plan = classify_objective(
        entry("Соберите 100 Тисса и 100 Вьюнка узколистного.", kind="collect")
    )

    assert plan.kind is ObjectiveRouteKind.GATHER_RESOURCE
    assert [item.text for item in plan.unmet_requirements] == [
        "100 Тисса",
        "100 Вьюнка узколистного",
    ]


def test_npc_purchase_and_auction_acquisition_remain_distinct_planning_evidence() -> None:
    npc = classify_objective(
        entry("Купите 3 панциря у торговца Богдана.", kind="unknown")
    )
    auction = classify_objective(
        entry("Приобретите 3 панциря на аукционе.", kind="unknown")
    )

    assert npc.kind is ObjectiveRouteKind.NPC_PURCHASE
    assert auction.kind is ObjectiveRouteKind.AUCTION_ACQUISITION


def test_quest31_style_composite_preserves_order_and_does_not_route_purchase() -> None:
    plan = classify_objective(
        entry(
            "Добыть 3 осиных крыла, купить 3 панциря у Богдана, "
            "взять бычий рог у Оксайта и отнести всё волхву Алстарду.",
            kind="unknown",
        )
    )

    assert plan.status is ObjectiveRouteStatus.READY
    assert plan.kind is ObjectiveRouteKind.COMPOSITE
    assert [item.kind for item in plan.unmet_requirements] == [
        ObjectiveRouteKind.GATHER_RESOURCE,
        ObjectiveRouteKind.NPC_PURCHASE,
        ObjectiveRouteKind.NPC_DIALOGUE_OR_HANDOFF,
        ObjectiveRouteKind.TURN_IN,
    ]
    assert plan.unmet_requirements[1].text.startswith("купить")


def test_complete_progress_routes_to_turn_in_before_any_missing_location_guess() -> None:
    plan = classify_objective(
        entry(
            "Убейте 5 Ос.",
            kind="combat",
            navigation=(("Оса", "Оса [5]"),),
            progress=MappingProxyType({"current": 5, "required": 5, "complete": True}),
        )
    )

    assert plan.kind is ObjectiveRouteKind.TURN_IN
    assert plan.reason == "objective_progress_complete"


def test_combat_prefix_cannot_hide_later_purchase_and_alternatives_are_unsafe() -> None:
    monster = (("Оса", "Оса [5]"),)
    sequenced = classify_objective(
        entry(
            "Убейте Осу и купите панцирь у Богдана.",
            kind="combat",
            navigation=monster,
        )
    )
    delayed = classify_objective(
        entry(
            "Добудьте крыло с Осы затем купите панцирь у Богдана.",
            kind="combat",
            navigation=monster,
        )
    )
    alternative = classify_objective(
        entry(
            "Убейте Осу или купите панцирь у Богдана.",
            kind="combat",
            navigation=monster,
        )
    )

    for plan in (sequenced, delayed):
        assert plan.kind is ObjectiveRouteKind.COMPOSITE
        assert ObjectiveRouteKind.NPC_PURCHASE in [item.kind for item in plan.requirements]
    assert alternative.status is ObjectiveRouteStatus.UNSAFE
    assert alternative.reason == "objective_alternative_unsafe"


def test_find_drop_then_turn_in_composite_preserves_semantic_order() -> None:
    plan = classify_objective(
        entry(
            "Убивая Свирепых кентавров найдите ступку, "
            "после чего отнесите её ведунье Ильмет в Курганы Бренности.",
            kind="combat",
            navigation=(
                ("Свирепый кентавр", "Свирепый кентавр [6]"),
                ("Курганы Бренности", "Курганы Бренности"),
            ),
        )
    )

    assert plan.status is ObjectiveRouteStatus.READY
    assert plan.kind is ObjectiveRouteKind.COMPOSITE
    assert [item.kind for item in plan.unmet_requirements] == [
        ObjectiveRouteKind.COMBAT_DROP,
        ObjectiveRouteKind.TURN_IN,
    ]


def test_progress_contradiction_is_unsafe_and_consistent_completion_precedes_target_ambiguity() -> None:
    targets = (("Оса", "Оса [5]"), ("Волк", "Волк [5]"))
    contradictory = classify_objective(
        entry(
            "Убейте 5 Ос.",
            kind="combat",
            navigation=targets,
            progress=MappingProxyType({"current": 3, "required": 5, "complete": True}),
        )
    )
    completed = classify_objective(
        entry(
            "Убейте 5 Ос.",
            kind="combat",
            navigation=targets,
            progress=MappingProxyType({"current": 5, "required": 5, "complete": True}),
        )
    )

    assert contradictory.status is ObjectiveRouteStatus.UNSAFE
    assert contradictory.reason == "objective_progress_complete_contradicts_ratio"
    assert completed.kind is ObjectiveRouteKind.TURN_IN


def test_travel_to_npc_requires_exact_bound_location_and_nonconflicting_kind() -> None:
    bound = classify_objective(
        entry(
            "Отправляйтесь к алхимику Филониду в Туманные луга.",
            kind="dialogue",
        )
    )
    conflicting = classify_objective(
        entry(
            "Отправляйтесь к алхимику Филониду в Туманные луга.",
            kind="travel",
        )
    )

    assert bound.kind is ObjectiveRouteKind.NPC_DIALOGUE_OR_HANDOFF
    assert conflicting.status is ObjectiveRouteStatus.UNSAFE
    assert conflicting.reason == "objective_travel_npc_ambiguous"


def test_ambiguity_and_malformed_structured_kind_fail_closed() -> None:
    ambiguous = classify_objective(
        entry(
            "Убейте врага.",
            kind="combat",
            navigation=(("Оса", "Оса [5]"), ("Волк", "Волк [5]")),
        )
    )
    malformed = classify_objective(entry("Сделайте что-то.", kind="purchase"))

    assert ambiguous.status is ObjectiveRouteStatus.UNSAFE
    assert ambiguous.reason == "objective_monster_ambiguous"
    assert malformed.status is ObjectiveRouteStatus.UNSAFE
    assert malformed.reason == "objective_kind_invalid"


def test_broad_keyword_prose_remains_unsupported() -> None:
    plan = classify_objective(
        entry("Богдан может купить панцирь, если вы его найдёте.", kind="unknown")
    )

    assert plan.status is ObjectiveRouteStatus.READY
    assert plan.kind is ObjectiveRouteKind.UNSUPPORTED


def test_director_exposes_action_free_plan_for_pinned_composite() -> None:
    item = {
        "id": "31",
        "title": "Поиски Рокоша",
        "status": "active",
        "objective": (
            "Добыть 3 осиных крыла, купить 3 панциря у Богдана, "
            "взять бычий рог у Оксайта."
        ),
        "objectiveKind": "unknown",
        "navigation": [{"text": "Городская площадь", "target": "Городская площадь"}],
        "progress": None,
    }
    runtime = QuestDirectorRuntime()
    runtime.begin_catalog_refresh()
    runtime.ingest_catalog_page(
        {
            "loadStatus": "loaded",
            "mode": "avail",
            "currentPage": 0,
            "pageCount": 1,
            "hasNextPage": False,
            "items": [],
            "truncated": False,
        }
    )
    runtime.begin_active_refresh()
    runtime.ingest_active_page(
        {
            "loadStatus": "loaded",
            "mode": "started",
            "currentPage": 0,
            "pageCount": 1,
            "hasNextPage": False,
            "items": [item],
            "truncated": False,
        }
    )
    runtime.chain.pin_entry(runtime.active_catalog.result[0], revision=runtime.active_catalog.revision)

    decision = runtime.decision(current_level_cap=5)

    assert decision.intent is QuestDirectorIntent.EXECUTE_ACTIVE
    assert decision.reason == "pinned_chain_requires_non_monster_executor"
    assert runtime.active_route_plan is not None
    assert runtime.active_route_plan.kind is ObjectiveRouteKind.COMPOSITE
    assert [item.kind for item in runtime.active_route_plan.unmet_requirements] == [
        ObjectiveRouteKind.GATHER_RESOURCE,
        ObjectiveRouteKind.NPC_PURCHASE,
        ObjectiveRouteKind.NPC_DIALOGUE_OR_HANDOFF,
    ]


def test_director_dispatches_opening_combat_phase_for_supported_composite() -> None:
    item = {
        "id": "32",
        "title": "Composite monster",
        "status": "active",
        "objective": "Добудьте крыло с Осы затем купите панцирь у Богдана.",
        "objectiveKind": "combat",
        "navigation": [{"text": "Оса", "target": "Оса [5]"}],
        "progress": None,
    }
    runtime = _cold_director(item)

    decision = runtime.decision(current_level_cap=5)

    assert decision.intent is QuestDirectorIntent.EXECUTE_ACTIVE
    assert decision.quest is not None and decision.quest.id == "32"
    assert runtime.active_objective is not None
    assert runtime.active_objective.quest_id == "32"
    assert runtime.active_route_plan is not None
    assert runtime.active_route_plan.kind is ObjectiveRouteKind.COMPOSITE


def test_director_turn_in_priority_prevents_monster_executor_dispatch() -> None:
    item = {
        "id": "33",
        "title": "Completed combat",
        "status": "active",
        "objective": "Убейте 5 ос.",
        "objectiveKind": "combat",
        "navigation": [
            {"text": "Оса", "target": "Оса [5]"},
            {"text": "Волк", "target": "Волк [5]"},
        ],
        "progress": {"current": 5, "required": 5, "complete": True},
    }
    runtime = _cold_director(item)

    decision = runtime.decision(current_level_cap=5)

    assert decision.intent is QuestDirectorIntent.EXECUTE_ACTIVE
    assert runtime.active_objective is None
    assert runtime.active_route_plan is not None
    assert runtime.active_route_plan.kind is ObjectiveRouteKind.TURN_IN


def _cold_director(*items: dict[str, object], state_path=None) -> QuestDirectorRuntime:
    runtime = QuestDirectorRuntime(chain_state_path=state_path)
    runtime.begin_catalog_refresh()
    runtime.ingest_catalog_page(
        {
            "loadStatus": "loaded",
            "mode": "avail",
            "currentPage": 0,
            "pageCount": 1,
            "hasNextPage": False,
            "items": [],
            "truncated": False,
        }
    )
    runtime.begin_active_refresh()
    runtime.ingest_active_page(
        {
            "loadStatus": "loaded",
            "mode": "started",
            "currentPage": 0,
            "pageCount": 1,
            "hasNextPage": False,
            "items": list(items),
            "truncated": False,
        }
    )
    return runtime


def _raw_item(quest_id: str, objective: str, kind: str) -> dict[str, object]:
    return {
        "id": quest_id,
        "title": f"Quest {quest_id}",
        "status": "active",
        "objective": objective,
        "objectiveKind": kind,
        "navigation": [{"text": "Туманные луга", "target": "Туманные луга"}],
        "progress": None,
    }


@pytest.mark.parametrize(
    ("item", "kind"),
    [
        (_raw_item("41", "Поговорите с Алстардом.", "dialogue"), ObjectiveRouteKind.NPC_DIALOGUE_OR_HANDOFF),
        (_raw_item("42", "Соберите 5 Тисса.", "collect"), ObjectiveRouteKind.GATHER_RESOURCE),
        (
            _raw_item(
                "43",
                "Собрать 3 крыла, купить панцирь у Богдана.",
                "unknown",
            ),
            ObjectiveRouteKind.COMPOSITE,
        ),
    ],
)
def test_cold_start_pins_one_safe_non_monster_step(item, kind) -> None:
    runtime = _cold_director(item)

    decision = runtime.decision(current_level_cap=5)

    assert decision.intent is QuestDirectorIntent.EXECUTE_ACTIVE
    assert runtime.chain.lease is not None
    assert runtime.active_route_plan is not None and runtime.active_route_plan.kind is kind


def test_quest_local_unsafe_is_quarantined_while_safe_active_step_is_selected() -> None:
    blocked = _raw_item(
        "51",
        "Поговорите с Алстардом или идите дальше.",
        "dialogue",
    )
    safe = _raw_item("52", "Поговорите с Филонидом.", "dialogue")
    runtime = _cold_director(blocked, safe)

    decision = runtime.decision(current_level_cap=5)

    assert decision.intent is QuestDirectorIntent.EXECUTE_ACTIVE
    assert decision.quest is not None and decision.quest.id == "52"
    assert [item.quest_id for item in runtime.chain.quarantines] == ["51"]


def test_all_quest_local_blockers_never_fall_through_to_profit_farm() -> None:
    first = _raw_item("61", "Поговорите или уйдите.", "dialogue")
    second = _raw_item("62", "Купите или получите.", "unknown")
    runtime = _cold_director(first, second)

    quarantined = runtime.decision(current_level_cap=5)
    decision = runtime.decision(current_level_cap=5)

    assert quarantined.intent is QuestDirectorIntent.WAIT
    assert quarantined.reason == "quest_active_quarantined"
    assert decision.intent is QuestDirectorIntent.WAIT
    assert decision.reason == "quest_active_quarantined"
    assert {item.quest_id for item in runtime.chain.quarantines} == {"61", "62"}
    assert runtime.chain.lease is None


def test_quarantine_survives_restart_and_fingerprint_change_unblocks(tmp_path) -> None:
    state_path = tmp_path / "quest-chain.json"
    blocked = _raw_item("71", "Поговорите или уйдите.", "dialogue")
    runtime = _cold_director(blocked, state_path=state_path)
    assert runtime.decision(current_level_cap=5).intent is QuestDirectorIntent.WAIT
    assert state_path.exists()

    restored = QuestDirectorRuntime(chain_state_path=state_path)
    assert [item.quest_id for item in restored.chain.quarantines] == ["71"]
    changed = _raw_item("71", "Поговорите с Алстардом.", "dialogue")
    # Reuse the catalogue freezer instead of treating the quarantine record as
    # authorization: a changed authoritative fingerprint is merely retryable.
    probe = _cold_director(changed).active_catalog.result[0]
    assert restored.chain.is_quarantined(
        probe,
        capability_version="objective_router_v3",
    ) is False
    assert restored.chain.quarantines == ()


def test_unsupported_executor_is_durably_quarantined_without_action_requests(
    test_config,
) -> None:
    from src.antibot_cv.automation.actions import DryRunActionSink
    from src.antibot_cv.automation.config import AutomationConfig, to_plain_dict
    from src.antibot_cv.automation.controller import AutomationController
    from src.antibot_cv.automation.quest_chain_runtime import QuestChainRuntime
    from src.antibot_cv.telemetry.event_logger import InMemoryEventLogger

    data = to_plain_dict(test_config)
    data["leveling"] = {
        **data["leveling"],
        "enabled": True,
        "autonomous_quest_director": True,
        "target_level": 20,
    }
    controller = AutomationController(
        AutomationConfig.from_dict(data),
        sink_mode="replay",
        logger=InMemoryEventLogger(),
    )
    sink = DryRunActionSink(controller.logger)
    controller.action_executor.sink = sink
    controller.current_level = 5
    director = controller._quest_director
    assert director is not None
    item = _raw_item(
        "81",
        "Собрать 3 крыла, купить панцирь у Богдана.",
        "unknown",
    )
    cold = _cold_director(item)
    director.catalog = cold.catalog
    director.active_catalog = cold.active_catalog
    director.discovery_initialized = True
    director.available_snapshot_fresh = True
    director.active_snapshot_fresh = True
    director.active_quests = cold.active_quests
    decision = director.decision(current_level_cap=5)
    assert decision.quest is not None

    assert controller._begin_non_combat_quest_executor("81") is True

    assert sink.requests == []
    assert director.chain.lease is None
    assert len(director.chain.quarantines) == 1
    assert director.chain.quarantines[0].quest_id == "81"
    assert director.chain.quarantines[0].reason == "objective_executor_unavailable"
    checkpoint = director.chain.checkpoint()
    assert checkpoint is not None
    restored_chain = QuestChainRuntime()
    restored = restored_chain.restore(checkpoint)
    assert restored is None
    assert restored_chain.quarantines[0].quest_id == "81"
    controller.current_page_kind = "quests"
    for _ in range(3):
        wait = director.decision(current_level_cap=5)
        assert wait.quest is None or wait.quest.id != "81"
        assert controller._maybe_start_quest_refresh() is True
    assert sink.requests == []
