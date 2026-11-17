"""Persist and identify the two catalogue-only sample suppliers."""
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.db.models import InventoryItem, Vendor, VendorItem


class SampleCatalogueError(ValueError):
    pass


SAMPLE_VENDORS = (
    {"name": "West Delhi Industrial Supply", "email": "quotes@westdelhi-industrial.example",
     "phone": "+91 11 4XXX 0142"},
    {"name": "NCR Rapid Hardware Traders", "email": "quotes@ncr-rapid-hardware.example",
     "phone": "+91 11 4XXX 0186"},
)
CENT = Decimal("0.01")
MAX_PRICE = Decimal("9999999999.99")  # VendorItem Numeric(12, 2)


SAMPLE_VENDOR_NAMES = {spec["email"]: spec["name"] for spec in SAMPLE_VENDORS}


def is_sample_vendor(vendor: Vendor | None) -> bool:
    return vendor is not None and not vendor.is_active and SAMPLE_VENDOR_NAMES.get(vendor.email) == vendor.name


def sample_vendors(db: Session) -> list[Vendor]:
    vendors = list(db.scalars(
        select(Vendor).where(Vendor.email.in_(SAMPLE_VENDOR_NAMES)).order_by(Vendor.id)
    ))
    if any(not is_sample_vendor(vendor) for vendor in vendors):
        raise SampleCatalogueError("A sample vendor identity conflicts with an existing vendor")
    return vendors


def sample_prices(unit_cost: Decimal) -> tuple[Decimal, Decimal]:
    economy = (unit_cost * Decimal("1.05")).quantize(CENT, rounding=ROUND_HALF_UP)
    express = (unit_cost * Decimal("1.13")).quantize(CENT, rounding=ROUND_HALF_UP)
    economy = min(MAX_PRICE - CENT, max(CENT, economy))
    express = min(MAX_PRICE, max(economy + CENT, express))
    return economy, express


def sync_sample_quotes(db: Session, *, item_id: int | None = None) -> None:
    """Fill missing catalogue mappings in the caller's transaction."""
    insert = sqlite_insert if db.get_bind().dialect.name == "sqlite" else postgresql_insert
    # Keep sample identities out of ordinary replenishment and forecasting.
    db.execute(insert(Vendor).on_conflict_do_nothing(index_elements=[Vendor.email]), [
        {**spec, "is_active": False} for spec in SAMPLE_VENDORS
    ])
    vendors = {vendor.email: vendor.id for vendor in sample_vendors(db)}
    economy_id, express_id = (vendors[spec["email"]] for spec in SAMPLE_VENDORS)
    items = select(InventoryItem.id, InventoryItem.unit_cost)
    mappings = select(VendorItem.inventory_item_id, VendorItem.vendor_id).where(
        VendorItem.vendor_id.in_((economy_id, express_id))
    )
    if item_id is not None:
        items = items.where(InventoryItem.id == item_id)
        mappings = mappings.where(VendorItem.inventory_item_id == item_id)
    existing = set(db.execute(mappings))
    missing = []
    for inventory_id, unit_cost in db.execute(items):
        economy, express = sample_prices(unit_cost)
        for vendor_id, price, days in ((economy_id, economy, 4), (express_id, express, 2)):
            if (inventory_id, vendor_id) not in existing:
                missing.append({
                    "inventory_item_id": inventory_id, "vendor_id": vendor_id,
                    "last_known_unit_price": price, "expected_lead_time_days": days,
                })
    if missing:
        db.execute(insert(VendorItem).on_conflict_do_nothing(
            index_elements=[VendorItem.inventory_item_id, VendorItem.vendor_id]
        ), missing)
