from __future__ import annotations

import json
import time
from dataclasses import replace

from src.antibot_cv.automation.actions import DryRunActionSink
from src.antibot_cv.automation.browser_injector import InjectorResult
from src.antibot_cv.automation.config import AutomationConfig, to_plain_dict
from src.antibot_cv.automation.controller import AutomationController
from src.antibot_cv.automation.quest_director_policy import QuestRef
from src.antibot_cv.automation.quest_objective_router import classify_objective
from src.antibot_cv.automation.quest_turnin_runtime import QuestTurnInPhase
from src.antibot_cv.automation.state_machine import GameState
from src.antibot_cv.telemetry.event_logger import InMemoryEventLogger


def active_page(*items: dict[str, object]) -> dict[str, object]:
    return {
        "loadStatus": "loaded",
        "mode": "started",
        "currentPage": 0,
        "pageCount": 1,
        "hasNextPage": False,
        "items": list(items),
        "truncated": False,
    }


def completed_item() -> dict[str, object]:
    return {
        "id": "246",
        "title": "Охота на волка",
        "status": "active",
        "objective": "Убить Волка 5/5",
        "navigation": [{"text": "Тёмный лес", "target": "Тёмный лес"}],
        "progress": {"current": 5, "required": 5, "complete": True},
    }


def continued_item() -> dict[str, object]:
    return {
        "id": "246",
        "title": "Охота на волка",
        "status": "active",
        "objective": "Убить Лиса 0/3",
        "navigation": [{"text": "Лиса", "target": "Лиса [5]"}],
        "progress": {"current": 0, "required": 3, "complete": False},
    }


def existing_turn_in_item() -> dict[str, object]:
    return {
        "id": "280",
        "title": "Фамильная ступка",
        "status": "active",
        "objective": "Вернитесь к разбойнику Аскорду в Земли Пращуров.",
        "objectiveKind": "dialogue",
        "navigation": [{"text": "Земли Пращуров", "target": "Земли Пращуров"}],
        "progress": {"current": None, "required": None, "complete": False},
    }


def controller(test_config: AutomationConfig) -> tuple[AutomationController, DryRunActionSink]:
    data = to_plain_dict(test_config)
    data["leveling"] = {**data["leveling"], "autonomous_quest_director": True}
    result = AutomationController(
        AutomationConfig.from_dict(data), sink_mode="replay", logger=InMemoryEventLogger()
    )
    sink = DryRunActionSink(result.logger)
    result.action_executor.sink = sink
    result.current_location_name = "Южная застава"
    result.current_page_kind = "quests"
    result.state_machine.state = GameState.QUEST_REFRESH_PENDING
    result._quest_refresh_requested_monotonic = time.monotonic()
    director = result._quest_director
    assert director is not None
    director.begin_active_refresh()
    director.ingest_active_page(active_page(completed_item()))
    director.chain.pin_entry(director.active_catalog.result[0], revision=director.active_catalog.revision)
    return result, sink


def test_legacy_completed_lease_blocks_turn_in_without_actions(test_config: AutomationConfig) -> None:
    result, sink = controller(test_config)

    result._handle_quest_refresh()

    assert result.state_machine.state is GameState.STOPPED
    assert result.last_error_reason == "quest_turn_in_step_ref_missing"
    assert sink.requests == []
    missing_ref_event = next(
        event for event in result.logger.events
        if event["event_type"] == "quest_turn_in_ref_missing"
    )
    assert json.loads(json.dumps(missing_ref_event, ensure_ascii=False)) == missing_ref_event


