from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from src.antibot_cv.automation.area_object_activity import parse_area_object_plan
from src.antibot_cv.automation.quest_active_catalog import ActiveQuestEntry
from src.antibot_cv.automation.quest_area_object_runtime import QuestAreaObjectRuntime
from src.antibot_cv.automation.quest_compiler import compile_quest_plan
from src.antibot_cv.automation.quest_objective_runtime import (
    MonsterTarget,
    ObjectiveKind,
    QuestObjective,
)
from src.antibot_cv.automation.quest_refresh_runtime import QuestRefreshRuntimeMixin
from src.antibot_cv.automation.quest_turnin_admission import ActiveCatalogAuthority
from src.antibot_cv.automation.quest_plan_evaluator import EvaluationStatus, evaluate_quest_plan


OBJECTIVE = (
    "Убивая Непобедимых кабанов, получите 5 пузырьков крови, также "
    "найдите в Пристанище трёх ветров Светящийся мох, в Длани Рода Пятнистый гриб "
    "и 5 свежих листьев кустарника на Просторах безмолвия. Собрав необходимое, "
    "возвращайтесь к колдунье Вилене."
)


def _entry() -> ActiveQuestEntry:
    data = {
        "id": "304", "title": "Цветочная болезнь", "status": "active",
        "objective": OBJECTIVE, "objectiveKind": "collect",
        "navigation": [
            {"text": "Непобедимый кабан", "target": "Непобедимый кабан [5]"},
            {"text": "Пристанище трёх ветров", "target": "Пристанище трёх ветров"},
            {"text": "Длани Рода", "target": "Длань Рода"},
            {"text": "Просторах безмолвия", "target": "Просторы безмолвия"},
        ],
    }
    return ActiveQuestEntry("304", "Цветочная болезнь", data)


def _snapshot(revision: int, *items: tuple[str, int], **updates: object) -> dict[str, object]:
    result: dict[str, object] = {
        "ok": True, "category": "quest", "categoryConfirmed": True,
        "truncated": False, "snapshotId": f"inventory-{revision}",
        "generatedAt": 100.0 + revision, "revision": revision,
        "clientId": "client", "profileId": "profile", "tabId": "7",
        "causalBaseline": "catalog-base",
        "items": [{"artAltTitle": name, "count": count} for name, count in items],
    }
    result.update(updates)
    return result


class _Sink:
    def __init__(self, snapshot: dict[str, object]) -> None:
        self.last_quest_inventory_snapshot = snapshot
        self.last_area_object_result = None
        self.requests = []

    def execute(self, request) -> bool:
        self.requests.append(request)
        if request.action_type == "area_object_snapshot":
            self.last_area_object_result = {
                "ok": True, "snapshotId": "area-1",
                "candidates": [{"candidateId": "c1", "fingerprint": "f1"}],
            }
        return True


class _Runtime(QuestRefreshRuntimeMixin):
    def __init__(self, snapshot: dict[str, object]) -> None:
        entry = _entry()
        compiled = compile_quest_plan(entry)
        assert compiled.plan is not None
        objective = QuestObjective(
            ObjectiveKind.MONSTER_HUNT, "304", "Цветочная болезнь", OBJECTIVE,
            "objective-fingerprint", MonsterTarget("Непобедимый кабан [5]", "Непобедимый кабан", 5),
            "Непобедимых кабанов", 5, 5, False, 0,
        )
        self.config = SimpleNamespace(
            dry_run=True,
            leveling=SimpleNamespace(
                quest_engine_mode="q280_q304", snapshot_stale_timeout_ms=30_000,
            ),
            item_recovery=SimpleNamespace(inventory_open_delay_ms=0),
        )
        self.session = SimpleNamespace(cycle_id=1, battle_id=None)
        self.action_executor = SimpleNamespace(sink=_Sink(snapshot), execute=lambda request: self.action_executor.sink.execute(request))
        self._quest_director = SimpleNamespace(
            active_objective=objective,
            active_catalog=SimpleNamespace(result=(entry,)),
            chain=SimpleNamespace(lease=SimpleNamespace(current_fingerprint="lease-fingerprint")),
        )
        self._quest_active_catalog_authority = ActiveCatalogAuthority(
            (entry,), 1, "catalog-1", 100.0, 30.0,
            "client", "profile", "7", "catalog-base",
            compiled.plan.fingerprint, "lease-fingerprint",
        )
        self._quest_area_objects = QuestAreaObjectRuntime()
        self.logger = SimpleNamespace(log_event=lambda *args, **kwargs: None)
        self.state_machine = SimpleNamespace(state=SimpleNamespace(value="quest_refresh_pending"))
        self.current_page_kind = "area"
        self.current_location_name = "Пристанище трёх ветров"
        self.stopped_reason = None

    def _stop_leveling_unsafe(self, reason: str) -> bool:
        self.stopped_reason = reason
        return True


