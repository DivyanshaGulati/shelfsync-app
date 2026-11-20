"""Purchase request lifecycle endpoints and Owner-authorized purchasing actions.

Includes quotation collection, vendor comparison, authorization and receiving.
"""
from base64 import b64encode
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, aliased, sessionmaker

from app.api.auth import require_roles
from app.core import mock_purchasing
from app.core.sample_vendor_catalogue import is_sample_vendor
from app.core.vendor_bills import BillDetails, render_bill, verify_bill
from app.db.dependencies import get_db
from app.db.models import OrderItem, PurchaseOrder, User, Vendor, VendorBill, VendorQuotation

router = APIRouter(prefix="/api/purchase-requests", tags=["Purchase Requests"])


class RequestSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    inventory_item_id: int
    created_by: int | None
    status: str
    vendor_id: int | None
    total_amount: Decimal | None
    ordered_quantity: int
    received_quantity: int
    created_at: datetime
    mock_order: bool = False
    ai_recommendation_reason: str | None = None
    vendor_name: str | None = None
    unit_price: Decimal | None = None
    catalogue_purchase: bool = False
    tracking_status: str | None = None
    expected_delivery_days: Decimal | None = None


class RequestList(BaseModel):
    items: list[RequestSummary]
    total: int
    limit: int
    offset: int


@router.get("", response_model=RequestList)
def list_purchase_requests(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_roles("OWNER"))],
    status: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    display_status = case(
        ((PurchaseOrder.status == "PLACED") & PurchaseOrder.mock_order
         & PurchaseOrder.mock_in_transit_at.is_not(None), "IN_TRANSIT"),
        else_=PurchaseOrder.status,
    )
    tracking_status = case(
        (PurchaseOrder.status == "RECEIVED", "RECEIVED"),
        (PurchaseOrder.status == "PARTIALLY_RECEIVED", "IN_TRANSIT"),
        ((PurchaseOrder.status == "PLACED")
         & (PurchaseOrder.mock_in_transit_at.is_not(None) | PurchaseOrder.mock_delivered_at.is_not(None)), "IN_TRANSIT"),
        (PurchaseOrder.status == "PLACED", "PLACED"),
    )
    selected_quote = aliased(VendorQuotation)
    where = [display_status == status] if status else []
    total = db.scalar(select(func.count()).select_from(PurchaseOrder).where(*where)) or 0
    records = db.execute(
        select(PurchaseOrder, OrderItem, display_status.label("display_status"), Vendor,
               VendorQuotation.quoted_unit_price, tracking_status.label("tracking_status"),
               func.coalesce(selected_quote.quoted_lead_time_days, VendorQuotation.quoted_lead_time_days))
        .join(OrderItem, OrderItem.purchase_order_id == PurchaseOrder.id)
        .outerjoin(VendorQuotation, VendorQuotation.id == PurchaseOrder.backend_candidate_quotation_id)
        .outerjoin(selected_quote, (selected_quote.purchase_order_id == PurchaseOrder.id)
                   & (selected_quote.vendor_id == PurchaseOrder.vendor_id) & (selected_quote.status == "SELECTED"))
        .outerjoin(Vendor, Vendor.id == func.coalesce(PurchaseOrder.vendor_id, VendorQuotation.vendor_id))
        .where(*where)
        .order_by(PurchaseOrder.created_at.desc(), PurchaseOrder.id.desc())
        .limit(limit).offset(offset)
    ).all()
    return RequestList(
        items=[RequestSummary(
            id=order.id, inventory_item_id=order.inventory_item_id,
            created_by=order.created_by, status=state,
            vendor_id=order.vendor_id, total_amount=order.total_amount,
            ordered_quantity=line.ordered_quantity,
            received_quantity=line.received_quantity,
            created_at=order.created_at,
            mock_order=order.mock_order,
            ai_recommendation_reason=order.ai_recommendation_reason,
            vendor_name=vendor.name if vendor else None,
            unit_price=line.unit_price if line.unit_price is not None else candidate_price,
            catalogue_purchase=order.mock_order and is_sample_vendor(vendor),
            tracking_status=tracking,
            expected_delivery_days=delivery_days,
        ) for order, line, state, vendor, candidate_price, tracking, delivery_days in records],
        total=total, limit=limit, offset=offset,
    )


