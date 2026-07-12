from src.antibot_cv.automation.death_recovery import (
    Checkpoint,
    DeathCounter,
    Decision,
    Policy,
    ReviveOption,
    Snapshot,
)


def snap(clock, **changes):
    values = {
        "snapshot_id": "s1",
        "captured_at": clock[0],
        "is_dead": False,
        "revive_options": (),
        "activity": "farm",
        "location": "forest",
        "quest": "q1",
    }
    values.update(changes)
    return Snapshot(**values)


def test_alive_is_not_dead_and_no_side_effects():
    clock = [100.0]
    policy = Policy(3, ("free",), now=lambda: clock[0])
    assert policy.decide(snap(clock)).decision is Decision.NOT_DEAD


def test_only_allowlisted_free_safe_available_option_can_revive():
    clock = [100.0]
    policy = Policy(3, ("free",), now=lambda: clock[0])
    options = (
        ReviveOption("paid", free=False, safe=True, available=True),
        ReviveOption("unknown", free=True, safe=True, available=True),
        ReviveOption("free", free=True, safe=True, available=True),
    )
    result = policy.decide(snap(clock, is_dead=True, revive_options=options))
    assert (result.decision, result.option_id) == (Decision.REVIVE, "free")


def test_unknown_or_paid_revive_fails_closed():
    clock = [100.0]
    policy = Policy(3, ("free",), now=lambda: clock[0])
    result = policy.decide(snap(clock, is_dead=True, revive_options=(ReviveOption("free", True, None, True),)))
    assert result.decision is Decision.STOP_UNSAFE


def test_confirmation_requires_new_fresh_alive_snapshot_and_restore_is_idempotent():
    clock = [100.0]
    policy = Policy(3, ("free",), now=lambda: clock[0])
    checkpoint = Checkpoint("farm", "forest", "q1", "before")
    dead = snap(clock, is_dead=True, revive_options=(ReviveOption("free", True, True, True),))
    assert policy.decide(dead, checkpoint=checkpoint).decision is Decision.REVIVE
    assert policy.decide(dead, checkpoint=checkpoint).decision is Decision.WAIT_CONFIRMATION
    alive = snap(clock, snapshot_id="s2", is_dead=False)
    restored = policy.decide(alive, checkpoint=checkpoint, revived=True)
    assert (restored.decision, restored.checkpoint) == (Decision.RESTORE_CHECKPOINT, checkpoint)
    assert policy.decide(alive, checkpoint=checkpoint, revived=True).decision is Decision.COMPLETE


def test_stale_snapshot_death_limit_and_incomplete_checkpoint_stop():
    clock = [100.0]
    policy = Policy(0, ("free",), now=lambda: clock[0], snapshot_max_age_seconds=2)
    stale = snap(clock, captured_at=97.0, is_dead=True)
    assert policy.decide(stale).decision is Decision.STOP_UNSAFE
    policy.record_death("d1")
    assert policy.record_death("d1") == 1
    assert policy.decide(snap(clock, is_dead=True, revive_options=(ReviveOption("free", True, True, True),))).decision is Decision.STOP_UNSAFE


def test_configured_limit_allows_that_many_deaths_and_stops_on_next():
    clock = [100.0]
    policy = Policy(1, ("free",), now=lambda: clock[0])
    policy.record_death("d1")
    assert policy.decide(
        snap(clock, snapshot_id="d1", is_dead=True, revive_options=(ReviveOption("free", True, True, True),))
    ).decision is Decision.REVIVE

    policy._pending_snapshot_id = None
    policy.record_death("d2")
    assert policy.decide(
        snap(clock, snapshot_id="d2", is_dead=True, revive_options=(ReviveOption("free", True, True, True),))
    ).decision is Decision.STOP_UNSAFE


def test_alive_state_is_not_blocked_by_zero_death_budget():
    clock = [100.0]
    policy = Policy(0, ("free",), now=lambda: clock[0])
    assert policy.decide(snap(clock)).decision is Decision.NOT_DEAD


def test_checkpoint_does_not_require_optional_quest():
    assert Checkpoint("farm", "forest", None).complete is True


def test_death_counter_is_idempotent():
    counter = DeathCounter()
    assert [counter.record("x"), counter.record("x"), counter.record("y")] == [1, 1, 2]
