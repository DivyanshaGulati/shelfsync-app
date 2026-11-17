"""Explicit, idempotent development-data seed. Run: python -m app.db.seed

Requires an upgraded database and three SEED_* passwords in backend/.env.
The synthetic checkout history is clearly tagged and does not imply actual
vendor delivery performance or live prices. It is already reflected in the
seeded on-hand snapshots (no physical stock deduction occurs in this seed).
"""
from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.db.models import InventoryItem, Transaction, User, Vendor, VendorItem
from app.db.session import get_session_factory

# Seed values and mapping order are deterministic.
ITEMS = (
    ("Gypsum Screw 1.5 inch", "HW-SCR-15", "Fasteners", "box", 180, 40),
    ("Gitti (Wall Plugs) 6mm", "HW-GIT-06", "Fasteners", "packet", 45, 80),
    ("Teflon Tape", "PL-TEF-01", "Plumbing", "roll", 20, 60),
    ("CPVC Pipe 3/4 inch", "PL-CPV-34", "Plumbing", "piece", 110, 45),
    ("PVC Elbow 3/4 inch", "PL-ELB-34", "Plumbing", "piece", 25, 75),
    ("2.5mm Copper Wire Coil", "EL-WIR-25", "Electrical", "coil", 1650, 12),
    ("Insulation Tape Roll", "EL-TAP-01", "Electrical", "roll", 25, 100),
    ("4 inch Tile Cutting Blade", "TL-BLD-04", "Tools & Adhesives", "piece", 105, 24),
    ("M-Seal Epoxy Compound", "TL-MSL-01", "Tools & Adhesives", "packet", 85, 42),
    ("Door Hinges 4 inch SS", "HW-HNG-04", "Hardware Fittings", "pair", 150, 32),
)
VENDORS = (
    ("Bharat Hardware & Fasteners", "bharat.hardware@example.com"),
    ("Shreeji Plumbing & Pipes", "shreeji.plumbing@example.com"),
    ("Metro Electrical & Tools", "metro.tools@example.com"),
)
# vendor index (0=Bharat, 1=Shreeji, 2=Metro), SKU, old price, expected days, MOQ, preferred
MAPPINGS = (
    (0, "HW-SCR-15", "175", 2, 1, True),
    (2, "HW-SCR-15", "182", 3, 1, False),
    (0, "HW-GIT-06", "42", 2, 1, True),
    (1, "HW-GIT-06", "45", 3, 5, False),
    (1, "PL-TEF-01", "18", 2, 5, True),
    (0, "PL-TEF-01", "20", 3, 1, False),
    (1, "PL-CPV-34", "105", 3, 5, True),
    (0, "PL-CPV-34", "112", 4, 2, False),
    (1, "PL-ELB-34", "24", 2, 5, True),
    (0, "PL-ELB-34", "26", 3, 2, False),
    (2, "EL-WIR-25", "1600", 2, 1, True),
    (0, "EL-WIR-25", "1650", 4, 1, False),
    (2, "EL-TAP-01", "22", 2, 10, True),
    (0, "EL-TAP-01", "25", 3, 5, False),
    (2, "TL-BLD-04", "95", 2, 1, True),
    (0, "TL-BLD-04", "100", 3, 1, False),
    (0, "TL-MSL-01", "82", 2, 1, True),
    (1, "TL-MSL-01", "86", 3, 2, False),
    (2, "TL-MSL-01", "88", 2, 1, False),
    (0, "HW-HNG-04", "145", 2, 1, True),
    (2, "HW-HNG-04", "155", 3, 1, False),
)
HISTORY_TAG = "DEMO SIMULATED CONSUMPTION (seed-v1); already reflected in initial on_hand"
HISTORY_PREFIX = "shelfsync-seed-v1:history:"


def _require_password(name: str) -> str:
    value = os.getenv(name, "")
    # Settings reads .env, but direct environment takes precedence.
    if not value:
        from dotenv import dotenv_values
        value = dotenv_values(settings.model_config["env_file"]).get(name) or ""
    if len(value) < 12:
        raise ValueError(f"{name} must be set to a private password of at least 12 characters")
    return value