# Quotation preparation and owner-verified responses.
# No SMTP/email is attempted; the requests are not represented as delivered.
from fastapi import HTTPException, status
from pydantic import Field
from app.core.quotations import (prepare_vendor_quotations, collection_expired,
                                 expire_unanswered, utcnow)
from app.db.models import QuotationEmailDispatch


class QuotationSummary(BaseModel):
    id: int
    vendor_id: int
    vendor_name: str
    vendor_email: str
    status: str
    requested_at: datetime
    responded_at: datetime | None
    quoted_unit_price: Decimal | None
    quoted_lead_time_days: Decimal | None
    is_simulated: bool
    dispatch_status: str = "NOT_SENT_EMAIL_INTEGRATION_PENDING"


class QuotationCollection(BaseModel):
    purchase_order_id: int
    inventory_item_id: int
    order_status: str
    required_quantity: int
    quote_deadline_at: datetime | None
    collection_state: str
    requested_count: int
    received_count: int
    expired_count: int
    quotations: list[QuotationSummary]


def make_collection(db: Session, order: PurchaseOrder) -> QuotationCollection:
    quotes = db.execute(
        select(VendorQuotation, Vendor)
        .join(Vendor, Vendor.id == VendorQuotation.vendor_id)
        .where(VendorQuotation.purchase_order_id == order.id)
        .order_by(Vendor.name)
    ).all()
    line = db.scalar(select(OrderItem).where(OrderItem.purchase_order_id == order.id))
    if line is None:
        raise HTTPException(status_code=409, detail="Order line not found")
    dispatches = {d.quotation_id: d.status for d in db.scalars(select(QuotationEmailDispatch).where(
        QuotationEmailDispatch.quotation_id.in_([q.id for q, _ in quotes])))} if quotes else {}
    pending = sum(q.status == "REQUESTED" for q, v in quotes)
    received = sum(q.status == "RECEIVED" for q, v in quotes)
    expired = sum(q.status == "EXPIRED" for q, v in quotes)
    if not quotes:
        state = "NO_ACTIVE_MAPPED_VENDORS"
    elif pending and collection_expired(order):
        state = "WINDOW_EXPIRED_PENDING_CLOSE"
    elif pending:
        state = "COLLECTING"
    else:
        state = "READY_FOR_COMPARISON" if received else "NO_VALID_QUOTES"
    return QuotationCollection(
        purchase_order_id=order.id, inventory_item_id=order.inventory_item_id,
        order_status=order.status, required_quantity=line.ordered_quantity,
        quote_deadline_at=order.quote_deadline_at, collection_state=state,
        requested_count=pending, received_count=received, expired_count=expired,
        quotations=[QuotationSummary(
            id=q.id, vendor_id=q.vendor_id, vendor_name=v.name, vendor_email=v.email,
            status=q.status, requested_at=q.requested_at, responded_at=q.responded_at,
            quoted_unit_price=q.quoted_unit_price, quoted_lead_time_days=q.quoted_lead_time_days,
            is_simulated=q.is_simulated,
            dispatch_status=dispatches.get(q.id, "NOT_SENT_EMAIL_INTEGRATION_PENDING"),
        ) for q, v in quotes],
    )


def get_waiting_order(db: Session, order_id: int, *, lock: bool = False) -> PurchaseOrder:
    query = select(PurchaseOrder).where(PurchaseOrder.id == order_id)
    if lock:
        query = query.with_for_update()
    order = db.scalar(query)
    if order is None:
        raise HTTPException(status_code=404, detail="Purchase request not found")
    if order.status != "WAITING_FOR_QUOTES":
        raise HTTPException(status_code=409, detail="Purchase request is not waiting for quotations")
    return order


@router.get("/{order_id}/quotations", response_model=QuotationCollection)
def list_quotations(
    order_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_roles("OWNER"))],
):
    order = db.get(PurchaseOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Purchase request not found")
    return make_collection(db, order)


@router.post("/{order_id}/request-quotations", response_model=QuotationCollection)
def request_quotations(
    order_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_roles("OWNER"))],
):
    """Retry preparation for existing unstarted request (e.g. vendors newly configured).

    This never sends messages or restarts an existing collection window.
    """
    try:
        order = get_waiting_order(db, order_id, lock=True)
        prepare_vendor_quotations(db, order)
        db.commit()
        return make_collection(db, order)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


class VerifiedQuoteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quoted_unit_price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    quoted_lead_time_days: Decimal = Field(ge=0, max_digits=10, decimal_places=3)


@router.post("/{order_id}/quotations/{quotation_id}/response", response_model=QuotationCollection)
def record_verified_response(
    order_id: int,
    quotation_id: int,
    payload: VerifiedQuoteResponse,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_roles("OWNER"))],
):
    """Owner manually records a verified supplier reply, never a simulated quote.

    Supplier replies are entered manually; simulated quotations use separate demo endpoints.
    """
    order = get_waiting_order(db, order_id, lock=True)
    quote = db.scalar(select(VendorQuotation).where(
        VendorQuotation.id == quotation_id,
        VendorQuotation.purchase_order_id == order.id,
    ).with_for_update())
    if quote is None:
        raise HTTPException(status_code=404, detail="Quotation not found for purchase request")
    if quote.status != "REQUESTED":
        raise HTTPException(status_code=409, detail="Only REQUESTED quotations can receive a response")
    if collection_expired(order):
        raise HTTPException(status_code=409, detail="Quotation window has expired")
    quote.quoted_unit_price = payload.quoted_unit_price
    quote.quoted_lead_time_days = payload.quoted_lead_time_days
    quote.responded_at = utcnow()
    quote.status = "RECEIVED"
    quote.is_simulated = False
    db.commit()
    return make_collection(db, order)


@router.post("/{order_id}/close-quotations", response_model=QuotationCollection)
def close_quotations(
    order_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_roles("OWNER"))],
):
    """Expire unanswered quotes after deadline, or close early once all replied."""
    order = get_waiting_order(db, order_id, lock=True)
    try:
        expire_unanswered(db, order)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return make_collection(db, order)

# Deterministic, read-only comparison of fresh received quotes.
from app.core.quote_comparison import compare_quotations


class VendorPerformanceSummary(BaseModel):
    completed_orders_with_delivery_data: int
    on_time_deliveries: int
    on_time_rate_percent: Decimal | None


class QuoteComparisonEntry(BaseModel):
    quotation_id: int
    vendor_id: int
    vendor_name: str | None
    is_active: bool
    is_new_vendor: bool | None
    is_slow: bool | None
    is_preferred: bool
    minimum_order_quantity: int | None
    required_quantity: int
    available_quantity: int
    last_known_unit_price: Decimal | None
    quoted_unit_price: Decimal | None
    price_change_percent: Decimal | None
    quoted_delivery_days: Decimal | None
    total_quoted_cost: Decimal | None
    is_simulated: bool
    reliability: VendorPerformanceSummary
    eligible_for_comparison: bool
    ineligibility_reasons: list[str]


class QuoteComparisonResponse(BaseModel):
    purchase_order_id: int
    required_quantity: int
    currency: str
    comparison_state: str
    eligible_count: int
    received_count: int
    quotations: list[QuoteComparisonEntry]
    lowest_cost_quotation_id: int | None
    fastest_quotation_id: int | None
    note: str


@router.get('/{order_id}/compare-quotations', response_model=QuoteComparisonResponse)
def compare_received_quotations(
    order_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_roles('OWNER'))],
):
    order = db.get(PurchaseOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail='Purchase request not found')
    try:
        return compare_quotations(db, order)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

# Strict backend rules and audited owner decisions.
from app.core.order_decisions import DecisionError, evaluate, decide_owner


class EvaluatePayload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    quotation_id: int | None = Field(default=None, gt=0)


class OwnerDecisionPayload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: str
    quotation_id: int | None = Field(default=None, gt=0)
    comments: str | None = Field(default=None, max_length=2000)


