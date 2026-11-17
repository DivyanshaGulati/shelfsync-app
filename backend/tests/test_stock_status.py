"""Unit tests for the single Low Stock definition used by AI inventory insights."""
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.core.stock_status import (
    available_stock, build_stock_snapshot, is_low_stock, order_display_status, summarize_on_order,
)

NOW = datetime(2026, 11, 16, tzinfo=timezone.utc)


def item(id, sku, name, on_hand, rop, reserved=0):
    return SimpleNamespace(id=id, sku=sku, name=name, on_hand=on_hand, reserved_qty=reserved,
                           reorder_point=Decimal(str(rop)))


def line(item_id, ordered, received=0, order_id=1, status="PLACED", mock=False, in_transit=None):
    return (item_id, ordered, received, order_id, status, mock, in_transit)


def test_low_stock_is_at_or_below_rop_using_available_stock():
    assert is_low_stock(4, 0, Decimal("108.0")) is True
    assert is_low_stock(108, 0, Decimal("108.0")) is True      # equal to ROP is still low
    assert is_low_stock(109, 0, Decimal("108.0")) is False
    assert available_stock(110, 2) == 108
    assert is_low_stock(110, 2, Decimal("108.0")) is True      # reserved units are not available


def test_order_status_matches_purchasing_page():
    assert order_display_status("PLACED", False, None) == "PLACED"
    assert order_display_status("PLACED", True, None) == "PLACED"
    assert order_display_status("PLACED", True, NOW) == "IN_TRANSIT"
    assert order_display_status("PARTIALLY_RECEIVED", False, None) == "PARTIALLY_RECEIVED"


def test_on_order_counts_only_unreceived_units_of_open_lines():
    result = summarize_on_order([
        line(1, 20, 5, order_id=7, status="PARTIALLY_RECEIVED"),
        line(2, 10, 10, order_id=8),                        # fully received: nothing on order
    ])
    assert result == {1: (15, "PARTIALLY_RECEIVED")}


def test_newest_order_status_is_reported_and_quantities_add_up():
    result = summarize_on_order([
        line(1, 10, order_id=3, status="PLACED"),
        line(1, 6, 1, order_id=9, status="PARTIALLY_RECEIVED"),
    ])
    assert result == {1: (15, "PARTIALLY_RECEIVED")}


def test_placed_or_in_transit_order_never_hides_low_stock():
    low = item(1, "SKU-A", "Item A", on_hand=4, rop=108)
    for lines in ([line(1, 120)], [line(1, 120, status="PLACED", mock=True, in_transit=NOW)]):
        snapshot = build_stock_snapshot([low], lines)
        assert snapshot["low_stock_items"] == 1
        candidate = snapshot["low_stock_candidates"][0]
        assert candidate["name"] == "Item A"
        assert candidate["on_hand"] == 4
        assert candidate["available_stock"] == 4
        assert candidate["reorder_point"] == Decimal("108.0")
        assert candidate["on_order"] == 120


def test_snapshot_reports_order_status_only_where_an_order_exists():
    snapshot = build_stock_snapshot(
        [item(1, "SKU-A", "Item A", 1, 5), item(2, "SKU-B", "Item B", 1, 5)],
        [line(1, 20, status="PLACED", mock=True, in_transit=NOW)],
    )
    by_sku = {c["sku"]: c for c in snapshot["low_stock_candidates"]}
    assert (by_sku["SKU-A"]["on_order"], by_sku["SKU-A"]["order_status"]) == (20, "IN_TRANSIT")
    assert (by_sku["SKU-B"]["on_order"], by_sku["SKU-B"]["order_status"]) == (0, None)


def test_delivered_stock_is_healthy_only_when_above_rop():
    delivered = []  # the delivery closed the order line, so nothing is on order any more
    at_rop = build_stock_snapshot([item(1, "SKU-A", "Item A", 108, 108)], delivered)
    above_rop = build_stock_snapshot([item(1, "SKU-A", "Item A", 109, 108)], delivered)
    assert at_rop["low_stock_items"] == 1
    assert at_rop["low_stock_candidates"][0]["on_order"] == 0
    assert above_rop["low_stock_items"] == 0
    assert above_rop["low_stock_candidates"] == []
    assert above_rop["inventory_items"] == 1


def test_candidates_are_ordered_by_distance_below_rop_then_sku_and_capped():
    items = [item(i, f"SKU-{i:02d}", f"Item {i}", on_hand=i, rop=50) for i in range(1, 16)]
    items.append(item(99, "SKU-OK", "Healthy", on_hand=500, rop=50))
    snapshot = build_stock_snapshot(items, [])
    assert snapshot["inventory_items"] == 16
    assert snapshot["low_stock_items"] == 15
    skus = [c["sku"] for c in snapshot["low_stock_candidates"]]
    assert len(skus) == 10
    assert skus[:3] == ["SKU-01", "SKU-02", "SKU-03"]
    assert "SKU-OK" not in skus