def _seed_users(session: Session, *, owner_email: str, owner_password: str, employee_password: str) -> dict[str, User]:
    specs = (
        ("owner", "ShelfSync Owner", owner_email, "OWNER", owner_password),
        ("employee1", "Demo Employee One", "employee1@shelfsync.example", "EMPLOYEE", employee_password),
        ("employee2", "Demo Employee Two", "employee2@shelfsync.example", "EMPLOYEE", employee_password),
    )
    results = {}
    for key, name, email, role, password in specs:
        existing = session.scalar(select(User).where(func.lower(User.email) == email.lower()))
        if existing is not None:
            if existing.role != role:
                raise ValueError(f"Existing user {email} has a different role; refusing to change it")
            results[key] = existing
            continue  # Never reset an existing user's password or activation status.
        user = User(name=name, email=email, role=role, password_hash=hash_password(password), is_active=True)
        session.add(user)
        session.flush()
        results[key] = user
    return results


def _seed_items(session: Session) -> dict[str, InventoryItem]:
    result = {}
    for name, sku, category, unit, cost, starting_stock in ITEMS:
        item = session.scalar(select(InventoryItem).where(InventoryItem.sku == sku))
        if item is None:
            # Initial values are demo snapshots; forecast is not presented as measured.
            item = InventoryItem(
                name=name, sku=sku, category=category, unit=unit,
                unit_cost=Decimal(cost), on_hand=starting_stock, reserved_qty=0,
                avg_daily_demand=Decimal("0"), demand_std_dev=Decimal("0"),
                avg_lead_time_days=Decimal("0"), lead_time_std_dev=Decimal("0"),
                safety_stock=Decimal("0"), reorder_point=Decimal("0"), low_confidence=True,
                description="Illustrative demo item; unit cost is not a current vendor quote.",
            )
            session.add(item)
            session.flush()
        elif (item.unit, item.category) != (unit, category):
            raise ValueError(f"Existing SKU {sku} differs in category/unit; refusing to overwrite")
        result[sku] = item
    return result


def _seed_vendors(session: Session) -> list[Vendor]:
    result = []
    for name, email in VENDORS:
        vendor = session.scalar(select(Vendor).where(Vendor.email == email))
        if vendor is None:
            vendor = Vendor(name=name, email=email, phone=None, is_active=True, is_new_vendor=False, is_slow=False)
            session.add(vendor)
            session.flush()
        elif vendor.name != name:
            raise ValueError(f"Existing vendor email {email} belongs to another name")
        result.append(vendor)
    return result


def _seed_mappings(session: Session, items: dict[str, InventoryItem], vendors: list[Vendor]) -> None:
    for idx, sku, price, lead, moq, preferred in MAPPINGS:
        item, vendor = items[sku], vendors[idx]
        mapping = session.scalar(select(VendorItem).where(
            VendorItem.inventory_item_id == item.id, VendorItem.vendor_id == vendor.id,
        ))
        if mapping is None:
            if preferred:
                conflicting = session.scalar(select(VendorItem).where(
                    VendorItem.inventory_item_id == item.id, VendorItem.is_preferred.is_(True),
                ))
                if conflicting is not None:
                    raise ValueError(f"Existing item {sku} has another preferred vendor")
            session.add(VendorItem(
                inventory_item_id=item.id, vendor_id=vendor.id, is_preferred=preferred,
                last_known_unit_price=Decimal(price), expected_lead_time_days=Decimal(lead),
                min_order_qty=moq,
            ))
            session.flush()
        elif (mapping.is_preferred, mapping.min_order_qty) != (preferred, moq):
            # Never overwrite edited supplier settings silently.
            raise ValueError(f"Existing mapping for {sku} / {vendor.name} differs; review manually")


