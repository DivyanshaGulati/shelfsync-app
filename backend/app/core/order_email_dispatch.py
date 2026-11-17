"""OWNER-triggered final purchase-order email delivery, mock-first.

Internal status PLACED is not proof that an SMTP email has been sent.
Both simulation and real dispatch use the selected fresh quote and canonical
backend-generated order draft. No LLM is involved in sending.
"""
from __future__ import annotations

import smtplib
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.email_service import preflight, send_smtp, EmailDispatchError
from app.core.vendor_drafts import DraftError, order_email_context, make_order_draft
from app.db.models import PurchaseOrder, PurchaseOrderEmailDispatch, VendorQuotation


class PurchaseOrderDispatchError(ValueError):
    pass


def _validated_draft(db: Session, order: PurchaseOrder) -> tuple[dict, bool]:
    # This checks PLACED, selected quotation, mapped active supplier, exact unit
    # and total price matches, quantity and minimum order quantity.
    try:
        context = order_email_context(db, order)
    except DraftError as exc:
        raise PurchaseOrderDispatchError(str(exc)) from exc
    quote = db.get(VendorQuotation, context['quotation_id'])
    if quote is None or quote.status != 'SELECTED' or quote.purchase_order_id != order.id:
        raise PurchaseOrderDispatchError('Selected fresh quotation is missing or invalid')
    return make_order_draft(context), quote.is_simulated


def record_workflow_mock_dispatch(db: Session, order: PurchaseOrder) -> None:
    """Record the mock send in the same transaction as authorization."""
    db.flush()
    draft, simulated = _validated_draft(db, order)
    if not order.mock_order or not simulated or settings.email_mode.strip().lower() != 'mock' or settings.enable_real_email_dispatch:
        raise PurchaseOrderDispatchError('Automatic purchasing requires a simulated quote and mock-only email')
    try:
        preflight('mock', [draft])
    except EmailDispatchError as exc:
        raise PurchaseOrderDispatchError(str(exc)) from exc
    db.add(PurchaseOrderEmailDispatch(
        purchase_order_id=order.id, quotation_id=draft['quotation_id'],
        mode='mock', status='MOCKED', recipient=draft['to'],
        message_id=f'<shelfsync-po-{order.id}-q{draft["quotation_id"]}@shelfsync.invalid>',
        completed_at=datetime.now(timezone.utc),
    ))


def dispatch_authorized_order(db: Session, order_id: int) -> dict:
    """Reserve one final-order dispatch before SMTP to avoid automatic resends.

    An existing MOCKED reservation can be promoted to real SMTP only when the
    selected quotation is genuine. SENT/IN_PROGRESS/UNCERTAIN never auto-retry.
    """
    mode = settings.email_mode.strip().lower()
    order = db.scalar(select(PurchaseOrder).where(PurchaseOrder.id == order_id).with_for_update())
    if order is None:
        raise PurchaseOrderDispatchError('Purchase order not found')
    draft, is_simulated = _validated_draft(db, order)
    if mode == 'smtp' and is_simulated:
        raise PurchaseOrderDispatchError('Simulated quotations cannot be dispatched as real purchase orders')
    try:
        preflight(mode, [draft])
    except EmailDispatchError as exc:
        raise PurchaseOrderDispatchError(str(exc)) from exc

    attempt = db.scalar(select(PurchaseOrderEmailDispatch)
                        .where(PurchaseOrderEmailDispatch.purchase_order_id == order_id)
                        .with_for_update())
    if attempt is not None:
        if attempt.quotation_id != draft['quotation_id']:
            raise PurchaseOrderDispatchError('Selected quotation changed after dispatch reservation')
        if attempt.status in ('SENT', 'IN_PROGRESS', 'UNCERTAIN') or (attempt.status == 'MOCKED' and mode == 'mock'):
            result = _result(order, attempt, replayed=True)
            db.rollback()
            return result
        if attempt.status != 'MOCKED' or mode != 'smtp':
            raise PurchaseOrderDispatchError('Purchase order dispatch cannot be retried automatically')

    sender_domain = settings.email_username.rsplit('@', 1)[-1] if mode == 'smtp' else 'shelfsync.invalid'
    message_id = f'<shelfsync-po-{order_id}-q{draft["quotation_id"]}@{sender_domain}>'
    if attempt is None:
        attempt = PurchaseOrderEmailDispatch(
            purchase_order_id=order_id, quotation_id=draft['quotation_id'],
            mode=mode, status='IN_PROGRESS', recipient=draft['to'], message_id=message_id)
        db.add(attempt)
    else:
        attempt.mode = mode
        attempt.status = 'IN_PROGRESS'
        attempt.recipient = draft['to']
        attempt.message_id = message_id
        attempt.error_code = None
        attempt.completed_at = None
    # Commit before any external SMTP call. This is critical for retry safety.
    db.commit()

    final_status = 'MOCKED' if mode == 'mock' else 'SENT'
    error_code = None
    if mode == 'smtp':
        try:
            send_smtp(to=draft['to'], subject=draft['subject'],
                      body=draft['body'], message_id=message_id)
        except (OSError, smtplib.SMTPException, TimeoutError):
            final_status = 'UNCERTAIN'
            error_code = 'SMTP_OUTCOME_UNKNOWN'
        except Exception:
            final_status = 'UNCERTAIN'
            error_code = 'SMTP_UNEXPECTED_ERROR'
    # The audit row owns send outcome; do not treat an exception as safe to retry.
    attempt = db.scalar(select(PurchaseOrderEmailDispatch).where(
        PurchaseOrderEmailDispatch.purchase_order_id == order_id).with_for_update())
    if attempt is None or attempt.status != 'IN_PROGRESS' or attempt.quotation_id != draft['quotation_id']:
        db.rollback()
        raise PurchaseOrderDispatchError('Dispatch reservation changed; manual reconciliation required')
    attempt.status = final_status
    attempt.completed_at = datetime.now(timezone.utc)
    attempt.error_code = error_code
    db.commit()
    return _result(order, attempt, replayed=False)


def _result(order: PurchaseOrder, attempt: PurchaseOrderEmailDispatch, *, replayed: bool) -> dict:
    return {
        'purchase_order_id': order.id,
        'purchase_order_status': order.status,
        'selected_quotation_id': attempt.quotation_id,
        'email_mode': attempt.mode,
        'dispatch_status': attempt.status,
        'replayed': replayed,
        'smtp_accepted': attempt.status == 'SENT',
        'vendor_email': attempt.recipient,
        'message_id': attempt.message_id,
        'manual_reconciliation_required': attempt.status in ('IN_PROGRESS', 'UNCERTAIN'),
    }


def get_dispatch_status(db: Session, order_id: int) -> dict:
    order = db.get(PurchaseOrder, order_id)
    if order is None:
        raise PurchaseOrderDispatchError('Purchase order not found')
    attempt = db.scalar(select(PurchaseOrderEmailDispatch).where(
        PurchaseOrderEmailDispatch.purchase_order_id == order_id))
    if attempt is None:
        return {'purchase_order_id': order.id, 'purchase_order_status': order.status,
                'dispatch_status': 'NOT_SENT', 'smtp_accepted': False,
                'manual_reconciliation_required': False}
    return _result(order, attempt, replayed=True)