def test_q304_inventory_evidence_is_merged_by_requirement_id(monkeypatch) -> None:
    monkeypatch.setattr("src.antibot_cv.automation.quest_refresh_runtime.time.time", lambda: 108.0)
    runtime = _Runtime(_snapshot(
        7, ("Кровь Непобедимого кабана", 5), ("Светящийся мох", 1),
    ))
    plan = parse_area_object_plan(_entry())

    assert runtime._inspect_quest_area_inventory(plan) is not None
    assert runtime.action_executor.sink.requests[0].metadata["causal_baseline"] == "catalog-base"
    assert runtime.action_executor.sink.requests[0].metadata["minimum_revision"] == 1
    first = runtime._quest_semantic_evidence
    assert len(first) == 2
    assert len({item.requirement_id for item in first}) == 2
    assert runtime._quest_semantic_evaluation_context.min_revision == 7
    assert runtime._quest_semantic_evaluation_context.now == 108.0

    runtime.action_executor.sink.last_quest_inventory_snapshot = _snapshot(
        8,
        ("Кровь Непобедимого кабана", 5),
        ("Светящийся мох", 1),
        ("Пятнистый гриб", 1),
    )
    assert runtime._inspect_quest_area_inventory(plan) is not None
    assert len(runtime._quest_semantic_evidence) == 3
    assert len({item.requirement_id for item in runtime._quest_semantic_evidence}) == 3
    assert runtime._quest_semantic_evaluation_context.min_revision == 7
    revisions = {item.fact.subject: item.revision for item in runtime._quest_semantic_evidence}
    assert revisions["Пузырёк крови"] == 7
    assert revisions["Пятнистый гриб"] == 8


def test_q304_missing_provenance_fails_before_area_route(monkeypatch) -> None:
    monkeypatch.setattr("src.antibot_cv.automation.quest_refresh_runtime.time.time", lambda: 108.0)
    runtime = _Runtime(_snapshot(
        7, ("Кровь Непобедимого кабана", 5), causalBaseline=None,
    ))

    assert runtime._begin_quest_area_object_executor(parse_area_object_plan(_entry())) is True
    assert runtime.stopped_reason == "quest_area_object_inventory_unconfirmed"
    assert runtime._quest_area_objects.pending is None
    assert [item.action_type for item in runtime.action_executor.sink.requests] == [
        "inspect_quest_inventory"
    ]


def test_q304_foreign_semantic_binding_blocks_area_click(monkeypatch) -> None:
    monkeypatch.setattr("src.antibot_cv.automation.quest_refresh_runtime.time.time", lambda: 108.0)
    runtime = _Runtime(_snapshot(
        7, ("Кровь Непобедимого кабана", 5),
    ))
    plan = parse_area_object_plan(_entry())
    items = runtime._inspect_quest_area_inventory(plan)
    assert items is not None
    pending = runtime._quest_area_objects.begin(plan, items)
    assert pending is not None
    runtime.current_location_name = pending.requirement.location
    runtime._quest_area_objects.mark_route_arrived(runtime.current_location_name)
    runtime._quest_semantic_evidence = (
        replace(runtime._quest_semantic_evidence[0], client_id="foreign"),
    )
    runtime.action_executor.sink.requests.clear()

    assert runtime._continue_quest_area_object_executor() is True
    assert runtime.stopped_reason == "quest_area_object_semantic_evidence_unbound"
    assert runtime.action_executor.sink.requests == []


