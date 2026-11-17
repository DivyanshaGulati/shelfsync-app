"""Public, fixed-recipient contact mail with separate opt-in and bounded rate limits."""
import smtplib
from collections import deque
from threading import Lock
from time import monotonic
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.core.config import settings
from app.core.email_service import send_smtp, valid_email

router = APIRouter(prefix='/api', tags=['contact'])
_lock = Lock()
_attempts: deque[tuple[float, str]] = deque()


class ContactMessage(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=254)
    message: str = Field(min_length=10, max_length=4000)
    website: str = Field(default='', max_length=200)  # Honeypot

    @field_validator('name', 'email', 'message')
    @classmethod
    def clean(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('This field is required')
        return value

    @field_validator('email')
    @classmethod
    def check_email(cls, value: str) -> str:
        if not valid_email(value, allow_demo=False):
            raise ValueError('Enter a valid email address')
        return value


def reserve_attempt(address: str) -> None:
    # One backend process: 20 attempts/hour total, at most 3/address/10 minutes.
    # Ignore forwarded headers supplied by clients. Caddy normally shares one
    # peer address, so this deliberately conservative limit can be site-wide.
    now = monotonic()
    with _lock:
        while _attempts and _attempts[0][0] <= now - 3600:
            _attempts.popleft()
        recent = sum(t > now - 600 and ip == address for t, ip in _attempts)
        if recent >= 3 or len(_attempts) >= 20:
            raise HTTPException(429, 'Too many messages. Please try again later.', headers={'Retry-After': '600'})
        _attempts.append((now, address))


@router.post('/contact')
def contact(payload: ContactMessage, request: Request):
    if not settings.enable_contact_email:
        raise HTTPException(503, 'Contact email is not available yet.')
    if payload.website:
        return {'status': 'accepted'}
    if (settings.email_port not in (465, 587)
        or not settings.email_host or not settings.email_password
        or not valid_email(settings.email_username, allow_demo=False)
        or not valid_email(settings.owner_email, allow_demo=False)):
        raise HTTPException(503, 'Contact email is not available yet.')
    reserve_attempt(request.client.host if request.client else 'unknown')
    body = (f'Website contact from: {payload.name}\n'
            f'Reply to: {payload.email}\n\n{payload.message}\n')
    try:
        send_smtp(to=settings.owner_email, subject='ShelfSync website contact', body=body,
                  message_id=f'<shelfsync-contact-{uuid4()}@{settings.email_username.rsplit("@", 1)[-1]}>')
    except (OSError, smtplib.SMTPException, TimeoutError):
        # No automatic retry: the mail server may already have accepted the mail.
        raise HTTPException(502, 'Delivery could not be confirmed. Please contact us directly.') from None
    return {'status': 'accepted'}
