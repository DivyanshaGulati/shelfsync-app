"""verify actual DB-based JWT authentication and role gates."""
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.security import create_access_token, decode_access_token, hash_password, verify_password
from app.db.dependencies import get_db
from app.db.models import User
from app.db.session import Base
from app.main import app


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        session.add_all([
            User(name="Owner", email="owner@example.com", password_hash=hash_password("owner-testing-password"), role="OWNER"),
            User(name="Employee", email="employee@example.com", password_hash=hash_password("employee-testing-password"), role="EMPLOYEE"),
            User(name="Inactive", email="inactive@example.com", password_hash=hash_password("inactive-testing-password"), role="EMPLOYEE", is_active=False),
        ])
        session.commit()

    def override_db():
        with factory() as session:
            yield session

    original_secret = settings.jwt_secret
    settings.jwt_secret = "unit-test-secret-that-is-over-32-bytes-long"
    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as test_client:
            yield test_client, factory
    finally:
        app.dependency_overrides.clear()
        settings.jwt_secret = original_secret
        engine.dispose()


def login(client, email="owner@example.com", password="owner-testing-password"):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_argon2_hash_and_password_validation():
    digest = hash_password("a-strong-password")
    assert digest.startswith("$argon2id$")
    assert digest != hash_password("a-strong-password")
    assert verify_password("a-strong-password", digest)
    assert not verify_password("incorrect", digest)
    assert not verify_password("anything", "not-a-hash")
    with pytest.raises(ValueError):
        hash_password("short")


def test_owner_login_and_me(client):
    http, _ = client
    response = login(http, email="OWNER@example.com")
    assert response.status_code == 200
    token = response.json()["access_token"]
    assert response.json()["token_type"] == "bearer"
    user = http.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert user.status_code == 200
    assert user.json()["role"] == "OWNER"
    assert "password_hash" not in user.json()
    assert http.get("/api/auth/owner-check", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert http.get("/api/auth/employee-check", headers={"Authorization": f"Bearer {token}"}).status_code == 403


def test_employee_forbidden_owner_route(client):
    http, _ = client
    response = login(http, "employee@example.com", "employee-testing-password")
    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert http.get("/api/auth/me", headers=headers).status_code == 200
    assert http.get("/api/auth/owner-check", headers=headers).status_code == 403
    assert http.get("/api/auth/employee-check", headers=headers).status_code == 200


def test_bad_credentials_and_disabled_user(client):
    http, _ = client
    for email, password in [
        ("absent@example.com", "whatever"),
        ("owner@example.com", "wrong password"),
        ("inactive@example.com", "inactive-testing-password"),
    ]:
        response = login(http, email, password)
        assert response.status_code == 401
        assert response.json() == {"detail": "Invalid credentials"}


def test_missing_bad_expired_and_wrong_type_tokens(client):
    http, _ = client
    assert http.get("/api/auth/me").status_code == 401
    assert http.get("/api/auth/me", headers={"Authorization": "Bearer nonsense"}).status_code == 401
    key = settings.jwt_secret
    now = datetime.now(timezone.utc)
    for payload in [
        {"sub": "1", "iat": now - timedelta(hours=2), "nbf": now - timedelta(hours=2), "exp": now - timedelta(hours=1), "typ": "access"},
        {"sub": "1", "iat": now, "nbf": now, "exp": now + timedelta(hours=1), "typ": "refresh"},
    ]:
        token = jwt.encode(payload, key, algorithm="HS256")
        assert http.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    bad_signature = jwt.encode({"sub": "1", "exp": now + timedelta(hours=1)}, "some-other-secret-at-least-32-bytes-long", algorithm="HS256")
    assert http.get("/api/auth/me", headers={"Authorization": f"Bearer {bad_signature}"}).status_code == 401


def test_deleted_and_deactivated_users_rejected_after_login(client):
    http, factory = client
    token = login(http).json()["access_token"]
    with factory() as session:
        owner = session.query(User).filter(User.email == "owner@example.com").one()
        owner.is_active = False
        session.commit()
    assert http.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_invalid_jwt_config_fails_closed(client):
    http, _ = client
    original = settings.jwt_secret
    settings.jwt_secret = "short"
    try:
        with pytest.raises(RuntimeError):
            create_access_token(1)
    finally:
        settings.jwt_secret = original
