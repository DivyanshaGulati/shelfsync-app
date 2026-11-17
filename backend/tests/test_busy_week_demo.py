"""Busy Week integration tests against actual checkout business service."""
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.core.config import settings
from app.core.security import hash_password
from app.db.dependencies import get_db
from app.db.models import InventoryItem, PurchaseOrder, Transaction, User
from app.db.session import Base
from app.main import app

@pytest.fixture
def demo():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as db:
        db.add_all([
            User(name='Owner', email='owner@demo.test', password_hash=hash_password('demo-owner-password'), role='OWNER'),
            User(name='Employee', email='employee@demo.test', password_hash=hash_password('demo-employee-password'), role='EMPLOYEE'),
            InventoryItem(name='Gypsum Screws', sku='HW-SCR-15', category='Hardware', unit='box', unit_cost=180, on_hand=20, avg_daily_demand=Decimal('5'), avg_lead_time_days=Decimal('2')),
            InventoryItem(name='Copper Wire', sku='EL-WIR-25', category='Electrical', unit='coil', unit_cost=1650, on_hand=10, avg_daily_demand=Decimal('3'), avg_lead_time_days=Decimal('3')),
            InventoryItem(name='Teflon Tape', sku='PL-TEF-01', category='Plumbing', unit='roll', unit_cost=20, on_hand=10, avg_daily_demand=Decimal('5'), avg_lead_time_days=Decimal('3')),
        ])
    def override():
        with factory() as db: yield db
    old = (settings.jwt_secret, settings.enable_busy_week_simulation, settings.email_mode, settings.enable_real_email_dispatch)
    settings.jwt_secret = 'phase8-demo-secret-at-least-thirty-two-chars'
    settings.enable_busy_week_simulation = True
    settings.email_mode = 'mock'
    settings.enable_real_email_dispatch = False
    app.dependency_overrides[get_db] = override
    try:
        with TestClient(app) as client:
            def auth(email, password):
                r = client.post('/api/auth/login', json={'email':email,'password':password})
                assert r.status_code == 200
                return {'Authorization': 'Bearer '+r.json()['access_token']}
            yield client, factory, auth('owner@demo.test','demo-owner-password'), auth('employee@demo.test','demo-employee-password')
    finally:
        app.dependency_overrides.clear()
        settings.jwt_secret, settings.enable_busy_week_simulation, settings.email_mode, settings.enable_real_email_dispatch = old
        engine.dispose()

def post(demo, run='demo-1', who=2):
    client, _, owner, employee = demo
    return client.post('/api/demo/simulate-busy-week', headers=owner if who==2 else employee, json={'run_id':run})

def test_real_checkout_replenishment_and_replay(demo):
    first = post(demo)
    assert first.status_code == 200, first.text
    assert [x['quantity'] for x in first.json()['checkouts']] == [8,4,2]
    assert all(not x['replayed'] for x in first.json()['checkouts'])
    assert first.json()['emails_sent'] is False and first.json()['orders_authorized_by_demo'] is False
    with demo[1]() as db:
        assert [db.scalar(select(InventoryItem.on_hand).where(InventoryItem.sku == sku)) for sku in ['HW-SCR-15','EL-WIR-25','PL-TEF-01']] == [12,6,8]
        assert db.scalar(select(func.count()).select_from(Transaction)) == 3
        assert all(o.status == 'WAITING_FOR_QUOTES' and o.total_amount is None for o in db.scalars(select(PurchaseOrder)))
    replay = post(demo)
    assert replay.status_code == 200 and all(x['replayed'] for x in replay.json()['checkouts'])
    with demo[1]() as db:
        assert db.scalar(select(func.count()).select_from(Transaction)) == 3

def test_employee_and_disabled_are_blocked(demo):
    assert post(demo, who=1).status_code == 403
    settings.enable_busy_week_simulation = False
    assert post(demo).status_code == 403
    with demo[1]() as db:
        assert db.scalar(select(func.count()).select_from(Transaction)) == 0

def test_smtp_mode_is_blocked(demo):
    settings.email_mode = 'smtp'
    assert post(demo).status_code == 409
    with demo[1]() as db:
        assert db.scalar(select(func.count()).select_from(Transaction)) == 0

def test_missing_sku_is_atomic_preflight(demo):
    with demo[1].begin() as db:
        db.query(InventoryItem).filter(InventoryItem.sku == 'PL-TEF-01').delete()
    assert post(demo).status_code == 409
    with demo[1]() as db:
        assert db.scalar(select(func.count()).select_from(Transaction)) == 0

def test_partial_failure_retries_previous_steps(demo):
    with demo[1].begin() as db:
        db.query(InventoryItem).filter(InventoryItem.sku == 'EL-WIR-25').update({'on_hand': 1})
    failed = post(demo)
    assert failed.status_code == 409
    assert failed.json()['detail']['failed_sku'] == 'EL-WIR-25'
    with demo[1]() as db:
        assert db.scalar(select(func.count()).select_from(Transaction)) == 1
    with demo[1].begin() as db:
        db.query(InventoryItem).filter(InventoryItem.sku == 'EL-WIR-25').update({'on_hand': 6})
    success = post(demo)
    assert success.status_code == 200, success.text
    assert success.json()['checkouts'][0]['replayed'] is True
    with demo[1]() as db:
        assert db.scalar(select(func.count()).select_from(Transaction)) == 3
