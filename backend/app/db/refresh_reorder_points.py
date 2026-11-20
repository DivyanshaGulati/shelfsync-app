"""Recalculate missing stored reorder points for specified SKUs."""

import argparse
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.forecast import calculate_forecast
from app.db.models import InventoryItem
from app.db.session import get_session_factory


def refresh_missing_reorder_points(db: Session, skus: set[str]) -> dict[str, str]:
    items = db.scalars(
        select(InventoryItem).where(InventoryItem.sku.in_(skus))
        .order_by(InventoryItem.id).with_for_update()
    ).all()
    now = datetime.now(timezone.utc)
    results = {}
    for item in items:
        if item.reorder_point > 0:
            results[item.sku] = f"Existing reorder point {item.reorder_point} was kept"
            continue
        forecast = calculate_forecast(db, item, now=now)
        if forecast["reorder_point"] > 0:
            calculate_forecast(db, item, persist=True, now=now)
            results[item.sku] = f"Reorder point updated to {item.reorder_point}"
        else:
            results[item.sku] = (
                f"Reorder point is still 0.0 (daily demand {forecast['avg_daily_demand']}, "
                f"vendor lead time {forecast['avg_lead_time_days']} days)"
            )
    for sku in sorted(skus - results.keys()):
        results[sku] = "SKU not found"
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skus", metavar="SKU", nargs="+")
    args = parser.parse_args()
    with get_session_factory().begin() as db:
        results = refresh_missing_reorder_points(db, {sku.strip().upper() for sku in args.skus})
    for sku, result in results.items():
        print(f"{sku}: {result}")


if __name__ == "__main__":
    main()
