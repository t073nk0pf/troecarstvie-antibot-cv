from __future__ import annotations

from dataclasses import replace
import json
import time

import pytest

from src.antibot_cv.automation.actions import DryRunActionSink
from src.antibot_cv.automation.browser_injector import InjectorResult
from src.antibot_cv.automation.config import AutomationConfig, to_plain_dict
from src.antibot_cv.automation.controller import AutomationController
from src.antibot_cv.automation.quest_acceptance_settle import (
    AcceptanceSettleIntent,
    acceptance_client_matches,
    assess_acceptance_area_snapshot,
)
from src.antibot_cv.automation.quest_director_policy import QuestRef
from src.antibot_cv.automation.quest_intake_runtime import QuestAcceptPhase
from src.antibot_cv.automation.quest_npc_action_journal import make_pending_npc_quest_action
from src.antibot_cv.automation.state_machine import GameState
from src.antibot_cv.telemetry.event_logger import InMemoryEventLogger


def area_snapshot(
    snapshot_id: str,
    *,
    location_id: object = "171",
    location_name: object = "Пригород Арсы",
    items: object = None,
    generated_at: object = None,
) -> dict[str, object]:
    return {
        "ok": True,
        "truncated": False,
        "snapshotId": snapshot_id,
        "generatedAt": time.time() if generated_at is None else generated_at,
        "location": {"id": location_id, "name": location_name},
        "items": (
            [{"dataId": "2", "routeRef": "402", "name": "Царевич Придон", "actionable": True}]
            if items is None
            else items
        ),
    }


def acceptance_controller(
    test_config: AutomationConfig, quest: QuestRef | None = None,
) -> tuple[AutomationController, DryRunActionSink]:
    data = to_plain_dict(test_config)
    data["dry_run"] = False
    data["leveling"] = {**data["leveling"], "autonomous_quest_director": True}
    controller = AutomationController(
        AutomationConfig.from_dict(data),
        sink_mode="live",
        logger=InMemoryEventLogger(dry_run=False),
        browser_client_id="client-a",
    )
    sink = DryRunActionSink(controller.logger)
    controller.action_executor.sink = sink
    controller.state_machine.state = GameState.LOCATION_SEARCH
    controller.current_page_kind = "area"
    quest = quest or QuestRef(
        "236", "Послание царевичу", location="Пригород Арсы",
        giver_names=("Царевича Придона",), catalog_page=0,
    )
    next_quest = QuestRef(
        "237", "Следующее поручение", location="Пригород Арсы",
        giver_names=("Царевича Придона",), catalog_page=0,
    )
    director = controller._quest_director
    assert director is not None
    director.available_snapshot_fresh = True
    director.active_snapshot_fresh = True
    director.discovery_initialized = True
    director.available_quests = (quest, next_quest)
    director.intake_queue = (quest, next_quest)
    director.begin_accept(quest.id)
    controller._quest_intake.begin(quest, already_at_location=True)
    assert controller._open_area_for_quest_accept("test_accept_settle") is True
    return controller, sink


def test_ambiguous_navigator_target_quarantines_only_current_acceptance(
    test_config: AutomationConfig,
) -> None:
    controller, _ = acceptance_controller(test_config)

    class RejectSelectionSink:
        def __init__(self) -> None:
            self.requests = []

        def execute(self, request) -> bool:
            self.requests.append(request)
            return request.action_type == "open_area"

    sink = RejectSelectionSink()
    controller.action_executor.sink = sink
    controller.state_machine.state = GameState.NAVIGATOR_PENDING
    controller._route_recovery_kind = "quest_accept"
    controller._navigator_target_name = "Недоступная локация"
    controller._navigator_client_id = "navigator-client"
    controller._navigator_requires_target_selection = True
    controller._navigator_opened_monotonic = time.monotonic()
    controller._navigator_client_bound_monotonic = time.monotonic() - 5
    controller._find_navigator_client = lambda: {"client_id": "navigator-client"}

    controller._handle_navigator_pending()

    assert controller.state_machine.state is GameState.QUEST_REFRESH_PENDING
    assert controller.last_error_reason is None
    assert [request.action_type for request in sink.requests] == [
        "navigator_select_target",
        "open_area",
    ]
    director = controller._quest_director
    assert director is not None
    assert director.chain.intake_quarantines
    assert director.pending_accept is None


def install_snapshots(
    monkeypatch, snapshots, *, client_id: object = "client-a",
    profile_id: str = "profile-a", tab_id: int = 42,
) -> list[str]:
    values = iter(snapshots)
    calls: list[str] = []

    class FakeInjector:
        def execute(self, command, payload=None, **kwargs):
            calls.append(command)
            return InjectorResult(
                True,
                json.dumps(next(values), ensure_ascii=False),
                client_id,  # type: ignore[arg-type]
            )

        def client_snapshot(self, observed_client_id):
            return {
                "client_id": observed_client_id,
                "profile_id": profile_id,
                "tab_id": tab_id,
                "href": "https://3kingdoms.ru/npc.php",
            }

    monkeypatch.setattr(
        "src.antibot_cv.automation.browser_injector.global_browser_injector",
        lambda: FakeInjector(),
    )
    return calls


def npc_open_proof(controller: AutomationController) -> dict[str, object]:
    staged = (
        controller._quest_director.chain.pending_npc_open
        or controller._quest_director.chain.pending_npc_dialog
    )
    assert staged is not None
    return {
        "ok": True, "truncated": False, "snapshotId": "npc-dialog-proof",
        "generatedAt": time.time(), "pageKind": "npc",
        "href": "https://3kingdoms.ru/npc.php?f_id=2",
        "expectedName": staged.giver_name, "expectedNpcId": staged.npc_id,
        "identityMatches": True, "npcId": staged.npc_id,
        "matchingHeaders": ["Царевич Придон"],
        "questActions": [{
            "questId": staged.quest_id, "npcId": staged.npc_id,
            "action": "open", "title": staged.quest_title,
            "visible": True, "disabled": False,
        }],
    }


