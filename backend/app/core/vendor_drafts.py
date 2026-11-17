"""Read-only, backend-validated email previews. No SMTP, stock, or order mutation.

Claude may suggest polite supplementary prose; all financial values and addresses
are rendered by the backend from the current purchase request and fresh quote.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import InventoryItem, OrderItem, PurchaseOrder, Vendor, VendorItem, VendorQuotation
from app.core.quotations import collection_expired
from app.core.sample_vendor_catalogue import is_sample_vendor


class DraftError(ValueError):
    pass


def _line_and_item(db: Session, order: PurchaseOrder):
    line = db.scalar(select(OrderItem).where(OrderItem.purchase_order_id == order.id))
    item = db.get(InventoryItem, order.inventory_item_id)
    if not line or not item or line.inventory_item_id != item.id or line.ordered_quantity <= 0:
        raise DraftError('Missing or invalid purchase-request item')
    return line, item


def quotation_request_context(db: Session, order: PurchaseOrder) -> dict[str, Any]:
    """Return all eligible prepared quotation requests, including only active mapped suppliers.

    A vendor-specific preview is allowed only while collecting the current request.
    REQUESTED records mean prepared, NOT emailed.
    """
    if order.status != 'WAITING_FOR_QUOTES' or collection_expired(order):
        raise DraftError('Quotation request is not open for drafting')
    line, item = _line_and_item(db, order)
    rows = db.execute(select(VendorQuotation, Vendor, VendorItem)
        .join(Vendor, Vendor.id == VendorQuotation.vendor_id)
        .join(VendorItem, (VendorItem.vendor_id == Vendor.id) & (VendorItem.inventory_item_id == item.id))
        .where(VendorQuotation.purchase_order_id == order.id,
               VendorQuotation.inventory_item_id == item.id,
               VendorQuotation.status == 'REQUESTED', Vendor.is_active.is_(True))
        .order_by(Vendor.id)).all()
    if not rows:
        raise DraftError('No active mapped vendors with pending quotation requests')
    return {'purchase_order_id': order.id, 'item_name': item.name, 'sku': item.sku,
            'quantity': line.ordered_quantity, 'unit': item.unit,
            'vendors': [{'vendor_id': v.id, 'vendor_name': v.name, 'vendor_email': v.email,
                         'quotation_id': quote.id} for quote,v,mapping in rows]}


def order_email_context(db: Session, order: PurchaseOrder) -> dict[str, Any]:
    """Read a final authorized quote, never an AI candidate or last-known price."""
    if order.status != 'PLACED' or order.vendor_id is None:
        raise DraftError('A final order email requires a PLACED, authorized purchase order')
    line, item = _line_and_item(db, order)
    rows = db.execute(select(VendorQuotation, Vendor, VendorItem)
        .join(Vendor, Vendor.id == VendorQuotation.vendor_id)
        .join(VendorItem, (VendorItem.vendor_id == Vendor.id) & (VendorItem.inventory_item_id == item.id))
        .where(VendorQuotation.purchase_order_id == order.id,
               VendorQuotation.inventory_item_id == item.id,
               VendorQuotation.vendor_id == order.vendor_id,
               VendorQuotation.status == 'SELECTED')).all()
    rows = [(q, v, m) for q, v, m in rows
            if v.is_active or (order.mock_order and q.is_simulated and is_sample_vendor(v))]
    if len(rows) != 1:
        raise DraftError('No unique selected, active and mapped fresh quotation for this order')
    quote, vendor, mapping = rows[0]
    if (quote.quoted_unit_price is None or quote.quoted_unit_price <= 0 or
        quote.quoted_lead_time_days is None or quote.responded_at is None):
        raise DraftError('Selected quotation lacks a fresh valid unit price or lead time')
    quoted_total = (quote.quoted_unit_price * line.ordered_quantity).quantize(Decimal('0.01'))
    if line.unit_price != quote.quoted_unit_price or line.total_price != quoted_total or order.total_amount != quoted_total:
        raise DraftError('Final order values disagree with the selected fresh quotation')
    if line.ordered_quantity < mapping.min_order_qty:
        raise DraftError('Selected vendor minimum order quantity is not met')
    return {'purchase_order_id': order.id, 'quotation_id': quote.id,
            'vendor_id': vendor.id, 'vendor_name': vendor.name, 'vendor_email': vendor.email,
            'item_name': item.name, 'sku': item.sku, 'quantity': line.ordered_quantity,
            'unit': item.unit, 'quoted_unit_price': str(quote.quoted_unit_price),
            'quoted_lead_time_days': str(quote.quoted_lead_time_days),
            'total_amount': str(quoted_total), 'currency': 'INR'}


def make_quote_drafts(context: dict, ai_note: str | None = None) -> list[dict]:
    result = []
    for vendor in context['vendors']:
        body = (f"Dear {vendor['vendor_name']},\n\n"
                f"Please provide a fresh quotation for {context['quantity']} {context['unit']} "
                f"of {context['item_name']} (SKU: {context['sku']}).\n"
                "Please confirm your current unit price in INR, the expected delivery time "
                "in days, and availability for this exact quantity.\n"
                f"Reference: ShelfSync RFQ-{context['purchase_order_id']}-Q{vendor['quotation_id']}.\n\n"
                "Thank you,\nShelfSync Purchasing")
        result.append({'vendor_id': vendor['vendor_id'], 'quotation_id':vendor['quotation_id'],
                       'to':vendor['vendor_email'],
                       'subject': f"Quotation Request RFQ-{context['purchase_order_id']} — {context['sku']}",
                       'body': body, 'ai_suggested_note': ai_note,
                       'dispatch_status': 'NOT_SENT_PREVIEW_ONLY'})
    return result


def make_order_draft(ctx: dict, ai_note: str | None = None) -> dict:
    body = (f"Dear {ctx['vendor_name']},\n\n"
            f"Purchase Order PO-{ctx['purchase_order_id']}\n"
            f"Product: {ctx['item_name']} (SKU: {ctx['sku']})\n"
            f"Quantity: {ctx['quantity']} {ctx['unit']}\n"
            f"Accepted fresh quotation: Q{ctx['quotation_id']}\n"
            f"Quoted unit price: INR {ctx['quoted_unit_price']}\n"
            f"Total order value: INR {ctx['total_amount']}\n"
            f"Quoted delivery time: {ctx['quoted_lead_time_days']} days\n\n"
            "Please acknowledge this purchase order and confirm the delivery schedule.\n\n"
            "Thank you,\nShelfSync Purchasing")
    return {'to':ctx['vendor_email'], 'vendor_id':ctx['vendor_id'],
            'quotation_id':ctx['quotation_id'],
            'subject':f"Purchase Order PO-{ctx['purchase_order_id']} — {ctx['sku']}",
            'body': body, 'ai_suggested_note':ai_note,
            'dispatch_status':'NOT_SENT_PREVIEW_ONLY'}
