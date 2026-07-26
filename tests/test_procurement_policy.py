from __future__ import annotations

from datetime import datetime, timezone

from src.antibot_cv.automation.procurement_policy import (
    ProcurementPlanStatus,
    ProcurementRequirement,
    ProcurementSource,
    plan_exact_deficit_procurement,
)


def requirement(**overrides: object) -> ProcurementRequirement:
    values = {
        "quest_id": "31",
        "quest_fingerprint": "quest:31:step:2",
        "transport_client_id": "client-main",
        "expected_character_name": "v3g45",
        "source": ProcurementSource.AUCTION,
        "item_name": "Осиное крыло",
        "required": 5,
        "item_id": "wing-7",
    }
    values.update(overrides)
    return ProcurementRequirement(**values)  # type: ignore[arg-type]


def inventory(count: int = 2) -> dict[str, object]:
    return {
        "complete": True,
        "truncated": False,
        "snapshotId": "inventory-9",
        "generatedAt": "2026-07-17T10:00:00Z",
        "transportClientId": "client-main",
        "expectedCharacterName": "v3g45",
        "observedCharacterName": "v3g45",
        "characterStatus": "available",
        "requestScope": {"questId": "31", "questFingerprint": "quest:31:step:2"},
        "items": [{"itemId": "wing-7", "itemName": "Осиное крыло", "count": count}],
    }


def market(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "complete": True,
        "truncated": False,
        "source": "auction",
        "shopId": "auction-main",
        "snapshotId": "auction-12",
        "generatedAt": "2026-07-17T10:00:00Z",
        "transportClientId": "client-main",
        "expectedCharacterName": "v3g45",
        "observedCharacterName": "v3g45",
        "characterStatus": "available",
        "requestScope": {"questId": "31", "questFingerprint": "quest:31:step:2"},
        "currency": "round",
        "balance": 100,
        "listings": [
            {"listingId": "lot-b", "itemId": "wing-7", "itemName": "Осиное крыло", "unitPrice": 9, "totalPrice": 18, "availableQuantity": 2, "currency": "round"},
            {"listingId": "lot-a", "itemId": "wing-7", "itemName": "Осиное крыло", "unitPrice": 7, "totalPrice": 14, "availableQuantity": 2, "currency": "round"},
        ],
    }
    values.update(overrides)
    return values


NOW = datetime(2026, 7, 17, 10, 0, 10, tzinfo=timezone.utc)


def plan(
    expected: ProcurementRequirement,
    inventory_snapshot: dict[str, object],
    market_snapshot: dict[str, object],
):
    return plan_exact_deficit_procurement(
        expected,
        inventory_snapshot,
        market_snapshot,
        now=NOW,
    )


def test_plans_cheapest_orders_for_exact_deficit_without_overbuy() -> None:
    result = plan(requirement(), inventory(), market())
    assert result.status is ProcurementPlanStatus.READY
    assert result.deficit == 3
    assert [(order.listing_id, order.quantity) for order in result.orders] == [("lot-a", 2), ("lot-b", 1)]
    assert sum(order.quantity for order in result.orders) == 3
    assert result.total_price == 23
    assert result.balance == 100


def test_distinguishes_npc_shop_from_auction() -> None:
    result = plan(
        requirement(source=ProcurementSource.NPC_SHOP),
        inventory(),
        market(source="npc_shop", shopId="npc:merchant-4"),
    )
    assert result.status is ProcurementPlanStatus.READY
    assert result.source is ProcurementSource.NPC_SHOP


def test_already_satisfied_never_produces_orders() -> None:
    result = plan(requirement(), inventory(5), market())
    assert result.status is ProcurementPlanStatus.COMPLETE
    assert result.deficit == 0
    assert result.orders == ()


def test_rejects_premium_unknown_or_unaffordable_currency() -> None:
    premium = plan(requirement(), inventory(), market(currency="gold"))
    premium_listing = market(listings=[
        {"listingId": "one", "itemId": "wing-7", "itemName": "Осиное крыло", "unitPrice": 1, "totalPrice": 3, "availableQuantity": 3, "currency": "premium"}
    ])
    premium_item = plan(requirement(), inventory(), premium_listing)
    unknown = plan(requirement(), inventory(), market(balance=None))
    poor = plan(requirement(), inventory(), market(balance=20))
    assert premium.reason == "round_currency_unconfirmed"
    assert premium_item.reason == "premium_currency_forbidden"
    assert unknown.reason == "round_currency_unconfirmed"
    assert poor.reason == "round_balance_insufficient"
    assert all(result.status is ProcurementPlanStatus.UNSAFE for result in (premium, premium_item, unknown, poor))


def test_rejects_incomplete_inventory_and_market_snapshots() -> None:
    inv = inventory()
    inv["complete"] = False
    shop = market()
    shop["complete"] = False
    assert plan(requirement(), inv, market()).reason == "inventory_snapshot_unconfirmed"
    assert plan(requirement(), inventory(), shop).reason == "procurement_snapshot_incomplete"