def stage_dialog_action_directly(
    controller: AutomationController, action: str, *, dispatched: bool,
):
    director = controller._quest_director
    assert director is not None and director.pending_accept is not None
    dialog = director.chain.pending_npc_dialog
    assert dialog is not None
    if action in {"answer", "accept"} and not dialog.quest_opened:
        mark_dialog_opened_directly(controller)
        dialog = director.chain.pending_npc_dialog
        assert dialog is not None and dialog.quest_opened is True
    issued = time.time() - 0.5
    pending = make_pending_npc_quest_action(
        ref=director.pending_accept, client_id=dialog.client_id,
        profile_id=dialog.profile_id, tab_id=dialog.tab_id,
        giver_name=dialog.giver_name, npc_id=dialog.npc_id, action=action,
        expected_snapshot_id=f"npc-dialog-before-{action}",
        expected_generated_at=issued - 0.1, source_semantic_fingerprint="c" * 64,
        expected_ref="401" if action == "answer" else None,
        expected_text="Дальше" if action == "answer" else ("Взять задание" if action == "accept" else None),
        expected_title=director.pending_accept.title,
        quest_opened=dialog.quest_opened, dialog_steps=dialog.dialog_steps,
        issued_at=issued,
    )
    director.chain.stage_npc_quest_action(pending)
    return director.chain.mark_npc_quest_action_dispatched(pending) if dispatched else pending


def mark_dialog_opened_directly(controller: AutomationController) -> None:
    director = controller._quest_director
    assert director is not None and director.pending_accept is not None
    dialog = director.chain.pending_npc_dialog
    assert dialog is not None
    if not dialog.quest_opened:
        issued = time.time() - 1.0
        opened = make_pending_npc_quest_action(
            ref=director.pending_accept, client_id=dialog.client_id,
            profile_id=dialog.profile_id, tab_id=dialog.tab_id,
            giver_name=dialog.giver_name, npc_id=dialog.npc_id, action="open",
            expected_snapshot_id="npc-dialog-direct-open",
            expected_generated_at=issued - 0.1, source_semantic_fingerprint="a" * 64,
            expected_title=director.pending_accept.title,
            quest_opened=False, dialog_steps=0, issued_at=issued,
        )
        director.chain.stage_npc_quest_action(opened)
        opened = director.chain.mark_npc_quest_action_dispatched(opened)
        director.chain.settle_npc_dialog_action(opened, semantic_fingerprint="b" * 64)
        controller._quest_intake.mark_quest_opened()


def dialog_successor(controller: AutomationController) -> dict[str, object]:
    dialog = controller._quest_director.chain.pending_npc_dialog
    assert dialog is not None
    return {
        "ok": True, "truncated": False, "snapshotId": "npc-dialog-successor",
        "generatedAt": time.time(), "pageKind": "npc",
        "href": f"https://3kingdoms.ru/npc.php?f_id={dialog.npc_id}",
        "expectedName": dialog.giver_name, "expectedNpcId": dialog.npc_id,
        "identityMatches": True, "npcId": dialog.npc_id,
        "matchingHeaders": ["Царевич Придон"],
        "questActions": [],
        "dialogActions": [{
            "questId": dialog.quest_id, "npcId": dialog.npc_id,
            "action": "answer", "ref": "402", "text": "Новый шаг",
            "visible": True, "disabled": False,
        }],
        "acceptActions": [],
    }


def q269_ref() -> QuestRef:
    return QuestRef(
        "269", "Вступление в Чёрную лигу", location="След Велета",
        giver_names=("чёрного мага Вагарда",), catalog_page=0,
    )


def prepare_q269_opened_dialog(controller, monkeypatch) -> None:
    install_snapshots(monkeypatch, [area_snapshot(
        "area-npcs-q269", location_id="132", location_name="След Велета",
        items=[{"dataId": "10", "routeRef": "410", "name": "Башня Вагарда", "actionable": True}],
    )])
    assert controller._handle_pending_quest_acceptance() is True
    install_snapshots(monkeypatch, [npc_open_proof(controller)])
    assert controller._handle_pending_quest_acceptance() is True
    mark_dialog_opened_directly(controller)


def q269_answer_snapshot(controller, snapshot_id: str, generated_at: float, *, duplicate=False):
    value = dialog_successor(controller)
    action = {
        "questId": "269", "npcId": "10", "action": "answer",
        "ref": "3962", "pointId": "3961", "text": "Дальше",
        "visible": True, "disabled": False,
    }
    value.update({
        "snapshotId": snapshot_id, "generatedAt": generated_at,
        "matchingHeaders": ["Чёрный маг Вагард"],
        "headers": ["Чёрный маг Вагард"],
        "dialogActions": [action, dict(action)] if duplicate else [action],
    })
    return value


def test_missing_location_then_exact_giver_opens_npc_once(
    test_config: AutomationConfig,
    monkeypatch,
) -> None:
    controller, sink = acceptance_controller(test_config)
    calls = install_snapshots(
        monkeypatch,
        [
            area_snapshot("area-npcs-1", location_id=""),
            area_snapshot("area-npcs-2"),
        ],
    )

    assert controller._handle_pending_quest_acceptance() is True
    assert [request.action_type for request in sink.requests] == ["open_area"]
    assert controller.state_machine.state is GameState.QUEST_REFRESH_PENDING
    assert controller._handle_pending_quest_acceptance() is True

    assert calls == ["area_npc_snapshot", "area_npc_snapshot"]
    assert [request.action_type for request in sink.requests] == ["open_area", "open_exact_npc"]
    assert sink.requests[-1].metadata["npc_id"] == "2"
    assert sink.requests[-1].metadata["expected_name"] == "Царевич Придон"


