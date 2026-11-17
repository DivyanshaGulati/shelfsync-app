"""Read-only, deterministic comparison of fresh quotations for one MVP order.

No AI selection, purchase authorization, email transmission, or database writes.
Reliability uses only completed, placed orders with recorded timestamps and a
selected quotation describing the supplier's promised delivery time.
"""
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.quotations import aware, collection_expired
from app.core.sample_vendor_catalogue import is_sample_vendor
from app.db.models import OrderItem, PurchaseOrder, Vendor, VendorItem, VendorQuotation


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def performance_for_vendors(db: Session, vendor_ids: set[int]) -> dict[int, dict]:
    """On-time rate from historical completed orders with a selected fresh quote.

    No seeded or estimated performance data. Orders without a usable promise or
    timestamps are omitted, rather than counted as late or on-time.
    """
    result = {vid: {'completed_orders_with_delivery_data': 0, 'on_time_deliveries': 0,
                    'on_time_rate_percent': None} for vid in vendor_ids}
    if not vendor_ids:
        return result
    rows = db.execute(
        select(PurchaseOrder.vendor_id, PurchaseOrder.placed_at, PurchaseOrder.completed_at,
               VendorQuotation.quoted_lead_time_days)
        .join(VendorQuotation,
              (VendorQuotation.purchase_order_id == PurchaseOrder.id)
              & (VendorQuotation.vendor_id == PurchaseOrder.vendor_id)
              & (VendorQuotation.status == 'SELECTED'))
        .where(PurchaseOrder.vendor_id.in_(vendor_ids),
               PurchaseOrder.status == 'RECEIVED',
               PurchaseOrder.placed_at.is_not(None),
               PurchaseOrder.mock_order.is_(False),
               PurchaseOrder.completed_at.is_not(None),
               VendorQuotation.quoted_lead_time_days.is_not(None))
    ).all()
    for vendor_id, placed_at, completed_at, promised_days in rows:
        if promised_days < 0 or aware(completed_at) < aware(placed_at):
            continue
        entry = result[vendor_id]
        entry['completed_orders_with_delivery_data'] += 1
        if aware(completed_at) <= aware(placed_at) + timedelta(days=float(promised_days)):
            entry['on_time_deliveries'] += 1
    for entry in result.values():
        if entry['completed_orders_with_delivery_data']:
            entry['on_time_rate_percent'] = money(
                Decimal(100) * entry['on_time_deliveries'] /
                entry['completed_orders_with_delivery_data'])
    return result