def test_rejects_truncated_snapshots_even_when_marked_complete() -> None:
    inv = inventory()
    inv["truncated"] = True
    shop = market(truncated=True)
    assert plan(requirement(), inv, market()).reason == "inventory_snapshot_unconfirmed"
    assert plan(requirement(), inventory(), shop).reason == "procurement_snapshot_incomplete"


def test_truncated_field_is_required_and_strict_false() -> None:
    for bad_value in (None, "yes", 0):
        inv = inventory()
        shop = market()
        if bad_value is None:
            inv.pop("truncated")
            shop.pop("truncated")
        else:
            inv["truncated"] = bad_value
            shop["truncated"] = bad_value
        assert plan(requirement(), inv, market()).reason == "inventory_snapshot_unconfirmed"
        assert plan(requirement(), inventory(), shop).reason == "procurement_snapshot_incomplete"


def test_rejects_source_mismatch_identity_ambiguity_and_duplicate_listing() -> None:
    mismatch = plan(requirement(), inventory(), market(source="npc_shop"))
    ambiguous_listings = market(listings=[
        {"listingId": "one", "itemId": "wing-7", "itemName": "Осиное крыло", "unitPrice": 1, "totalPrice": 2, "availableQuantity": 2, "currency": "round"},
        {"listingId": "two", "itemId": "wing-8", "itemName": "Осиное крыло", "unitPrice": 1, "totalPrice": 2, "availableQuantity": 2, "currency": "round"},
    ])
    ambiguous = plan(requirement(item_id=None), inventory(), ambiguous_listings)
    duplicate = market(listings=[market()["listings"][0], market()["listings"][0]])
    duplicate_plan = plan(requirement(), inventory(), duplicate)
    assert mismatch.reason == "procurement_source_mismatch"
    assert ambiguous.reason == "item_identity_ambiguous"
    assert duplicate_plan.reason == "listing_identity_ambiguous"


def test_rejects_insufficient_quantity_and_malformed_total() -> None:
    scarce = market(listings=[
        {"listingId": "one", "itemId": "wing-7", "itemName": "Осиное крыло", "unitPrice": 1, "totalPrice": 2, "availableQuantity": 2, "currency": "round"}
    ])
    malformed = market(listings=[
        {"listingId": "one", "itemId": "wing-7", "itemName": "Осиное крыло", "unitPrice": 2, "totalPrice": 3, "availableQuantity": 2, "currency": "round"}
    ])
    assert plan(requirement(), inventory(), scarce).reason == "available_quantity_insufficient"
    assert plan(requirement(), inventory(), malformed).reason == "procurement_listing_invalid"


def test_rejects_stale_parse_error_or_mismatched_snapshot_bindings() -> None:
    stale_inventory = inventory()
    stale_inventory["generatedAt"] = "1999-01-01T00:00:00Z"
    bad_time_market = market(generatedAt="not-a-time")
    wrong_client = market(transportClientId="another-client")
    wrong_quest = market(requestScope={"questId": "31", "questFingerprint": "quest:31:other"})
    wrong_inventory_actor = inventory()
    wrong_inventory_actor["observedCharacterName"] = "another-character"
    assert plan(requirement(), stale_inventory, market()).reason == "inventory_snapshot_stale"
    assert plan(requirement(), inventory(), bad_time_market).reason == "procurement_generated_at_invalid"
    assert plan(requirement(), inventory(), wrong_client).reason == "procurement_binding_mismatch"
    assert plan(requirement(), inventory(), wrong_quest).reason == "procurement_binding_mismatch"
    assert plan(requirement(), wrong_inventory_actor, market()).reason == "inventory_binding_mismatch"


def test_unavailable_or_ambiguous_observed_actor_can_never_be_ready() -> None:
    for character_status, observed_name in (("partial", None), ("partial", "v3g45"), ("available", None)):
        observed = market(characterStatus=character_status, observedCharacterName=observed_name)
        result = plan(requirement(), inventory(), observed)
        assert (result.status, result.reason) == (
            ProcurementPlanStatus.UNSAFE,
            "procurement_binding_mismatch",
        )


def test_malformed_non_string_requirement_name_is_unsafe_not_exception() -> None:
    malformed = requirement(item_name=123)
    result = plan(malformed, inventory(), market())
    assert result.status is ProcurementPlanStatus.UNSAFE
    assert result.reason == "requirement_item_name_invalid"


def test_malformed_now_is_unsafe_not_exception() -> None:
    result = plan_exact_deficit_procurement(
        requirement(),
        inventory(),
        market(),
        now="2026-07-17T10:00:10Z",  # type: ignore[arg-type]
    )
    assert result.status is ProcurementPlanStatus.UNSAFE
    assert result.reason == "freshness_policy_invalid"
    false_now = plan_exact_deficit_procurement(
        requirement(), inventory(), market(), now=False  # type: ignore[arg-type]
    )
    assert false_now.status is ProcurementPlanStatus.UNSAFE
    assert false_now.reason == "freshness_policy_invalid"
