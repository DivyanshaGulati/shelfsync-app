"""deterministic forecast calculation tests, including HTTP checkout integration."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from math import isclose, sqrt
from statistics import mean, pstdev

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.forecast import calculate_forecast
from app.core.security import hash_password
from app.db.dependencies import get_db
from app.db.models import AIThresholdLog, InventoryItem, OrderItem, PurchaseOrder, Transaction, User, Vendor, VendorItem
from app.db.session import Base
from app.main import app


@pytest.fixture
def database():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as db:
        db.add(User(name="Employee", email="forecast@test.local", password_hash=hash_password("long-test-password"), role="EMPLOYEE"))
        db.add(InventoryItem(name="Teflon Tape", sku="PL-TEF-01", category="Plumbing", unit="roll", unit_cost=20, on_hand=50, reserved_qty=3))
        db.add(Vendor(name="Supplier", email="supplier@test.local", is_active=True, is_new_vendor=False))
        db.flush()
        db.add(VendorItem(inventory_item_id=1, vendor_id=1, is_preferred=True, expected_lead_time_days=3, min_order_qty=1))
    yield factory
    engine.dispose()


def make_history(db, start_day, quantities):
    for i, quantity in enumerate(quantities):
        if quantity:
            db.add(Transaction(user_id=1, inventory_item_id=1, transaction_type="CHECKOUT",
                               quantity=quantity, idempotency_key=f"historical-{i}",
                               created_at=datetime.combine(start_day + timedelta(days=i), datetime.min.time(), timezone.utc)))


def test_recent_weighted_demand_includes_zeros_and_standard_deviation(database):
    now = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
    days = [0] * 16 + [2] * 7 + [4] * 7
    with database.begin() as db:
        make_history(db, now.date() - timedelta(days=29), days)
    with database() as db:
        result = calculate_forecast(db, db.get(InventoryItem, 1), now=now)
    expected = 0.5 * mean(days[-7:]) + 0.3 * mean(days[-14:]) + 0.2 * mean(days)
    assert abs(float(result["avg_daily_demand"]) - expected) < 0.0001
    assert abs(float(result["demand_std_dev"]) - pstdev(days)) < 0.0001
    assert result["low_confidence"] is True  # No real deliveries
    assert "completed deliveries" in result["calculation_reason"]


def test_safety_stock_uses_both_demand_and_lead_variance(database):
    now = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
    quantities = [2 if i % 2 else 6 for i in range(30)]
    with database.begin() as db:
        make_history(db, now.date() - timedelta(days=29), quantities)
        for n, lead in enumerate([2, 4, 6]):
            placed = datetime(2026, 9, 1 + n * 7, tzinfo=timezone.utc)
            db.add(PurchaseOrder(inventory_item_id=1, status="RECEIVED", placed_at=placed,
                                 completed_at=placed + timedelta(days=lead)))
    with database() as db:
        result = calculate_forecast(db, db.get(InventoryItem, 1), now=now)
    demand = 0.5 * mean(quantities[-7:]) + 0.3 * mean(quantities[-14:]) + 0.2 * mean(quantities)
    sd = pstdev(quantities)
    expected_ss = 1.65 * sqrt(4 * sd**2 + demand**2 * pstdev([2, 4, 6])**2)
    assert abs(float(result["safety_stock"]) - expected_ss) < 0.0001
    assert abs(float(result["reorder_point"]) - (demand * 4 + expected_ss)) < 0.0001
    assert result["low_confidence"] is False
    assert result["avg_lead_time_days"] == Decimal("4.000")


@pytest.mark.parametrize("status,received,expected", [
    ("WAITING_FOR_QUOTES", 0, 0),
    ("PENDING_APPROVAL", 0, 0),
    ("PLACED", 0, 10),
    ("PARTIALLY_RECEIVED", 4, 6),
    ("RECEIVED", 10, 0),
    ("REJECTED", 0, 0),
])
def test_on_order_uses_only_confirmed_outstanding_quantities(database, status, received, expected):
    with database.begin() as db:
        po = PurchaseOrder(inventory_item_id=1, status=status)
        db.add(po)
        db.flush()
        db.add(OrderItem(purchase_order_id=po.id, inventory_item_id=1,
                         ordered_quantity=10, received_quantity=received,
                         is_open=status not in ("RECEIVED", "REJECTED")))
    with database() as db:
        result = calculate_forecast(db, db.get(InventoryItem, 1))
        assert result["on_order"] == expected
        assert result["inventory_position"] == 50 + expected - 3


def test_no_history_uses_configured_demand_and_vendor_lead_time(database):
    with database.begin() as db:
        item = db.get(InventoryItem, 1)
        item.avg_daily_demand = Decimal("5.0000")
        item.demand_std_dev = Decimal("1.0000")
    with database() as db:
        result = calculate_forecast(db, db.get(InventoryItem, 1))
        assert result["avg_daily_demand"] == Decimal("5.0000")
        assert result["avg_lead_time_days"] == Decimal("3.000")
        assert result["low_confidence"] is True
        assert result["suggested_order_quantity"] >= 0


def test_reorder_decision_uses_inventory_position_not_on_hand(database):
    with database.begin() as db:
        item = db.get(InventoryItem, 1)
        item.on_hand = 2
        item.reserved_qty = 1
        item.avg_daily_demand = Decimal("3")
        db.flush()
        po = PurchaseOrder(inventory_item_id=1, status="PLACED")
        db.add(po)
        db.flush()
        db.add(OrderItem(purchase_order_id=po.id, inventory_item_id=1, ordered_quantity=100, received_quantity=0))
    with database() as db:
        result = calculate_forecast(db, db.get(InventoryItem, 1))
        assert result["inventory_position"] == 101
        assert result["replenishment_required"] is False


def test_checkout_forecast_log_is_atomic_and_replay_creates_no_second_log(database):
    old_secret = settings.jwt_secret
    settings.jwt_secret = "a-long-forecast-test-secret-at-least-thirty-two"
    def override():
        with database() as db:
            yield db
    app.dependency_overrides[get_db] = override
    try:
        with TestClient(app) as http:
            auth = http.post("/api/auth/login", json={"email":"forecast@test.local", "password":"long-test-password"})
            assert auth.status_code == 200
            headers = {"Authorization": "Bearer " + auth.json()["access_token"]}
            payload = {"inventory_item_id":1, "quantity":2, "idempotency_key":"forecast-checkout"}
            response = http.post("/api/checkout", headers=headers, json=payload)
            assert response.status_code == 201, response.text
            assert http.post("/api/checkout", headers=headers, json=payload).json()["replayed"] is True
            projection = http.get("/api/inventory/1/forecast", headers=headers)
            assert projection.status_code == 200, projection.text
            assert projection.json()["on_hand"] == 48
            assert projection.json()["low_confidence"] is True
            assert http.get("/api/inventory/1/forecast").status_code == 401
            assert http.get("/api/inventory/999/forecast", headers=headers).status_code == 404
    finally:
        app.dependency_overrides.clear()
        settings.jwt_secret = old_secret
    with database() as db:
        item = db.get(InventoryItem, 1)
        assert item.on_hand == 48
        assert db.scalar(select(func.count()).select_from(AIThresholdLog)) == 1
        assert db.scalar(select(func.count()).select_from(Transaction)) == 1
        assert item.avg_daily_demand > 0


def test_forecast_read_only_never_creates_logs(database):
    with database() as db:
        result = calculate_forecast(db, db.get(InventoryItem, 1))
        assert "reorder_point" in result
        assert db.scalar(select(func.count()).select_from(AIThresholdLog)) == 0
