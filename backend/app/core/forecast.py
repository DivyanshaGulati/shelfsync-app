"""Deterministic inventory forecast calculations; no AI purchasing authority.

All date windows use UTC calendar days including today, with zero-consumption
calendar days included. The service writes only when persist=True, and the caller
must commit those writes in the same transaction as the inventory change.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from statistics import mean, pstdev

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import (
    AIThresholdLog, InventoryItem, OrderItem, PurchaseOrder, Transaction,
    Vendor, VendorItem,
)

ACTIVE_ON_ORDER_STATUSES = ("PLACED", "PARTIALLY_RECEIVED")
MIN_CONSUMPTION_DAYS = 14
MIN_DELIVERIES = 3


def _number(value: float, digits: str) -> Decimal:
    return Decimal(str(value)).quantize(Decimal(digits), rounding=ROUND_HALF_UP)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        # SQLite may return naive DB times in tests; interpret as UTC.
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def calculate_forecast(db: Session, item: InventoryItem, *, persist: bool = False,
                       now: datetime | None = None) -> dict:
    """Compute inventory position/demand/safety stock/reorder point for one item.

    An existing item is not locked here: callers performing writes must first lock
    the item (checkout's conditional UPDATE does that). Never commit in this module.
    """
    now = _utc(now or datetime.now(timezone.utc))
    today = now.date()
    start = datetime.combine(today - timedelta(days=29), datetime.min.time(), timezone.utc)
    end = datetime.combine(today + timedelta(days=1), datetime.min.time(), timezone.utc)

    events = db.scalars(select(Transaction).where(
        Transaction.inventory_item_id == item.id,
        Transaction.transaction_type == "CHECKOUT",
        Transaction.created_at >= start,
        Transaction.created_at < end,
    )).all()
    daily = defaultdict(int)
    for event in events:
        daily[_utc(event.created_at).date()] += event.quantity
    days = [daily[today - timedelta(days=i)] for i in range(29, -1, -1)]
    demand = 0.5 * mean(days[-7:]) + 0.3 * mean(days[-14:]) + 0.2 * mean(days)
    sigma_d = pstdev(days)
    distinct_consumption_days = sum(1 for qty in days if qty > 0)

    # Historical delivery variability: actual (placed -> fully received) durations.
    completed_orders = db.scalars(select(PurchaseOrder).where(
        PurchaseOrder.inventory_item_id == item.id,
        PurchaseOrder.status == "RECEIVED",
        PurchaseOrder.mock_order.is_(False),
        PurchaseOrder.placed_at.is_not(None),
        PurchaseOrder.completed_at.is_not(None),
    )).all()
    lead_samples = [(_utc(p.completed_at) - _utc(p.placed_at)).total_seconds() / 86400
                    for p in completed_orders if _utc(p.completed_at) >= _utc(p.placed_at)]
    confidence_demand = distinct_consumption_days >= MIN_CONSUMPTION_DAYS
    confidence_lead = len(lead_samples) >= MIN_DELIVERIES

    # Prefer observed demand. For a truly new item, use its configured baseline.
    # No synthetic purchases/deliveries are created to fill history gaps.
    if not events:
        if item.estimated_daily_demand is not None:
            demand = float(item.estimated_daily_demand)
        elif item.avg_daily_demand:
            demand = float(item.avg_daily_demand)
            sigma_d = float(item.demand_std_dev)

    if confidence_lead:
        lead = mean(lead_samples)
        sigma_l = pstdev(lead_samples)
    elif item.estimated_lead_time_days is not None:
        lead = float(item.estimated_lead_time_days)
        sigma_l = 0.0
    else:
        # The item's configured baseline (if any) wins; otherwise the mapped
        # preferred vendor's expected lead time, then other active mappings.
        mappings = db.execute(select(VendorItem, Vendor).join(
            Vendor, VendorItem.vendor_id == Vendor.id
        ).where(VendorItem.inventory_item_id == item.id, Vendor.is_active.is_(True))).all()
        expected = sorted(
            (m for m, _v in mappings if m.expected_lead_time_days is not None),
            key=lambda m: (not m.is_preferred, m.id),
        )
        lead = float(item.avg_lead_time_days or 0) or (float(expected[0].expected_lead_time_days) if expected else 0.0)
        sigma_l = float(item.lead_time_std_dev or 0)

    outstanding = db.execute(select(OrderItem, PurchaseOrder).join(
        PurchaseOrder, OrderItem.purchase_order_id == PurchaseOrder.id
    ).where(PurchaseOrder.inventory_item_id == item.id,
            PurchaseOrder.status.in_(ACTIVE_ON_ORDER_STATUSES),
            OrderItem.is_open.is_(True))).all()
    on_order = sum(max(0, line.ordered_quantity - line.received_quantity) for line, _ in outstanding)
    position = item.on_hand + on_order - item.reserved_qty
    ss = settings.service_level_z * math.sqrt(max(0.0, lead * sigma_d ** 2 + demand ** 2 * sigma_l ** 2))
    rop = demand * lead + ss
    target = demand * (lead + settings.review_period_days) + ss
    quantity = max(0, math.ceil(target - position - 1e-10))
    low_confidence = not confidence_demand or not confidence_lead
    reasons = []
    if not confidence_demand:
        reasons.append(f"LOW_CONFIDENCE: only {distinct_consumption_days} consumption days (need {MIN_CONSUMPTION_DAYS}); demand fallback if no records")
    if not confidence_lead:
        reasons.append(f"LOW_CONFIDENCE: only {len(lead_samples)} completed deliveries (need {MIN_DELIVERIES}); expected lead-time fallback")
    if not reasons:
        reasons.append("Calculated using 30-day checkout history and actual vendor delivery history")
    reason = "; ".join(reasons)
    result = {
        "inventory_item_id": item.id,
        "on_hand": item.on_hand,
        "on_order": on_order,
        "reserved_qty": item.reserved_qty,
        "inventory_position": position,
        "avg_daily_demand": _number(demand, "0.0001"),
        "demand_std_dev": _number(sigma_d, "0.0001"),
        "avg_lead_time_days": _number(lead, "0.001"),
        "lead_time_std_dev": _number(sigma_l, "0.001"),
        "safety_stock": _number(ss, "0.0001"),
        "reorder_point": _number(rop, "0.0001"),
        "replenishment_required": position <= rop,
        "suggested_order_quantity": quantity,
        "low_confidence": low_confidence,
        "calculation_reason": reason,
    }
    if persist:
        for name in ("avg_daily_demand", "demand_std_dev", "avg_lead_time_days",
                     "lead_time_std_dev", "safety_stock", "reorder_point", "low_confidence"):
            setattr(item, name, result[name])
        db.add(AIThresholdLog(
            inventory_item_id=item.id,
            avg_daily_demand=result["avg_daily_demand"],
            demand_std_dev=result["demand_std_dev"],
            avg_lead_time_days=result["avg_lead_time_days"],
            lead_time_std_dev=result["lead_time_std_dev"],
            safety_stock=result["safety_stock"],
            reorder_point=result["reorder_point"],
            inventory_position=position,
            calculation_reason=reason,
        ))
    return result
