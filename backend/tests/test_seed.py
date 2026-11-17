"""Step 3 demo seed contract; uses temporary migrated DB, never live PostgreSQL."""
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.core.security import verify_password
from app.db.models import User, InventoryItem, Vendor, VendorItem, Transaction, PurchaseOrder, VendorQuotation
from app.db.seed import seed_database, ITEMS, MAPPINGS, VENDORS, HISTORY_PREFIX, HISTORY_TAG
from app.verification.demo_preflight import clean_demo_findings
from test_schema import db  # isolated Alembic-upgraded sqlite fixture

OWNER_EMAIL = "real-owner@localhost.test"
OWNER_PASS = "demo-owner-secret-123!"
EMP_PASS = "demo-employee-secret-123!"
AS_OF = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)


def run(session):
    return seed_database(session, owner_email=OWNER_EMAIL, owner_password=OWNER_PASS,
                         employee_password=EMP_PASS, as_of=AS_OF)


def test_seed_all_users_items_vendors_mappings_and_history(db):
    with Session(db) as session:
        counts = run(session)
        session.commit()
        assert counts["users"] == 3
        assert counts["inventory_items"] == len(ITEMS) == 10
        assert counts["vendors"] == len(VENDORS) == 3
        assert counts["vendor_items"] == len(MAPPINGS) == 21
        assert counts["historical_transactions"] > 100
        users = session.scalars(select(User).order_by(User.role)).all()
        assert sorted(u.role for u in users) == ["EMPLOYEE", "EMPLOYEE", "OWNER"]
        assert all(u.is_active for u in users)
        assert verify_password(OWNER_PASS, next(u.password_hash for u in users if u.role == "OWNER"))
        assert all(u.password_hash not in (OWNER_PASS, EMP_PASS) for u in users)
        assert {i.sku: (i.name, i.unit, i.unit_cost) for i in session.scalars(select(InventoryItem))} == {
            sku: (name, unit, Decimal(cost)) for name, sku, _category, unit, cost, _stock in ITEMS}
        assert session.scalar(select(func.count()).select_from(VendorItem).where(VendorItem.is_preferred.is_(True))) == 10
        assert session.scalar(select(func.count()).select_from(PurchaseOrder)) == 0
        assert session.scalar(select(func.count()).select_from(VendorQuotation)) == 0
        assert clean_demo_findings(session, now=AS_OF) == []
        assert all(v.phone is None and v.is_active and not v.is_new_vendor and not v.is_slow for v in session.scalars(select(Vendor)))
        assert all(i.low_confidence for i in session.scalars(select(InventoryItem)))
        assert all(t.usage_note == HISTORY_TAG for t in session.scalars(select(Transaction)))
        assert all(t.created_at.date().isoformat() >= "2026-10-16" for t in session.scalars(select(Transaction)))
        by_item = {item.sku: item for item in session.scalars(select(InventoryItem))}
        for _, sku, _, _, _, _ in ITEMS:
            assert len(by_item[sku].vendor_items) >= 2
        shreeji = session.scalar(select(Vendor).where(Vendor.name == "Shreeji Plumbing & Pipes"))
        teflon = by_item["PL-TEF-01"]
        mapping = session.scalar(select(VendorItem).where(VendorItem.vendor_id == shreeji.id, VendorItem.inventory_item_id == teflon.id))
        assert mapping.last_known_unit_price == Decimal("18") and mapping.min_order_qty == 5 and mapping.is_preferred


def test_seed_is_idempotent_and_does_not_rewrite_existing_values(db):
    with Session(db) as session:
        first = run(session)
        session.commit()
        tape = session.scalar(select(InventoryItem).where(InventoryItem.sku == "PL-TEF-01"))
        tape.on_hand = 23
        session.commit()
        user = session.scalar(select(User).where(User.email == OWNER_EMAIL))
        original_hash = user.password_hash
        second = seed_database(session, owner_email=OWNER_EMAIL, owner_password="different-password-123",
                               employee_password="changed-employee-pass-123", as_of=datetime(2027, 1, 1, tzinfo=timezone.utc))
        session.commit()
        assert first == second
        assert session.scalar(select(InventoryItem).where(InventoryItem.sku == "PL-TEF-01")).on_hand == 23
        assert "Stock for PL-TEF-01 differs from the seeded baseline" in clean_demo_findings(session, now=AS_OF)
        assert session.scalar(select(User).where(User.email == OWNER_EMAIL)).password_hash == original_hash
        assert session.scalar(select(func.count()).select_from(Transaction).where(Transaction.idempotency_key.like(f"{HISTORY_PREFIX}%"))) == first["historical_transactions"]


def test_seed_rejects_placeholder_owner_or_weak_password(db):
    with Session(db) as session:
        with pytest.raises(ValueError, match="OWNER_EMAIL"):
            seed_database(session, owner_email="owner@example.com", owner_password=OWNER_PASS, employee_password=EMP_PASS)
        with pytest.raises(ValueError, match="12 characters"):
            seed_database(session, owner_email=OWNER_EMAIL, owner_password="bad", employee_password=EMP_PASS)
        assert session.scalar(select(func.count()).select_from(User)) == 0


def test_conflicting_preferred_mapping_fails_without_overwriting(db):
    with Session(db) as session:
        run(session)
        session.commit()
        tape = session.scalar(select(InventoryItem).where(InventoryItem.sku == "PL-TEF-01"))
        preferred = session.scalar(select(VendorItem).where(VendorItem.inventory_item_id == tape.id, VendorItem.is_preferred.is_(True)))
        preferred.min_order_qty = 999
        session.commit()
        with pytest.raises(ValueError, match="differs"):
            run(session)
        session.rollback()
        assert preferred.min_order_qty == 999


def test_seeded_users_login_and_role_checks(db):
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker
    from app.core.config import settings
    from app.db.dependencies import get_db
    from app.main import app

    factory = sessionmaker(bind=db)
    with factory() as session:
        run(session)
        session.commit()

    def override_db():
        with factory() as session:
            yield session

    old_secret = settings.jwt_secret
    settings.jwt_secret = "integration-test-secret-over-thirty-two-bytes"
    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            for email, password, role in [
                (OWNER_EMAIL, OWNER_PASS, "OWNER"),
                ("employee1@shelfsync.example", EMP_PASS, "EMPLOYEE"),
                ("employee2@shelfsync.example", EMP_PASS, "EMPLOYEE"),
            ]:
                r = client.post("/api/auth/login", json={"email": email, "password": password})
                assert r.status_code == 200
                auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
                assert client.get("/api/auth/me", headers=auth).json()["role"] == role
                assert client.get("/api/auth/owner-check", headers=auth).status_code == (200 if role == "OWNER" else 403)
    finally:
        app.dependency_overrides.clear()
        settings.jwt_secret = old_secret
