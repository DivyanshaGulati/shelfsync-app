"""vendor mapping / windows / API regression tests."""
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.quotations import utcnow
from app.core.security import hash_password
from app.db.dependencies import get_db
from app.db.models import InventoryItem, PurchaseOrder, OrderItem, User, Vendor, VendorItem, VendorQuotation
from app.db.session import Base
from app.main import app


@pytest.fixture
def setup():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as db:
        db.add_all([
            User(name="Owner", email="owner@quotes.local", password_hash=hash_password("owner-pass-123456"), role="OWNER"),
            User(name="Employee", email="employee@quotes.local", password_hash=hash_password("employee-pass-123456"), role="EMPLOYEE"),
            InventoryItem(name="Teflon Tape", sku="PL-TEF-01", category="Plumbing", unit="roll", unit_cost=20,
                          on_hand=4, avg_daily_demand=Decimal("5"), avg_lead_time_days=Decimal("3")),
            Vendor(name="Shreeji", email="shreeji@example.com", is_active=True),
            Vendor(name="Bharat", email="bharat@example.com", is_active=True),
            Vendor(name="Inactive", email="inactive@example.com", is_active=False),
        ])
        db.flush()
        for vendor_id in (1, 2, 3):
            db.add(VendorItem(inventory_item_id=1, vendor_id=vendor_id,
                              last_known_unit_price=Decimal("18.00"), min_order_qty=1))
    old_secret, old_window = settings.jwt_secret, settings.quote_collection_window_minutes
    settings.jwt_secret = "quote-testing-secret-long-enough"
    settings.quote_collection_window_minutes = 60
    def override():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = override
    try:
        with TestClient(app) as client:
            def login(email, password):
                response = client.post("/api/auth/login", json={"email": email, "password": password})
                assert response.status_code == 200, response.text
                return {"Authorization": "Bearer " + response.json()["access_token"]}
            yield client, factory, login("owner@quotes.local", "owner-pass-123456"), login("employee@quotes.local", "employee-pass-123456")
    finally:
        app.dependency_overrides.clear()
        settings.jwt_secret = old_secret
        settings.quote_collection_window_minutes = old_window
        engine.dispose()


def create_request(setup, key="quote-checkout"):
    client, factory, owner, employee = setup
    r = client.post("/api/checkout", headers=employee, json={
        "inventory_item_id": 1, "quantity": 3, "idempotency_key": key,
    })
    assert r.status_code == 201, r.text
    with factory() as db:
        order = db.scalar(select(PurchaseOrder))
        return order.id


def test_checkout_prepares_only_active_mapped_vendor_requests(setup):
    client, factory, owner, employee = setup
    oid = create_request(setup)
    response = client.get(f"/api/purchase-requests/{oid}/quotations", headers=owner)
    assert response.status_code == 200
    data = response.json()
    assert data["requested_count"] == 2
    assert data["received_count"] == 0
    assert data["collection_state"] == "COLLECTING"
    assert {q["vendor_name"] for q in data["quotations"]} == {"Shreeji", "Bharat"}
    assert all(q["dispatch_status"] == "NOT_SENT_EMAIL_INTEGRATION_PENDING" for q in data["quotations"])
    with factory() as db:
        order = db.get(PurchaseOrder, oid)
        assert order.status == "WAITING_FOR_QUOTES"
        assert order.vendor_id is None and order.total_amount is None
        assert order.quote_deadline_at is not None
        assert all(q.quoted_unit_price is None for q in db.scalars(select(VendorQuotation)))


def test_idempotent_prepare_preserves_deadline_and_existing_quotes(setup):
    client, factory, owner, employee = setup
    oid = create_request(setup)
    original = client.get(f"/api/purchase-requests/{oid}/quotations", headers=owner).json()
    settings.quote_collection_window_minutes = 120
    replay = client.post(f"/api/purchase-requests/{oid}/request-quotations", headers=owner)
    assert replay.status_code == 200, replay.text
    assert replay.json()["quote_deadline_at"] == original["quote_deadline_at"]
    assert [q["id"] for q in replay.json()["quotations"]] == [q["id"] for q in original["quotations"]]
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(VendorQuotation)) == 2


def test_verified_response_status_and_duplicate_rejected(setup):
    client, factory, owner, employee = setup
    oid = create_request(setup)
    quote_id = client.get(f"/api/purchase-requests/{oid}/quotations", headers=owner).json()["quotations"][0]["id"]
    reply = client.post(f"/api/purchase-requests/{oid}/quotations/{quote_id}/response", headers=owner,
                        json={"quoted_unit_price": "18.50", "quoted_lead_time_days": "2"})
    assert reply.status_code == 200, reply.text
    assert reply.json()["received_count"] == 1
    assert reply.json()["requested_count"] == 1
    with factory() as db:
        quote = db.get(VendorQuotation, quote_id)
        assert quote.status == "RECEIVED" and quote.responded_at is not None
        assert quote.quoted_unit_price == Decimal("18.50") and not quote.is_simulated
    assert client.post(f"/api/purchase-requests/{oid}/quotations/{quote_id}/response", headers=owner,
                       json={"quoted_unit_price": "19.50", "quoted_lead_time_days": "3"}).status_code == 409
    assert client.post(f"/api/purchase-requests/{oid}/close-quotations", headers=owner).status_code == 409


