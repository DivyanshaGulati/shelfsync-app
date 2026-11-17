"""Quotation lifecycle. No email is transmitted here.

REQUESTED means a persisted request; email dispatch is tracked separately.
Explicit action endpoints record owner-verified responses and close the window;
there is no scheduler or hidden background job.
"""
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import InventoryItem, OrderItem, PurchaseOrder, Vendor, VendorItem, VendorQuotation


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime) -> datetime:
    """SQLite sometimes returns timezone-naive UTC timestamps; normalize them."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def collection_expired(order: PurchaseOrder, now: datetime | None = None) -> bool:
    return order.quote_deadline_at is not None and aware(order.quote_deadline_at) <= (now or utcnow())


def prepare_vendor_quotations(db: Session, order: PurchaseOrder, *, now: datetime | None = None) -> list[VendorQuotation]:
    """Prepare one REQUESTED record per active mapped vendor, without reissuing old requests.

    Caller holds the inventory-item row lock when creating the PO or the PO lock
    when retrying explicitly. Existing rows never get a new requested_at or price.
    """
    if order.status != "WAITING_FOR_QUOTES":
        raise ValueError("Only waiting requests can collect quotations")
    current = list(db.scalars(select(VendorQuotation).where(VendorQuotation.purchase_order_id == order.id)
                              .order_by(VendorQuotation.vendor_id)))
    if current:
        return current  # Cannot add vendors mid-window or reset an existing collection.
    if order.quote_deadline_at is not None:
        return []  # A collection was started previously, and must not be restarted.
    line = db.scalar(select(OrderItem).where(OrderItem.purchase_order_id == order.id))
    if line is None or line.ordered_quantity <= 0:
        raise ValueError("Purchase request requires a positive order line")
    mappings = db.execute(
        select(VendorItem, Vendor)
        .join(Vendor, Vendor.id == VendorItem.vendor_id)
        .where(VendorItem.inventory_item_id == order.inventory_item_id, Vendor.is_active.is_(True))
        .order_by(Vendor.id)
    ).all()
    if not mappings:
        return []  # Remain WAITING_FOR_QUOTES until active vendors are configured.
    start = now or utcnow()
    order.quote_deadline_at = start + timedelta(minutes=settings.quote_collection_window_minutes)
    result = []
    for mapping, vendor in mappings:
        q = VendorQuotation(purchase_order_id=order.id, inventory_item_id=order.inventory_item_id,
                            vendor_id=vendor.id, status="REQUESTED", requested_at=start,
                            is_simulated=False)
        db.add(q)
        result.append(q)
    db.flush()
    return result


def expire_unanswered(db: Session, order: PurchaseOrder, *, now: datetime | None = None) -> int:
    """Close unanswered quotes only after deadline or when all vendors replied.

    Order comparison and approval are handled separately.
    """
    if order.status != "WAITING_FOR_QUOTES":
        raise ValueError("Request not waiting for quotations")
    quotes = list(db.scalars(select(VendorQuotation).where(VendorQuotation.purchase_order_id == order.id)))
    if not quotes:
        raise ValueError("No quotation requests have been prepared")
    pending = [q for q in quotes if q.status == "REQUESTED"]
    if pending and not collection_expired(order, now):
        raise ValueError("Quotation collection window is still open")
    for quote in pending:
        quote.status = "EXPIRED"
    db.flush()
    return len(pending)
