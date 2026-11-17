"""Password hashing and short-lived signed access tokens.

No secrets or database access are delegated to the frontend or AI.
"""
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

from app.core.config import settings

password_hasher = PasswordHasher()  # Argon2id, salted per password
ALGORITHM = "HS256"
ACCESS_TOKEN_MINUTES = 30


def hash_password(password: str) -> str:
    if not isinstance(password, str) or not (8 <= len(password) <= 1024):
        raise ValueError("Password length must be between 8 and 1024 characters")
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (VerificationError, ValueError, TypeError):
        return False


def _jwt_secret() -> str:
    secret = settings.jwt_secret
    if len(secret.encode("utf-8")) < 32 or secret.startswith("replace-with-"):
        raise RuntimeError("JWT_SECRET must be a random secret of at least 32 bytes")
    return secret


def create_access_token(user_id: int) -> str:
    if user_id <= 0:
        raise ValueError("Invalid user ID")
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": str(user_id), "iat": now, "nbf": now, "exp": now + timedelta(minutes=ACCESS_TOKEN_MINUTES), "typ": "access"},
        _jwt_secret(), algorithm=ALGORITHM,
    )


def decode_access_token(token: str) -> int:
    payload = jwt.decode(
        token, _jwt_secret(), algorithms=[ALGORITHM],
        options={"require": ["sub", "iat", "nbf", "exp", "typ"]},
        leeway=0,
    )
    if payload.get("typ") != "access":
        raise jwt.InvalidTokenError("Wrong token type")
    subject = payload["sub"]
    if not isinstance(subject, str) or not subject.isdecimal() or int(subject) <= 0:
        raise jwt.InvalidTokenError("Invalid subject")
    return int(subject)
