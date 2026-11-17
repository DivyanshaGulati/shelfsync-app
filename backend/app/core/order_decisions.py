"""Backend-owned quotation selection, strict auto-order validation, owner approval.

PLACED means internally authorized; vendor email requires separate dispatch. Never infer actual supplier dispatch from the PLACED status alone.
"""
from __future__ import annotations

import math
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.quote_comparison import compare_quotations, money
from app.db.models import (ApprovalLog, InventoryItem, OrderItem, PurchaseOrder,
                           VendorItem, VendorQuotation)
from app.core.quotations import utcnow


class DecisionError(ValueError):
    pass


def order_line(db: Session, order: PurchaseOrder) -> OrderItem:
    line = db.scalar(select(OrderItem).where(OrderItem.purchase_order_id == order.id))
    if not line or line.ordered_quantity <= 0:
        raise DecisionError('Missing positive order line')
    return line


def normal_order_quantity(db: Session, order: PurchaseOrder, item: InventoryItem) -> int:
    """Expected order quantity before including this WAITING order in OnOrder.

    Only actual outstanding PLACED / PARTIALLY_RECEIVED orders count. The
    initial requested quantity is excluded to avoid self-referential results.
    """
    outstanding = db.execute(
        select(OrderItem.ordered_quantity, OrderItem.received_quantity)
        .join(PurchaseOrder, PurchaseOrder.id == OrderItem.purchase_order_id)
        .where(PurchaseOrder.inventory_item_id == item.id,
               PurchaseOrder.id != order.id,
               PurchaseOrder.status.in_(['PLACED', 'PARTIALLY_RECEIVED']))
    ).all()
    position = item.on_hand - item.reserved_qty + sum(q - r for q, r in outstanding)
    target = (float(item.avg_daily_demand) *
              (float(item.avg_lead_time_days) + settings.review_period_days) +
              float(item.safety_stock))
    return max(0, math.ceil(target - position - 1e-10))


def eligibility(db: Session, order: PurchaseOrder, entry: dict) -> dict:
    """Evaluate ALL strict business rules with unrounded price comparison."""
    item = db.get(InventoryItem, order.inventory_item_id)
    line = order_line(db, order)
    mapping = db.scalar(select(VendorItem).where(
        VendorItem.inventory_item_id == order.inventory_item_id,
        VendorItem.vendor_id == entry['vendor_id']))
    reasons: list[str] = list(entry['ineligibility_reasons'])
    if not entry['eligible_for_comparison']:
        reasons.append('INELIGIBLE_QUOTATION')
    if entry['total_quoted_cost'] is None or entry['total_quoted_cost'] > Decimal(str(settings.auto_order_limit)):
        reasons.append('ORDER_EXCEEDS_AUTO_ORDER_LIMIT')
    old = mapping.last_known_unit_price if mapping else None
    quoted = entry['quoted_unit_price']
    if old is None or old <= 0:
        reasons.append('MISSING_PRIOR_VENDOR_PRICE')
    elif quoted is None or quoted > old * Decimal('1.10'):
        reasons.append('PRICE_INCREASE_OVER_10_PERCENT')
    if (entry['quoted_delivery_days'] is None or
            entry['quoted_delivery_days'] > Decimal(str(settings.max_acceptable_lead_time_days))):
        reasons.append('LEAD_TIME_TOO_LONG')
    if entry['is_new_vendor'] or entry['is_slow'] or not entry['is_active']:
        reasons.append('VENDOR_NOT_ROUTINE_ELIGIBLE')
    if item.low_confidence:
        reasons.append('LOW_CONFIDENCE_ITEM')
    normal = normal_order_quantity(db, order, item)
    if normal <= 0 or line.ordered_quantity > 2 * normal:
        reasons.append('ABNORMAL_ORDER_QUANTITY')
    return {'can_auto_order': not reasons, 'failure_reasons': list(dict.fromkeys(reasons)),
            'normal_order_quantity': normal, 'max_normal_quantity': 2 * normal}