def _daily_quantity(sku: str, day_offset: int) -> int:
    """Deterministic contractor-style consumption with real zero-consumption days."""
    idx = next(i for i, row in enumerate(ITEMS) if row[1] == sku)
    # 0 on some days; exact patterns are synthetic, not actual sales history.
    if sku == "EL-WIR-25":
        return 1 if (day_offset + idx) % 5 == 0 else 0
    if sku == "TL-BLD-04":
        return 1 if (day_offset + idx) % 4 == 0 else 0
    return (day_offset * 3 + idx * 5) % (4 if sku == "PL-TEF-01" else 5)


def _seed_history(session: Session, items: dict[str, InventoryItem], employee: User, as_of: datetime) -> None:
    existing = session.scalar(select(Transaction.id).where(Transaction.idempotency_key.like(f"{HISTORY_PREFIX}%")).limit(1))
    if existing is not None:
        return  # Do not create another rolling history window on a subsequent run.
    start_day = as_of.date() - timedelta(days=30)
    for day in range(30):
        occurred = datetime.combine(start_day + timedelta(days=day), datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=12)
        for sku, item in items.items():
            qty = _daily_quantity(sku, day)
            if qty == 0:
                continue  # Missing daily rows mean zero consumption for forecasting.
            session.add(Transaction(
                user_id=employee.id, inventory_item_id=item.id, transaction_type="CHECKOUT",
                quantity=qty, usage_note=HISTORY_TAG,
                idempotency_key=f"{HISTORY_PREFIX}{sku}:{occurred.date().isoformat()}",
                created_at=occurred,
            ))
    session.flush()


def seed_database(session: Session, *, owner_email: str, owner_password: str,
                  employee_password: str, as_of: datetime | None = None) -> dict[str, int]:
    """Seed one transaction atomically. Existing business data is left untouched."""
    if not owner_email or "@" not in owner_email or owner_email.rsplit("@", 1)[-1] in {"example.com", "example.org", "example.net"} or owner_email.rsplit("@", 1)[-1].endswith(".example"):
        raise ValueError("OWNER_EMAIL must be a valid non-placeholder email in backend/.env")
    if len(owner_password) < 12 or len(employee_password) < 12:
        raise ValueError("Seed passwords must contain at least 12 characters")
    as_of = as_of or datetime.now(timezone.utc)
    users = _seed_users(session, owner_email=owner_email.strip().lower(), owner_password=owner_password, employee_password=employee_password)
    items = _seed_items(session)
    vendors = _seed_vendors(session)
    _seed_mappings(session, items, vendors)
    _seed_history(session, items, users["employee1"], as_of)
    session.flush()
    return {
        "users": session.scalar(select(func.count()).select_from(User)),
        "inventory_items": session.scalar(select(func.count()).select_from(InventoryItem)),
        "vendors": session.scalar(select(func.count()).select_from(Vendor)),
        "vendor_items": session.scalar(select(func.count()).select_from(VendorItem)),
        "historical_transactions": session.scalar(select(func.count()).select_from(Transaction).where(Transaction.idempotency_key.like(f"{HISTORY_PREFIX}%"))),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Idempotently seed ShelfSync local demo data")
    parser.add_argument("--confirm-demo", action="store_true", help="Required acknowledgement: writes simulated history")
    args = parser.parse_args()
    if not args.confirm_demo:
        parser.error("Pass --confirm-demo to seed the database")
    if not settings.database_url.startswith("postgresql+"):
        parser.error("CLI seeding is restricted to the configured PostgreSQL database")
    owner_email = settings.owner_email
    owner_password = _require_password("SEED_OWNER_PASSWORD")
    employee_password = _require_password("SEED_EMPLOYEE_PASSWORD")
    factory = get_session_factory()
    with factory() as session:
        with session.begin():
            counts = seed_database(session, owner_email=owner_email, owner_password=owner_password,
                                   employee_password=employee_password)
    print("Seed complete (safe to re-run). Totals in database:")
    for key, val in counts.items():
        print(f"  {key}: {val}")
    print("Vendor prices are last-known demo references, not fresh quotes. No purchase orders or delivery performance seeded.")


if __name__ == "__main__":
    main()