def test_turn_in_plan_after_pin_enters_turn_in_coordinator_not_dialogue(
    test_config: AutomationConfig,
) -> None:
    result, sink = controller(test_config)
    director = result._quest_director
    assert director is not None
    director.begin_active_refresh()
    director.ingest_active_page(active_page(existing_turn_in_item()))
    entry = director.active_catalog.result[0]
    director.chain.release_completed("246")
    director.chain.pin_entry(entry, revision=director.active_catalog.revision)
    director.active_route_plan = classify_objective(entry)
    result.current_location_name = "Длань Рода"

    assert result._begin_non_combat_quest_executor("280") is True

    assert result._quest_dialogue.pending is None
    assert result._quest_turn_in.pending is not None
    assert result._quest_turn_in.pending.phase is QuestTurnInPhase.ROUTE
    assert result._quest_turn_in.pending.objective.giver_name == "Разбойник Аскорд"
    assert result._quest_turn_in.pending.objective.location == "Земли Пращуров"
    assert [request.action_type for request in sink.requests] == ["open_location_navigator"]

    result.state_machine.state = GameState.ROUTE_RECOVERY
    result.current_location_name = "Земли пращуров"
    result.current_page_kind = "area"
    result._route_destination_name = "Земли Пращуров"

    assert result._finish_route_arrival("navigator_route_arrived") is True
    assert result.last_error_reason is None
    assert result._quest_turn_in.pending.phase is QuestTurnInPhase.NPC_LOOKUP
    assert [request.action_type for request in sink.requests] == [
        "open_location_navigator",
        "open_area",
    ]


def test_confirmed_acceptance_to_completed_turn_in_releases_after_fresh_absence(
    test_config: AutomationConfig,
    monkeypatch,
) -> None:
    result, sink = controller(test_config)
    director = result._quest_director
    assert director is not None
    accepted = QuestRef(
        "246",
        "Охота на волка",
        location="Южная застава",
        giver_names=("Воевода Ратмир",),
    )
    director.pending_accept = accepted
    director.intake_queue = (accepted,)
    director.available_quests = (accepted,)
    director.acknowledge_accept("246")
    assert director.chain.lease is not None and director.chain.lease.accepted_ref == accepted
    snapshots = iter(
        [
            {
                "ok": True,
                "truncated": False,
                "snapshotId": "area-npcs-turn-in-1",
                "location": {"id": "77", "name": "Южная застава"},
                "items": [{"dataId": "12", "name": "Дом Ратмира", "actionable": True}],
            },
            {
                "ok": True,
                "truncated": False,
                "identityMatches": True,
                "snapshotId": "npc-dialog-open",
                "questActions": [{
                    "questId": "246", "title": "Разговор с Ратмиром о волках", "action": "open",
                    "visible": True, "disabled": False,
                }],
                "dialogActions": [], "doneActions": [],
            },
            {
                "ok": True,
                "truncated": False,
                "identityMatches": True,
                "snapshotId": "npc-dialog-answer",
                "questActions": [],
                "dialogActions": [{
                    "questId": "246", "npcId": "12", "action": "answer", "ref": "81",
                    "text": "Волки уничтожены.", "visible": True, "disabled": False,
                }],
                "doneActions": [],
            },
            {
                "ok": True,
                "truncated": False,
                "identityMatches": True,
                "snapshotId": "npc-dialog-done",
                "questActions": [], "dialogActions": [],
                "doneActions": [{
                    "questId": "246", "npcId": "12", "action": "done", "pointId": "9",
                    "text": "Получить награду", "visible": True, "disabled": False,
                }],
            },
        ]
    )

    injector_calls: list[tuple[str, dict[str, object]]] = []

    class FakeInjector:
        def execute(self, command, payload=None, **kwargs):
            injector_calls.append((command, dict(payload or {})))
            return InjectorResult(True, json.dumps(next(snapshots), ensure_ascii=False), "client")

    monkeypatch.setattr(
        "src.antibot_cv.automation.browser_injector.global_browser_injector",
        lambda: FakeInjector(),
    )

    result._handle_quest_refresh()
    result.current_page_kind = "area"
    for _ in range(4):
        result._handle_quest_refresh()

    assert [request.action_type for request in sink.requests] == [
        "open_area",
        "open_exact_npc",
        "npc_quest_action",
        "npc_quest_action",
        "npc_quest_action",
        "open_active_quest_page",
    ]
    dialog_snapshots = [payload for command, payload in injector_calls if command == "npc_dialog_snapshot"]
    assert dialog_snapshots
    assert all(payload["expectedName"] == "Воевода Ратмир" for payload in dialog_snapshots)
    assert all(payload["expectedNpcId"] == "12" for payload in dialog_snapshots)
    assert director.chain.lease is not None
    assert director.chain.pending_turn_in_completion is not None

    director.ingest_active_page(active_page())
    result._quest_active_page_requested = None
    result._quest_active_snapshot_requested = False
    result._handle_quest_refresh()

    assert director.chain.lease is None
    assert any(event["event_type"] == "quest_turn_in_completed" for event in result.logger.events)


