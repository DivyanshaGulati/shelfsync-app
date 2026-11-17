"""Single backend definition of inventory Low Stock for read-only AI insights.

This mirrors what the Inventory and Overview pages already show
(``inventoryRows`` in ``frontend/src/lib/purchaseStatus.js``):

    available stock = on hand - reserved
    Low Stock       = available stock <= reorder point (ROP)

The ROP is the stored ``inventory_items.reorder_point`` that the forecast
service keeps current on every checkout and receipt.

On Order quantity is stock that has not arrived yet. It is reported next to the
item for context but is never counted as physically available, so a placed or
in-transit order cannot hide a low-stock item. Once a delivery is received,
``on_hand`` rises and the item turns Healthy only when available stock is above
the ROP.

This module is deliberately free of database and framework imports so the
calculation stays easy to test and to reuse.
"""
from __future__ import annotations

from typing import Any, Iterable

SNAPSHOT_CANDIDATE_LIMIT = 10


def available_stock(on_hand: int, reserved_qty: int) -> int:
    """Units physically on the shelf and not reserved. Never includes On Order."""
    return on_hand - reserved_qty


def is_low_stock(on_hand: int, reserved_qty: int, reorder_point: Any) -> bool:
    """True when available stock is at or below the reorder point."""
    return available_stock(on_hand, reserved_qty) <= reorder_point


def order_display_status(status: str, mock_order: bool, mock_in_transit_at: Any) -> str:
    """Order status as the Purchasing page shows it.

    Mirrors ``display_status`` in ``purchase_requests.list_purchase_requests``:
    a placed mock order that has been marked in transit is IN_TRANSIT.
    """
    if status == "PLACED" and mock_order and mock_in_transit_at is not None:
        return "IN_TRANSIT"
    return status


def summarize_on_order(lines: Iterable[tuple]) -> dict[int, tuple[int, str]]:
    """Return ``{inventory_item_id: (on_order_quantity, order_status)}``.

    ``lines`` are open order lines of active (placed or partially received)
    orders, as ``(inventory_item_id, ordered_quantity, received_quantity,
    order_id, order_status, mock_order, mock_in_transit_at)``. Only the
    unreceived remainder counts as On Order. When an item somehow has several
    active orders, the quantities add up and the newest order's status is shown.
    """
    totals: dict[int, int] = {}
    newest: dict[int, tuple[int, str]] = {}
    for item_id, ordered, received, order_id, status, mock_order, in_transit_at in lines:
        remaining = max(0, ordered - received)
        if remaining == 0:
            continue
        totals[item_id] = totals.get(item_id, 0) + remaining
        if item_id not in newest or order_id > newest[item_id][0]:
            newest[item_id] = (order_id, order_display_status(status, mock_order, in_transit_at))
    return {item_id: (totals[item_id], newest[item_id][1]) for item_id in totals}


def build_stock_snapshot(items: Iterable[Any], lines: Iterable[tuple],
                         limit: int = SNAPSHOT_CANDIDATE_LIMIT) -> dict:
    """Build the read-only snapshot the AI explains.

    ``items`` need ``sku``, ``name``, ``on_hand``, ``reserved_qty`` and
    ``reorder_point`` attributes plus ``id``. Low-stock candidates are ordered
    from furthest below their ROP to closest, then by SKU.
    """
    on_order = summarize_on_order(lines)
    rows = list(items)
    low = sorted(
        (row for row in rows if is_low_stock(row.on_hand, row.reserved_qty, row.reorder_point)),
        key=lambda row: (available_stock(row.on_hand, row.reserved_qty) - row.reorder_point, row.sku),
    )
    candidates = []
    for row in low[:limit]:
        quantity, order_status = on_order.get(row.id, (0, None))
        candidates.append({
            "sku": row.sku,
            "name": row.name,
            "on_hand": row.on_hand,
            "reserved_qty": row.reserved_qty,
            "available_stock": available_stock(row.on_hand, row.reserved_qty),
            "reorder_point": row.reorder_point,
            "on_order": quantity,
            "order_status": order_status,
        })
    return {
        "inventory_items": len(rows),
        "low_stock_items": len(low),
        "low_stock_candidates": candidates,
    }
