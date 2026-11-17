"""regression tests: no vendor selection or old-price orders."""
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.replenishment import ensure_replenishment_request
from app.core.security import hash_password
from app.db.dependencies import get_db
from app.db.models import (
    AIThresholdLog, InventoryItem, OrderItem, PurchaseOrder, Transaction, User,
)
from app.db.session import Base
from app.main import app


@pytest.fixture
def env():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as db:
        db.add_all([
            User(name="Owner", email="owner@phase4.local", password_hash=hash_password("owner-secret-password"), role="OWNER"),
            User(name="Employee", email="employee@phase4.local", password_hash=hash_password("employee-secret-password"), role="EMPLOYEE"),
            InventoryItem(name="Teflon Tape", sku="PL-TEF-01", category="Plumbing", unit="roll", unit_cost=20,
                          on_hand=4, reserved_qty=0, avg_daily_demand=Decimal("5"),
                          avg_lead_time_days=Decimal("3")),
        ])
    def override():
        with factory() as db:
            yield db
    original = settings.jwt_secret
    settings.jwt_secret = "testing-secret-long-enough-for-jwt-phase4"
    app.dependency_overrides[get_db] = override
    try:
        with TestClient(app) as client:
            def credentials(email, password):
                response = client.post("/api/auth/login", json={"email": email, "password": password})
                assert response.status_code == 200, response.text
                return {"Authorization": "Bearer " + response.json()["access_token"]}
            yield client, factory, credentials("owner@phase4.local", "owner-secret-password"), credentials("employee@phase4.local", "employee-secret-password")
    finally:
        app.dependency_overrides.clear()
        settings.jwt_secret = original
        engine.dispose()


def checkout(key, quantity=1):
    return {"inventory_item_id": 1, "quantity": quantity, "idempotency_key": key, "usage_note": "demo"}


def test_checkout_creates_unpriced_request_and_line_atomically(env):
    client, factory, owner, employee = env
    response = client.post("/api/checkout", json=checkout("phase4-first", quantity=3), headers=employee)
    assert response.status_code == 201, response.text
    with factory() as db:
        order = db.scalar(select(PurchaseOrder))
        assert order is not None
        assert order.status == "WAITING_FOR_QUOTES"
        assert order.inventory_item_id == 1 and order.created_by == 2
        assert order.vendor_id is None and order.total_amount is None
        line = db.scalar(select(OrderItem))
        assert line.purchase_order_id == order.id
        assert line.ordered_quantity > 0 and line.received_quantity == 0
        assert line.unit_price is None and line.total_price is None
        assert db.scalar(select(func.count()).select_from(AIThresholdLog)) == 1
    listing = client.get("/api/purchase-requests?status=WAITING_FOR_QUOTES", headers=owner)
    assert listing.status_code == 200, listing.text
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["ordered_quantity"] == line.ordered_quantity


def test_repeat_checkout_and_second_checkout_do_not_duplicate_open_requests(env):
    client, factory, _, employee = env
    assert client.post("/api/checkout", json=checkout("same-1", quantity=3), headers=employee).status_code == 201
    replay = client.post("/api/checkout", json=checkout("same-1", quantity=3), headers=employee)
    assert replay.status_code == 201 and replay.json()["replayed"] is True
    assert client.post("/api/checkout", json=checkout("another-2", quantity=1), headers=employee).status_code == 201
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(PurchaseOrder)) == 1
        assert db.scalar(select(func.count()).select_from(OrderItem)) == 1
        assert db.scalar(select(func.count()).select_from(Transaction)) == 2
        assert db.get(InventoryItem, 1).on_hand == 0


def test_no_request_if_position_above_reorder_point(env):
    client, factory, _, employee = env
    with factory.begin() as db:
        db.get(InventoryItem, 1).on_hand = 200
    response = client.post("/api/checkout", json=checkout("healthy"), headers=employee)
    assert response.status_code == 201, response.text
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(PurchaseOrder)) == 0
        assert db.get(InventoryItem, 1).on_hand == 199


def test_failed_checkout_leaves_no_request_and_no_threshold_log(env):
    client, factory, _, employee = env
    assert client.post("/api/checkout", json=checkout("too-many", quantity=100), headers=employee).status_code == 409
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(PurchaseOrder)) == 0
        assert db.scalar(select(func.count()).select_from(AIThresholdLog)) == 0
        assert db.scalar(select(func.count()).select_from(Transaction)) == 0


def test_no_zero_quantity_request_and_forecast_item_mismatch(env):
    _, factory, _, _ = env
    with factory.begin() as db:
        item = db.get(InventoryItem, 1)
        zero = {"inventory_item_id": 1, "replenishment_required": True, "suggested_order_quantity": 0}
        assert ensure_replenishment_request(db, item, zero, created_by=2) is None
        with pytest.raises(ValueError, match="different inventory item"):
            ensure_replenishment_request(db, item, {**zero, "inventory_item_id": 99}, created_by=2)
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(PurchaseOrder)) == 0


def test_owner_only_purchase_request_read_access(env):
    client, _, owner, employee = env
    assert client.get("/api/purchase-requests").status_code == 401
    assert client.get("/api/purchase-requests", headers=employee).status_code == 403
    assert client.get("/api/purchase-requests", headers=owner).status_code == 200
    assert client.get("/api/purchase-requests?limit=0", headers=owner).status_code == 422


def test_open_order_uniqueness_enforced_by_database(env):
    from sqlalchemy.exc import IntegrityError
    _, factory, _, _ = env
    with factory.begin() as db:
        db.add(PurchaseOrder(inventory_item_id=1, status="WAITING_FOR_QUOTES"))
    with pytest.raises(IntegrityError):
        with factory.begin() as db:
            db.add(PurchaseOrder(inventory_item_id=1, status="WAITING_FOR_QUOTES"))


def test_existing_placed_order_blocks_new_quote_request(env):
    client, factory, _, employee = env
    with factory.begin() as db:
        po = PurchaseOrder(inventory_item_id=1, status="PLACED", total_amount=Decimal("40"))
        db.add(po)
        db.flush()
        db.add(OrderItem(purchase_order_id=po.id, inventory_item_id=1,
                         ordered_quantity=2, received_quantity=0, unit_price=20, total_price=40))
    response = client.post("/api/checkout", json=checkout("placed-existing", quantity=3), headers=employee)
    assert response.status_code == 201, response.text
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(PurchaseOrder)) == 1
        assert db.scalar(select(func.count()).select_from(OrderItem)) == 1
