from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select, union_all
from sqlalchemy.orm import Session

from app.db.models import InventoryItem, StockReceipt, Transaction
from app.db.seed import HISTORY_PREFIX


def inventory_value_trend(db: Session, *, days: int) -> list[dict]:
    today = datetime.now(timezone.utc).date()
    dates = [today - timedelta(days=offset) for offset in reversed(range(days))]
    cutoff = datetime.combine(dates[0], datetime.min.time(), timezone.utc)
    movements = union_all(
        select(Transaction.inventory_item_id.label("item_id"),
               Transaction.created_at.label("occurred_at"), (-Transaction.quantity).label("change"))
        .where(Transaction.transaction_type == "CHECKOUT", Transaction.created_at >= cutoff,
               ~Transaction.idempotency_key.startswith(HISTORY_PREFIX)),
        select(StockReceipt.inventory_item_id, StockReceipt.received_at, StockReceipt.quantity)
        .where(StockReceipt.received_at >= cutoff),
    ).subquery()
    rows = db.execute(
        select(InventoryItem.id, InventoryItem.created_at, InventoryItem.on_hand, InventoryItem.unit_cost,
               movements.c.occurred_at, movements.c.change)
        .outerjoin(movements, (movements.c.item_id == InventoryItem.id)
                   & (movements.c.occurred_at >= InventoryItem.created_at))
    )
    items = {}
    net_by_day = defaultdict(int)
    for item_id, created, on_hand, cost, occurred, quantity in rows:
        items[item_id] = (created.date(), on_hand, cost)
        if occurred is not None:
            net_by_day[item_id, occurred.date()] += quantity

    values = dict.fromkeys(dates, Decimal(0))
    for item_id, (created, quantity, cost) in items.items():
        for day in reversed(dates):
            if day < created:
                break
            values[day] += quantity * cost
            quantity -= net_by_day.get((item_id, day), 0)
    return [{"date": day, "value": value} for day, value in values.items()]
