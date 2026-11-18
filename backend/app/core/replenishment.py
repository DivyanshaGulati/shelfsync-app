"""Deterministic creation of an unpriced replenishment request.

No vendor selection, quotation, approval or email delivery occurs here.
Call inside the SAME database transaction as the inventory change, holding the
inventory item's PostgreSQL row lock. A partial unique index provides a final
one-open-request-per-item guarantee across concurrent callers.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import InventoryItem, OrderItem, PurchaseOrder, OPEN_ORDER_STATUSES
from app.core.quotations import prepare_vendor_quotations


def ensure_replenishment_request(
    db: Session, item: InventoryItem, forecast: dict, *, created_by: int | None
) -> PurchaseOrder | None:
    """Create one WAITING_FOR_QUOTES request only when a positive quantity is needed.

    The caller must hold an item row lock and commit/rollback the enclosing unit
    of work. Existing requests are reused without rewriting quantity or price.
    """
    if forecast["inventory_item_id"] != item.id:
        raise ValueError("Forecast belongs to a different inventory item")
    if not forecast["replenishment_required"]:
        return None

    existing = db.scalar(
        select(PurchaseOrder).where(
            PurchaseOrder.inventory_item_id == item.id,
            PurchaseOrder.status.in_(OPEN_ORDER_STATUSES),
        )
    )
    if existing is not None:
        return existing

    quantity = int(forecast["suggested_order_quantity"])
    if quantity <= 0:
        # ROP may be zero at zero stock; never create a zero-quantity PO.
        return None

    return create_purchase_request(db, item, quantity, created_by=created_by)


def create_purchase_request(
    db: Session, item: InventoryItem, quantity: int, *, created_by: int | None
) -> PurchaseOrder:
    """Create a request for a positive quantity when the item has no open request.

    The caller must lock the existing item or insert the new item in this transaction.
    """
    order = PurchaseOrder(
        inventory_item_id=item.id,
        created_by=created_by,
        status="WAITING_FOR_QUOTES",
        vendor_id=None,
        total_amount=None,
    )
    db.add(order)
    db.flush()
    db.add(OrderItem(
        purchase_order_id=order.id,
        inventory_item_id=item.id,
        ordered_quantity=quantity,
        received_quantity=0,
        unit_price=None,
        total_price=None,
        is_open=True,
    ))
    db.flush()
    prepare_vendor_quotations(db, order)
    return order
