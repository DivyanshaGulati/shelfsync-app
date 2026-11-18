import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from itertools import combinations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.claude_agent import AgentInvalidResponse, AgentUnavailable, suggest_checkout_pair_action
from app.db.models import InventoryItem, Transaction

UUID = r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}"
CART_KEY = re.compile(rf"checkout-cart-({UUID})-{UUID}")
WINDOW_DAYS = 30


def checkout_growth_tip(db: Session) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
    rows = db.execute(
        select(Transaction.user_id, Transaction.idempotency_key, Transaction.inventory_item_id)
        .where(Transaction.transaction_type == "CHECKOUT", Transaction.created_at >= cutoff,
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
        return {"tip": None}
    pair, together = min(pairs.items(), key=lambda entry: (-entry[1], entry[0]))
    if together < 3:
        return {"tip": None}

    products = {item_id: name for item_id, name in db.execute(
        select(InventoryItem.id, InventoryItem.name).where(InventoryItem.id.in_(pair))
    )}
    db.rollback()
    if any(item_id not in products for item_id in pair):
        return {"tip": None}

    share = round(100 * together / total)
    try:
        action = suggest_checkout_pair_action(together=together, total=total)
    except (AgentUnavailable, AgentInvalidResponse):
        action = "display_together"
    return {"tip": {
        "products": [products[item_id] for item_id in pair],
        "together": together,
        "total": total,
        "share": share,
        "window_days": WINDOW_DAYS,
        "action": action,
    }}
