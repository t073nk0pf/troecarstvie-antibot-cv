from __future__ import annotations

from dataclasses import replace
import pytest

from src.antibot_cv.automation.quest_available_eligibility import (
    classify_available_quest_refs,
    restore_unsupported_available_entries,
    serialize_unsupported_available,
)
from src.antibot_cv.automation.quest_director_policy import QuestRef
from src.antibot_cv.automation.quest_chain_runtime import QuestChainRuntime


def test_available_eligibility_skips_incomplete_refs_without_fingerprinting() -> None:
    eligible = QuestRef("236", "Quest 236", location="Wilds", giver_names=("Frank",), catalog_page=2)
    result = classify_available_quest_refs((
        QuestRef("10", "Missing location", location=None, giver_names=("Frank",), catalog_page=0),
        QuestRef("11", "Missing giver", location="Wilds", giver_names=(), catalog_page=0),
        QuestRef("12", "Multiple givers", location="Wilds", giver_names=("A", "B"), catalog_page=1),
        eligible,
    ))

    assert result.eligible == (eligible,)
    assert [(item.quest_id, item.reason) for item in result.unsupported] == [
        ("10", "missing_or_invalid_location"),
        ("11", "missing_giver"),
        ("12", "multiple_givers"),
    ]


def test_available_eligibility_keeps_exact_ids_for_unsupported_entries() -> None:
    result = classify_available_quest_refs((
        QuestRef("91", "Incomplete", location="Wilds", giver_names=(), catalog_page=0),
    ))

    assert result.eligible == ()
    assert result.unsupported[0].quest_id == "91"
    assert result.unsupported[0].catalog_page == 0


def test_staged_authoritative_ref_reconstructs_only_missing_observed_fields() -> None:
    staged = QuestRef("236", "Quest 236", location="Wilds", giver_names=("Frank",), catalog_page=2)
    raw = QuestRef("236", "Quest 236", location=None, giver_names=(), catalog_page=2)

    result = classify_available_quest_refs((raw,), staged_ref=staged)

    assert result.eligible == (staged,)
    assert result.unsupported == ()
    assert result.conflict_reason is None


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (QuestRef("236", "Wrong title", location=None, giver_names=(), catalog_page=2), "staged_available_title_conflict"),
        (QuestRef("236", "Quest 236", location="Other", giver_names=(), catalog_page=2), "staged_available_location_conflict"),
        (QuestRef("236", "Quest 236", location=None, giver_names=("Other",), catalog_page=2), "staged_available_giver_conflict"),
    ],
)
def test_staged_available_conflicts_are_explicit(raw, reason) -> None:
    staged = QuestRef("236", "Quest 236", location="Wilds", giver_names=("Frank",), catalog_page=2)
    assert classify_available_quest_refs((raw,), staged_ref=staged).conflict_reason == reason


def test_unsupported_available_evidence_round_trip_is_strict() -> None:
    item = classify_available_quest_refs((
        QuestRef("91", "Incomplete", location="Wilds", giver_names=(), catalog_page=0),
    )).unsupported[0]
    raw = serialize_unsupported_available(item)

    assert restore_unsupported_available_entries([raw], max_items=10) == (item,)
    with pytest.raises(ValueError):
        restore_unsupported_available_entries([{**raw, "extra": True}], max_items=10)
    with pytest.raises(ValueError):
        restore_unsupported_available_entries([raw, raw], max_items=10)


def test_unsupported_available_evidence_is_durable(tmp_path) -> None:
    item = classify_available_quest_refs((
        QuestRef("91", "Incomplete", location="Wilds", giver_names=(), catalog_page=0),
    )).unsupported[0]
    path = tmp_path / "chain.json"
    chain = QuestChainRuntime(state_path=path)

    chain.replace_unsupported_available_entries((item,))

    assert QuestChainRuntime(state_path=path).unsupported_available_entries == (item,)


def test_old_capability_evidence_restores_under_current_version() -> None:
    current = classify_available_quest_refs((
        QuestRef("91", "Incomplete", location="Wilds", giver_names=(), catalog_page=0),
    )).unsupported[0]
    old = replace(current, capability_version="available_eligibility_v1")
    raw = serialize_unsupported_available(old)

    assert current.capability_version == "available_eligibility_v2"
    assert restore_unsupported_available_entries([raw], max_items=10) == (old,)


@pytest.mark.parametrize(
    "capability",
    ["", "bad version", "UPPER_VERSION", "x" * 81],
)
def test_malformed_capability_evidence_is_rejected(capability) -> None:
    item = classify_available_quest_refs((
        QuestRef("91", "Incomplete", location="Wilds", giver_names=(), catalog_page=0),
    )).unsupported[0]
    raw = {**serialize_unsupported_available(item), "capability_version": capability}
    with pytest.raises(ValueError):
        restore_unsupported_available_entries([raw], max_items=10)


def test_fresh_reconciliation_replaces_observed_stale_and_preserves_unrelated(tmp_path) -> None:
    first, unrelated = classify_available_quest_refs((
        QuestRef("91", "Incomplete 91", location="Wilds", giver_names=(), catalog_page=0),
        QuestRef("92", "Incomplete 92", location="Wilds", giver_names=(), catalog_page=0),
    )).unsupported
    old_first = replace(first, capability_version="available_eligibility_v1")
    old_unrelated = replace(unrelated, capability_version="available_eligibility_v1")
    current_first = classify_available_quest_refs((
        QuestRef("91", "Incomplete 91", location=None, giver_names=("Frank",), catalog_page=0),
    )).unsupported[0]
    path = tmp_path / "chain.json"
    chain = QuestChainRuntime(state_path=path)
    chain.replace_unsupported_available_entries((old_first, old_unrelated))

    chain.reconcile_unsupported_available_entries((current_first,), observed_quest_ids={"91"})

    assert chain.unsupported_available_entries == (old_unrelated, current_first)
    assert QuestChainRuntime(state_path=path).unsupported_available_entries == (
        old_unrelated,
        current_first,
    )
