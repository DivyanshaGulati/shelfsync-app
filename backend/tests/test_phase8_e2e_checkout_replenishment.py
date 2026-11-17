"""API-level checkout -> forecast -> replenishment safety journey.

Runs on isolated SQLite; PostgreSQL concurrency remains in dedicated tests.
"""
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.security import hash_password
from app.db.dependencies import get_db
from app.db.models import AIThresholdLog, InventoryItem, OrderItem, PurchaseOrder, Transaction, User
from app.db.session import Base
from app.main import app


@pytest.fixture
def journey():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as db:
        db.add_all([
            User(name='Owner', email='owner@journey.test', password_hash=hash_password('owner-journey-password'), role='OWNER'),
            User(name='Employee', email='employee@journey.test', password_hash=hash_password('employee-journey-password'), role='EMPLOYEE'),
            InventoryItem(name='Teflon Tape', sku='PL-TEF-01', category='Plumbing', unit='roll', unit_cost=20, on_hand=8, reserved_qty=1,
                          avg_daily_demand=Decimal('5'), avg_lead_time_days=Decimal('3')),
        ])
    def override():
        with factory() as db:
            yield db
    old_secret = settings.jwt_secret
    settings.jwt_secret = 'phase8-journey-secret-at-least-thirty-two-chars'
    app.dependency_overrides[get_db] = override
    try:
        with TestClient(app) as client:
            def token(email, password):
                result = client.post('/api/auth/login', json={'email': email, 'password': password})
                assert result.status_code == 200, result.text
                return {'Authorization': 'Bearer ' + result.json()['access_token']}
            yield client, factory, token('owner@journey.test', 'owner-journey-password'), token('employee@journey.test', 'employee-journey-password')
    finally:
        app.dependency_overrides.clear()
        settings.jwt_secret = old_secret
        engine.dispose()


def checkout(key, qty):
    return {'inventory_item_id': 1, 'quantity': qty, 'usage_note': 'Contractor renovation site', 'idempotency_key': key}


def test_end_to_end_checkout_forecast_and_unpriced_replenishment(journey):
    client, factory, owner, employee = journey
    result = client.post('/api/checkout', headers=employee, json=checkout('phase8-e2e-1', 4))
    assert result.status_code == 201, result.text
    assert result.json()['on_hand'] == 4
    assert result.json()['available_stock'] == 3
    assert result.json()['replayed'] is False
    with factory() as db:
        item = db.get(InventoryItem, 1)
        assert item.on_hand == 4 and item.reserved_qty == 1
        assert item.reorder_point >= 0 and item.safety_stock >= 0
        assert db.scalar(select(func.count()).select_from(AIThresholdLog)) == 1
        order = db.scalar(select(PurchaseOrder))
        assert order is not None and order.status == 'WAITING_FOR_QUOTES'
        assert order.vendor_id is None and order.total_amount is None
        line = db.scalar(select(OrderItem).where(OrderItem.purchase_order_id == order.id))
        assert line.ordered_quantity > 0 and line.unit_price is None and line.total_price is None
    response = client.get('/api/purchase-requests?status=WAITING_FOR_QUOTES', headers=owner)
    assert response.status_code == 200 and response.json()['total'] == 1
    assert client.get('/api/purchase-requests', headers=employee).status_code == 403


def test_end_to_end_retries_and_followup_checkout_never_duplicate_order(journey):
    client, factory, _, employee = journey
    first = client.post('/api/checkout', headers=employee, json=checkout('phase8-retry-1', 4))
    assert first.status_code == 201
    replay = client.post('/api/checkout', headers=employee, json=checkout('phase8-retry-1', 4))
    assert replay.status_code == 201 and replay.json()['replayed'] is True
    assert replay.json()['transaction']['id'] == first.json()['transaction']['id']
    assert client.post('/api/checkout', headers=employee, json=checkout('phase8-retry-1', 2)).status_code == 409
    second = client.post('/api/checkout', headers=employee, json=checkout('phase8-followup-2', 2))
    assert second.status_code == 201 and second.json()['on_hand'] == 2
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Transaction)) == 2
        assert db.scalar(select(func.count()).select_from(PurchaseOrder)) == 1
        assert db.scalar(select(func.count()).select_from(OrderItem)) == 1
        assert db.get(InventoryItem, 1).on_hand == 2


def test_end_to_end_insufficient_stock_and_auth_do_not_mutate(journey):
    client, factory, owner, employee = journey
    assert client.post('/api/checkout', json=checkout('phase8-no-auth', 1)).status_code == 401
    assert client.post('/api/checkout', headers=employee, json=checkout('phase8-too-many', 8)).status_code == 409
    assert client.post('/api/checkout', headers=employee, json=checkout('phase8-invalid', 0)).status_code == 422
    with factory() as db:
        assert db.get(InventoryItem, 1).on_hand == 8
        assert db.scalar(select(func.count()).select_from(Transaction)) == 0
        assert db.scalar(select(func.count()).select_from(PurchaseOrder)) == 0
        assert db.scalar(select(func.count()).select_from(AIThresholdLog)) == 0
    assert client.get('/api/purchase-requests', headers=owner).json()['total'] == 0


def test_end_to_end_employee_history_visibility(journey):
    client, _, owner, employee = journey
    assert client.post('/api/checkout', headers=employee, json=checkout('phase8-history-1', 2)).status_code == 201
    own = client.get('/api/transactions', headers=employee)
    assert own.status_code == 200 and own.json()['total'] == 1
    assert own.json()['items'][0]['usage_note'] == 'Contractor renovation site'
    assert client.get('/api/transactions', headers=owner).json()['total'] == 1