@router.post('/{order_id}/evaluate')
def evaluate_quote_and_rules(
    order_id: int, payload: EvaluatePayload,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_roles('OWNER'))],
):
    # Lock the order so concurrent evaluations cannot authorize it twice.
    order = db.scalar(select(PurchaseOrder).where(PurchaseOrder.id == order_id).with_for_update())
    if order is None:
        raise HTTPException(status_code=404, detail='Purchase request not found')
    try:
        if mock_purchasing.enabled() or order.mock_order:
            raise DecisionError('Use automatic AI ordering in the mock purchasing workflow')
        result = evaluate(db, order, payload.quotation_id)
        db.commit()
        return result
    except (ValueError, DecisionError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post('/{order_id}/owner-decision')
def owner_decision(
    order_id: int, payload: OwnerDecisionPayload,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_roles('OWNER'))],
):
    order = db.scalar(select(PurchaseOrder).where(PurchaseOrder.id == order_id).with_for_update())
    if order is None:
        raise HTTPException(status_code=404, detail='Purchase request not found')
    try:
        decision = mock_purchasing.decide_mock_order if order.mock_order else decide_owner
        result = decision(db, order, user.id, payload.action, payload.quotation_id, payload.comments)
        db.commit()
        return result
    except (ValueError, DecisionError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get('/{order_id}/approval-history')
def approval_history(
    order_id: int, db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_roles('OWNER'))],
):
    if db.get(PurchaseOrder, order_id) is None:
        raise HTTPException(status_code=404, detail='Purchase request not found')
    from app.db.models import ApprovalLog
    records = db.scalars(select(ApprovalLog).where(
        ApprovalLog.purchase_order_id == order_id).order_by(ApprovalLog.id)).all()
    return {'order_id': order_id, 'approvals': [
        {'id': log.id, 'owner_id': log.owner_id, 'action': log.action,
         'comments': log.comments, 'created_at': log.created_at}
        for log in records]}

# Optional, advisory Claude recommendation. Never authorizes PO.
from app.core.claude_agent import recommend, AgentUnavailable, AgentInvalidResponse


@router.get('/mock-workflow')
def mock_workflow_config(user: Annotated[User, Depends(require_roles('OWNER'))]):
    return {'enabled': mock_purchasing.enabled(), 'approval_limit': mock_purchasing.MOCK_APPROVAL_LIMIT}


@router.get('/{order_id}/mock-comparison', response_model=QuoteComparisonResponse)
def mock_quote_comparison(
    order_id: int, db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_roles('OWNER'))],
):
    order = db.get(PurchaseOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail='Purchase request not found')
    if not order.mock_order:
        raise HTTPException(status_code=409, detail='Mock quotations are not available for this order')
    try:
        return compare_quotations(db, order, include_processed=True)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post('/{order_id}/mock-process')
def retry_mock_purchase(
    order_id: int, db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_roles('OWNER'))],
):
    try:
        return mock_purchasing.process_order(db, order_id)
    except AgentUnavailable as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (AgentInvalidResponse, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post('/{order_id}/mock-transit')
def mark_mock_transit(
    order_id: int, db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_roles('OWNER'))],
):
    try:
        return mock_purchasing.simulate_transit(db, order_id)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post('/{order_id}/mock-delivery')
def record_mock_delivery(
    order_id: int, db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_roles('OWNER'))],
):
    try:
        return mock_purchasing.complete_delivery(db, order_id, user.id)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post('/{order_id}/mock-bill')
def preview_mock_bill(
    order_id: int,
    response: Response,
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_roles('OWNER'))],
):
    order = db.scalar(select(PurchaseOrder).where(PurchaseOrder.id == order_id).with_for_update())
    if order is None:
        raise HTTPException(status_code=404, detail='Purchase request not found')
    if not order.mock_order or order.status != 'RECEIVED':
        raise HTTPException(status_code=409, detail='The bill is available after full delivery of a mock order.')

    bill = db.get(VendorBill, order_id)
    if bill is not None:
        image = b64encode(bill.image).decode('ascii')
    else:
        completed = order.completed_at or order.mock_delivered_at
        lines = order.order_items
        if (completed is None or order.vendor is None or order.total_amount is None
                or len(lines) != 1 or lines[0].unit_price is None
                or lines[0].received_quantity != lines[0].ordered_quantity
                or lines[0].unit_price * lines[0].ordered_quantity != order.total_amount):
            raise HTTPException(status_code=409, detail='This order does not have complete billing details.')
        if completed.tzinfo is None:
            completed = completed.replace(tzinfo=timezone.utc)
        details = BillDetails(
            invoice_number=f'INV-{order.id:06d}',
            invoice_date=completed.astimezone(timezone(timedelta(hours=5, minutes=30))).date(),
            total=order.total_amount,
        )
        png = render_bill(order, details)
        image = b64encode(png).decode('ascii')
        db.add(VendorBill(purchase_order_id=order_id, image=png, check_status='PENDING'))
        db.commit()
        background_tasks.add_task(verify_bill, sessionmaker(bind=db.get_bind()), order_id, image, details)
    response.headers['Cache-Control'] = 'no-store'
    return {'image': image}