def test_exact_empty_npc_returns_to_area_and_continues_with_next_quest(
    test_config: AutomationConfig,
    monkeypatch,
) -> None:
    controller, sink = acceptance_controller(test_config)
    install_snapshots(monkeypatch, [area_snapshot("area-npcs-empty-dialog")])
    assert controller._handle_pending_quest_acceptance() is True
    director = controller._quest_director
    assert director is not None and director.chain.pending_npc_open is not None
    staged = director.chain.pending_npc_open
    empty = {
        "ok": True, "truncated": False,
        "snapshotId": "npc-dialog-empty-exact",
        "generatedAt": time.time(), "pageKind": "npc",
        "href": f"https://3kingdoms.ru/npc.php?f_id={staged.npc_id}",
        "expectedName": staged.giver_name, "expectedNpcId": staged.npc_id,
        "identityMatches": True, "npcId": staged.npc_id,
        "matchingHeaders": ["Царевич Придон"],
        "questActions": [], "dialogActions": [],
    }
    install_snapshots(monkeypatch, [empty])

    assert controller._handle_pending_quest_acceptance() is True

    assert controller.last_error_reason is None
    assert [request.action_type for request in sink.requests] == [
        "open_area", "open_exact_npc", "open_area",
    ]
    assert director.chain.pending_npc_open is None
    assert controller._quest_intake.pending is None
    assert director.chain.intake_quarantines[-1].quest_id == "236"
    assert director.chain.intake_quarantines[-1].reason == (
        "quest_accept_exact_npc_has_no_quest_action"
    )
    assert director.decision().reason == "quest_intake_quarantine_recorded"
    decision = director.decision()
    assert decision.quest is not None and decision.quest.id == "237"


def test_quest_accept_navigator_timeout_is_local_and_returns_to_area(
    test_config: AutomationConfig,
) -> None:
    controller, sink = acceptance_controller(test_config)
    controller.state_machine.state = GameState.NAVIGATOR_PENDING
    controller._route_recovery_kind = "quest_accept"
    controller._navigator_target_name = "Просторы безмолвия"
    controller._navigator_requires_target_selection = True
    controller._navigator_opened_monotonic = time.monotonic() - 60
    controller._navigator_client_id = "navigator-client"
    controller._navigator_client_bound_monotonic = time.monotonic() - 60
    controller._find_navigator_client = lambda: {"client_id": "navigator-client"}

    controller._handle_navigator_pending()

    director = controller._quest_director
    assert director is not None
    assert controller.last_error_reason is None
    assert controller.state_machine.state is GameState.QUEST_REFRESH_PENDING
    assert [request.action_type for request in sink.requests] == ["open_area", "open_area"]
    assert controller._quest_intake.pending is None
    assert director.chain.intake_quarantines[-1].reason == (
        "quest_accept_navigation_unavailable"
    )
    assert controller._route_recovery_kind is None


def test_quest_accept_long_route_deadline_is_local_after_intermediate_progress(
    test_config: AutomationConfig,
) -> None:
    controller, sink = acceptance_controller(test_config)
    controller.state_machine.state = GameState.ROUTE_RECOVERY
    controller._route_recovery_kind = "quest_accept"
    controller._route_destination_name = "Жемчужный залив"
    controller._route_go_submitted_monotonic = time.monotonic() - 60
    controller._route_started_monotonic = time.monotonic() - 60
    controller._route_deadline_monotonic = time.monotonic() - 0.01
    controller._state_snapshot_via_injector = lambda force=False: {
        "schemaVersion": 1,
        "sections": {
            "location": {"data": {
                "pageKind": "area", "semanticName": "Курганы бренности",
            }},
            "battle": {"data": {"rawHasFight": False, "hasFight": False}},
            "deathRevive": {"data": {"dead": False}},
        },
    }

    controller._handle_route_recovery()

    director = controller._quest_director
    assert director is not None
    assert controller.last_error_reason is None
    assert controller.state_machine.state is GameState.QUEST_REFRESH_PENDING
    assert controller.current_location_name == "Курганы бренности"
    assert [request.action_type for request in sink.requests] == ["open_area", "open_area"]
    assert director.chain.intake_quarantines[-1].reason == (
        "quest_accept_navigation_unavailable"
    )


@pytest.mark.parametrize("confirmed", [False, True])
def test_restart_npc_stage_never_reissues_open_exact_npc(
    test_config: AutomationConfig, monkeypatch, tmp_path, confirmed: bool,
) -> None:
    data = to_plain_dict(test_config)
    data["runs_dir"] = str(tmp_path)
    data["leveling"] = {
        **data["leveling"], "required_character_name": "restart-npc-test",
    }
    configured = AutomationConfig.from_dict(data)
    first, _ = acceptance_controller(configured)
    install_snapshots(monkeypatch, [area_snapshot("area-npcs-restart")])
    assert first._handle_pending_quest_acceptance() is True
    director = first._quest_director
    assert director is not None and director.chain.pending_npc_open is not None
    staged = director.chain.pending_npc_open
    if confirmed:
        director.chain.settle_npc_open(staged)

    restarted = AutomationController(
        first.config, sink_mode="live", logger=InMemoryEventLogger(dry_run=False),
        browser_client_id="client-a",
    )
    restarted_sink = DryRunActionSink(restarted.logger)
    restarted.action_executor.sink = restarted_sink
    proof = npc_open_proof(first)
    install_snapshots(monkeypatch, [proof])
    assert restarted._handle_pending_quest_acceptance() is True
    assert all(request.action_type != "open_exact_npc" for request in restarted_sink.requests)


