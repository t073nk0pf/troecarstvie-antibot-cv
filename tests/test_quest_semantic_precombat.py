from __future__ import annotations

from types import SimpleNamespace
import time

from src.antibot_cv.automation.navigation_runtime import NavigationRuntimeMixin
from src.antibot_cv.automation.quest_active_catalog import ActiveQuestEntry
from src.antibot_cv.automation.quest_objective_runtime import (
    MonsterTarget,
    ObjectiveKind,
    QuestObjective,
)


Q304_OBJECTIVE = (
    "Убивая Непобедимых кабанов, получите 5 пузырьков крови, также найдите "
    "в Пристанище трёх ветров Светящийся мох, в Длани Рода Пятнистый гриб "
    "и 5 свежих листьев кустарника на Просторах безмолвия. Собрав необходимое, "
    "возвращайтесь к колдунье Вилене."
)


def _entry() -> ActiveQuestEntry:
    return ActiveQuestEntry("304", "Цветочная болезнь", {
        "id": "304",
        "title": "Цветочная болезнь",
        "status": "active",
        "objective": Q304_OBJECTIVE,
        "objectiveKind": "collect",
        "navigation": (
            {"text": "Непобедимый кабан", "target": "Непобедимый кабан [5]"},
            {"text": "Пристанище трёх ветров", "target": "Пристанище трёх ветров"},
            {"text": "Длани Рода", "target": "Длань Рода"},
            {"text": "Просторах безмолвия", "target": "Просторы безмолвия"},
        ),
        "progress": {"current": 5, "required": 5, "complete": False},
    })


class _Sink:
    def __init__(self, *, provenance: bool = True, blood_count: int = 5) -> None:
        self.requests = []
        self.last_quest_inventory_snapshot = None
        self.provenance = provenance
        self.blood_count = blood_count

    def execute(self, request) -> bool:
        self.requests.append(request)
        snapshot = {
            "ok": True,
            "category": "quest",
            "categoryConfirmed": True,
            "truncated": False,
            "items": (
                [{"artAltTitle": "Кровь Непобедимого кабана", "count": self.blood_count}]
                if self.blood_count else []
            ),
        }
        if self.provenance:
            snapshot.update({
                "snapshotId": "inventory-1",
                "generatedAt": time.time(),
                "revision": int(time.time() * 1000),
                "clientId": "client",
                "profileId": "profile",
                "tabId": "17",
                "causalBaseline": "catalog-baseline",
            })
        self.last_quest_inventory_snapshot = snapshot
        return True


class _Harness(NavigationRuntimeMixin):
    def __init__(
        self, mode: str, *, provenance: bool = True, blood_count: int = 5,
    ) -> None:
        entry = _entry()
        objective = QuestObjective(
            ObjectiveKind.MONSTER_HUNT,
            "304",
            "Цветочная болезнь",
            Q304_OBJECTIVE,
            "legacy-fingerprint",
            MonsterTarget("Непобедимый кабан [5]", "Непобедимый кабан", 5),
            "Непобедимых кабанов",
            None,
            None,
            False,
            5,
        )
        self.config = SimpleNamespace(
            dry_run=False,
            leveling=SimpleNamespace(quest_engine_mode=mode),
            item_recovery=SimpleNamespace(inventory_open_delay_ms=0),
        )
        self.session = SimpleNamespace(completed_cycles=0, cycle_id=1, battle_id=None)
        self.state_machine = SimpleNamespace(state=SimpleNamespace(value="location_search"))
        self.logger = SimpleNamespace(log_event=lambda *args, **kwargs: None)
        self.action_executor = SimpleNamespace(
            sink=_Sink(provenance=provenance, blood_count=blood_count),
        )
        self.action_executor.execute = self.action_executor.sink.execute
        catalog = SimpleNamespace(complete=True, result=(entry,), revision=1)
        self._quest_director = SimpleNamespace(
            active_objective=objective,
            active_snapshot_fresh=True,
            active_catalog=catalog,
        )
        observation = SimpleNamespace(
            client_id="client",
            profile_id="profile",
            tab_id="17",
            causal_baseline="catalog-baseline",
        )
        self._quest_active_catalog_authority = SimpleNamespace(
            revision=1, causal_baseline="catalog-baseline",
        )
        self._quest_active_catalog_authority_accumulator = SimpleNamespace(
            observation=observation,
        )
        self._quest_inventory_checked_fingerprint = None
        self._quest_inventory_checked_cycle = None
        self._quest_target_names = ("Непобедимый кабан",)
        self._quest_target_levels = (5,)
        self._quest_target_specs = (("Непобедимый кабан", 5),)
        self._selected_target = object()
        self._current_target = object()
        self.refreshes = 0
        self.unsafe_reason = None

    def _maybe_start_quest_refresh(self) -> bool:
        self.refreshes += 1
        return True

    def _stop_leveling_unsafe(self, reason: str) -> None:
        self.unsafe_reason = reason


def test_q304_blood_blocks_combat_and_advances_to_area_object() -> None:
    controller = _Harness("q280_q304")

    assert controller._quest_inventory_allows_attack() is False
    assert controller._quest_semantic_precombat_attack_allowed is False
    assert controller._quest_semantic_plan.quest_id == "304"
    assert len(controller._quest_semantic_evidence) == 1
    assert controller._quest_target_names == ()
    assert controller.refreshes == 1
    assert controller.unsafe_reason is None

    # The cached authoritative decision must not fall through to combat on the
    # next hunt frame while the refresh/area-object handoff is pending.
    assert controller._quest_inventory_allows_attack() is False
    assert len(controller.action_executor.sink.requests) == 1
    assert controller.action_executor.sink.requests[0].metadata["causal_baseline"] == "catalog-baseline"
    assert controller.action_executor.sink.requests[0].metadata["minimum_revision"] == 1

    # A newer authoritative inventory replaces the old requirement evidence;
    # it must not leave a stale "blood satisfied" fact in the shared store.
    controller.action_executor.sink.blood_count = 0
    controller.session.completed_cycles = 1
    assert controller._quest_inventory_allows_attack() is False
    assert controller._quest_semantic_evidence == ()
    assert controller._quest_semantic_precombat_attack_allowed is True


def test_authoritative_mode_rejects_inventory_without_provenance() -> None:
    controller = _Harness("q280_q304", provenance=False)

    assert controller._quest_inventory_allows_attack() is False
    assert controller.unsafe_reason == "quest_semantic_inventory:inventory_actor_identity_mismatch"
    assert controller.refreshes == 0


def test_shadow_without_sealed_authority_keeps_legacy_inventory_request() -> None:
    controller = _Harness("shadow")
    controller._quest_active_catalog_authority = None

    controller._quest_inventory_allows_attack()

    metadata = controller.action_executor.sink.requests[0].metadata
    assert "causal_baseline" not in metadata
    assert "minimum_revision" not in metadata


def test_q304_missing_blood_allows_only_combat_drop_after_inspection_pass() -> None:
    controller = _Harness("q280_q304", blood_count=0)

    assert controller._quest_inventory_allows_attack() is False
    assert controller._quest_semantic_precombat_attack_allowed is True
    assert controller.refreshes == 0
    assert controller.unsafe_reason is None
    assert controller._quest_inventory_allows_attack() is True


def test_legacy_mode_keeps_existing_inventory_behavior() -> None:
    controller = _Harness("legacy", provenance=False)

    assert controller._quest_inventory_allows_attack() is False
    assert controller._quest_inventory_terminal_completion_evidence.quest_id == "304"
    assert controller.refreshes == 1
    assert controller.unsafe_reason is None
