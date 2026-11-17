"""Read-only demo preflight. Run: python -m app.verification.demo_preflight.

Checks seed presence and mappings, configuration, and demo readiness. Does not
write data, seed users, generate quotations, or dispatch messages.
"""
from __future__ import annotations

import argparse

from sqlalchemy import select

from app.core.claude_agent import AgentConfigurationError, provider_route
from app.core.config import settings
from app.core.forecast import calculate_forecast
from app.db.models import InventoryItem, PurchaseOrder, Transaction, Vendor, VendorItem, User
from app.db.seed import HISTORY_PREFIX, ITEMS
from app.db.session import get_session_factory

REQUIRED_SKUS = ("HW-SCR-15", "EL-WIR-25", "PL-TEF-01")
REQUIRED_VENDOR_MAPPINGS = {
    "PL-TEF-01": {"Shreeji Plumbing & Pipes", "Bharat Hardware & Fasteners"},
    "EL-WIR-25": {"Metro Electrical & Tools", "Bharat Hardware & Fasteners"},
}


def inspect_demo(session):
    """Return non-secret readiness findings; no SQL writes."""
    issues = []
    items = {i.sku: i for i in session.scalars(select(InventoryItem).where(InventoryItem.sku.in_(REQUIRED_SKUS))).all()}
    for sku in REQUIRED_SKUS:
        if sku not in items:
            issues.append(f"Missing seeded inventory item {sku}")
        elif items[sku].on_hand < 0 or items[sku].reserved_qty > items[sku].on_hand:
            issues.append(f"Invalid stock state for {sku}")
    for sku, expected in REQUIRED_VENDOR_MAPPINGS.items():
        if sku not in items:
            continue
        names = set(session.scalars(select(Vendor.name).join(VendorItem, VendorItem.vendor_id == Vendor.id).where(VendorItem.inventory_item_id == items[sku].id, Vendor.is_active.is_(True))).all())
        for missing in sorted(expected - names):
            issues.append(f"Missing active vendor mapping: {sku} -> {missing}")
    roles = set(session.scalars(select(User.role).where(User.is_active.is_(True))).all())
    for role in ("OWNER", "EMPLOYEE"):
        if role not in roles:
            issues.append(f"No active {role} user")
    return issues


def configuration_findings(config):
    issues = []
    if config.email_mode.lower() != "mock" or config.enable_real_email_dispatch:
        issues.append("Demo requires EMAIL_MODE=mock and ENABLE_REAL_EMAIL_DISPATCH=false")
    if not config.enable_quote_simulation:
        issues.append("ENABLE_QUOTE_SIMULATION must be true for demo replies")
    if not config.enable_busy_week_simulation:
        issues.append("ENABLE_BUSY_WEEK_SIMULATION must be true for the dashboard demo action")
    if config.ai_provider.strip().lower() not in ("groq", "claude"):
        issues.append("Use AI_PROVIDER=groq or claude for the cloud demo")
    try:
        provider_route(config)
    except AgentConfigurationError as exc:
        issues.append(str(exc))
    return issues


def clean_demo_findings(session, *, now=None):
    issues = []
    starting_stock = {sku: stock for _, sku, _, _, _, stock in ITEMS}
    items = {item.sku: item for item in session.scalars(select(InventoryItem))}
    if items.keys() != starting_stock.keys():
        issues.append("Clean demo inventory must contain exactly the seeded SKUs")
    for sku in sorted(items.keys() & starting_stock.keys()):
        item = items[sku]
        if item.on_hand != starting_stock[sku] or item.reserved_qty != 0:
            issues.append(f"Stock for {sku} differs from the seeded baseline")
        if item.on_hand - item.reserved_qty <= item.reorder_point or calculate_forecast(
            session, item, now=now,
        )["replenishment_required"]:
            issues.append(f"Stock for {sku} is below or at its reorder point")
    if session.scalar(select(PurchaseOrder.id).limit(1)) is not None:
        issues.append("Clean demo must have zero purchase requests and orders")
    non_seed_activity = select(Transaction.id).where(
        (Transaction.idempotency_key.is_(None)) | (~Transaction.idempotency_key.startswith(HISTORY_PREFIX))
    ).limit(1)
    if session.scalar(non_seed_activity) is not None:
        issues.append("Clean demo must have no checkout activity beyond the seed history")
    return issues


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect-clean", action="store_true", help="Verify the freshly seeded demo baseline")
    args = parser.parse_args()
    if not settings.database_url.startswith("postgresql+"):
        print("FAIL: Demo preflight requires a PostgreSQL DATABASE_URL")
        raise SystemExit(2)
    issues = configuration_findings(settings)
    try:
        with get_session_factory()() as session:
            issues.extend(inspect_demo(session))
            if args.expect_clean:
                issues.extend(clean_demo_findings(session))
    except Exception as exc:
        print(f"FAIL: Cannot read demo database ({type(exc).__name__}); check PostgreSQL and DATABASE_URL")
        raise SystemExit(2) from None
    if issues:
        print("DEMO PREFLIGHT: NOT READY")
        for issue in issues:
            print(f" - {issue}")
        raise SystemExit(1)
    print("DEMO PREFLIGHT: READY — seeded items, active vendor mappings, roles, and mock-only settings verified")
    if args.expect_clean:
        print("CLEAN BASELINE: healthy seeded stock, zero purchase requests and orders, seed history only")
    print("Note: readiness does not guarantee sufficient stock, new replenishment, or either purchasing decision path.")


if __name__ == "__main__":
    main()
