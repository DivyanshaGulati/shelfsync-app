"""Explicit demo-only vendor replies. Never sends mail, recommends, or orders.

Simulation is intentionally deterministic and repeatable for the approved demo.
Reference prices are input to *fictional* quotes only; the is_simulated flag is
always persisted so they are never misrepresented as real supplier offers.
"""
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.quotations import collection_expired, utcnow
from app.db.models import (PurchaseOrder, Vendor, VendorItem, VendorQuotation,
                           QuotationEmailDispatch)


class SimulationError(ValueError):
    pass


# Illustrative replies from the approved implementation plan, never live offers.
DEMO_REPLIES = {
    ("PL-TEF-01", "Shreeji Plumbing & Pipes"): (Decimal("18.50"), Decimal("2")),
    ("PL-TEF-01", "Bharat Hardware & Fasteners"): (Decimal("19.00"), Decimal("3")),
    ("EL-WIR-25", "Metro Electrical & Tools"): (Decimal("1825.00"), Decimal("2")),
    ("EL-WIR-25", "Bharat Hardware & Fasteners"): (Decimal("1850.00"), Decimal("4")),
}


def demo_reply(*, sku: str, vendor_name: str, vendor_id: int,
               previous_price: Decimal | None, expected_days: Decimal | None) -> tuple[Decimal, Decimal]:
    """Generate reproducible differentiated fictional quotes for demo-only use."""
    known = DEMO_REPLIES.get((sku, vendor_name))
    if known is not None:
        return known
    # No real-time price information is claimed. A positive synthetic unit price
    # is derived from the per-item reference or item unit cost when missing.
    reference = Decimal(previous_price) if previous_price is not None else None
    if reference is None or reference <= 0:
        raise SimulationError("Simulation requires a positive vendor-specific last-known price")
    # Different vendor IDs receive visibly different fictional offers.
    variation = Decimal("1.00") + Decimal(vendor_id % 5 + 1) / Decimal("100")
    unit_price = max(Decimal("0.01"), (reference * variation).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    days = Decimal(expected_days) if expected_days is not None else Decimal("3")
    return unit_price, max(Decimal("1"), days)


def simulate_pending_quotations(db: Session, order: PurchaseOrder, *, now: datetime | None = None,
                                renew_expired: bool = False) -> int:
    """Fill only REQUESTED quotes under an order lock; caller commits atomically.

    Refuses to impersonate actual vendor communications and cannot update real
    quotations or replay simulation into different prices.
    """
    if not settings.enable_quote_simulation:
        raise SimulationError("Quote simulation is disabled; set ENABLE_QUOTE_SIMULATION=true for a demo")
    if settings.email_mode.strip().lower() != 'mock' or settings.enable_real_email_dispatch:
        raise SimulationError("Quote simulation requires EMAIL_MODE=mock and real email dispatch disabled")
    if order.status != "WAITING_FOR_QUOTES":
        raise SimulationError("Only requests waiting for quotations may be simulated")
    quotes = list(db.scalars(select(VendorQuotation).where(
        VendorQuotation.purchase_order_id == order.id).order_by(VendorQuotation.id).with_for_update()))
    if not quotes:
        raise SimulationError("No mapped vendor quotations exist for this request")
    # Do not inject fictional replies into a workflow with actual SMTP activity,
    # including uncertain sends, even if the mode was changed back to mock.
    attempts = list(db.scalars(select(QuotationEmailDispatch).where(
        QuotationEmailDispatch.quotation_id.in_([q.id for q in quotes]))))
    if any(a.mode != 'mock' or a.status != 'MOCKED' for a in attempts):
        raise SimulationError("Cannot simulate quotations with real or uncertain email dispatch attempts")
    pending = [q for q in quotes if q.status == "REQUESTED"]
    if not pending:
        return 0  # Idempotent retry; existing real or simulated replies are untouched.
    if collection_expired(order, now):
        if not renew_expired:
            raise SimulationError("Quotation window has expired; close unanswered requests instead")
        order.quote_deadline_at = (now or utcnow()) + timedelta(minutes=settings.quote_collection_window_minutes)
    # Validate every pending supplier and quote before writing anything.
    proposals = []
    for quote in pending:
        row = db.execute(select(Vendor, VendorItem).join(
            VendorItem, (VendorItem.vendor_id == Vendor.id) &
            (VendorItem.inventory_item_id == quote.inventory_item_id)).where(
            Vendor.id == quote.vendor_id,
            quote.inventory_item_id == order.inventory_item_id,
            Vendor.is_active.is_(True))).first()
        if row is None:
            raise SimulationError("A requested vendor is inactive or not mapped to the item")
        vendor, mapping = row
        unit_price, days = demo_reply(
            sku=order.inventory_item.sku, vendor_name=vendor.name, vendor_id=vendor.id,
            previous_price=mapping.last_known_unit_price,
            expected_days=mapping.expected_lead_time_days)
        proposals.append((quote, unit_price, days))
    timestamp = now or utcnow()
    for quote, price, days in proposals:
        quote.quoted_unit_price = price
        quote.quoted_lead_time_days = days
        quote.responded_at = timestamp
        quote.status = "RECEIVED"
        quote.is_simulated = True
    db.flush()
    return len(proposals)
