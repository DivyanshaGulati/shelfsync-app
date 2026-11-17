"""Opt-in real PostgreSQL concurrency tests.

Set SHELFSYNC_TEST_DATABASE_URL to an isolated database whose name ends in
_test (for example shelfsync_test). Never point this at the app database.
"""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import pytest
from sqlalchemy import create_engine, text, select, func
from sqlalchemy.orm import sessionmaker

from app.api.checkout import CheckoutRequest, checkout
from app.db.models import InventoryItem, Transaction, User
from app.db.session import Base


@pytest.fixture(scope="module")
def pg_factory():
    url = os.environ.get("SHELFSYNC_TEST_DATABASE_URL", "")
    if not url:
        pytest.skip("Set SHELFSYNC_TEST_DATABASE_URL for PostgreSQL concurrency tests")
    db_name = urlsplit(url).path.strip("/")
    if not db_name.endswith("_test") or not url.startswith("postgresql+"):
        pytest.fail("Refusing to run: test URL must be PostgreSQL and database name must end in _test")
    schema = "checkout_test_" + uuid.uuid4().hex[:12]
    admin = create_engine(url)
    try:
        with admin.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"}, pool_size=5)
        try:
            Base.metadata.create_all(engine)
            factory = sessionmaker(engine, expire_on_commit=False)
            with factory.begin() as db:
                db.add_all([
                    User(name="Employee", email="pgtest@example.com", password_hash="not-used", role="EMPLOYEE"),
                    InventoryItem(name="Concurrency test", sku="TEST-RACE-1", category="Test", unit="piece", unit_cost=1, on_hand=5, reserved_qty=0),
                    InventoryItem(name="Idempotency test", sku="TEST-RACE-2", category="Test", unit="piece", unit_cost=1, on_hand=5, reserved_qty=0),
                ])
            yield factory
        finally:
            engine.dispose()
    finally:
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


def _call(factory, *, item_id, quantity, key):
    with factory() as db:
        user = db.get(User, 1)
        try:
            result = checkout(CheckoutRequest(inventory_item_id=item_id, quantity=quantity, idempotency_key=key), db, user)
            return result.replayed, result.transaction.id
        except Exception as exc:
            from fastapi import HTTPException
            if isinstance(exc, HTTPException):
                return exc.status_code, None
            raise


def test_two_distinct_checkouts_cannot_oversell(pg_factory):
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda args: _call(pg_factory, **args), [
            dict(item_id=1, quantity=4, key="postgres-concurrent-a"),
            dict(item_id=1, quantity=4, key="postgres-concurrent-b"),
        ]))
    assert sorted(str(x[0]) for x in results) == ["409", "False"]
    with pg_factory() as db:
        assert db.get(InventoryItem, 1).on_hand == 1
        assert db.scalar(select(func.count()).select_from(Transaction).where(Transaction.inventory_item_id == 1)) == 1


def test_concurrent_same_key_only_deducts_once(pg_factory):
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: _call(pg_factory, item_id=2, quantity=5, key="postgres-same-key"), range(2)))
    assert sorted(str(x[0]) for x in results) == ["False", "True"]
    assert results[0][1] == results[1][1]
    with pg_factory() as db:
        assert db.get(InventoryItem, 2).on_hand == 0
        assert db.scalar(select(func.count()).select_from(Transaction).where(Transaction.inventory_item_id == 2)) == 1