def validate_quote(db: Session, order: PurchaseOrder, quote_id: int) -> tuple[VendorQuotation, dict]:
    # Approval always revalidates the closed collection and selected quotation.
    quotes = list(db.scalars(select(VendorQuotation).where(VendorQuotation.purchase_order_id == order.id)))
    if any(q.status == 'REQUESTED' for q in quotes):
        raise DecisionError('Quotation collection is not closed')
    quote = next((q for q in quotes if q.id == quote_id), None)
    if quote is None or quote.status != 'RECEIVED':
        raise DecisionError('Quotation does not belong to this request or is not RECEIVED')
    comparison = compare_quotations(db, order)
    match = next((x for x in comparison['quotations'] if x['quotation_id'] == quote_id), None)
    if match is None or not match['eligible_for_comparison']:
        raise DecisionError('Quotation is ineligible for this order')
    return quote, match


def authorize_order(db: Session, order: PurchaseOrder, quote: VendorQuotation, entry: dict) -> None:
    """Persist internal placement without sending a supplier email."""
    line = order_line(db, order)
    mapping = db.scalar(select(VendorItem).where(VendorItem.inventory_item_id == order.inventory_item_id,
                                                VendorItem.vendor_id == quote.vendor_id))
    if mapping is None:
        raise DecisionError('Selected vendor is not mapped to this item')
    total = money(quote.quoted_unit_price * line.ordered_quantity)
    quote.status = 'SELECTED'
    order.vendor_id = quote.vendor_id
    order.total_amount = total
    line.unit_price = quote.quoted_unit_price
    line.total_price = total
    order.placed_at = utcnow()
    order.status = 'PLACED'
    if not order.mock_order:
        mapping.last_known_unit_price = quote.quoted_unit_price


def evaluate(db: Session, order: PurchaseOrder, quotation_id: int | None = None) -> dict:
    if order.status != 'WAITING_FOR_QUOTES':
        raise DecisionError('Purchase request must be WAITING_FOR_QUOTES')
    comparison = compare_quotations(db, order)
    if comparison['comparison_state'] != 'READY':
        raise DecisionError('No eligible fresh quotations are available')
    candidate_id = quotation_id or comparison['lowest_cost_quotation_id']
    quote, entry = validate_quote(db, order, candidate_id)
    check = eligibility(db, order, entry)
    order.backend_candidate_quotation_id = quote.id  # backend candidate, NOT a Claude AI Pick
    if check['can_auto_order']:
        authorize_order(db, order, quote, entry)
        order.approval_reason = None
        outcome = 'AUTO_AUTHORIZED'
    else:
        order.status = 'PENDING_APPROVAL'
        order.approval_reason = ', '.join(check['failure_reasons'])
        outcome = 'OWNER_APPROVAL_REQUIRED'
    db.flush()
    return {'order_id': order.id, 'status': order.status, 'outcome': outcome,
            'backend_candidate_quotation_id': candidate_id, **check,
            'dispatch_status': 'NOT_SENT_EMAIL_INTEGRATION_PENDING'}


def decide_owner(db: Session, order: PurchaseOrder, owner_id: int, action: str,
                 quotation_id: int | None, comments: str | None) -> dict:
    if order.status != 'PENDING_APPROVAL':
        raise DecisionError('Order is not pending owner approval')
    if action == 'APPROVED':
        chosen = quotation_id or order.backend_candidate_quotation_id
        if chosen is None:
            raise DecisionError('Select a received quotation')
        quote, entry = validate_quote(db, order, chosen)
        authorize_order(db, order, quote, entry)
    elif action == 'REJECTED':
        if quotation_id is not None:
            raise DecisionError('Cannot select a quotation when rejecting')
        order.status = 'REJECTED'
    else:
        raise DecisionError('Unsupported approval action')
    db.add(ApprovalLog(purchase_order_id=order.id, owner_id=owner_id,
                       action=action, comments=comments))
    db.flush()
    return {'order_id': order.id, 'status': order.status, 'action': action,
            'vendor_id': order.vendor_id, 'total_amount': order.total_amount,
            'dispatch_status': 'NOT_SENT_EMAIL_INTEGRATION_PENDING' if action == 'APPROVED' else 'NOT_APPLICABLE'}
