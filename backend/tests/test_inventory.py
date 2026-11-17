"""Inventory API tests exercise the real JWT dependencies and SQLAlchemy persistence."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.security import hash_password
from app.db.dependencies import get_db
from app.db.models import InventoryItem, User
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
    assert http.post("/api/inventory", headers=owner, json=data).status_code == 409


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


def test_invalid_input_and_no_phantom_records(client):
    http, _, owner, _ = client
    data = {"name": "  ", "sku": "X", "category": "Tools", "unit": "piece", "unit_cost": "-1"}
    assert http.post("/api/inventory", headers=owner, json=data).status_code == 422
    assert http.patch("/api/inventory/99999", headers=owner, json={"name": "X"}).status_code == 404
    assert http.get("/api/inventory", headers=owner).json()["total"] == 2
