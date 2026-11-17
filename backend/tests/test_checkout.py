"""Checkout and history integration tests with real FastAPI auth and database sessions."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.security import hash_password
from app.db.dependencies import get_db
from app.db.models import InventoryItem, User, Transaction
from app.db.session import Base
from app.main import app


@pytest.fixture
def env():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as db:
        db.add_all([
            User(name="Owner", email="owner@test.local", password_hash=hash_password("owner-long-password"), role="OWNER"),
            User(name="Employee", email="employee@test.local", password_hash=hash_password("employee-long-password"), role="EMPLOYEE"),
            User(name="Other employee", email="other@test.local", password_hash=hash_password("other-long-password"), role="EMPLOYEE"),
            InventoryItem(name="Teflon Tape", sku="PL-TEF-01", category="Plumbing", unit="roll", unit_cost=20, on_hand=10, reserved_qty=3),
        ])
    def override():
        with factory() as db:
            yield db
    old = settings.jwt_secret
    settings.jwt_secret = "long-test-jwt-secret-at-least-thirty-two-chars"
    app.dependency_overrides[get_db] = override
    try:
        with TestClient(app) as http:
            def auth(email, password):
                response = http.post("/api/auth/login", json={"email": email, "password": password})
                assert response.status_code == 200, response.text
                return {"Authorization": "Bearer " + response.json()["access_token"]}
            yield http, factory, auth("owner@test.local", "owner-long-password"), auth("employee@test.local", "employee-long-password"), auth("other@test.local", "other-long-password")
    finally:
        app.dependency_overrides.clear()
        settings.jwt_secret = old
        engine.dispose()


def request(key="checkout-test-1", quantity=4, **kwargs):
    return {"inventory_item_id": 1, "quantity": quantity, "idempotency_key": key, "usage_note": "Site renovation", **kwargs}


def test_checkout_atomic_stock_and_transaction(env):
    http, factory, _, employee, _ = env
    response = http.post("/api/checkout", headers=employee, json=request())
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["on_hand"] == 6 and data["available_stock"] == 3 and data["replayed"] is False
    assert data["transaction"]["quantity"] == 4
    with factory() as db:
        item = db.get(InventoryItem, 1)
        assert item.on_hand == 6 and item.reserved_qty == 3
        tx = db.scalar(select(Transaction).where(Transaction.idempotency_key == "checkout-test-1"))
        assert tx is not None and tx.transaction_type == "CHECKOUT" and tx.quantity == 4


def test_idempotent_retry_and_conflicting_reuse(env):
    http, factory, owner, employee, _ = env
    assert http.post("/api/checkout", headers=employee, json=request()).status_code == 201
    retry = http.post("/api/checkout", headers=employee, json=request())
    assert retry.status_code == 201 and retry.json()["replayed"] is True
    assert retry.json()["transaction"]["id"] == 1
    assert http.post("/api/checkout", headers=employee, json=request(quantity=2)).status_code == 409
    assert http.post("/api/checkout", headers=owner, json=request()).status_code == 409
    with factory() as db:
        assert db.get(InventoryItem, 1).on_hand == 6
        assert db.scalar(select(func.count()).select_from(Transaction)) == 1


def test_reject_invalid_and_unavailable_without_writing(env):
    http, factory, _, employee, _ = env
    for payload in (request(quantity=0), request(quantity=-1), request(key="  "), request(usage_note="x" * 5001)):
        assert http.post("/api/checkout", headers=employee, json=payload).status_code == 422
    assert http.post("/api/checkout", headers=employee, json=request(quantity=8)).status_code == 409
    missing = http.post("/api/checkout", headers=employee, json={**request(), "inventory_item_id": 200})
    assert missing.status_code == 404
    with factory() as db:
        assert db.get(InventoryItem, 1).on_hand == 10
        assert db.scalar(select(func.count()).select_from(Transaction)) == 0


def test_transactions_role_history_and_pagination(env):
    http, _, owner, employee, other = env
    assert http.get("/api/transactions").status_code == 401
    assert http.post("/api/checkout", json=request()).status_code == 401
    assert http.post("/api/checkout", headers=employee, json=request(key="employee-1", quantity=2)).status_code == 201
    assert http.post("/api/checkout", headers=other, json=request(key="other-1", quantity=1)).status_code == 201
    assert http.get("/api/transactions", headers=owner).json()["total"] == 2
    assert http.get("/api/transactions", headers=employee).json()["total"] == 1
    assert http.get("/api/transactions", headers=other).json()["total"] == 1
    assert http.get("/api/transactions?item_id=999", headers=owner).json()["total"] == 0
    assert len(http.get("/api/transactions?limit=1", headers=owner).json()["items"]) == 1
    assert http.get("/api/transactions?limit=101", headers=owner).status_code == 422


def test_multiple_sequential_checkouts_preserve_reserved_stock(env):
    http, factory, _, employee, other = env
    assert http.post("/api/checkout", headers=employee, json=request(key="first", quantity=4)).status_code == 201
    assert http.post("/api/checkout", headers=other, json=request(key="second", quantity=4)).status_code == 409
    response = http.post("/api/checkout", headers=other, json=request(key="third", quantity=3))
    assert response.status_code == 201
    assert response.json()["on_hand"] == 3 and response.json()["available_stock"] == 0
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Transaction)) == 2
