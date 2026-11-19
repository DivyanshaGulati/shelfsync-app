"""Inventory API tests exercise the real JWT dependencies and SQLAlchemy persistence."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import mock_purchasing
from app.core.claude_agent import AgentUnavailable, Recommendation
from app.core.config import settings
from app.core.quote_comparison import compare_quotations
from app.core.security import hash_password
from app.db.dependencies import get_db
from app.db.models import InventoryItem, PurchaseOrder, PurchaseOrderEmailDispatch, User
from app.db.session import Base
from app.main import app


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory.begin() as db:
        db.add_all([
            User(name="Owner", email="owner@test.local", password_hash=hash_password("test-owner-password"), role="OWNER"),
            User(name="Employee", email="employee@test.local", password_hash=hash_password("test-employee-password"), role="EMPLOYEE"),
            InventoryItem(name="Teflon Tape", sku="PL-TEF-01", category="Plumbing", unit="roll", unit_cost=20, on_hand=20, reserved_qty=3),
            InventoryItem(name="Gypsum Screw", sku="HW-SCR-15", category="Fasteners", unit="box", unit_cost=180),
        ])
    def override_db():
        with factory() as db:
            yield db
    old = settings.jwt_secret
    settings.jwt_secret = "test-secret-value-longer-than-thirty-two-characters"
    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as http:
            def header(email, password):
                response = http.post("/api/auth/login", json={"email": email, "password": password})
                assert response.status_code == 200
                return {"Authorization": "Bearer " + response.json()["access_token"]}
            yield http, factory, header("owner@test.local", "test-owner-password"), header("employee@test.local", "test-employee-password")
    finally:
        app.dependency_overrides.clear()
        settings.jwt_secret = old
        engine.dispose()


def test_auth_required_and_role_gates(client):
    http, _, owner, employee = client
    assert http.get("/api/inventory").status_code == 401
    assert http.get("/api/inventory", headers=employee).status_code == 200
    assert http.post("/api/inventory", headers=employee, json={}).status_code == 403
    assert http.patch("/api/inventory/1", headers=employee, json={"name": "No"}).status_code == 403
    assert http.get("/api/inventory/1", headers=owner).status_code == 200


def test_list_search_category_and_pagination(client):
    http, _, owner, _ = client
    response = http.get("/api/inventory", headers=owner).json()
    assert response["total"] == 2
    assert len(response["items"]) == 2
    assert response["items"][0]["on_hand"] == 20
    assert http.get("/api/inventory?q=teflon", headers=owner).json()["total"] == 1
    assert http.get("/api/inventory?q=hw-scr", headers=owner).json()["total"] == 1
    assert http.get("/api/inventory?category=plumbing", headers=owner).json()["total"] == 1
    assert len(http.get("/api/inventory?limit=1&offset=1", headers=owner).json()["items"]) == 1
    assert http.get("/api/inventory?limit=101", headers=owner).status_code == 422
    assert http.get("/api/inventory/10000", headers=owner).status_code == 404


def test_owner_creates_item_with_safe_stock_defaults(client):
    http, factory, owner, _ = client
    data = {"name": "  New Hinges  ", "sku": " hw-new-01 ", "category": "Hardware", "unit": "pair", "unit_cost": "150.00"}
    response = http.post("/api/inventory", headers=owner, json=data)
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["sku"] == "HW-NEW-01" and result["name"] == "New Hinges"
    assert result["on_hand"] == result["reserved_qty"] == 0
    assert result["low_confidence"] is True
    with factory() as db:
        assert db.get(InventoryItem, result["id"]).sku == "HW-NEW-01"
        assert db.scalar(select(PurchaseOrder)) is None
    assert http.post("/api/inventory", headers=owner, json=data).status_code == 409


def test_item_thumbnail_is_saved_listed_and_removed(client):
    http, _, owner, employee = client
    thumbnail = "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAAIAAgBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAAAP/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AP//Z"
    data = {"name": "New drill bit", "sku": "DRILL-01", "category": "Tools & Adhesives",
            "unit": "pcs", "unit_cost": "15.00", "thumbnail": thumbnail}

    for invalid in ("https://example.test/photo.jpg", "data:image/jpeg;base64,AAAA",
                    "data:image/jpeg;base64," + "A" * 12000):
        result = http.post("/api/inventory", headers=owner, json={**data, "thumbnail": invalid})
        assert result.status_code == 422

    created = http.post("/api/inventory", headers=owner, json=data)
    assert created.status_code == 201, created.text
    assert created.json()["thumbnail"] == thumbnail
    url = f"/api/inventory/{created.json()['id']}"
    assert http.get("/api/inventory?q=DRILL-01", headers=employee).json()["items"][0]["thumbnail"] == thumbnail

    renamed = http.patch(url, headers=owner, json={"name": "Drill bit"})
    assert renamed.status_code == 200
    assert renamed.json()["thumbnail"] == thumbnail
    removed = http.patch(url, headers=owner, json={"thumbnail": None})
    assert removed.status_code == 200
    assert http.get(url, headers=employee).json()["thumbnail"] is None
    added = http.patch(url, headers=owner, json={"thumbnail": thumbnail})
    assert added.status_code == 200
    assert added.json()["thumbnail"] == thumbnail


@pytest.mark.parametrize(
    ("quantity", "mock_mode", "status", "dispatch"),
    [
        (10, "enabled", "PLACED", True),
        (100, "enabled", "PLACED", True),
        (1000, "enabled", "PENDING_APPROVAL", False),
        (10, "unavailable", "WAITING_FOR_QUOTES", False),
        (10, "real_dispatch_enabled", "WAITING_FOR_QUOTES", False),
    ],
)
def test_first_order_uses_existing_purchasing_workflow(client, monkeypatch, quantity, mock_mode, status, dispatch):
    http, factory, owner, employee = client
    monkeypatch.setattr(settings, "enable_mock_purchasing", True)
    monkeypatch.setattr(settings, "enable_quote_simulation", True)
    monkeypatch.setattr(settings, "email_mode", "mock")
    monkeypatch.setattr(settings, "enable_real_email_dispatch", mock_mode == "real_dispatch_enabled")

    def recommend(db, order):
        assert mock_mode != "real_dispatch_enabled"
        if mock_mode == "unavailable":
            raise AgentUnavailable("Provider unavailable")
        comparison = compare_quotations(db, order)
        return Recommendation(
            recommended_quotation_id=comparison["lowest_cost_quotation_id"],
            reason="Lowest valid simulated total.",
        ), comparison

    monkeypatch.setattr(mock_purchasing, "recommend", recommend)
    data = {"name": "Gitti", "sku": "GITTI-01", "category": "Fasteners", "unit": "packet",
            "unit_cost": "150.00", "initial_order_quantity": quantity}
    assert http.post("/api/inventory", headers=employee, json=data).status_code == 403
    for invalid in (0, -1, 1.5, 2_147_483_648):
        assert http.post("/api/inventory", headers=owner, json={**data, "initial_order_quantity": invalid}).status_code == 422
    response = http.post("/api/inventory", headers=owner, json=data)
    assert response.status_code == 201, response.text
    item = response.json()
    assert item["on_hand"] == 0
    assert http.post("/api/inventory", headers=owner, json=data).status_code == 409

    orders = http.get("/api/purchase-requests", headers=owner).json()["items"]
    assert len(orders) == 1
    assert orders[0]["inventory_item_id"] == item["id"]
    assert orders[0]["ordered_quantity"] == quantity
    assert orders[0]["status"] == status
    with factory() as db:
        assert db.get(InventoryItem, item["id"]).on_hand == 0
        audit = db.scalar(select(PurchaseOrderEmailDispatch))
        if dispatch:
            assert audit.mode == "mock" and audit.status == "MOCKED"
        else:
            assert audit is None


def test_patch_prevents_stock_editing_and_handles_conflicts(client):
    http, factory, owner, _ = client
    changed = http.patch("/api/inventory/1", headers=owner, json={"name": "  PTFE Tape ", "unit_cost": "22.50", "description": "Updated"})
    assert changed.status_code == 200, changed.text
    assert changed.json()["name"] == "PTFE Tape"
    assert changed.json()["on_hand"] == 20
    for payload in ({"on_hand": 999}, {"reserved_qty": 0}, {"reorder_point": 999}):
        assert http.patch("/api/inventory/1", headers=owner, json=payload).status_code == 422
    assert http.patch("/api/inventory/1", headers=owner, json={"sku": "HW-SCR-15"}).status_code == 409
    assert http.patch("/api/inventory/1", headers=owner, json={"unit": None}).status_code == 422
    with factory() as db:
        item = db.get(InventoryItem, 1)
        assert item.sku == "PL-TEF-01" and item.on_hand == 20 and item.reserved_qty == 3


def test_existing_empty_item_can_request_its_first_order_once(client, monkeypatch):
    http, _, owner, employee = client
    monkeypatch.setattr(settings, "enable_mock_purchasing", False)
    data = {"initial_order_quantity": 12}
    assert http.patch("/api/inventory/2", headers=employee, json=data).status_code == 403
    assert http.patch("/api/inventory/1", headers=owner, json=data).status_code == 409
    response = http.patch("/api/inventory/2", headers=owner, json=data)
    assert response.status_code == 200, response.text
    assert response.json()["on_hand"] == 0
    assert http.patch("/api/inventory/2", headers=owner, json=data).status_code == 409
    orders = http.get("/api/purchase-requests", headers=owner).json()["items"]
    assert len(orders) == 1
    assert orders[0]["inventory_item_id"] == 2
    assert orders[0]["ordered_quantity"] == 12
    assert orders[0]["status"] == "WAITING_FOR_QUOTES"


def test_invalid_input_and_no_phantom_records(client):
    http, _, owner, _ = client
    data = {"name": "  ", "sku": "X", "category": "Tools", "unit": "piece", "unit_cost": "-1"}
    assert http.post("/api/inventory", headers=owner, json=data).status_code == 422
    assert http.patch("/api/inventory/99999", headers=owner, json={"name": "X"}).status_code == 404
    assert http.get("/api/inventory", headers=owner).json()["total"] == 2
