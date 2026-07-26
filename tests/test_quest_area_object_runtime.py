from __future__ import annotations

from src.antibot_cv.automation.area_object_activity import (
    AreaObjectPlan,
    AreaObjectPlanStatus,
    AreaObjectRequirement,
)
from src.antibot_cv.automation.quest_area_object_runtime import (
    AreaObjectPhase,
    QuestAreaObjectRuntime,
)


def _plan() -> AreaObjectPlan:
    return AreaObjectPlan(
        AreaObjectPlanStatus.READY,
        "304",
        "Цветочная болезнь",
        (
            AreaObjectRequirement("Светящийся мох", 1, "Пристанище трёх ветров"),
            AreaObjectRequirement("Пятнистый гриб", 1, "Длань Рода"),
        ),
        "area_object_plan_ready",
    )


def test_routes_to_next_location_only_after_inventory_proves_current_resource() -> None:
    runtime = QuestAreaObjectRuntime()
    pending = runtime.begin(_plan(), [])
    assert pending is not None
    runtime.mark_route_arrived("Пристанище трёх ветров")
    runtime.accept_snapshot(
        {
            "ok": True,
            "candidates": [
                {"candidateId": "unsafe", "fingerprint": "x", "requiresConfirmation": True},
                {"candidateId": "moss", "fingerprint": "m", "requiresConfirmation": False},
            ],
        }
    )
    assert runtime.next_click_payload("snapshot") == {
        "expectedSnapshotId": "snapshot", "candidateId": "moss", "expectedFingerprint": "m"
    }
    progress = runtime.reconcile_inventory([{"name": "Светящийся мох", "count": 1}])
    assert progress.complete is False
    assert runtime.pending is not None
    assert runtime.pending.phase is AreaObjectPhase.ROUTE
    assert runtime.pending.requirement.location == "Длань Рода"


def test_exhaustion_is_fail_closed_when_click_does_not_change_inventory() -> None:
    runtime = QuestAreaObjectRuntime()
    runtime.begin(_plan(), [])
    runtime.mark_route_arrived("Пристанище трёх ветров")
    runtime.accept_snapshot(
        {"ok": True, "candidates": [{"candidateId": "stone", "fingerprint": "s"}]}
    )
    try:
        runtime.reconcile_inventory([])
    except RuntimeError as exc:
        assert str(exc) == "area_object_candidates_exhausted"
    else:
        raise AssertionError("unproven object search must fail closed")
