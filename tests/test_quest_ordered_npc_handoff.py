from __future__ import annotations

from types import MappingProxyType
import time

import pytest

from src.antibot_cv.automation.quest_active_catalog import ActiveQuestEntry
from src.antibot_cv.automation.quest_chain_runtime import QuestChainCheckpointConflict, QuestChainRuntime
from src.antibot_cv.automation.quest_objective_router import ObjectiveRouteKind, classify_objective
from src.antibot_cv.automation.quest_ordered_npc_handoff import (
    OrderedHandoffRequirementKind,
    OrderedHandoffStatus,
    make_ordered_handoff_cursor,
    parse_ordered_npc_handoff,
)


OBJECTIVE = (
    "Отправляйтесь к стражу Всебою на Заставу храбрых и сделайте все, о чем он попросит, "
    "затем возвращайтесь к богатырю Туру на городскую площадь Арсы."
)


def q360(*, objective: str = OBJECTIVE, navigation=None) -> ActiveQuestEntry:
    navigation = navigation or (
        ("Заставу храбрых", "Застава храбрых"),
        ("городскую площадь Арсы", "Город Арса"),
    )
    data = MappingProxyType({
        "id": "360", "title": "Зов Лихих земель", "status": "active",
        "objective": objective, "objectiveKind": "unknown",
        "navigation": tuple(MappingProxyType({"text": text, "target": target}) for text, target in navigation),
        "progress": None,
        "reward": "250 опыта, 50 монет",
    })
    return ActiveQuestEntry("360", "Зов Лихих земель", data)


def make_cursor(entry: ActiveQuestEntry, *, revision: int = 1):
    plan = parse_ordered_npc_handoff(entry)
    generated = time.time() - 1
    return make_ordered_handoff_cursor(
        plan, lease_fingerprint=plan.fingerprint or "", active_catalog_revision=revision,
        active_snapshot_id="active-q360", active_snapshot_generated_at=generated,
        client_id="client-a", profile_id="profile-a", tab_id=42, staged_at=generated + 0.5,
    )


def test_q360_parser_binds_ordered_requirements_to_exact_navigation() -> None:
    plan = parse_ordered_npc_handoff(q360())

    assert plan.status is OrderedHandoffStatus.READY
    assert [item.kind for item in plan.requirements] == [
        OrderedHandoffRequirementKind.ROUTE_TO_NPC,
        OrderedHandoffRequirementKind.FULFIL_NPC_REQUESTS,
        OrderedHandoffRequirementKind.RETURN_TO_NPC,
    ]
    assert plan.requirements[0].location == "Застава храбрых"
    assert plan.requirements[2].location == "Город Арса"
    assert classify_objective(q360()).kind is ObjectiveRouteKind.COMPOSITE


@pytest.mark.parametrize(
    "entry",
    [
        q360(objective=OBJECTIVE.replace("затем", "либо")),
        q360(navigation=(("Заставу храбрых", "A"), ("Заставу храбрых", "B"), ("городскую площадь Арсы", "Город Арса"))),
        q360(objective=OBJECTIVE.replace("сделайте все, о чем он попросит", "купите все, что он попросит")),
    ],
)
def test_unsupported_alternative_ambiguous_mapping_and_mixed_purchase_are_never_ready(entry) -> None:
    assert parse_ordered_npc_handoff(entry).status is not OrderedHandoffStatus.READY


def test_cursor_checkpoint_round_trip_keeps_order_lease_and_browser_baseline(tmp_path) -> None:
    path = tmp_path / "q360.json"
    entry = q360()
    chain = QuestChainRuntime(state_path=path)
    chain.pin_entry(entry, revision=1)
    cursor = make_cursor(entry)
    chain.stage_ordered_handoff(cursor)

    restored = QuestChainRuntime(state_path=path)
    assert restored.ordered_handoff_cursor == cursor
    assert restored.lease is not None
    assert restored.ordered_handoff_cursor.requirements[0].ordinal == 0
    assert restored.ordered_handoff_cursor.client_id == "client-a"


def test_cursor_stage_is_cas_and_rolls_back_memory_on_checkpoint_conflict(tmp_path) -> None:
    path = tmp_path / "q360.json"
    entry = q360()
    first = QuestChainRuntime(state_path=path)
    first.pin_entry(entry, revision=1)
    stale = QuestChainRuntime(state_path=path)
    first.mark_awaiting_executor(
        "dialogue_unavailable", capability_version="objective_router_v2",
    )

    with pytest.raises(QuestChainCheckpointConflict):
        stale.stage_ordered_handoff(make_cursor(entry))
    assert stale.ordered_handoff_cursor is None
