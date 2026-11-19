"""Mock checkout-to-purchase automation and Owner-controlled shipment simulation."""
import logging
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.claude_agent import recommend
from app.core.config import settings
from app.core.order_decisions import DecisionError, authorize_order, decide_owner, validate_quote
from app.core.order_email_dispatch import record_workflow_mock_dispatch
from app.core.quotations import utcnow
from app.core.sample_purchase_quotes import prepare_catalogue_quotes
from app.core.sample_vendor_catalogue import is_sample_vendor
from app.core.stock_receipts import record_receipt
from app.db.models import PurchaseOrder, PurchaseOrderEmailDispatch, Vendor


MOCK_APPROVAL_LIMIT = Decimal("100000")
logger = logging.getLogger(__name__)


def enabled() -> bool:
    return (
        settings.enable_mock_purchasing
        and settings.enable_quote_simulation
        and settings.email_mode.strip().lower() == "mock"
        and not settings.enable_real_email_dispatch
    )


def require_mock_mode() -> None:
    if not enabled():
        raise DecisionError("Mock purchasing requires mock-only email with purchasing and quote simulation enabled")


def locked_order(db: Session, order_id: int) -> PurchaseOrder:
    order = db.scalar(select(PurchaseOrder).where(PurchaseOrder.id == order_id).with_for_update())
    if order is None:
        raise DecisionError("Purchase order not found")
    return order


def process_order(db: Session, order_id: int) -> dict:
    """Get an advisory AI pick, then revalidate it and apply the mock threshold."""
    require_mock_mode()
    order = locked_order(db, order_id)
    if order.status != "WAITING_FOR_QUOTES":
        return {"order_id": order.id, "status": order.status}
    prepare_catalogue_quotes(db, order)
    db.commit()

    recommendation, comparison = recommend(db, order)
    db.rollback()
    require_mock_mode()
    order = locked_order(db, order_id)
    if order.status != "WAITING_FOR_QUOTES":
        return {"order_id": order.id, "status": order.status}
    quote, entry = validate_quote(db, order, recommendation.recommended_quotation_id)
    if not quote.is_simulated or not is_sample_vendor(db.get(Vendor, quote.vendor_id)):
        raise DecisionError("Mock purchasing requires a simulated quotation")
    before = next((row for row in comparison["quotations"] if row["quotation_id"] == quote.id), None)
    fields = ("required_quantity", "quoted_unit_price", "quoted_delivery_days", "total_quoted_cost")
    if before is None or any(before[key] != entry[key] for key in fields):
        raise DecisionError("Quotation terms changed during AI comparison; retry the request")
    if entry["total_quoted_cost"] > Decimal("999999999999.99"):
        raise DecisionError("Order total exceeds the supported currency range")

    order.ai_recommended_quotation_id = quote.id
    order.ai_recommendation_reason = recommendation.reason
    order.backend_candidate_quotation_id = quote.id
    if entry["total_quoted_cost"] > MOCK_APPROVAL_LIMIT:
        order.status = "PENDING_APPROVAL"
        order.total_amount = entry["total_quoted_cost"]
        order.approval_reason = "ORDER_EXCEEDS_MOCK_APPROVAL_LIMIT"
    else:
        authorize_order(db, order, quote, entry)
        record_workflow_mock_dispatch(db, order)
    db.commit()
    return {"order_id": order.id, "status": order.status}


def process_in_background(bind, order_id: int) -> None:
    with Session(bind, autoflush=False, expire_on_commit=False) as db:
        try:
            process_order(db, order_id)
        except Exception as exc:
            db.rollback()
            # The request is already committed. The Owner can retry processing it.
            logger.warning("Mock purchase %s needs a retry (%s)", order_id, type(exc).__name__)


def decide_mock_order(db: Session, order: PurchaseOrder, owner_id: int, action: str,
                      quotation_id: int | None, comments: str | None) -> dict:
    require_mock_mode()
    if quotation_id is not None and quotation_id != order.backend_candidate_quotation_id:
        raise DecisionError("Mock approvals apply to the AI-selected quotation")
    if action == "APPROVED" and order.status == "PENDING_APPROVAL":
        quote, entry = validate_quote(db, order, order.backend_candidate_quotation_id)
        if not quote.is_simulated or entry["total_quoted_cost"] != order.total_amount:
            raise DecisionError("The simulated quotation no longer matches the amount awaiting approval")
    result = decide_owner(db, order, owner_id, action, quotation_id, comments)
    if action == "APPROVED":
        record_workflow_mock_dispatch(db, order)
        result["dispatch_status"] = "MOCKED"
    return result


def simulate_transit(db: Session, order_id: int) -> dict:
    order = locked_order(db, order_id)
    if order.status not in ("PLACED", "PARTIALLY_RECEIVED") or order.mock_delivered_at is not None:
        raise DecisionError("Only an undelivered placed order can enter transit")
    if order.vendor_id is None or order.placed_at is None:
        raise DecisionError("Order must have an authorized vendor and placement date")
    if order.mock_order:
        audit = db.scalar(select(PurchaseOrderEmailDispatch.id).where(
            PurchaseOrderEmailDispatch.purchase_order_id == order_id,
            PurchaseOrderEmailDispatch.mode == "mock",
            PurchaseOrderEmailDispatch.status == "MOCKED",
        ))
        if audit is None:
            raise DecisionError("Mock dispatch must be recorded before transit")
    replayed = order.mock_in_transit_at is not None or order.status == "PARTIALLY_RECEIVED"
    if not replayed:
        order.mock_in_transit_at = utcnow()
    db.commit()
    return {"order_id": order_id, "status": "IN_TRANSIT", "replayed": replayed}


def complete_delivery(db: Session, order_id: int, owner_id: int) -> dict:
    return record_receipt(db, order_id=order_id, quantity=None,
                          idempotency_key=f"delivery-{uuid4().hex}", owner_id=owner_id)