def test_restart_expired_accept_journal_stops_without_injector_or_mutation(
    test_config: AutomationConfig, monkeypatch, tmp_path,
) -> None:
    data = to_plain_dict(test_config)
    data["runs_dir"] = str(tmp_path)
    data["leveling"] = {
        **data["leveling"], "required_character_name": "restart-expired-accept",
    }
    first, _ = acceptance_controller(AutomationConfig.from_dict(data))
    install_snapshots(monkeypatch, [area_snapshot("area-npcs-expired")])
    assert first._handle_pending_quest_acceptance() is True
    install_snapshots(monkeypatch, [npc_open_proof(first)])
    assert first._handle_pending_quest_acceptance() is True

    director = first._quest_director
    assert director is not None and director.pending_accept is not None
    dialog = director.chain.pending_npc_dialog
    assert dialog is not None
    issued = time.time()
    opened = make_pending_npc_quest_action(
        ref=director.pending_accept, client_id=dialog.client_id,
        profile_id=dialog.profile_id, tab_id=dialog.tab_id,
        giver_name=dialog.giver_name, npc_id=dialog.npc_id, action="open",
        expected_snapshot_id="npc-dialog-before-open",
        expected_generated_at=issued - 0.1,
        source_semantic_fingerprint="a" * 64,
        expected_title=director.pending_accept.title,
        quest_opened=False, dialog_steps=0, issued_at=issued,
    )
    director.chain.stage_npc_quest_action(opened)
    opened = director.chain.mark_npc_quest_action_dispatched(opened)
    director.chain.settle_npc_dialog_action(opened, semantic_fingerprint="b" * 64)
    first._quest_intake.mark_quest_opened()

    dialog = director.chain.pending_npc_dialog
    assert dialog is not None and dialog.quest_opened is True
    accepting = make_pending_npc_quest_action(
        ref=director.pending_accept, client_id=dialog.client_id,
        profile_id=dialog.profile_id, tab_id=dialog.tab_id,
        giver_name=dialog.giver_name, npc_id=dialog.npc_id, action="accept",
        expected_snapshot_id="npc-dialog-before-accept",
        expected_generated_at=issued,
        source_semantic_fingerprint="b" * 64,
        expected_text="Взять задание", expected_title=director.pending_accept.title,
        quest_opened=True, dialog_steps=0, issued_at=issued + 0.1,
        timeout_s=1.0,
    )
    director.chain.stage_npc_quest_action(accepting)
    accepting = director.chain.mark_npc_quest_action_dispatched(accepting)

    restarted = AutomationController(
        first.config, sink_mode="live", logger=InMemoryEventLogger(dry_run=False),
        browser_client_id="client-a",
    )
    restarted_sink = DryRunActionSink(restarted.logger)
    restarted.action_executor.sink = restarted_sink

    class ForbiddenInjector:
        def execute(self, *args, **kwargs):
            raise AssertionError("expired accept journal must not read or mutate through injector")

    monkeypatch.setattr(
        "src.antibot_cv.automation.browser_injector.global_browser_injector",
        lambda: ForbiddenInjector(),
    )
    monkeypatch.setattr(
        "src.antibot_cv.automation.quest_acceptance_coordinator.time.time",
        lambda: accepting.deadline,
    )

    assert restarted._handle_pending_quest_acceptance() is True
    assert restarted.state_machine.state is GameState.STOPPED
    assert restarted.last_error_reason == "quest_accept_action_active_proof_missing"
    assert restarted_sink.requests == []


@pytest.mark.parametrize("action", ["open", "answer"])
@pytest.mark.parametrize("dispatched", [False, True])
def test_restart_dialog_action_never_reissues_and_settles_from_read_only_snapshot(
    test_config: AutomationConfig, monkeypatch, tmp_path, action: str, dispatched: bool,
) -> None:
    data = to_plain_dict(test_config)
    data["runs_dir"] = str(tmp_path)
    data["leveling"] = {
        **data["leveling"], "required_character_name": f"restart-{action}-{dispatched}",
    }
    first, _ = acceptance_controller(AutomationConfig.from_dict(data))
    install_snapshots(monkeypatch, [area_snapshot("area-npcs-action-restart")])
    assert first._handle_pending_quest_acceptance() is True
    install_snapshots(monkeypatch, [npc_open_proof(first)])
    assert first._handle_pending_quest_acceptance() is True
    staged = stage_dialog_action_directly(first, action, dispatched=dispatched)

    restarted = AutomationController(
        first.config, sink_mode="live", logger=InMemoryEventLogger(dry_run=False),
        browser_client_id="client-a",
    )
    restarted_sink = DryRunActionSink(restarted.logger)
    restarted.action_executor.sink = restarted_sink
    calls = install_snapshots(monkeypatch, [dialog_successor(restarted)])

    assert restarted._handle_pending_quest_acceptance() is True

    assert calls == ["npc_dialog_snapshot"]
    assert restarted_sink.requests == []
    assert restarted._quest_director.chain.pending_npc_action is None
    settled = restarted._quest_director.chain.pending_npc_dialog
    assert settled is not None
    assert settled.quest_opened is True
    assert settled.dialog_steps == (1 if action == "answer" else 0)
    assert staged.action.value == action


def test_restart_accept_verify_settles_after_complete_active_refresh(
    test_config: AutomationConfig, monkeypatch, tmp_path,
) -> None:
    data = to_plain_dict(test_config)
    data["runs_dir"] = str(tmp_path)
    data["leveling"] = {
        **data["leveling"], "required_character_name": "restart-accept-success",
    }
    first, _ = acceptance_controller(AutomationConfig.from_dict(data))
    install_snapshots(monkeypatch, [area_snapshot("area-npcs-accept-restart")])
    assert first._handle_pending_quest_acceptance() is True
    install_snapshots(monkeypatch, [npc_open_proof(first)])
    assert first._handle_pending_quest_acceptance() is True
    staged = stage_dialog_action_directly(first, "accept", dispatched=True)

    restarted = AutomationController(
        first.config, sink_mode="live", logger=InMemoryEventLogger(dry_run=False),
        browser_client_id="client-a",
    )
    restarted_sink = DryRunActionSink(restarted.logger)
    restarted.action_executor.sink = restarted_sink
    director = restarted._quest_director
    assert director is not None
    director.begin_active_refresh()
    assert director.ingest_active_page({
        "loadStatus": "loaded", "mode": "started", "currentPage": 0,
        "pageCount": 1, "hasNextPage": False, "truncated": False,
        "items": [{
            "id": staged.quest_id, "title": staged.quest_title, "status": "active",
            "objective": "Убить Волк [5]",
            "navigation": [{"text": "Волк [5]", "target": "Волк [5]"}],
            "progress": None,
        }],
    }) is None

    assert restarted._handle_pending_quest_acceptance() is False

    assert restarted_sink.requests == []
    assert director.chain.pending_npc_action is None
    assert director.chain.pending_npc_dialog is None
    assert director.chain.pending_accepted_ref is None
    assert director.chain.lease is not None
    assert director.chain.lease.quest_id == staged.quest_id
    assert restarted._quest_intake.pending is None


