"""Read inventory and manage descriptive item details.

Stock mutations are handled by the transactional checkout and receiving services.
"""
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.auth import require_roles
from app.core.claude_agent import AgentInvalidResponse, AgentUnavailable, InventoryInsight, explain_inventory
from app.db.dependencies import get_db
from app.db.models import InventoryItem, OrderItem, PurchaseOrder, User
from app.core.forecast import ACTIVE_ON_ORDER_STATUSES, calculate_forecast
from app.core.stock_status import build_stock_snapshot

router = APIRouter(prefix="/api/inventory", tags=["Inventory"])


class ItemFields(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    sku: str = Field(min_length=1, max_length=64)
    category: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=5000)
    unit: str = Field(min_length=1, max_length=40)
    unit_cost: Decimal = Field(ge=0, max_digits=12, decimal_places=2)

    @field_validator("name", "sku", "category", "unit")
    @classmethod
    def trim_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Must not be blank")
        return value

    @field_validator("sku")
    @classmethod
    def normalize_sku(cls, value: str) -> str:
        return value.upper()


class ItemCreate(ItemFields):
    model_config = ConfigDict(extra="forbid")


class ItemUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    sku: str | None = Field(default=None, min_length=1, max_length=64)
    category: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=5000)
    unit: str | None = Field(default=None, min_length=1, max_length=40)
    unit_cost: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)

    @field_validator("name", "sku", "category", "unit")
    @classmethod
    def trim_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Must not be blank")
        return value

    @field_validator("sku")
    @classmethod
    def normalize_sku(cls, value: str | None) -> str | None:
        return value.upper() if value is not None else None


class ItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    sku: str
    category: str
    description: str | None
    unit: str
    unit_cost: Decimal
    on_hand: int
    reserved_qty: int
    avg_daily_demand: Decimal
    demand_std_dev: Decimal
    avg_lead_time_days: Decimal
    lead_time_std_dev: Decimal
    safety_stock: Decimal
    reorder_point: Decimal
    low_confidence: bool


class ItemList(BaseModel):
    items: list[ItemResponse]
    total: int
    limit: int
    offset: int


def item_or_404(db: Session, item_id: int) -> InventoryItem:
    item = db.get(InventoryItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Inventory item not found")
    return item


def commit_item(db: Session, item: InventoryItem) -> InventoryItem:
    try:
        db.add(item)
        db.commit()
        db.refresh(item)
    except IntegrityError as exc:
        db.rollback()
        # Database uniqueness constraint remains the final authority under concurrency.
        raise HTTPException(status_code=409, detail="An inventory item with this SKU already exists") from exc
    return item


@router.get("", response_model=ItemList)
def list_items(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_roles("OWNER", "EMPLOYEE"))],
    q: Annotated[str | None, Query(max_length=200)] = None,
    category: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    filters = []
    if q and q.strip():
        term = f"%{q.strip()}%"
        filters.append(InventoryItem.name.ilike(term) | InventoryItem.sku.ilike(term))
    if category and category.strip():
        filters.append(func.lower(InventoryItem.category) == category.strip().lower())
    total = db.scalar(select(func.count()).select_from(InventoryItem).where(*filters)) or 0
    rows = db.scalars(select(InventoryItem).where(*filters).order_by(InventoryItem.id).limit(limit).offset(offset)).all()
    return ItemList(items=list(rows), total=total, limit=limit, offset=offset)


@router.post("/ai-insights", response_model=InventoryInsight)
def inventory_ai_insights(
    db: Annotated[Session, Depends(get_db)],
    _owner: Annotated[User, Depends(require_roles("OWNER"))],
):
    # Low Stock is decided by app.core.stock_status (available stock <= ROP), the same
    # rule the Inventory and Overview pages show. On Order is incoming stock: it is
    # reported alongside each item but never added to what is physically available.
    items = db.execute(
        select(InventoryItem.id, InventoryItem.sku, InventoryItem.name, InventoryItem.on_hand,
               InventoryItem.reserved_qty, InventoryItem.reorder_point)
    ).all()
    order_lines = db.execute(
        select(OrderItem.inventory_item_id, OrderItem.ordered_quantity, OrderItem.received_quantity,
               PurchaseOrder.id, PurchaseOrder.status, PurchaseOrder.mock_order,
               PurchaseOrder.mock_in_transit_at)
        .join(PurchaseOrder, PurchaseOrder.id == OrderItem.purchase_order_id)
        .where(PurchaseOrder.status.in_(ACTIVE_ON_ORDER_STATUSES), OrderItem.is_open.is_(True))
    ).all()
    snapshot = build_stock_snapshot(items, order_lines)
    db.rollback()  # Release the read transaction before the external call.
    try:
        return explain_inventory(snapshot)
    except AgentUnavailable as exc:
        raise HTTPException(status_code=503, detail={"ai_status": "UNAVAILABLE", "message": str(exc)}) from exc
    except AgentInvalidResponse as exc:
        raise HTTPException(status_code=409, detail={"ai_status": "INVALID_INSIGHT", "message": str(exc)}) from exc


@router.get("/{item_id}", response_model=ItemResponse)
def get_item(
    item_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_roles("OWNER", "EMPLOYEE"))],
):
    return item_or_404(db, item_id)


@router.post("", response_model=ItemResponse, status_code=status.HTTP_201_CREATED)
def create_item(
    data: ItemCreate,
    db: Annotated[Session, Depends(get_db)],
    _owner: Annotated[User, Depends(require_roles("OWNER"))],
):
    # All inventory quantities and forecasting fields use safe DB defaults.
    # No stock additions through this endpoint.
    return commit_item(db, InventoryItem(**data.model_dump()))


@router.patch("/{item_id}", response_model=ItemResponse)
def update_item(
    item_id: int,
    data: ItemUpdate,
    db: Annotated[Session, Depends(get_db)],
    _owner: Annotated[User, Depends(require_roles("OWNER"))],
):
    item = item_or_404(db, item_id)
    updates = data.model_dump(exclude_unset=True)
    if any(value is None for key, value in updates.items() if key != "description"):
        raise HTTPException(status_code=422, detail="Required item fields cannot be null")
    for key, value in updates.items():
        setattr(item, key, value)
    return commit_item(db, item)


@router.get("/{item_id}/forecast", tags=["Inventory Forecast"])
def inventory_forecast(
    item_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_roles("OWNER", "EMPLOYEE"))],
):
    """Read-only live projection; only checkout/receipt persist forecast logs."""
    item = item_or_404(db, item_id)
    return calculate_forecast(db, item)
