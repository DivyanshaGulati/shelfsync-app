"""Simulate an RFQ and a supplier response for each catalogue quote."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.email_service import preflight
from app.core.order_decisions import DecisionError, order_line
from app.core.quotations import utcnow
from app.core.sample_vendor_catalogue import sample_vendors, sync_sample_quotes
from app.core.vendor_drafts import make_quote_drafts
from app.db.models import PurchaseOrder, QuotationEmailDispatch, VendorItem, VendorQuotation


def prepare_catalogue_quotes(db: Session, order: PurchaseOrder) -> None:
    """Caller holds the order lock and commits both mock exchanges atomically."""
    line = order_line(db, order)
    sync_sample_quotes(db, item_id=order.inventory_item_id)
    vendors = {vendor.id: vendor for vendor in sample_vendors(db)}
    mappings = {mapping.vendor_id: mapping for mapping in db.scalars(select(VendorItem).where(
        VendorItem.inventory_item_id == order.inventory_item_id,
        VendorItem.vendor_id.in_(vendors),
    ))}
    if set(mappings) != set(vendors) or len(vendors) != 2:
        raise DecisionError("Both catalogue vendors must have terms for this product")

    quotes = {quote.vendor_id: quote for quote in db.scalars(select(VendorQuotation).where(
        VendorQuotation.purchase_order_id == order.id,
    ))}
    audits = {audit.quotation_id: audit for audit in db.scalars(select(QuotationEmailDispatch).where(
        QuotationEmailDispatch.quotation_id.in_([quote.id for quote in quotes.values()])
    ))}
    if any(not quote.is_simulated and quote.status == "RECEIVED" for quote in quotes.values()):
        raise DecisionError("A request with actual vendor quotes cannot enter mock purchasing")
    if any(audit.mode != "mock" or audit.status != "MOCKED" for audit in audits.values()):
        raise DecisionError("A request with real or uncertain RFQ dispatch cannot enter mock purchasing")

    order.mock_order = True
    now = utcnow()
    for vendor_id, quote in quotes.items():
        if vendor_id not in vendors and quote.status in ("REQUESTED", "RECEIVED"):
            quote.status = "EXPIRED"

    pending = []
    for vendor_id, vendor in vendors.items():
        quote = quotes.get(vendor_id)
        if quote is not None:
            if not quote.is_simulated or quote.status not in ("RECEIVED", "EXPIRED") or quote.id not in audits:
                raise DecisionError("Existing catalogue quotation has an invalid simulation record")
            continue
        quote = VendorQuotation(
            purchase_order_id=order.id, inventory_item_id=order.inventory_item_id,
            vendor_id=vendor_id, status="REQUESTED", requested_at=now, is_simulated=True,
        )
        db.add(quote)
        pending.append((quote, vendor))
    db.flush()

    item = order.inventory_item
    drafts = make_quote_drafts({
        "purchase_order_id": order.id, "sku": item.sku, "item_name": item.name,
        "quantity": line.ordered_quantity, "unit": item.unit,
        "vendors": [
            {"vendor_id": vendor.id, "vendor_name": vendor.name, "vendor_email": vendor.email,
             "quotation_id": quote.id}
            for quote, vendor in pending
        ],
    })
    preflight("mock", drafts)
    for draft, (quote, vendor) in zip(drafts, pending, strict=True):
        db.add(QuotationEmailDispatch(
            quotation_id=quote.id, mode="mock", status="MOCKED", recipient=draft["to"],
            message_id=f'<shelfsync-rfq-{order.id}-{quote.id}@shelfsync.invalid>',
            completed_at=now,
        ))
        terms = mappings[vendor.id]
        # A valid catalogue reply promises the full requested quantity. There
        # is no external stock feed in this simulation.
        quote.quoted_unit_price = terms.last_known_unit_price
        quote.quoted_lead_time_days = terms.expected_lead_time_days
        quote.responded_at = now
        complete = (quote.quoted_unit_price is not None and quote.quoted_unit_price > 0
                    and quote.quoted_lead_time_days is not None and line.ordered_quantity >= terms.min_order_qty)
        quote.status = "RECEIVED" if complete else "EXPIRED"