def test_transient_snapshots_timeout_with_zero_npc_mutation(
    test_config: AutomationConfig,
    monkeypatch,
) -> None:
    controller, sink = acceptance_controller(test_config)
    install_snapshots(
        monkeypatch,
        [area_snapshot("area-npcs-1", location_id=""), area_snapshot("area-npcs-2", items=[])],
    )

    assert controller._handle_pending_quest_acceptance() is True
    controller._quest_accept_area_opened_monotonic = time.monotonic() - 60
    assert controller._handle_pending_quest_acceptance() is True

    assert controller.state_machine.state is GameState.QUEST_REFRESH_PENDING
    assert controller.last_error_reason is None
    assert [request.action_type for request in sink.requests] == ["open_area"]
    waits = [event for event in controller.logger.events if event["event_type"] == "quest_accept_npc_settle_wait"]
    assert [event["reason"] for event in waits] == [
        "quest_accept_location_identity_pending",
        "quest_accept_giver_not_observed",
    ]
    director = controller._quest_director
    assert director is not None
    assert [item.quest_id for item in director.chain.intake_quarantines] == ["236"]
    assert director.chain.intake_quarantines[0].reason == "quest_accept_giver_not_observed"
    assert controller._quest_intake.pending is None
    assert director.decision().reason == "quest_intake_quarantine_recorded"
    next_decision = director.decision()
    assert next_decision.quest is not None
    assert next_decision.quest.id == "237"


@pytest.mark.parametrize(
    ("snapshot", "last_reason"),
    (
        ({"ok": False}, "quest_accept_npc_snapshot_unavailable"),
        (
            {
                "ok": True,
                "truncated": True,
                "snapshotId": "area-npcs-truncated",
                "generatedAt": 1.0,
            },
            "quest_accept_npc_snapshot_truncated",
        ),
        (
            area_snapshot("area-npcs-time-missing", generated_at=""),
            "quest_accept_npc_snapshot_time_pending",
        ),
        (
            area_snapshot("area-npcs-stale", generated_at=1.0),
            "quest_accept_npc_snapshot_before_area_open",
        ),
    ),
)
def test_shape_or_freshness_timeout_is_global_not_quarantined(
    test_config: AutomationConfig,
    monkeypatch,
    snapshot: dict[str, object],
    last_reason: str,
) -> None:
    controller, sink = acceptance_controller(test_config)
    controller._quest_accept_area_opened_monotonic = time.monotonic() - 60
    install_snapshots(monkeypatch, [snapshot])

    assert controller._handle_pending_quest_acceptance() is True

    assert controller.state_machine.state is GameState.STOPPED
    assert controller.last_error_reason == (
        f"quest_accept_npc_snapshot_settle_timeout:{last_reason}"
    )
    assert controller._quest_director is not None
    assert controller._quest_director.chain.intake_quarantines == ()
    assert controller._quest_intake.pending is not None
    assert [request.action_type for request in sink.requests] == ["open_area"]


def test_reused_snapshot_timeout_is_global_not_quarantined(
    test_config: AutomationConfig,
    monkeypatch,
) -> None:
    controller, sink = acceptance_controller(test_config)
    snapshot = area_snapshot("area-npcs-reused-timeout", location_id="")
    install_snapshots(monkeypatch, [snapshot, snapshot])
    assert controller._handle_pending_quest_acceptance() is True
    controller._quest_accept_area_opened_monotonic = time.monotonic() - 60

    assert controller._handle_pending_quest_acceptance() is True

    assert controller.state_machine.state is GameState.STOPPED
    assert controller.last_error_reason == (
        "quest_accept_npc_snapshot_settle_timeout:"
        "quest_accept_npc_snapshot_not_fresh"
    )
    assert controller._quest_director is not None
    assert controller._quest_director.chain.intake_quarantines == ()
    assert [request.action_type for request in sink.requests] == ["open_area"]


def test_ambiguous_giver_and_lookup_client_provenance_stop_immediately(
    test_config: AutomationConfig,
    monkeypatch,
) -> None:
    controller, sink = acceptance_controller(test_config)
    ambiguous = area_snapshot(
        "area-npcs-ambiguous",
        items=[
            {"dataId": "2", "routeRef": "402", "name": "Царевич Придон", "actionable": True},
            {"dataId": "3", "routeRef": "403", "name": "Царевич Придон", "actionable": True},
        ],
    )
    install_snapshots(monkeypatch, [ambiguous])

    assert controller._handle_pending_quest_acceptance() is True
    assert controller.last_error_reason is None
    assert controller.state_machine.state is GameState.QUEST_REFRESH_PENDING
    assert [request.action_type for request in sink.requests] == ["open_area"]
    assert controller._quest_director is not None
    assert controller._quest_director.chain.intake_quarantines[0].reason == "quest_accept_giver_ambiguous"

    for client_id in (None, "client-b"):
        controller, sink = acceptance_controller(test_config)
        install_snapshots(monkeypatch, [area_snapshot("area-npcs-client")], client_id=client_id)
        assert controller._handle_pending_quest_acceptance() is True
        assert controller.last_error_reason == "quest_accept_npc_client_mismatch"
        assert [request.action_type for request in sink.requests] == ["open_area"]


