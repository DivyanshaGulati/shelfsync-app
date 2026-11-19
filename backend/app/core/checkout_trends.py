import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from itertools import combinations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.claude_agent import AgentInvalidResponse, AgentUnavailable, suggest_checkout_pair_action
from app.core.stock_status import available_stock, is_low_stock
from app.db.models import InventoryItem, Transaction
from app.db.seed import HISTORY_PREFIX

UUID = r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}"
CART_KEY = re.compile(rf"checkout-cart-({UUID})-{UUID}")
WINDOW_DAYS = 30
PAIR_WINDOW_DAYS = 7


def single_item_tip(db: Session, cutoff: datetime) -> dict | None:
    demand = (
        select(Transaction.inventory_item_id, func.sum(Transaction.quantity).label("units"))
        .where(Transaction.transaction_type == "CHECKOUT", Transaction.created_at >= cutoff,
               ~Transaction.idempotency_key.startswith(HISTORY_PREFIX))
        .group_by(Transaction.inventory_item_id)
        .subquery()
    )
    units = func.coalesce(demand.c.units, 0)
    available = InventoryItem.on_hand - InventoryItem.reserved_qty
    item = db.execute(
        select(InventoryItem.name, InventoryItem.on_hand, InventoryItem.reserved_qty,
               InventoryItem.reorder_point, units.label("units"))
        .outerjoin(demand, demand.c.inventory_item_id == InventoryItem.id)
        .order_by(units.desc(), available.desc(), InventoryItem.sku)
        .limit(1)
    ).first()
    if item is None:
        return None

    return {
        "product": item.name,
        "available": available_stock(item.on_hand, item.reserved_qty),
        "units_checked_out": item.units,
        "window_days": WINDOW_DAYS,
        "action": "review_stock" if is_low_stock(item.on_hand, item.reserved_qty, item.reorder_point)
        else "promote_item",
    }


def checkout_growth_tip(db: Session) -> dict:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=WINDOW_DAYS)
    pair_cutoff = now - timedelta(days=PAIR_WINDOW_DAYS)
    rows = db.execute(
        select(Transaction.user_id, Transaction.idempotency_key, Transaction.inventory_item_id)
        .where(Transaction.transaction_type == "CHECKOUT", Transaction.created_at >= pair_cutoff,
               Transaction.idempotency_key.like("checkout-cart-%"))
    )
    carts = defaultdict(set)
    for user_id, key, item_id in rows:
        match = CART_KEY.fullmatch(key)
        if match:
            carts[user_id, match[1]].add(item_id)

    pairs = Counter(pair for items in carts.values() for pair in combinations(sorted(items), 2))
    total = len(carts)
    if total < 10 or not pairs:
        return {"tip": single_item_tip(db, cutoff)}
    pair, together = min(pairs.items(), key=lambda entry: (-entry[1], entry[0]))
    if together < 3:
        return {"tip": single_item_tip(db, cutoff)}

    products = {item_id: name for item_id, name in db.execute(
        select(InventoryItem.id, InventoryItem.name).where(InventoryItem.id.in_(pair))
    )}
    db.rollback()
    if any(item_id not in products for item_id in pair):
        return {"tip": single_item_tip(db, cutoff)}

    cart_counts = Counter(item_id for items in carts.values() for item_id in items)
    primary, companion = sorted(pair, key=lambda item_id: (-cart_counts[item_id], item_id))
    primary_total = cart_counts[primary]
    primary_share = round(100 * together / primary_total)
    try:
        action = suggest_checkout_pair_action(together=together, total=primary_total)
    except (AgentUnavailable, AgentInvalidResponse):
        action = "display_together"
    return {"tip": {
        "products": [products[primary], products[companion]],
        "together": together,
        "total": total,
        "share": round(100 * together / total),
        "primary_total": primary_total,
        "primary_share": primary_share,
        "target_share": min(100, primary_share + 10) if primary_share < 100 else None,
        "window_days": PAIR_WINDOW_DAYS,
        "action": action,
    }}