def compare_quotations(db: Session, order: PurchaseOrder, *, include_processed: bool = False) -> dict:
    """Compare RECEIVED quotations only after collection has finished.

    The result is informational: no quotation is selected and no PO is placed.
    """
    if order.status not in ('WAITING_FOR_QUOTES', 'PENDING_APPROVAL') and not (include_processed and order.mock_order):
        raise ValueError('Comparison is only available while collecting quotes or awaiting Owner approval')
    line = db.scalar(select(OrderItem).where(OrderItem.purchase_order_id == order.id))
    if line is None or line.ordered_quantity <= 0:
        raise ValueError('Purchase request is missing a valid order quantity')
    all_quotes = list(db.scalars(select(VendorQuotation).where(
        VendorQuotation.purchase_order_id == order.id).order_by(VendorQuotation.id)))
    if not all_quotes:
        raise ValueError('No quotation requests exist for this purchase request')
    if any(q.status == 'REQUESTED' for q in all_quotes):
        if collection_expired(order):
            raise ValueError('Close the quotation window before comparing received quotes')
        raise ValueError('Quotation collection is still open')
    vendor_ids = {q.vendor_id for q in all_quotes}
    vendors = {v.id: v for v in db.scalars(select(Vendor).where(Vendor.id.in_(vendor_ids)))}
    quotes = [
        q for q in all_quotes if q.status == 'RECEIVED'
        or (include_processed and order.mock_order and q.status == 'SELECTED')
        or (order.mock_order and q.is_simulated and q.status == 'EXPIRED'
            and q.responded_at is not None and is_sample_vendor(vendors.get(q.vendor_id)))
    ]
    if not quotes:
        return {
            'purchase_order_id': order.id, 'required_quantity': line.ordered_quantity,
            'currency': 'INR', 'comparison_state': 'NO_VALID_QUOTES',
            'eligible_count': 0, 'received_count': 0, 'quotations': [],
            'lowest_cost_quotation_id': None, 'fastest_quotation_id': None,
            'note': 'No received quotations are available; no purchasing decision is possible.',
        }
    vendor_ids = {q.vendor_id for q in quotes}
    mappings = {m.vendor_id: m for m in db.scalars(select(VendorItem).where(
        VendorItem.inventory_item_id == order.inventory_item_id,
        VendorItem.vendor_id.in_(vendor_ids)))}
    history = performance_for_vendors(db, vendor_ids)
    entries = []
    for quote in quotes:
        vendor = vendors.get(quote.vendor_id)
        mapping = mappings.get(quote.vendor_id)
        catalogue_quote = order.mock_order and quote.is_simulated and is_sample_vendor(vendor)
        issues = []
        if vendor is None or mapping is None:
            issues.append('VENDOR_NOT_MAPPED')
        if vendor is not None and not vendor.is_active and not catalogue_quote:
            issues.append('VENDOR_INACTIVE')
        if quote.status == 'EXPIRED':
            issues.append('REQUESTED_QUANTITY_UNAVAILABLE')
        if mapping is not None and line.ordered_quantity < mapping.min_order_qty:
            issues.append('BELOW_VENDOR_MINIMUM_ORDER_QUANTITY')
        if quote.quoted_unit_price is None or quote.quoted_unit_price <= 0 or quote.quoted_lead_time_days is None or quote.quoted_lead_time_days < 0 or quote.responded_at is None:
            issues.append('INCOMPLETE_OR_INVALID_QUOTE')
        last = mapping.last_known_unit_price if mapping else None
        increase = None
        if last is not None and last > 0 and quote.quoted_unit_price is not None:
            increase = money((quote.quoted_unit_price - last) * Decimal('100') / last)
        total = money(quote.quoted_unit_price * line.ordered_quantity) if quote.quoted_unit_price is not None else None
        entries.append({
            'quotation_id': quote.id, 'vendor_id': quote.vendor_id,
            'vendor_name': vendor.name if vendor else None,
            'is_active': vendor.is_active if vendor else False,
            'is_new_vendor': vendor.is_new_vendor if vendor else None,
            'is_slow': vendor.is_slow if vendor else None,
            'is_preferred': mapping.is_preferred if mapping else False,
            'minimum_order_quantity': mapping.min_order_qty if mapping else None,
            'required_quantity': line.ordered_quantity,
            'available_quantity': line.ordered_quantity if not issues else 0,
            'last_known_unit_price': last, 'quoted_unit_price': quote.quoted_unit_price,
            'price_change_percent': increase,
            'quoted_delivery_days': quote.quoted_lead_time_days,
            'total_quoted_cost': total,
            'is_simulated': quote.is_simulated,
            'catalogue_quote': catalogue_quote,
            'reliability': history[quote.vendor_id],
            'eligible_for_comparison': not issues, 'ineligibility_reasons': issues,
        })
    entries.sort(key=lambda x: (
        not x['eligible_for_comparison'],
        x['total_quoted_cost'] if x['total_quoted_cost'] is not None else Decimal('Infinity'),
        x['quoted_delivery_days'] if x['quoted_delivery_days'] is not None else Decimal('Infinity'),
        x['vendor_id'],
    ))
    eligible = [e for e in entries if e['eligible_for_comparison']]
    by_speed = min(eligible, key=lambda x: (x['quoted_delivery_days'], x['total_quoted_cost'], x['vendor_id'])) if eligible else None
    return {
        'purchase_order_id': order.id, 'required_quantity': line.ordered_quantity,
        'currency': 'INR',
        'comparison_state': 'READY' if eligible else 'NO_ELIGIBLE_QUOTES',
        'eligible_count': len(eligible), 'received_count': sum(q.status != 'EXPIRED' for q in quotes),
        'quotations': entries,
        'lowest_cost_quotation_id': eligible[0]['quotation_id'] if eligible else None,
        'fastest_quotation_id': by_speed['quotation_id'] if by_speed else None,
        'note': 'Read-only backend comparison; no vendor selected, AI recommendation made, or order placed.',
    }
