"""Opt-in Owner-only Busy Week demo: reuse the real checkout service, never fake stock.

A caller-supplied run ID is persisted indirectly through each checkout's unique key.
Retries are safe even if the network fails between individual commits.
"""
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import require_roles
from app.api.checkout import CheckoutRequest, checkout
from app.core.config import settings
from app.db.dependencies import get_db
from app.db.models import InventoryItem, PurchaseOrder, User, OPEN_ORDER_STATUSES

router = APIRouter(prefix='/api/demo', tags=['Owner Demo'])

# These are actual inventory SKUs in the approved ShelfSync demonstration.
SCENARIO = (
    ('HW-SCR-15', 8, 'Issued 8 boxes of Gypsum Screws and 4 coils of 2.5mm Copper Wire for site renovation work.'),
    ('EL-WIR-25', 4, 'Issued 8 boxes of Gypsum Screws and 4 coils of 2.5mm Copper Wire for site renovation work.'),
    ('PL-TEF-01', 2, 'Issued Teflon Tape for site renovation plumbing work.'),
)

class BusyWeekRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    run_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,40}$')


@router.post('/simulate-busy-week')
def simulate_busy_week(
    data: BusyWeekRequest,
    db: Annotated[Session, Depends(get_db)],
    owner: Annotated[User, Depends(require_roles('OWNER'))],
    background_tasks: BackgroundTasks = None,
):
    if not settings.enable_busy_week_simulation:
        raise HTTPException(status_code=403, detail='Busy Week demo is disabled')
    if settings.email_mode.lower() != 'mock' or settings.enable_real_email_dispatch:
        raise HTTPException(status_code=409, detail='Busy Week requires mock-only email configuration')

    # Verify the complete scenario before the first write, including mappings.
    items = {}
    for sku, _, _ in SCENARIO:
        item = db.scalar(select(InventoryItem).where(InventoryItem.sku == sku))
        if item is None:
            raise HTTPException(status_code=409, detail=f'Demo SKU {sku} is missing; seed the demo inventory first')
        items[sku] = item

    outcomes = []
    for index, (sku, qty, note) in enumerate(SCENARIO):
        key = f'busyweek-{owner.id}-{data.run_id}-{index}'
        # Calling the exact production checkout function retains row-locking,
        # stock predicates, forecasting, replenishment, and unique-key replay.
        try:
            result = checkout(CheckoutRequest(inventory_item_id=items[sku].id, quantity=qty,
                                              usage_note=note, idempotency_key=key), db, owner, background_tasks)
        except HTTPException as exc:
            # Earlier successful steps remain committed; retries replay them.
            raise HTTPException(status_code=409, detail={
                'message': 'Demo stopped; retry with the SAME run_id after resolving the issue',
                'failed_sku': sku, 'reason': exc.detail,
                'completed': outcomes,
            }) from exc
        outcomes.append({'sku': sku, 'quantity': qty, 'transaction_id': result.transaction.id,
                         'replayed': result.replayed, 'on_hand': result.on_hand})

    orders = db.scalars(select(PurchaseOrder).where(
        PurchaseOrder.inventory_item_id.in_([item.id for item in items.values()]),
        PurchaseOrder.status.in_(OPEN_ORDER_STATUSES),
    )).all()
    return {'run_id': data.run_id, 'checkouts': outcomes,
            'open_purchase_requests': [{'id': o.id, 'inventory_item_id': o.inventory_item_id,
                                        'status': o.status} for o in orders],
            'emails_sent': False, 'orders_authorized_by_demo': False,
            'next_action': 'Review the purchase requests in Purchasing.'}
