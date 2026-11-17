"""Sample vendor catalogue using the existing vendor and product mappings."""
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import require_roles
from app.core.sample_vendor_catalogue import SampleCatalogueError, sample_vendors, sync_sample_quotes
from app.db.dependencies import get_db
from app.db.models import InventoryItem, User, Vendor, VendorItem


router = APIRouter(prefix="/api/vendors", tags=["Vendors"])

class SampleVendor(BaseModel):
    id: int
    name: str
    email: str
    phone: str | None


class SampleQuote(BaseModel):
    vendor_id: int
    inventory_item_id: int
    sku: str
    product_name: str
    unit: str
    unit_price: Decimal
    delivery_days: Decimal


class SampleCatalogue(BaseModel):
    vendors: list[SampleVendor]
    quotations: list[SampleQuote]


def catalogue(db: Session) -> SampleCatalogue:
    try:
        vendors = sample_vendors(db)
    except SampleCatalogueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    rows = db.execute(
        select(VendorItem, InventoryItem.sku, InventoryItem.name, InventoryItem.unit)
        .join(InventoryItem, InventoryItem.id == VendorItem.inventory_item_id)
        .where(VendorItem.vendor_id.in_([vendor.id for vendor in vendors]))
        .order_by(InventoryItem.sku, VendorItem.vendor_id)
    )
    return SampleCatalogue(
        vendors=[SampleVendor(id=v.id, name=v.name, email=v.email, phone=v.phone) for v in vendors],
        quotations=[
            SampleQuote(vendor_id=m.vendor_id, inventory_item_id=m.inventory_item_id, sku=sku,
                        product_name=name, unit=unit, unit_price=m.last_known_unit_price,
                        delivery_days=m.expected_lead_time_days)
            for m, sku, name, unit in rows
        ],
    )


@router.get("/sample-quotations", response_model=SampleCatalogue)
def get_sample_quotations(
    db: Annotated[Session, Depends(get_db)],
    _owner: Annotated[User, Depends(require_roles("OWNER"))],
):
    return catalogue(db)


@router.post("/sample-quotations", response_model=SampleCatalogue)
def sync_sample_quotations(
    db: Annotated[Session, Depends(get_db)],
    _owner: Annotated[User, Depends(require_roles("OWNER"))],
):
    try:
        sync_sample_quotes(db)
        db.commit()
        return catalogue(db)
    except SampleCatalogueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