def test_turn_in_route_arrival_reenters_quest_refresh_and_opens_area(
    test_config: AutomationConfig,
) -> None:
    result, sink = controller(test_config)
    director = result._quest_director
    assert director is not None and director.chain.lease is not None
    quest_ref = QuestRef(
        "246", "Охота на волка", location="Южная застава", giver_names=("Воевода Ратмир",)
    )
    director.chain.bind_accepted_ref(quest_ref)
    result.current_location_name = "Другая локация"
    result._quest_turn_in.begin(
        director.active_catalog.result[0],
        lease=director.chain.lease,
        quest_ref=quest_ref,
        already_at_location=False,
    )
    result.state_machine.state = GameState.ROUTE_RECOVERY
    result.current_location_name = "Южная застава"

    assert result._on_quest_turn_in_route_arrived("quest_turn_in_arrived") is True

    assert result.state_machine.state is GameState.QUEST_REFRESH_PENDING
    assert sink.requests[-1].action_type == "open_area"


def test_turn_in_fresh_next_step_keeps_and_advances_pinned_chain(
    test_config: AutomationConfig,
) -> None:
    result, sink = controller(test_config)
    director = result._quest_director
    assert director is not None and director.chain.lease is not None
    quest_ref = QuestRef(
        "246", "Охота на волка", location="Южная застава",
        giver_names=("Воевода Ратмир",),
    )
    director.chain.bind_accepted_ref(quest_ref)
    result._quest_turn_in.begin(
        director.active_catalog.result[0],
        lease=director.chain.lease,
        quest_ref=quest_ref,
        already_at_location=True,
    )
    assert result._quest_turn_in.pending is not None
    result._quest_turn_in.pending = replace(
        result._quest_turn_in.pending,
        phase=QuestTurnInPhase.VERIFY_ACTIVE,
        completion_after_revision=director.active_catalog.revision,
    )
    result._quest_turn_in_started_monotonic = time.monotonic()
    result.current_level = 5
    director.begin_active_refresh()
    director.ingest_active_page(active_page(continued_item()))

    assert result._handle_pending_quest_turn_in() is True

    assert director.chain.lease is not None
    assert director.chain.lease.completed_steps == 1
    assert result._quest_turn_in.pending is None
    assert result.state_machine.state is GameState.QUEST_REFRESH_PENDING
    assert any(
        event["event_type"] == "quest_turn_in_advanced"
        for event in result.logger.events
    )

    next_completed = continued_item()
    next_completed["progress"] = {"current": 3, "required": 3, "complete": True}
    director.begin_active_refresh()
    director.ingest_active_page(active_page(next_completed))
    request_count = len(sink.requests)

    assert result._maybe_begin_quest_turn_in() is True

    assert result.state_machine.state is GameState.STOPPED
    assert result.last_error_reason == "quest_turn_in_step_ref_missing"
    assert len(sink.requests) == request_count


def test_turn_in_area_page_wait_is_bounded(
    test_config: AutomationConfig,
) -> None:
    result, _ = controller(test_config)
    director = result._quest_director
    assert director is not None and director.chain.lease is not None
    quest_ref = QuestRef(
        "246", "Охота на волка", location="Южная застава",
        giver_names=("Воевода Ратмир",),
    )
    director.chain.bind_accepted_ref(quest_ref)
    result._quest_turn_in.begin(
        director.active_catalog.result[0],
        lease=director.chain.lease,
        quest_ref=quest_ref,
        already_at_location=True,
    )
    result._quest_turn_in_started_monotonic = time.monotonic() - 60
    result.current_page_kind = "quests"

    result._handle_quest_refresh()

    assert result.state_machine.state is GameState.STOPPED
    assert result.last_error_reason == "quest_turn_in_area_page_timeout"


