"""Read inventory and manage descriptive item details.

Stock mutations are handled by the transactional checkout and receiving services.
"""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.auth import require_roles
from app.core.claude_agent import AgentInvalidResponse, AgentUnavailable, InventoryInsight, explain_inventory
from app.core.forecast import ACTIVE_ON_ORDER_STATUSES, calculate_forecast
from app.core.mock_purchasing import enabled as mock_purchasing_enabled, process_in_background
from app.core.replenishment import create_purchase_request
from app.core.stock_status import build_stock_snapshot
from app.db.dependencies import get_db
from app.db.models import InventoryInsightReport, InventoryItem, OrderItem, PurchaseOrder, User

router = APIRouter(prefix="/api/inventory", tags=["Inventory"])
InitialOrderQuantity = Annotated[int, Field(strict=True, gt=0, le=2_147_483_647)]


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
    initial_order_quantity: InitialOrderQuantity | None = None


class ItemUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    sku: str | None = Field(default=None, min_length=1, max_length=64)
    category: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=5000)
    unit: str | None = Field(default=None, min_length=1, max_length=40)
    unit_cost: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    initial_order_quantity: InitialOrderQuantity | None = None

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


def item_or_404(db: Session, item_id: int, *, lock: bool = False) -> InventoryItem:
    item = db.get(InventoryItem, item_id, with_for_update=lock)
    if item is None:
        raise HTTPException(status_code=404, detail="Inventory item not found")
    return item


def save_item(
    db: Session, item: InventoryItem, *, initial_order_quantity: int | None,
    owner_id: int, background_tasks: BackgroundTasks,
) -> InventoryItem:
    try:
        db.add(item)
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        # Database uniqueness constraint remains the final authority under concurrency.
        raise HTTPException(status_code=409, detail="An inventory item with this SKU already exists") from exc
    order_id = None
    if initial_order_quantity is not None:
        order = create_purchase_request(db, item, initial_order_quantity, created_by=owner_id)
        order_id = order.id
    db.commit()
    if order_id is not None and mock_purchasing_enabled():
        background_tasks.add_task(process_in_background, db.get_bind(), order_id)
    db.refresh(item)
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


class InsightPriorityItem(BaseModel):
    sku: str
    name: str
    on_hand: int
    reserved_qty: int
    available_stock: int
    reorder_point: Decimal
    on_order: int
    order_status: str | None


class DashboardInventoryInsight(InventoryInsight):
    inventory_items: int
    low_stock_items: int
    priority_item: InsightPriorityItem | None


class SavedInventoryInsight(DashboardInventoryInsight):
    id: int
    created_at: datetime


class InsightHistory(BaseModel):
    items: list[SavedInventoryInsight]
    next_before_id: int | None


class InsightStockOverview(BaseModel):
    inventory_items: int
    low_stock_items: int


def _stock_snapshot(db: Session) -> dict:
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
    return build_stock_snapshot(items, order_lines)


def _saved_insight(report: InventoryInsightReport) -> SavedInventoryInsight:
    # SQLite timestamps have no timezone.
    created_at = report.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return SavedInventoryInsight(**report.payload, id=report.id, created_at=created_at)


@router.get("/ai-insights/snapshot", response_model=InsightStockOverview)
def inventory_insight_stock_overview(
    db: Annotated[Session, Depends(get_db)],
    _owner: Annotated[User, Depends(require_roles("OWNER"))],
):
    return _stock_snapshot(db)


@router.get("/ai-insights", response_model=InsightHistory)
def inventory_insight_history(
    db: Annotated[Session, Depends(get_db)],
    owner: Annotated[User, Depends(require_roles("OWNER"))],
    before_id: Annotated[int | None, Query(gt=0)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 12,
):
    query = select(InventoryInsightReport).where(InventoryInsightReport.owner_id == owner.id)
    if before_id is not None:
        query = query.where(InventoryInsightReport.id < before_id)
    reports = db.scalars(query.order_by(InventoryInsightReport.id.desc()).limit(limit + 1)).all()
    return InsightHistory(
        items=[_saved_insight(report) for report in reports[:limit]],
        next_before_id=reports[limit - 1].id if len(reports) > limit else None,
    )


@router.post("/ai-insights", response_model=SavedInventoryInsight)
def inventory_ai_insights(
    db: Annotated[Session, Depends(get_db)],
    owner: Annotated[User, Depends(require_roles("OWNER"))],
):
    owner_id = owner.id
    snapshot = _stock_snapshot(db)
    captured_at = datetime.now(timezone.utc)
    db.rollback()  # Release the read transaction before the external call.
    try:
        insight = explain_inventory(snapshot)
    except AgentUnavailable as exc:
        raise HTTPException(status_code=503, detail={"ai_status": "UNAVAILABLE", "message": str(exc)}) from exc
    except AgentInvalidResponse as exc:
        raise HTTPException(status_code=409, detail={"ai_status": "INVALID_INSIGHT", "message": str(exc)}) from exc

    candidates = snapshot["low_stock_candidates"]
    priority = next(
        (item for item in candidates if item["sku"] == insight.priority_sku),
        candidates[0] if candidates else None,
    )
    result = DashboardInventoryInsight(
        summary=insight.summary,
        priority_sku=priority["sku"] if priority is not None else None,
        inventory_items=snapshot["inventory_items"],
        low_stock_items=snapshot["low_stock_items"],
        priority_item=InsightPriorityItem.model_validate(priority) if priority is not None else None,
    )
    report = InventoryInsightReport(
        owner_id=owner_id, created_at=captured_at, payload=result.model_dump(mode="json")
    )
    db.add(report)
    db.flush()
    response = _saved_insight(report)
    db.commit()
    return response


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
    owner: Annotated[User, Depends(require_roles("OWNER"))],
    background_tasks: BackgroundTasks,
):
    item = InventoryItem(**data.model_dump(exclude={"initial_order_quantity"}))
    return save_item(
        db, item, initial_order_quantity=data.initial_order_quantity,
        owner_id=owner.id, background_tasks=background_tasks,
    )


@router.patch("/{item_id}", response_model=ItemResponse)
def update_item(
    item_id: int,
    data: ItemUpdate,
    db: Annotated[Session, Depends(get_db)],
    owner: Annotated[User, Depends(require_roles("OWNER"))],
    background_tasks: BackgroundTasks,
):
    quantity = data.initial_order_quantity
    item = item_or_404(db, item_id, lock=quantity is not None)
    if quantity is not None:
        existing_order = db.scalar(
            select(PurchaseOrder.id).where(PurchaseOrder.inventory_item_id == item_id).limit(1)
        )
        if item.on_hand != 0 or existing_order is not None:
            raise HTTPException(
                status_code=409, detail="A first order requires zero on-hand stock and no previous purchase orders",
            )
    updates = data.model_dump(exclude_unset=True, exclude={"initial_order_quantity"})
    if any(value is None for key, value in updates.items() if key != "description"):
        raise HTTPException(status_code=422, detail="Required item fields cannot be null")
    for key, value in updates.items():
        setattr(item, key, value)
    return save_item(
        db, item, initial_order_quantity=quantity, owner_id=owner.id, background_tasks=background_tasks,
    )


@router.get("/{item_id}/forecast", tags=["Inventory Forecast"])
def inventory_forecast(
    item_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_roles("OWNER", "EMPLOYEE"))],
):
    """Read-only live projection; only checkout/receipt persist forecast logs."""
    item = item_or_404(db, item_id)
    return calculate_forecast(db, item)