def test_expiry_keeps_valid_quote_and_expires_unanswered(setup):
    client, factory, owner, employee = setup
    oid = create_request(setup)
    qid = client.get(f"/api/purchase-requests/{oid}/quotations", headers=owner).json()["quotations"][0]["id"]
    assert client.post(f"/api/purchase-requests/{oid}/quotations/{qid}/response", headers=owner,
                       json={"quoted_unit_price": "19.00", "quoted_lead_time_days": "3"}).status_code == 200
    with factory.begin() as db:
        db.get(PurchaseOrder, oid).quote_deadline_at = utcnow() - timedelta(minutes=1)
    before = client.get(f"/api/purchase-requests/{oid}/quotations", headers=owner)
    assert before.json()["collection_state"] == "WINDOW_EXPIRED_PENDING_CLOSE"
    result = client.post(f"/api/purchase-requests/{oid}/close-quotations", headers=owner)
    assert result.status_code == 200, result.text
    assert result.json()["expired_count"] == 1 and result.json()["received_count"] == 1
    assert result.json()["collection_state"] == "READY_FOR_COMPARISON"
    with factory() as db:
        assert db.get(PurchaseOrder, oid).status == "WAITING_FOR_QUOTES"


def test_expired_window_blocks_late_reply_and_cannot_restart(setup):
    client, factory, owner, employee = setup
    oid = create_request(setup)
    with factory.begin() as db:
        db.get(PurchaseOrder, oid).quote_deadline_at = utcnow() - timedelta(days=1)
    pending_id = client.get(f"/api/purchase-requests/{oid}/quotations", headers=owner).json()["quotations"][0]["id"]
    assert client.post(f"/api/purchase-requests/{oid}/quotations/{pending_id}/response", headers=owner,
                       json={"quoted_unit_price": "18.00", "quoted_lead_time_days": "2"}).status_code == 409
    closed = client.post(f"/api/purchase-requests/{oid}/close-quotations", headers=owner)
    assert closed.status_code == 200
    assert closed.json()["collection_state"] == "NO_VALID_QUOTES"
    replay = client.post(f"/api/purchase-requests/{oid}/request-quotations", headers=owner)
    assert replay.status_code == 200
    assert replay.json()["expired_count"] == 2
    assert replay.json()["requested_count"] == 0


def test_no_active_vendor_then_retry_after_activation(setup):
    client, factory, owner, employee = setup
    with factory.begin() as db:
        for vendor in db.scalars(select(Vendor)):
            vendor.is_active = False
    oid = create_request(setup)
    initial = client.get(f"/api/purchase-requests/{oid}/quotations", headers=owner)
    assert initial.json()["collection_state"] == "NO_ACTIVE_MAPPED_VENDORS"
    assert initial.json()["quote_deadline_at"] is None
    with factory.begin() as db:
        db.get(Vendor, 2).is_active = True
    result = client.post(f"/api/purchase-requests/{oid}/request-quotations", headers=owner)
    assert result.status_code == 200
    assert result.json()["requested_count"] == 1
    assert result.json()["quotations"][0]["vendor_name"] == "Bharat"


def test_access_validation_and_wrong_order_quote(setup):
    client, factory, owner, employee = setup
    oid = create_request(setup)
    url = f"/api/purchase-requests/{oid}/quotations"
    assert client.get(url).status_code == 401
    assert client.get(url, headers=employee).status_code == 403
    assert client.post(f"/api/purchase-requests/{oid}/close-quotations", headers=employee).status_code == 403
    assert client.post(f"/api/purchase-requests/{oid}/request-quotations", headers=employee).status_code == 403
    assert client.post(f"/api/purchase-requests/{oid}/quotations/999/response", headers=owner,
                       json={"quoted_unit_price": "18.00", "quoted_lead_time_days": "2"}).status_code == 404
    assert client.post(f"/api/purchase-requests/{oid}/quotations/1/response", headers=employee,
                       json={"quoted_unit_price": "18.00", "quoted_lead_time_days": "2"}).status_code == 403
    assert client.post(f"/api/purchase-requests/{oid}/quotations/1/response", headers=owner,
                       json={"quoted_unit_price": "0", "quoted_lead_time_days": "2"}).status_code == 422
    assert client.get("/api/purchase-requests/999999/quotations", headers=owner).status_code == 404


def test_all_vendors_reply_allows_early_close(setup):
    client, factory, owner, employee = setup
    oid = create_request(setup)
    quotes = client.get(f"/api/purchase-requests/{oid}/quotations", headers=owner).json()["quotations"]
    for q in quotes:
        resp = client.post(f"/api/purchase-requests/{oid}/quotations/{q['id']}/response", headers=owner,
                           json={"quoted_unit_price": "20.00", "quoted_lead_time_days": "1"})
        assert resp.status_code == 200, resp.text
    closed = client.post(f"/api/purchase-requests/{oid}/close-quotations", headers=owner)
    assert closed.status_code == 200
    assert closed.json()["collection_state"] == "READY_FOR_COMPARISON"
    assert closed.json()["requested_count"] == 0
    assert closed.json()["received_count"] == 2
    assert closed.json()["expired_count"] == 0
    with factory() as db:
        assert db.get(PurchaseOrder, oid).vendor_id is None
        assert db.get(PurchaseOrder, oid).total_amount is None
