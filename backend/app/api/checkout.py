"""Atomic inventory checkout and auditable transaction history.

Stock deduction and transaction insertion commit together. PostgreSQL's conditional
UPDATE acquires a row lock and serializes concurrent deductions of the same item.
Forecast recalculation and replenishment-request creation are atomic with successful checkout.
"""
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.auth import require_roles
from app.db.dependencies import get_db
from app.db.models import InventoryItem, Transaction, User
from app.core.forecast import calculate_forecast
from app.core.replenishment import ensure_replenishment_request
from app.core.mock_purchasing import enabled as mock_purchasing_enabled, process_in_background

router = APIRouter(prefix="/api", tags=["Checkout & Transactions"])


class CheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inventory_item_id: int = Field(gt=0)
    quantity: int = Field(gt=0)
    idempotency_key: str = Field(min_length=1, max_length=100)
    usage_note: str | None = Field(default=None, max_length=5000)

    @field_validator("idempotency_key")
    @classmethod
    def valid_key(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("idempotency_key must be nonblank and have no surrounding spaces")
        return value


class TransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: int
    inventory_item_id: int
    transaction_type: str
    quantity: int
    usage_note: str | None
    idempotency_key: str
    created_at: datetime


class CheckoutResponse(BaseModel):
    transaction: TransactionResponse
    on_hand: int
    available_stock: int
    replayed: bool


class TransactionList(BaseModel):
    items: list[TransactionResponse]
    total: int
    limit: int
    offset: int


def note_normalized(note: str | None) -> str | None:
    # Canonical representation makes idempotent retries predictable.
    if note is None:
        return None
    return note.strip() or None


def matching_transaction(tx: Transaction, request: CheckoutRequest, user_id: int) -> bool:
    return (tx.user_id == user_id and tx.inventory_item_id == request.inventory_item_id
            and tx.transaction_type == "CHECKOUT" and tx.quantity == request.quantity
            and note_normalized(tx.usage_note) == note_normalized(request.usage_note))


def checkout_result(db: Session, tx: Transaction, replayed: bool) -> CheckoutResponse:
    """Current stock (not snapshot at original checkout) on idempotent replay."""
    item = db.get(InventoryItem, tx.inventory_item_id)
    return CheckoutResponse(
        transaction=TransactionResponse.model_validate(tx),
        on_hand=item.on_hand,
        available_stock=item.on_hand - item.reserved_qty,
        replayed=replayed,
    )


@router.post("/checkout", response_model=CheckoutResponse, status_code=status.HTTP_201_CREATED)
def checkout(
    data: CheckoutRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_roles("OWNER", "EMPLOYEE"))],
    background_tasks: BackgroundTasks = None,
):
    """Apply a single checkout once, even across concurrent retries.

    A single SQL UPDATE with an availability predicate protects against simultaneous
    checkout requests. A DB unique constraint on transactions.idempotency_key is the
    final idempotency authority. On conflicting concurrent requests the losing DB
    transaction is rolled back, including any stock decrement.
    """
    existing = db.scalar(select(Transaction).where(Transaction.idempotency_key == data.idempotency_key))
    if existing is not None:
        if not matching_transaction(existing, data, user.id):
            raise HTTPException(status_code=409, detail="Idempotency key already used for a different checkout")
        return checkout_result(db, existing, replayed=True)

    try:
        # This predicate AND the update occur atomically in PostgreSQL. An item
        # with reserved units can only check out its unreserved quantity.
        result = db.execute(
            update(InventoryItem)
            .where(InventoryItem.id == data.inventory_item_id)
            .where(InventoryItem.on_hand - InventoryItem.reserved_qty >= data.quantity)
            .values(on_hand=InventoryItem.on_hand - data.quantity)
            .returning(InventoryItem.id)
        ).scalar_one_or_none()
        if result is None:
            db.rollback()
            # A racing request with the same key may have just committed and
            # used the remaining available stock. Return that transaction.
            winner = db.scalar(select(Transaction).where(Transaction.idempotency_key == data.idempotency_key))
            if winner is not None:
                if not matching_transaction(winner, data, user.id):
                    raise HTTPException(status_code=409, detail="Idempotency key already used for a different checkout")
                return checkout_result(db, winner, replayed=True)
            if db.get(InventoryItem, data.inventory_item_id) is None:
                raise HTTPException(status_code=404, detail="Inventory item not found")
            raise HTTPException(status_code=409, detail="Insufficient available stock")

        tx = Transaction(
            user_id=user.id,
            inventory_item_id=data.inventory_item_id,
            transaction_type="CHECKOUT",
            quantity=data.quantity,
            usage_note=note_normalized(data.usage_note),
            idempotency_key=data.idempotency_key,
        )
        db.add(tx)
        db.flush()  # Give checkout a transaction ID and DB timestamp for the 30-day history.
        item = db.get(InventoryItem, data.inventory_item_id)
        forecast = calculate_forecast(db, item, persist=True)
        order = ensure_replenishment_request(db, item, forecast, created_by=user.id)
        order_to_process = order.id if order and order.status == "WAITING_FOR_QUOTES" else None
        db.commit()
    except IntegrityError as exc:
        # Includes concurrent use of the SAME idempotency key. Stock update is
        # automatically undone by the rollback; now inspect the winning tx.
        db.rollback()
        winner = db.scalar(select(Transaction).where(Transaction.idempotency_key == data.idempotency_key))
        if winner is not None:
            if not matching_transaction(winner, data, user.id):
                raise HTTPException(status_code=409, detail="Idempotency key already used for a different checkout") from exc
            return checkout_result(db, winner, replayed=True)
        raise HTTPException(status_code=409, detail="Checkout could not be completed") from exc

    if order_to_process is not None and background_tasks is not None and mock_purchasing_enabled():
        background_tasks.add_task(process_in_background, db.get_bind(), order_to_process)
    db.refresh(tx)
    return checkout_result(db, tx, replayed=False)


@router.get("/transactions", response_model=TransactionList)
def transactions(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_roles("OWNER", "EMPLOYEE"))],
    item_id: Annotated[int | None, Query(gt=0)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    filters = [Transaction.transaction_type == "CHECKOUT"]
    if user.role == "EMPLOYEE":
        filters.append(Transaction.user_id == user.id)
    if item_id is not None:
        filters.append(Transaction.inventory_item_id == item_id)
    total = db.scalar(select(func.count()).select_from(Transaction).where(*filters)) or 0
    records = db.scalars(select(Transaction).where(*filters).order_by(Transaction.created_at.desc(), Transaction.id.desc()).limit(limit).offset(offset)).all()
    return TransactionList(items=list(records), total=total, limit=limit, offset=offset)
