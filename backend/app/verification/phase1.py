"""Read-only integration verification.

Run on a local developer PostgreSQL database after applying Alembic and seeds:
  python -m app.verification.phase1

No schema changes, seeding, order placement, or test writes are performed.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from decimal import Decimal

from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import Engine, inspect, select, func, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.dependencies import get_db
from app.db.models import (
    User, InventoryItem, Vendor, VendorItem, Transaction,
    PurchaseOrder, OrderItem, VendorQuotation, AIThresholdLog, ApprovalLog,
)
from app.db.seed import ITEMS, VENDORS, MAPPINGS, HISTORY_PREFIX, HISTORY_TAG
from app.db.session import get_engine
from app.main import app

TABLES = (
    User, InventoryItem, Transaction, Vendor, VendorItem, PurchaseOrder,
    OrderItem, VendorQuotation, AIThresholdLog, ApprovalLog,
)
EXPECTED_REVISIONS = {
    "0001_initial_schema", "0002_quote_collection_deadline", "0003_backend_candidate_quote",
    "0004_ai_recommendation", "0005_quotation_email_dispatch", "0006_purchase_order_email_dispatch",
    "0007_stock_receipts", "0008_mock_purchase_workflow", "0009_purchase_tracking",
    "0010_clean_item_descriptions",
    "0011_inventory_insight_reports",
    "0012_item_thumbnails",
}
REQUIRED_INDEXES = {
    "vendor_items": {"ix_vendor_items_one_preferred"},
    "purchase_orders": {"ix_purchase_orders_one_open_per_item"},
    "vendor_quotations": {"ix_vendor_quotations_one_selected"},
}


def verify_database(engine: Engine) -> list[str]:
    """Verify migrated schema and seeded data via SELECT/metadata reflection only."""
    passed: list[str] = []

    def check(ok: bool, label: str) -> None:
        if not ok:
            raise AssertionError(label)
        passed.append(label)

    with engine.connect() as connection:
        inspector = inspect(connection)
        actual_tables = set(inspector.get_table_names())
        check(all(model.__tablename__ in actual_tables for model in TABLES), "All ten Phase 1 tables exist")
        revision = MigrationContext.configure(connection).get_current_revision()
        check(revision in EXPECTED_REVISIONS, "Alembic includes Phase 1 base migration")
        for table, required in REQUIRED_INDEXES.items():
            indices = {row["name"]: row for row in inspector.get_indexes(table)}
            check(required <= indices.keys() and all(indices[name]["unique"] for name in required),
                  f"Unique protective index exists: {table}")
        # Check that the schema really has DB-level FKs, not just ORM relationships.
        for table in ("vendor_items", "transactions", "vendor_quotations", "order_items", "approval_logs"):
            check(bool(inspector.get_foreign_keys(table)), f"Foreign keys exist: {table}")
        # Confirm the database connection can execute a query without writing anything.
        check(connection.scalar(text("SELECT 1")) == 1, "SQL connection responds")

    with Session(engine) as session:
        users = session.scalars(select(User)).all()
        owner = next((u for u in users if u.email.lower() == settings.owner_email.strip().lower()), None)
        check(bool(owner and owner.role == "OWNER" and owner.is_active), "Configured OWNER account is active")
        employees = {u.email.lower(): u for u in users if u.role == "EMPLOYEE"}
        check(all(email in employees and employees[email].is_active for email in (
            "employee1@shelfsync.example", "employee2@shelfsync.example")), "Both demo employees are active")
        check(all(u.password_hash.startswith("$argon2id$") for u in users if u in ([owner] if owner else []) or u.email.lower() in employees),
              "Seeded account passwords use Argon2id hashes")

        items = {item.sku: item for item in session.scalars(select(InventoryItem))}
        check(all(sku in items and (items[sku].name, items[sku].category, items[sku].unit, items[sku].unit_cost) ==
                  (name, category, unit, Decimal(cost)) for name, sku, category, unit, cost, _ in ITEMS),
              "Ten seed inventory SKUs, names, units and INR costs match the plan")
        check(all(items[sku].low_confidence for _, sku, _, _, _, _ in ITEMS),
              "Seeded inventory is marked low-confidence")

        vendors = {v.email.lower(): v for v in session.scalars(select(Vendor))}
        check(all(email in vendors and vendors[email].name == name and vendors[email].is_active and
                  not vendors[email].is_new_vendor and not vendors[email].is_slow
                  for name, email in VENDORS), "Three seeded active vendors match the plan")
        mapped = {(m.inventory_item_id, m.vendor_id): m for m in session.scalars(select(VendorItem))}
        check(all((items[sku].id, vendors[VENDORS[idx][1]].id) in mapped and
                  (mapped[(items[sku].id, vendors[VENDORS[idx][1]].id)].last_known_unit_price,
                   mapped[(items[sku].id, vendors[VENDORS[idx][1]].id)].expected_lead_time_days,
                   mapped[(items[sku].id, vendors[VENDORS[idx][1]].id)].min_order_qty,
                   mapped[(items[sku].id, vendors[VENDORS[idx][1]].id)].is_preferred)
                  == (Decimal(price), Decimal(lead), moq, preferred)
                  for idx, sku, price, lead, moq, preferred in MAPPINGS),
              "All 21 vendor mappings match specified reference prices, lead times and MOQs")
        item_vendor_counts = Counter(m.inventory_item_id for m in mapped.values())
        check(all(item_vendor_counts[items[sku].id] >= 2 for _, sku, _, _, _, _ in ITEMS),
              "Every seeded item maps to at least two vendors")
        preferred_counts = Counter(m.inventory_item_id for m in mapped.values() if m.is_preferred)
        check(all(preferred_counts[items[sku].id] == 1 for _, sku, _, _, _, _ in ITEMS),
              "Exactly one preferred vendor per seeded item")
        check(len(set((m.inventory_item_id, m.vendor_id) for m in mapped.values())) == len(mapped),
              "Vendor-item pairs contain no duplicates")
        simulated = session.scalars(select(Transaction).where(Transaction.idempotency_key.like(f"{HISTORY_PREFIX}%"))).all()
        check(len(simulated) > 0 and all(t.usage_note == HISTORY_TAG and t.transaction_type == "CHECKOUT"
                                         for t in simulated), "Tagged synthetic consumption history is present")
        check(len({t.idempotency_key for t in simulated}) == len(simulated),
              "Synthetic transaction idempotency keys are unique")
        # No claims about reliability or quotation freshness: these cannot be inferred from seeded refs.
        passed.append("No write operations or quote/order creation performed")
    return passed


def verify_auth(engine: Engine, owner_password: str, employee_password: str) -> list[str]:
    """Use the real FastAPI login/dependencies and real DB, without starting a server."""
    checks: list[str] = []

    def session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = session_override
    try:
        with TestClient(app) as client:
            def login(email: str, password: str):
                return client.post("/api/auth/login", json={"email": email, "password": password})

            owner_response = login(settings.owner_email, owner_password)
            assert owner_response.status_code == 200, "Owner login failed"
            owner_token = owner_response.json()["access_token"]
            checks.append("Owner logs in against real PostgreSQL")
            employee_response = login("employee1@shelfsync.example", employee_password)
            assert employee_response.status_code == 200, "Employee login failed"
            employee_token = employee_response.json()["access_token"]
            checks.append("Employee logs in against real PostgreSQL")
            for expected, token in (("OWNER", owner_token), ("EMPLOYEE", employee_token)):
                result = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
                assert result.status_code == 200 and result.json()["role"] == expected, f"/me role mismatch for {expected}"
            checks.append("JWT /me returns correct database roles")
            owner_check = client.get("/api/auth/owner-check", headers={"Authorization": f"Bearer {owner_token}"})
            assert owner_check.status_code == 200, "Owner denied owner-only API"
            checks.append("Owner can access owner-only endpoint")
            employee_check = client.get("/api/auth/owner-check", headers={"Authorization": f"Bearer {employee_token}"})
            assert employee_check.status_code == 403, "Employee was incorrectly allowed owner-only access"
            checks.append("Employee cannot access owner-only endpoint (403)")
            assert login(settings.owner_email, owner_password + "-incorrect").status_code == 401
            checks.append("Incorrect password rejected (401)")
            assert client.get("/api/auth/me").status_code == 401
            assert client.get("/api/auth/me", headers={"Authorization": "Bearer invalid.token.value"}).status_code == 401
            checks.append("Missing/invalid tokens rejected (401)")
    finally:
        app.dependency_overrides.pop(get_db, None)
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only PostgreSQL / authentication integration verification")
    parser.add_argument("--skip-auth", action="store_true", help="Verify schema and data only; no login checks")
    args = parser.parse_args()
    if not settings.database_url.startswith("postgresql+"):
        parser.error("This verifier requires a PostgreSQL DATABASE_URL")
    if settings.owner_email.strip() == "":
        parser.error("OWNER_EMAIL must be configured")
    owner_password = os.getenv("SEED_OWNER_PASSWORD")
    employee_password = os.getenv("SEED_EMPLOYEE_PASSWORD")
    if not args.skip_auth and (not owner_password or not employee_password):
        # dotenv values not added to Pydantic Settings fields, read privately here.
        from dotenv import dotenv_values
        values = dotenv_values(settings.model_config["env_file"])
        owner_password = owner_password or values.get("SEED_OWNER_PASSWORD")
        employee_password = employee_password or values.get("SEED_EMPLOYEE_PASSWORD")
    if not args.skip_auth and (not owner_password or not employee_password):
        parser.error("SEED_OWNER_PASSWORD and SEED_EMPLOYEE_PASSWORD are required for auth verification")
    try:
        engine = get_engine()
        checks = verify_database(engine)
        if not args.skip_auth:
            checks += verify_auth(engine, owner_password, employee_password)
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    for entry in checks:
        print(f"PASS: {entry}")
    print(f"\nPHASE 1 INTEGRATION VERIFICATION PASSED ({len(checks)} checks; read-only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
