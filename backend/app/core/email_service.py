"""Explicit OWNER-triggered quotation email dispatch; no purchase order sending.

The database reservation is committed BEFORE an SMTP attempt. In case of crash,
IN_PROGRESS and UNCERTAIN are not silently retried. SMTP is not transactional;
this prevents automatic duplicate attempts, not an absolute exactly-once guarantee.
"""
from __future__ import annotations

import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parseaddr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.vendor_drafts import DraftError, make_quote_drafts, quotation_request_context
from app.db.models import PurchaseOrder, QuotationEmailDispatch, VendorQuotation


class EmailDispatchError(ValueError):
    pass


def valid_email(address: str, *, allow_demo: bool) -> bool:
    if not address or any(c in address for c in '\r\n\t,;<> '):
        return False
    parsed = parseaddr(address)[1]
    if parsed != address or address.count('@') != 1:
        return False
    user, domain = address.rsplit('@', 1)
    if not user or '.' not in domain or domain.startswith('.') or domain.endswith('.'):
        return False
    if not allow_demo and (domain.lower() == 'example.com' or domain.lower().endswith('.example') or domain.lower().endswith('.test') or domain.lower().endswith('.invalid') or domain.lower().endswith('.localhost')):
        return False
    return True


def preflight(mode: str, drafts: list[dict]) -> None:
    if mode not in ('mock', 'smtp'):
        raise EmailDispatchError('EMAIL_MODE must be mock or smtp')
    if mode == 'smtp':
        if not settings.enable_real_email_dispatch:
            raise EmailDispatchError('Real email dispatch is disabled; set ENABLE_REAL_EMAIL_DISPATCH=true explicitly')
        if not (settings.email_host and settings.email_username and settings.email_password):
            raise EmailDispatchError('SMTP configuration is incomplete')
        if settings.email_port not in (465, 587):
            raise EmailDispatchError('Only SSL port 465 or STARTTLS port 587 are supported')
        if not valid_email(settings.email_username, allow_demo=False):
            raise EmailDispatchError('SMTP sender address is invalid')
    if not all(valid_email(d['to'], allow_demo=mode == 'mock') for d in drafts):
        raise EmailDispatchError('One or more vendor email addresses are invalid or demo-only')


def send_smtp(*, to: str, subject: str, body: str, message_id: str) -> None:
    """Only network side effect; patch this function in unit tests."""
    message = EmailMessage()
    message['From'] = settings.email_username
    message['To'] = to
    message['Subject'] = subject
    message['Message-ID'] = message_id
    message.set_content(body)
    timeout = settings.email_timeout_seconds
    secure_context = ssl.create_default_context()
    if settings.email_port == 465:
        with smtplib.SMTP_SSL(settings.email_host, settings.email_port, timeout=timeout, context=secure_context) as smtp:
            smtp.login(settings.email_username, settings.email_password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(settings.email_host, settings.email_port, timeout=timeout) as smtp:
            smtp.ehlo()
            smtp.starttls(context=secure_context)
            smtp.ehlo()
            smtp.login(settings.email_username, settings.email_password)
            smtp.send_message(message)


def dispatch_quotation_emails(db: Session, order_id: int) -> dict:
    """Send only current REQUESTED RFQs for active mapped vendors.

    Existing SENT/MOCKED attempts return a replay outcome. MOCKED can be explicitly
    upgraded to SMTP when real delivery is enabled. UNKNOWN outcomes are never
    retried without an operator reconciling the external mailbox first.
    """
    mode = settings.email_mode.strip().lower()
    # No mutation or SMTP until all vendor mappings and addresses pass validation.
    order = db.get(PurchaseOrder, order_id)
    if order is None:
        raise EmailDispatchError('Purchase request not found')
    try:
        context = quotation_request_context(db, order)
    except DraftError as exc:
        raise EmailDispatchError(str(exc)) from exc
    drafts = make_quote_drafts(context)
    preflight(mode, drafts)
    db.rollback()

    results = []
    for draft in drafts:
        # Commit the idempotency reservation before external I/O.
        order = db.scalar(select(PurchaseOrder).where(PurchaseOrder.id == order_id).with_for_update())
        if order is None:
            db.rollback()
            raise EmailDispatchError('Purchase request not found')
        try:
            current = quotation_request_context(db, order)
        except DraftError as exc:
            db.rollback()
            raise EmailDispatchError(str(exc)) from exc
        if not any(v['quotation_id'] == draft['quotation_id'] and v['vendor_email'] == draft['to'] for v in current['vendors']):
            db.rollback()
            raise EmailDispatchError('Quotation vendor changed during dispatch')
        attempt = db.scalar(select(QuotationEmailDispatch)
                            .where(QuotationEmailDispatch.quotation_id == draft['quotation_id'])
                            .with_for_update())
        if attempt is not None and (attempt.status in ('SENT', 'IN_PROGRESS', 'UNCERTAIN') or
                                     (attempt.status == 'MOCKED' and mode == 'mock')):
            results.append({'quotation_id': draft['quotation_id'], 'vendor_id': draft['vendor_id'],
                            'status': attempt.status, 'replayed': True})
            db.rollback()
            continue
        sender_domain = settings.email_username.rsplit('@', 1)[-1] if mode == 'smtp' else 'shelfsync.invalid'
        message_id = f'<shelfsync-rfq-{order_id}-{draft["quotation_id"]}@{sender_domain}>'
        if attempt is None:
            attempt = QuotationEmailDispatch(quotation_id=draft['quotation_id'],
                mode=mode, status='IN_PROGRESS', recipient=draft['to'], message_id=message_id)
            db.add(attempt)
        else:
            # The only retry allowed is switching a MOCKED attempt to a real SMTP attempt.
            attempt.mode = mode
            attempt.status = 'IN_PROGRESS'
            attempt.recipient = draft['to']
            attempt.message_id = message_id
            attempt.error_code = None
            attempt.completed_at = None
        db.commit()

        final_status = 'MOCKED' if mode == 'mock' else 'SENT'
        error_code = None
        if mode == 'smtp':
            try:
                send_smtp(to=draft['to'], subject=draft['subject'], body=draft['body'], message_id=message_id)
            except (OSError, smtplib.SMTPException, TimeoutError):
                # The SMTP server may have accepted the message just before error.
                final_status = 'UNCERTAIN'
                error_code = 'SMTP_OUTCOME_UNKNOWN'
            except Exception:
                final_status = 'UNCERTAIN'
                error_code = 'SMTP_UNEXPECTED_ERROR'
        # No sensitive SMTP exception strings are persisted or returned.
        attempt = db.scalar(select(QuotationEmailDispatch).where(
            QuotationEmailDispatch.quotation_id == draft['quotation_id']).with_for_update())
        if attempt is None or attempt.status != 'IN_PROGRESS':
            db.rollback()
            raise EmailDispatchError('Dispatch reservation changed during send; manual reconciliation required')
        attempt.status = final_status
        attempt.completed_at = datetime.now(timezone.utc)
        attempt.error_code = error_code
        db.commit()
        results.append({'quotation_id': draft['quotation_id'], 'vendor_id': draft['vendor_id'],
                        'status': final_status, 'replayed': False})
    return {'purchase_order_id': order_id, 'email_mode': mode,
            'purchase_order_status': 'WAITING_FOR_QUOTES', 'dispatches': results,
            'final_purchase_order_email_sent': False}