def test_turn_in_transient_snapshot_retries_bounded_then_succeeds(
    test_config: AutomationConfig,
    monkeypatch,
) -> None:
    result, sink = controller(test_config)
    director = result._quest_director
    assert director is not None
    director.chain.bind_accepted_ref(QuestRef(
        "246", "Охота на волка", location="Южная застава", giver_names=("Воевода Ратмир",)
    ))
    result._handle_quest_refresh()
    result.current_page_kind = "area"
    snapshots = iter([
        {"ok": False},
        {
            "ok": True,
            "truncated": False,
            "snapshotId": "area-npcs-turn-in-retry",
            "location": {"id": "77", "name": "Южная застава"},
            "items": [{"dataId": "12", "name": "Воевода Ратмир", "actionable": True}],
        },
    ])

    class FakeInjector:
        def execute(self, command, payload=None, **kwargs):
            return InjectorResult(True, json.dumps(next(snapshots), ensure_ascii=False), "client")

    monkeypatch.setattr(
        "src.antibot_cv.automation.browser_injector.global_browser_injector",
        lambda: FakeInjector(),
    )

    result._handle_quest_refresh()
    assert result.state_machine.state is GameState.QUEST_REFRESH_PENDING
    assert [request.action_type for request in sink.requests] == ["open_area"]
    result._handle_quest_refresh()
    assert sink.requests[-1].action_type == "open_exact_npc"


def test_long_route_does_not_consume_post_arrival_snapshot_deadline(
    test_config: AutomationConfig,
    monkeypatch,
) -> None:
    result, _ = controller(test_config)
    director = result._quest_director
    assert director is not None and director.chain.lease is not None
    quest_ref = QuestRef(
        "246", "Охота на волка", location="Южная застава",
        giver_names=("Воевода Ратмир",),
    )
    director.chain.bind_accepted_ref(quest_ref)
    result.current_location_name = "Другая локация"
    result._quest_turn_in.begin(
        director.active_catalog.result[0],
        lease=director.chain.lease,
        quest_ref=quest_ref,
        already_at_location=False,
    )
    result._quest_turn_in_started_monotonic = time.monotonic() - 60
    result.state_machine.state = GameState.ROUTE_RECOVERY
    result.current_location_name = "Южная застава"

    assert result._on_quest_turn_in_route_arrived("quest_turn_in_arrived") is True
    result.current_page_kind = "area"

    class FakeInjector:
        def execute(self, command, payload=None, **kwargs):
            return InjectorResult(True, '{"ok":false}', "client")

    monkeypatch.setattr(
        "src.antibot_cv.automation.browser_injector.global_browser_injector",
        lambda: FakeInjector(),
    )

    assert result._handle_pending_quest_turn_in() is True
    assert result.state_machine.state is GameState.QUEST_REFRESH_PENDING
    assert result._quest_turn_in_started_monotonic > time.monotonic() - 2


def test_turn_in_transient_snapshot_stops_after_refresh_timeout(
    test_config: AutomationConfig,
    monkeypatch,
) -> None:
    result, _ = controller(test_config)
    director = result._quest_director
    assert director is not None
    director.chain.bind_accepted_ref(QuestRef(
        "246", "Охота на волка", location="Южная застава", giver_names=("Воевода Ратмир",)
    ))
    result._handle_quest_refresh()
    result.current_page_kind = "area"
    result._quest_turn_in_started_monotonic = time.monotonic() - 60

    class FakeInjector:
        def execute(self, command, payload=None, **kwargs):
            return InjectorResult(True, '{"ok":false}', "client")

    monkeypatch.setattr(
        "src.antibot_cv.automation.browser_injector.global_browser_injector",
        lambda: FakeInjector(),
    )

    result._handle_quest_refresh()

    assert result.state_machine.state is GameState.STOPPED
    assert result.last_error_reason == "quest_turn_in_npc:turn_in_npc_snapshot_invalid"