def test_q304_already_local_requires_typed_route_binding(monkeypatch) -> None:
    monkeypatch.setattr("src.antibot_cv.automation.quest_refresh_runtime.time.time", lambda: 108.0)
    runtime = _Runtime(_snapshot(7, ("Кровь Непобедимого кабана", 5)))
    checked: list[tuple[str, str]] = []
    runtime._validate_route_coordinator_binding = lambda kind, target: checked.append((kind, target)) or False

    assert runtime._begin_quest_area_object_executor(parse_area_object_plan(_entry())) is True
    assert checked == [("quest_area_object", "Пристанище трёх ветров")]
    assert runtime._quest_area_objects.pending.phase.value == "route"
    assert all(request.action_type != "open_area" for request in runtime.action_executor.sink.requests)


def test_q304_stale_authority_blocks_before_area_snapshot(monkeypatch) -> None:
    monkeypatch.setattr("src.antibot_cv.automation.quest_refresh_runtime.time.time", lambda: 108.0)
    runtime = _Runtime(_snapshot(7, ("Кровь Непобедимого кабана", 5)))
    plan = parse_area_object_plan(_entry())
    items = runtime._inspect_quest_area_inventory(plan)
    assert items is not None
    pending = runtime._quest_area_objects.begin(plan, items)
    assert pending is not None
    runtime._quest_area_objects.mark_route_arrived(pending.requirement.location)
    runtime._quest_active_catalog_authority = replace(
        runtime._quest_active_catalog_authority, generated_at=1.0,
    )
    runtime.action_executor.sink.requests.clear()

    assert runtime._continue_quest_area_object_executor() is True
    assert runtime.stopped_reason == "quest_area_object_semantic_evidence_unbound"
    assert runtime.action_executor.sink.requests == []


def test_q304_mismatched_pending_requirement_blocks_before_area_snapshot(monkeypatch) -> None:
    monkeypatch.setattr("src.antibot_cv.automation.quest_refresh_runtime.time.time", lambda: 108.0)
    runtime = _Runtime(_snapshot(7, ("Кровь Непобедимого кабана", 5)))
    plan = parse_area_object_plan(_entry())
    items = runtime._inspect_quest_area_inventory(plan)
    assert items is not None
    pending = runtime._quest_area_objects.begin(plan, items)
    assert pending is not None
    pending.requirement = replace(pending.requirement, resource_name="Чужой мох")
    runtime._quest_area_objects.mark_route_arrived(pending.requirement.location)
    runtime.action_executor.sink.requests.clear()

    assert runtime._continue_quest_area_object_executor() is True
    assert runtime.stopped_reason == "quest_area_object_semantic_evidence_unbound"
    assert runtime.action_executor.sink.requests == []


def test_q304_complete_inventory_advances_semantic_evaluator_to_turn_in(monkeypatch) -> None:
    monkeypatch.setattr("src.antibot_cv.automation.quest_refresh_runtime.time.time", lambda: 108.0)
    runtime = _Runtime(_snapshot(5, ("Кровь Непобедимого кабана", 5)))
    area_plan = parse_area_object_plan(_entry())
    assert runtime._inspect_quest_area_inventory(area_plan) is not None
    runtime.action_executor.sink.last_quest_inventory_snapshot = _snapshot(
        6, ("Кровь Непобедимого кабана", 5),
        ("Светящийся мох", 1),
    )
    assert runtime._inspect_quest_area_inventory(area_plan) is not None
    runtime.action_executor.sink.last_quest_inventory_snapshot = _snapshot(
        7, ("Кровь Непобедимого кабана", 5),
        ("Светящийся мох", 1),
        ("Пятнистый гриб", 1),
    )
    assert runtime._inspect_quest_area_inventory(area_plan) is not None
    runtime.action_executor.sink.last_quest_inventory_snapshot = _snapshot(
        8, ("Кровь Непобедимого кабана", 5),
        ("Светящийся мох", 1),
        ("Пятнистый гриб", 1),
        ("Свежие листья кустарника", 5),
    )
    assert runtime._inspect_quest_area_inventory(area_plan) is not None
    compiled = compile_quest_plan(_entry())
    assert compiled.plan is not None

    evaluation = evaluate_quest_plan(
        compiled.plan, runtime._quest_semantic_evidence,
        capabilities=("combat_drop", "area_object", "turn_in"),
        context=runtime._quest_semantic_evaluation_context,
    )

    assert evaluation.status is EvaluationStatus.ACTIONABLE
    assert evaluation.next_atom is not None
    assert evaluation.next_atom.__class__.__name__ == "TurnIn"
