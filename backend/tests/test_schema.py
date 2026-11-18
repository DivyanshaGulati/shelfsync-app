"""migration/ORM contract smoke tests (portable SQLite).

Run against PostgreSQL on macOS too: alembic upgrade head; alembic check.
"""
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, configure_mappers

from app.db import models
from app.db.session import Base


@pytest.fixture
def db(tmp_path):
    dbfile = tmp_path / "phase1.sqlite"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{dbfile}")
    # env.py always reads DATABASE_URL from Settings: temporarily override it.
    from app.core.config import settings
    original = settings.database_url
    settings.database_url = f"sqlite:///{dbfile}"
    try:
        command.upgrade(cfg, "head")
    finally:
        settings.database_url = original

    engine = create_engine(f"sqlite:///{dbfile}")
    @event.listens_for(engine, "connect")
    def enable_fk(dbapi_connection, _):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")
    yield engine
    engine.dispose()


def test_model_relationships_are_valid():
    configure_mappers()
    assert len(Base.metadata.tables) == 14


def test_migration_matches_models(db):
    inspector = inspect(db)
    assert set(inspector.get_table_names()) == set(Base.metadata.tables) | {"alembic_version"}
    with db.connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []


def test_multi_vendor_mapping_and_unique_preferred(db):
    with Session(db) as session:
        item = models.InventoryItem(name="Teflon Tape", sku="PL-TEF-01", category="Plumbing", unit="roll")
        v1 = models.Vendor(name="A", email="a@example.com")
        v2 = models.Vendor(name="B", email="b@example.com")
        session.add_all([item, v1, v2]); session.flush()
        session.add_all([
            models.VendorItem(inventory_item_id=item.id, vendor_id=v1.id, is_preferred=True),
            models.VendorItem(inventory_item_id=item.id, vendor_id=v2.id),
        ]); session.commit()
        assert len(item.vendor_items) == 2
        with pytest.raises(IntegrityError):
            session.add(models.VendorItem(inventory_item_id=item.id, vendor_id=v2.id, is_preferred=True))
            session.commit()
        session.rollback()
        with pytest.raises(IntegrityError):
            session.add(models.VendorItem(inventory_item_id=item.id, vendor_id=v1.id))
            session.commit()


def test_duplicate_open_order_blocked_but_closed_order_allowed(db):
    with Session(db) as session:
        item = models.InventoryItem(name="Tape", sku="TAPE", category="Electrical", unit="roll")
        session.add(item); session.flush()
        first = models.PurchaseOrder(inventory_item_id=item.id, status="WAITING_FOR_QUOTES")
        session.add(first); session.commit()
        with pytest.raises(IntegrityError):
            session.add(models.PurchaseOrder(inventory_item_id=item.id, status="WAITING_FOR_QUOTES"))
            session.commit()
        session.rollback()
        first.status = "REJECTED"; session.commit()
        session.add(models.PurchaseOrder(inventory_item_id=item.id, status="WAITING_FOR_QUOTES")); session.commit()


def test_quote_must_match_purchase_item_and_mapped_vendor(db):
    with Session(db) as session:
        item = models.InventoryItem(name="Tape", sku="PL01", category="Plumbing", unit="roll")
        other = models.InventoryItem(name="Wire", sku="EL01", category="Electrical", unit="coil")
        vendor = models.Vendor(name="Vendor", email="vendor@example.com")
        session.add_all([item, other, vendor]); session.flush()
        order = models.PurchaseOrder(inventory_item_id=item.id)
        session.add(order); session.commit()
        with pytest.raises(IntegrityError):
            session.add(models.VendorQuotation(purchase_order_id=order.id, inventory_item_id=item.id, vendor_id=vendor.id))
            session.commit()
        session.rollback()
        session.add(models.VendorItem(inventory_item_id=item.id, vendor_id=vendor.id)); session.commit()
        session.add(models.VendorQuotation(purchase_order_id=order.id, inventory_item_id=other.id, vendor_id=vendor.id))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        session.add(models.VendorQuotation(purchase_order_id=order.id, inventory_item_id=item.id, vendor_id=vendor.id))
        session.commit()


def test_checkout_idempotency_key_is_unique(db):
    with Session(db) as session:
        user = models.User(name="Employee", email="employee@example.com", password_hash="hashed", role="EMPLOYEE")
        item = models.InventoryItem(name="Tape", sku="T-01", category="Electrical", unit="roll")
        session.add_all([user, item]); session.flush()
        session.add(models.Transaction(user_id=user.id, inventory_item_id=item.id, transaction_type="CHECKOUT", quantity=1, idempotency_key="same-key"))
        session.commit()
        with pytest.raises(IntegrityError):
            session.add(models.Transaction(user_id=user.id, inventory_item_id=item.id, transaction_type="CHECKOUT", quantity=1, idempotency_key="same-key"))
            session.commit()


def test_received_quote_requires_fresh_price_and_response(db):
    with Session(db) as session:
        item = models.InventoryItem(name="Tape", sku="PL2", category="Plumbing", unit="roll")
        vendor = models.Vendor(name="A", email="a@example.com")
        session.add_all([item, vendor]); session.flush()
        session.add(models.VendorItem(inventory_item_id=item.id, vendor_id=vendor.id))
        session.commit()
        order = models.PurchaseOrder(inventory_item_id=item.id)
        session.add(order); session.commit()
        quote = models.VendorQuotation(purchase_order_id=order.id, inventory_item_id=item.id, vendor_id=vendor.id, status="RECEIVED")
        session.add(quote)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        quote = models.VendorQuotation(purchase_order_id=order.id, inventory_item_id=item.id, vendor_id=vendor.id, status="RECEIVED", quoted_unit_price=Decimal('18.50'), quoted_lead_time_days=Decimal('2'), responded_at=datetime.now(timezone.utc))
        session.add(quote); session.commit()