@router.post('/{order_id}/ai-recommendation')
def create_ai_recommendation(
    order_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_roles('OWNER'))],
):
    order = db.get(PurchaseOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail='Purchase request not found')
    if order.status != 'WAITING_FOR_QUOTES':
        raise HTTPException(status_code=409, detail='AI quotation recommendations must be generated before evaluation')
    try:
        suggested, _ = recommend(db, order)
    except AgentUnavailable as exc:
        raise HTTPException(status_code=503, detail={'ai_status': 'UNAVAILABLE', 'message': str(exc)}) from exc
    except AgentInvalidResponse as exc:
        raise HTTPException(status_code=409, detail={'ai_status': 'INVALID_RECOMMENDATION', 'message': str(exc)}) from exc

    # Recheck under row lock immediately before writing. Nothing from Claude is
    # used as a purchasing instruction, and no buyer-facing price is updated.
    db.rollback()
    locked = db.scalar(select(PurchaseOrder).where(PurchaseOrder.id == order_id).with_for_update())
    if locked is None or locked.status != 'WAITING_FOR_QUOTES':
        db.rollback()
        raise HTTPException(status_code=409, detail='Purchase request status changed during AI call')
    try:
        latest = compare_quotations(db, locked)
        allowed = {e['quotation_id'] for e in latest['quotations'] if e['eligible_for_comparison']}
        if suggested.recommended_quotation_id not in allowed:
            raise ValueError('Recommended quotation is no longer eligible')
        locked.ai_recommended_quotation_id = suggested.recommended_quotation_id
        locked.ai_recommendation_reason = suggested.reason
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {'ai_status': 'AVAILABLE', 'purchase_order_id': order_id,
            'recommended_quotation_id': suggested.recommended_quotation_id,
            'reason': suggested.reason, 'comparison_summary': [entry.model_dump() for entry in suggested.comparison_summary],
            'authorization_status': 'NOT_AUTHORIZED_BY_AI', 'dispatch_status': 'NOT_SENT'}


@router.get('/{order_id}/ai-recommendation')
def get_ai_recommendation(
    order_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_roles('OWNER'))],
):
    order = db.get(PurchaseOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail='Purchase request not found')
    return {'purchase_order_id': order_id, 'recommended_quotation_id': order.ai_recommended_quotation_id,
            'reason': order.ai_recommendation_reason,
            'ai_status': 'AVAILABLE' if order.ai_recommended_quotation_id else 'NOT_GENERATED',
            'authorization_status': 'NOT_AUTHORIZED_BY_AI'}

# Safe, read-only vendor email previews.
# No SMTP dispatch, quote creation, financial mutation or AI purchase authorization.
from app.core.vendor_drafts import (DraftError, quotation_request_context,
                                    order_email_context, make_quote_drafts, make_order_draft)
from app.core.claude_agent import optional_provider_draft_note


class DraftOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')
    use_ai: bool = False


@router.post('/{order_id}/draft-quotation-requests')
def preview_vendor_quotation_requests(
    order_id: int, payload: DraftOptions,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_roles('OWNER'))],
):
    order = db.get(PurchaseOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail='Purchase request not found')
    try:
        ctx = quotation_request_context(db, order)
        note = optional_provider_draft_note(db, order, 'request_vendor_quotations') if payload.use_ai else None
        # Re-read validated context after a possibly long AI network call.
        db.expire_all()
        ctx = quotation_request_context(db, db.get(PurchaseOrder, order_id))
    except DraftError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AgentUnavailable as exc:
        raise HTTPException(status_code=503, detail={'ai_status':'UNAVAILABLE','message':str(exc)}) from exc
    except AgentInvalidResponse as exc:
        raise HTTPException(status_code=409, detail={'ai_status':'INVALID_DRAFT','message':str(exc)}) from exc
    finally:
        db.rollback()  # no persistent changes, even after Claude tool calls
    return {'purchase_order_id':order_id, 'drafts':make_quote_drafts(ctx, note),
            'ai_status':'AVAILABLE' if payload.use_ai else 'NOT_REQUESTED',
            'dispatch_status':'NOT_SENT_PREVIEW_ONLY'}