def test_dialog_missing_or_mismatched_client_stops_before_action(
    test_config: AutomationConfig,
    monkeypatch,
) -> None:
    for client_id in (None, "client-b"):
        controller, sink = acceptance_controller(test_config)
        install_snapshots(monkeypatch, [area_snapshot("area-npcs-ready")])
        assert controller._handle_pending_quest_acceptance() is True
        baseline = len(sink.requests)
        assert sink.requests[-1].action_type == "open_exact_npc"
        install_snapshots(monkeypatch, [{}], client_id=client_id)

        assert controller._handle_pending_quest_acceptance() is True
        assert controller.last_error_reason == "npc_open_identity_mismatch"
        assert len(sink.requests) == baseline


def test_dialog_pre_action_ambiguity_is_quest_local_and_action_free(
    test_config: AutomationConfig,
    monkeypatch,
) -> None:
    controller, sink = acceptance_controller(test_config)
    install_snapshots(monkeypatch, [area_snapshot("area-npcs-ready")])
    assert controller._handle_pending_quest_acceptance() is True
    baseline = len(sink.requests)
    install_snapshots(monkeypatch, [npc_open_proof(controller)])
    assert controller._handle_pending_quest_acceptance() is True
    ambiguous_dialog = {
        "ok": True,
        "truncated": False,
        "identityMatches": True,
        "snapshotId": "npc-dialog-ambiguous",
        "headers": [],
        "actions": [],
        "questActions": [
            {"questId": "236", "title": "Послание царевичу", "action": "open", "visible": True, "disabled": False},
            {"questId": "236", "title": "Послание царевичу", "action": "open", "visible": True, "disabled": False},
        ],
        "dialogActions": [],
        "acceptActions": [],
    }
    install_snapshots(monkeypatch, [ambiguous_dialog])

    assert controller._handle_pending_quest_acceptance() is True

    assert controller.state_machine.state is GameState.STOPPED
    assert controller.last_error_reason == "quest_accept_open_action_missing_or_ambiguous"
    assert len(sink.requests) == baseline
    assert controller._quest_director is not None
    assert controller._quest_director.chain.intake_quarantines == ()
    assert controller._quest_director.chain.pending_npc_dialog is not None
    assert controller._quest_intake.pending is not None
    assert all(
        request.action_type != "npc_quest_action"
        for request in sink.requests[baseline:]
    )


def test_q269_transient_duplicate_waits_for_two_fresh_unique_answer_snapshots(
    test_config: AutomationConfig, monkeypatch,
) -> None:
    q269 = QuestRef(
        "269", "Вступление в Чёрную лигу", location="След Велета",
        giver_names=("чёрного мага Вагарда",), catalog_page=0,
    )
    controller, sink = acceptance_controller(test_config, q269)
    install_snapshots(monkeypatch, [area_snapshot(
        "area-npcs-q269", location_id="132", location_name="След Велета",
        items=[{"dataId": "10", "routeRef": "410", "name": "Башня Вагарда", "actionable": True}],
    )])
    assert controller._handle_pending_quest_acceptance() is True
    install_snapshots(monkeypatch, [npc_open_proof(controller)])
    assert controller._handle_pending_quest_acceptance() is True
    mark_dialog_opened_directly(controller)
    baseline = len(sink.requests)
    base = dialog_successor(controller)
    base.update({
        "matchingHeaders": ["Чёрный маг Вагард"],
        "headers": ["Чёрный маг Вагард"],
        "actions": [
            {"text": "Магазин Чёрной лиги Далее"},
            {"text": "Магазин Чёрной лиги Далее"},
            {"text": "Вступление в Чёрную лигу Далее"},
            {"text": "Вступление в Чёрную лигу Далее"},
        ],
        "dialogActions": [{
            "questId": "269", "npcId": "10", "action": "answer",
            "ref": "3962", "pointId": "3961", "text": "Дальше",
            "visible": True, "disabled": False,
        }],
    })
    observed = time.time()
    monkeypatch.setattr(
        "src.antibot_cv.automation.quest_acceptance_coordinator.time.time",
        lambda: observed + 1.0,
    )
    ambiguous = {
        **base, "snapshotId": "npc-dialog-q269-ambiguous", "generatedAt": observed + 0.1,
        "dialogActions": [base["dialogActions"][0], dict(base["dialogActions"][0])],
    }
    unique_first = {**base, "snapshotId": "npc-dialog-q269-unique-1", "generatedAt": observed + 0.2}
    unique_second = {**base, "snapshotId": "npc-dialog-q269-unique-2", "generatedAt": observed + 0.3}
    calls = install_snapshots(monkeypatch, [ambiguous, unique_first, unique_second])

    assert controller._handle_pending_quest_acceptance() is True
    assert [request.action_type for request in sink.requests[baseline:]] == []
    assert controller._handle_pending_quest_acceptance() is True
    assert [request.action_type for request in sink.requests[baseline:]] == []
    assert controller._handle_pending_quest_acceptance() is True

    assert calls == ["npc_dialog_snapshot", "npc_dialog_snapshot", "npc_dialog_snapshot"]
    assert [request.action_type for request in sink.requests[baseline:]] == ["npc_quest_action"], (
        controller.last_error_reason, getattr(controller, "_quest_answer_stability", None),
    )
    assert sink.requests[-1].metadata["action"] == "answer"
    assert sink.requests[-1].metadata["expected_ref"] == "3962"
    assert controller.state_machine.state is not GameState.STOPPED


