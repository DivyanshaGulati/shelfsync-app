"""Atomic physical goods receipt and actual lead-time reconciliation.

All mutation happens in a single DB transaction. Lock InventoryItem first to
serialize with checkout's stock UPDATE and other receipts, then lock the order.
No AI, SMTP, or purchase authorization side effects.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.forecast import calculate_forecast
from app.db.models import InventoryItem, OrderItem, PurchaseOrder, StockReceipt


class ReceiptError(ValueError):
    pass


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _response(db: Session, receipt: StockReceipt, *, replayed: bool) -> dict:
    order = db.get(PurchaseOrder, receipt.purchase_order_id)
    line = db.scalar(select(OrderItem).where(OrderItem.purchase_order_id == order.id))
    item = db.get(InventoryItem, receipt.inventory_item_id)
    actual_days = None
    if order.status == 'RECEIVED' and not order.mock_order and order.placed_at and order.completed_at:
        actual_days = round((_aware(order.completed_at) - _aware(order.placed_at)).total_seconds() / 86400, 3)
    return {
        'receipt_id': receipt.id,
        'purchase_order_id': order.id,
        'inventory_item_id': item.id,
        'quantity_received': receipt.quantity,
        'idempotency_key': receipt.idempotency_key,
        'replayed': replayed,
        'order_status': order.status,
        'ordered_quantity': line.ordered_quantity,
        'received_quantity': line.received_quantity,
        'on_order': line.ordered_quantity - line.received_quantity,
        'on_hand': item.on_hand,
        'available_stock': item.on_hand - item.reserved_qty,
        'first_received_at': order.first_received_at,
        'completed_at': order.completed_at,
        'actual_lead_time_days': actual_days,
    }


def record_receipt(db: Session, *, order_id: int, quantity: int | None,
                   idempotency_key: str, owner_id: int) -> dict:
    """Commit a receipt once, checking exact replay semantics even after completion."""
    completing_delivery = quantity is None

    def matches(record: StockReceipt) -> bool:
        return (record.purchase_order_id == order_id and record.quantity == quantity
                and record.received_by == owner_id)

    existing = None if completing_delivery else db.scalar(
        select(StockReceipt).where(StockReceipt.idempotency_key == idempotency_key)
    )
    if existing:
        if not matches(existing):
            raise ReceiptError('Idempotency key already used for a different receipt')
        return _response(db, existing, replayed=True)

    try:
        # Acquire locks in inventory -> order -> line order, consistent with checkout.
        item_id = db.scalar(select(PurchaseOrder.inventory_item_id).where(PurchaseOrder.id == order_id))
        if item_id is None:
            raise ReceiptError('Purchase order not found')
        item = db.scalar(select(InventoryItem).where(InventoryItem.id == item_id).with_for_update())
        order = db.scalar(select(PurchaseOrder).where(PurchaseOrder.id == order_id).with_for_update())
        line = db.scalar(select(OrderItem).where(OrderItem.purchase_order_id == order_id).with_for_update())
        if not item or not order or not line or line.inventory_item_id != item.id:
            raise ReceiptError('Purchase order line is missing or invalid')

        # Order state under lock is the replay key for full delivery, including
        # orders already completed through the separate physical receipt form.
        if completing_delivery and order.status == 'RECEIVED':
            receipt = db.scalar(select(StockReceipt).where(StockReceipt.purchase_order_id == order.id)
                                .order_by(StockReceipt.id.desc()).limit(1))
            if not receipt or line.received_quantity != line.ordered_quantity:
                raise ReceiptError('Order is already marked delivered')
            response = _response(db, receipt, replayed=True)
            db.rollback()
            return response

        # Recheck an explicit receipt key after any competing receipt commits.
        existing = None if completing_delivery else db.scalar(
            select(StockReceipt).where(StockReceipt.idempotency_key == idempotency_key)
        )
        if existing:
            if not matches(existing):
                raise ReceiptError('Idempotency key already used for a different receipt')
            response = _response(db, existing, replayed=True)
            db.rollback()
            return response

        if order.status not in ('PLACED', 'PARTIALLY_RECEIVED'):
            raise ReceiptError('Only authorized placed or partially received orders can receive stock')
        if order.mock_order and not completing_delivery:
            raise ReceiptError('Use the delivery action that matches this order type')
        if (completing_delivery and order.status == 'PLACED'
                and order.mock_in_transit_at is None and order.mock_delivered_at is None):
            raise ReceiptError('Mark the order as in transit before delivery')
        if order.vendor_id is None or order.placed_at is None:
            raise ReceiptError('Purchase order is missing vendor authorization or placement time')
        if not line.is_open:
            raise ReceiptError('Purchase order line is already closed')
        remaining = line.ordered_quantity - line.received_quantity
        if completing_delivery:
            quantity = remaining
        if quantity <= 0 or quantity > remaining:
            raise ReceiptError(f'Receipt quantity must be between 1 and {remaining}')

        now = datetime.now(timezone.utc)
        if _aware(order.placed_at) > now:
            raise ReceiptError('Order placement timestamp is in the future')
        line.received_quantity += quantity
        item.on_hand += quantity
        if order.first_received_at is None:
            order.first_received_at = now
        if line.received_quantity == line.ordered_quantity:
            order.status = 'RECEIVED'
            order.completed_at = now
            line.is_open = False
        else:
            order.status = 'PARTIALLY_RECEIVED'

        receipt = StockReceipt(purchase_order_id=order.id, inventory_item_id=item.id,
                               received_by=owner_id, quantity=quantity,
                               idempotency_key=idempotency_key, received_at=now)
        db.add(receipt)
        db.flush()
        # Status and open quantity have changed; forecast reads the current on_order
        # and completed delivery data, and writes its audit atomically with receipt.
        calculate_forecast(db, item, persist=True, now=now)
        db.commit()
        return _response(db, receipt, replayed=False)
    except IntegrityError as exc:
        db.rollback()
        winner = db.scalar(select(StockReceipt).where(StockReceipt.idempotency_key == idempotency_key))
        if winner is not None and not completing_delivery:
            if not matches(winner):
                raise ReceiptError('Idempotency key already used for a different receipt') from exc
            return _response(db, winner, replayed=True)
        raise ReceiptError('Receipt could not be completed') from exc
    except Exception:
        db.rollback()
        raise


def list_receipts(db: Session, order_id: int) -> dict:
    order = db.get(PurchaseOrder, order_id)
    if not order:
        raise ReceiptError('Purchase order not found')
    records = db.scalars(select(StockReceipt).where(StockReceipt.purchase_order_id == order_id)
                         .order_by(StockReceipt.id)).all()
    return {'purchase_order_id': order_id, 'receipts': [
        {'receipt_id': x.id, 'quantity_received': x.quantity,
         'received_by': x.received_by, 'received_at': x.received_at,
         'idempotency_key': x.idempotency_key} for x in records]}