@router.post('/{order_id}/draft-order-email')
def preview_authorized_order_email(
    order_id: int, payload: DraftOptions,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_roles('OWNER'))],
):
    order = db.get(PurchaseOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail='Purchase request not found')
    try:
        ctx = order_email_context(db, order)
        note = optional_provider_draft_note(db, order, 'draft_vendor_order_email') if payload.use_ai else None
        db.expire_all()
        ctx = order_email_context(db, db.get(PurchaseOrder, order_id))
    except DraftError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AgentUnavailable as exc:
        raise HTTPException(status_code=503, detail={'ai_status':'UNAVAILABLE','message':str(exc)}) from exc
    except AgentInvalidResponse as exc:
        raise HTTPException(status_code=409, detail={'ai_status':'INVALID_DRAFT','message':str(exc)}) from exc
    finally:
        db.rollback()
    return {'purchase_order_id':order_id, 'draft':make_order_draft(ctx, note),
            'ai_status':'AVAILABLE' if payload.use_ai else 'NOT_REQUESTED',
            'dispatch_status':'NOT_SENT_PREVIEW_ONLY'}


# Explicit RFQ dispatch. No AI involvement or purchase-order sending.
from app.core.email_service import EmailDispatchError, dispatch_quotation_emails

@router.post('/{order_id}/dispatch-quotation-requests')
def dispatch_vendor_quotation_requests(
    order_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_roles('OWNER'))],
):
    try:
        return dispatch_quotation_emails(db, order_id)
    except EmailDispatchError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# OWNER-only demo quotations. No AI, SMTP, or purchasing writes.
from app.core.quote_simulation import SimulationError, simulate_pending_quotations


@router.post('/{order_id}/simulate-vendor-quote-replies')
def simulate_vendor_quote_replies(
    order_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_roles('OWNER'))],
):
    order = get_waiting_order(db, order_id, lock=True)
    try:
        generated = simulate_pending_quotations(db, order)
        # Compare before committing: if the read-only comparison fails, the
        # simulated quote writes roll back together. No PO status is changed.
        result = compare_quotations(db, order)
        db.commit()
        return {
            'purchase_order_id': order_id,
            'new_simulated_replies': generated,
            'simulation': True,
            'collection': make_collection(db, order),
            'comparison': result,
            'purchase_order_authorized': False,
            'email_sent': False,
        }
    except (SimulationError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# Explicit OWNER-only final PO dispatch. No Claude/Groq tools
# can call this endpoint; all final prices are read from a selected fresh quote.
from app.core.order_email_dispatch import (
    PurchaseOrderDispatchError, dispatch_authorized_order, get_dispatch_status,
)


@router.post('/{order_id}/dispatch-order-email')
def dispatch_purchase_order_email(
    order_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_roles('OWNER'))],
):
    try:
        return dispatch_authorized_order(db, order_id)
    except PurchaseOrderDispatchError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get('/{order_id}/order-email-dispatch')
def order_email_dispatch_status(
    order_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_roles('OWNER'))],
):
    try:
        return get_dispatch_status(db, order_id)
    except PurchaseOrderDispatchError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

# Confirmed physical receipts; only OWNER can record stock.
from pydantic import Field, field_validator
from app.core.stock_receipts import ReceiptError, record_receipt, list_receipts


class ReceiptRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    quantity: int = Field(gt=0)
    idempotency_key: str = Field(min_length=1, max_length=100)

    @field_validator('idempotency_key')
    @classmethod
    def valid_receipt_key(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError('idempotency_key must be nonblank and have no surrounding spaces')
        return value


@router.post('/{order_id}/receive-stock')
def receive_authorized_stock(
    order_id: int, payload: ReceiptRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_roles('OWNER'))],
):
    try:
        return record_receipt(db, order_id=order_id, quantity=payload.quantity,
                              idempotency_key=payload.idempotency_key, owner_id=user.id)
    except ReceiptError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get('/{order_id}/stock-receipts')
def get_order_stock_receipts(
    order_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_roles('OWNER'))],
):
    try:
        return list_receipts(db, order_id)
    except ReceiptError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