def test_q269_cached_unique_pair_not_newer_than_ambiguity_is_action_free(
    test_config: AutomationConfig, monkeypatch,
) -> None:
    controller, sink = acceptance_controller(test_config, q269_ref())
    prepare_q269_opened_dialog(controller, monkeypatch)
    baseline = len(sink.requests)
    observed = time.time()
    monkeypatch.setattr(
        "src.antibot_cv.automation.quest_acceptance_coordinator.time.time",
        lambda: observed + 1.0,
    )
    snapshots = [
        q269_answer_snapshot(controller, "npc-dialog-ambiguous-watermark", observed + 0.4, duplicate=True),
        q269_answer_snapshot(controller, "npc-dialog-stale-unique-1", observed + 0.2),
        q269_answer_snapshot(controller, "npc-dialog-stale-unique-2", observed + 0.3),
    ]
    install_snapshots(monkeypatch, snapshots)

    for _ in snapshots:
        assert controller._handle_pending_quest_acceptance() is True

    assert sink.requests[baseline:] == []
    stage = controller._quest_director.chain.pending_npc_dialog
    assert stage is not None
    assert stage.answer_ambiguity_snapshot_id == "npc-dialog-ambiguous-watermark"


def test_q269_same_client_with_changed_profile_or_tab_stops_action_free(
    test_config: AutomationConfig, monkeypatch,
) -> None:
    for profile_id, tab_id in (("profile-b", 42), ("profile-a", 43)):
        controller, sink = acceptance_controller(test_config, q269_ref())
        prepare_q269_opened_dialog(controller, monkeypatch)
        baseline = len(sink.requests)
        observed = time.time()
        monkeypatch.setattr(
            "src.antibot_cv.automation.quest_acceptance_coordinator.time.time",
            lambda: observed + 1.0,
        )
        install_snapshots(
            monkeypatch,
            [q269_answer_snapshot(controller, "npc-dialog-profile-mismatch", observed + 0.2)],
            profile_id=profile_id, tab_id=tab_id,
        )

        assert controller._handle_pending_quest_acceptance() is True
        assert controller.state_machine.state is GameState.STOPPED
        assert sink.requests[baseline:] == []


@pytest.mark.parametrize("restart_after_first_unique", [False, True])
def test_q269_restart_after_ambiguity_requires_new_fresh_pair(
    test_config: AutomationConfig, monkeypatch, tmp_path, restart_after_first_unique: bool,
) -> None:
    data = to_plain_dict(test_config)
    data["runs_dir"] = str(tmp_path)
    data["leveling"] = {
        **data["leveling"],
        "required_character_name": f"q269-stability-{restart_after_first_unique}",
    }
    first, first_sink = acceptance_controller(AutomationConfig.from_dict(data), q269_ref())
    prepare_q269_opened_dialog(first, monkeypatch)
    first_baseline = len(first_sink.requests)
    observed = time.time()
    monkeypatch.setattr(
        "src.antibot_cv.automation.quest_acceptance_coordinator.time.time",
        lambda: observed + 1.0,
    )
    install_snapshots(monkeypatch, [
        q269_answer_snapshot(first, "npc-dialog-restart-ambiguity", observed + 0.2, duplicate=True),
    ])
    assert first._handle_pending_quest_acceptance() is True
    assert first_sink.requests[first_baseline:] == []
    if restart_after_first_unique:
        install_snapshots(monkeypatch, [
            q269_answer_snapshot(first, "npc-dialog-pre-restart-unique", observed + 0.3),
        ])
        assert first._handle_pending_quest_acceptance() is True
        assert first_sink.requests[first_baseline:] == []

    restarted = AutomationController(
        first.config, sink_mode="live", logger=InMemoryEventLogger(dry_run=False),
        browser_client_id="client-a",
    )
    restarted_sink = DryRunActionSink(restarted.logger)
    restarted.action_executor.sink = restarted_sink
    monkeypatch.setattr(
        "src.antibot_cv.automation.quest_acceptance_coordinator.time.time",
        lambda: observed + 2.0,
    )
    install_snapshots(monkeypatch, [
        q269_answer_snapshot(restarted, "npc-dialog-post-restart-unique-1", observed + 0.4),
        q269_answer_snapshot(restarted, "npc-dialog-post-restart-unique-2", observed + 0.5),
    ])

    assert restarted._handle_pending_quest_acceptance() is True
    assert restarted_sink.requests == []
    assert restarted._handle_pending_quest_acceptance() is True
    assert [request.action_type for request in restarted_sink.requests] == ["npc_quest_action"]


def test_q269_crash_before_action_stage_has_zero_mutation(
    test_config: AutomationConfig, monkeypatch,
) -> None:
    controller, sink = acceptance_controller(test_config, q269_ref())
    prepare_q269_opened_dialog(controller, monkeypatch)
    baseline = len(sink.requests)
    observed = time.time()
    monkeypatch.setattr(
        "src.antibot_cv.automation.quest_acceptance_coordinator.time.time",
        lambda: observed + 1.0,
    )
    install_snapshots(monkeypatch, [
        q269_answer_snapshot(controller, "npc-dialog-crash-unique-1", observed + 0.2),
        q269_answer_snapshot(controller, "npc-dialog-crash-unique-2", observed + 0.3),
    ])
    assert controller._handle_pending_quest_acceptance() is True
    monkeypatch.setattr(
        controller._quest_director.chain, "stage_npc_quest_action",
        lambda pending: (_ for _ in ()).throw(OSError("crash-before-stage")),
    )

    assert controller._handle_pending_quest_acceptance() is True
    assert controller.state_machine.state is GameState.STOPPED
    assert sink.requests[baseline:] == []


def test_executor_failure_and_quarantine_binding_mismatch_remain_global(
    test_config: AutomationConfig,
    monkeypatch,
) -> None:
    controller, sink = acceptance_controller(test_config)
    install_snapshots(monkeypatch, [area_snapshot("area-npcs-executor")])
    controller.action_executor.execute = lambda request: False  # type: ignore[method-assign]

    assert controller._handle_pending_quest_acceptance() is True
    assert controller.state_machine.state is GameState.STOPPED
    assert controller.last_error_reason == "quest_accept_npc_open_failed"
    assert [request.action_type for request in sink.requests] == ["open_area"]
    assert controller._quest_director is not None
    assert controller._quest_director.chain.intake_quarantines == ()
    assert controller._quest_intake.pending is not None

    controller, sink = acceptance_controller(test_config)
    assert controller._quest_director is not None
    director = controller._quest_director
    director.intake_queue = tuple(reversed(director.intake_queue))
    ambiguous = area_snapshot(
        "area-npcs-binding-mismatch",
        items=[
            {"dataId": "2", "routeRef": "402", "name": "Царевич Придон", "actionable": True},
            {"dataId": "3", "routeRef": "403", "name": "Царевич Придон", "actionable": True},
        ],
    )
    install_snapshots(monkeypatch, [ambiguous])
    assert controller._handle_pending_quest_acceptance() is True
    assert controller.state_machine.state is GameState.STOPPED
    assert controller.last_error_reason == "quest_accept_quarantine_binding_mismatch"
    assert director.chain.intake_quarantines == ()
    assert director.pending_accept is not None
    assert controller._quest_intake.pending is not None


def test_verify_started_ambiguity_is_global_and_never_quarantined(
    test_config: AutomationConfig,
) -> None:
    controller, sink = acceptance_controller(test_config)
    pending = controller._quest_intake.pending
    assert pending is not None
    controller._quest_intake.pending = replace(
        pending,
        phase=QuestAcceptPhase.VERIFY_STARTED,
        accept_submitted=True,
    )

    assert controller._handle_pending_quest_acceptance() is True

    assert controller.state_machine.state is GameState.STOPPED
    assert controller.last_error_reason == (
        "quest_accept_verify:accepted quest is missing from active snapshot"
    )
    assert controller._quest_director is not None
    assert controller._quest_director.chain.intake_quarantines == ()
    assert controller._quest_director.pending_accept is not None
    assert [request.action_type for request in sink.requests] == ["open_area"]


def test_repeated_snapshot_waits_without_mutation_or_deadline_reset(
    test_config: AutomationConfig,
    monkeypatch,
) -> None:
    controller, sink = acceptance_controller(test_config)
    snapshot = area_snapshot("area-npcs-repeat", location_id="")
    install_snapshots(monkeypatch, [snapshot, snapshot])
    deadline_start = controller._quest_accept_area_opened_monotonic

    assert controller._handle_pending_quest_acceptance() is True
    assert controller._handle_pending_quest_acceptance() is True

    assert controller._quest_accept_area_opened_monotonic == deadline_start
    assert [request.action_type for request in sink.requests] == ["open_area"]
    waits = [event for event in controller.logger.events if event["event_type"] == "quest_accept_npc_settle_wait"]
    assert waits[-1]["reason"] == "quest_accept_npc_snapshot_not_fresh"


def test_cached_first_snapshot_before_area_open_waits_without_mutation(
    test_config: AutomationConfig,
    monkeypatch,
) -> None:
    cached = area_snapshot("area-npcs-cached", generated_at=time.time() - 60)
    controller, sink = acceptance_controller(test_config)
    install_snapshots(monkeypatch, [cached])

    assert controller._handle_pending_quest_acceptance() is True
    assert [request.action_type for request in sink.requests] == ["open_area"]
    assert controller.logger.events[-1]["reason"] == "quest_accept_npc_snapshot_before_area_open"


def test_client_contract_is_strict_live_and_explicit_for_dry_run() -> None:
    assert acceptance_client_matches("client-a", "client-a", dry_run=False) is True
    assert acceptance_client_matches("client-a", None, dry_run=False) is False
    assert acceptance_client_matches(None, "client-a", dry_run=False) is False
    assert acceptance_client_matches(None, None, dry_run=False) is False
    assert acceptance_client_matches(None, None, dry_run=True) is True
    assert acceptance_client_matches("client-a", "client-a", dry_run=True) is True


def test_settle_policy_rejects_wrong_location_and_reused_snapshot() -> None:
    wrong = assess_acceptance_area_snapshot(
        area_snapshot("area-npcs-wrong", location_name="Городская площадь Арсы"),
        expected_location="Пригород Арсы",
        expected_giver="Царевича Придона",
        previous_snapshot_id=None,
        area_opened_epoch=0,
    )
    reused = assess_acceptance_area_snapshot(
        area_snapshot("area-npcs-reused"),
        expected_location="Пригород Арсы",
        expected_giver="Царевича Придона",
        previous_snapshot_id="area-npcs-reused",
        area_opened_epoch=0,
    )

    assert wrong.intent is AcceptanceSettleIntent.STOP_UNSAFE
    assert wrong.reason == "quest_accept_location_mismatch"
    assert reused.intent is AcceptanceSettleIntent.WAIT
    assert reused.reason == "quest_accept_npc_snapshot_not_fresh"


def test_settle_policy_accepts_zero_id_building_proxy_for_inflected_giver() -> None:
    decision = assess_acceptance_area_snapshot(
        {
            "ok": True,
            "truncated": False,
            "snapshotId": "area-npcs-live-vasilisa",
            "generatedAt": "2026-07-20T12:59:56.420Z",
            "location": {"id": "127", "name": "Пристанище трёх ветров"},
            "items": [
                {"name": "Дом Василисы", "dataId": "0", "routeRef": "420", "actionable": True},
                {"name": "Дом Торвара", "dataId": "3", "routeRef": "423", "actionable": True},
            ],
        },
        expected_location="Пристанище трёх ветров",
        expected_giver="крестьянки Василисы",
        previous_snapshot_id=None,
        area_opened_epoch=1_700_000_000.0,
    )

    assert decision.intent is AcceptanceSettleIntent.READY
    assert decision.reason == "quest_accept_giver_ready"
