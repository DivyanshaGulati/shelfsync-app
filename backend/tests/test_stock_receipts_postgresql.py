"""Opt-in true PostgreSQL concurrency verification.

Uses the same isolated *_test database and ephemeral schema as checkout tests.
Never run with a production DATABASE_URL.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from sqlalchemy import select, func

from test_checkout_postgresql import pg_factory
from app.core.stock_receipts import record_receipt, ReceiptError
from app.core.mock_purchasing import complete_delivery, simulate_transit
from app.db.models import InventoryItem, OrderItem, PurchaseOrder, StockReceipt, Vendor


def create_placed(factory, item_id):
    with factory.begin() as db:
        db.add(Vendor(name=f'Receipt Vendor {item_id}', email=f'receipt-{item_id}@test.invalid'))
        db.flush()
        vendor = db.scalar(select(Vendor).where(Vendor.email == f'receipt-{item_id}@test.invalid'))
        po = PurchaseOrder(inventory_item_id=item_id, vendor_id=vendor.id,
                           status='PLACED', placed_at=datetime.now(timezone.utc)-timedelta(days=1))
        db.add(po)
        db.flush()
        oid = po.id
        db.add(OrderItem(purchase_order_id=oid, inventory_item_id=item_id,
                         ordered_quantity=5, received_quantity=0, unit_price=1, total_price=5))
        return oid


def receive(factory, oid, qty, key):
    with factory() as db:
        try:
            return record_receipt(db, order_id=oid, quantity=qty,
                                  idempotency_key=key, owner_id=1)
        except ReceiptError:
            return {'error': 'CONFLICT'}


def test_concurrent_receipts_cannot_overreceive(pg_factory):
    oid = create_placed(pg_factory, 1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda args: receive(pg_factory, oid, 4, args), ('race-a', 'race-b')))
    assert len([x for x in results if 'error' not in x]) == 1
    with pg_factory() as db:
        line = db.scalar(select(OrderItem).where(OrderItem.purchase_order_id == oid))
        assert line.received_quantity == 4
        assert db.get(InventoryItem, 1).on_hand == 9
        assert db.scalar(select(func.count()).select_from(StockReceipt).where(StockReceipt.purchase_order_id == oid)) == 1


def test_concurrent_receipt_same_key_replays(pg_factory):
    oid = create_placed(pg_factory, 2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: receive(pg_factory, oid, 5, 'race-same'), range(2)))
    assert sorted([x['replayed'] for x in results]) == [False, True]
    with pg_factory() as db:
        line = db.scalar(select(OrderItem).where(OrderItem.purchase_order_id == oid))
        assert line.received_quantity == 5
        assert db.get(InventoryItem, 2).on_hand == 10
        assert db.scalar(select(func.count()).select_from(StockReceipt).where(StockReceipt.purchase_order_id == oid)) == 1


def test_concurrent_mark_delivered_updates_stock_once(pg_factory):
    with pg_factory.begin() as db:
        item = InventoryItem(sku='TRACK-DELIVERY', name='Tracked delivery', category='Hardware',
                             unit='unit', unit_cost=1, on_hand=6)
        db.add(item)
        db.flush()
        item_id = item.id
    oid = create_placed(pg_factory, item_id)
    with pg_factory() as db:
        assert simulate_transit(db, oid)['status'] == 'IN_TRANSIT'
    barrier = Barrier(2)

    def complete():
        with pg_factory() as db:
            barrier.wait(timeout=10)
            return complete_delivery(db, oid, owner_id=1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(complete), pool.submit(complete)
        results = [first.result(timeout=20), second.result(timeout=20)]
    assert sorted(result['replayed'] for result in results) == [False, True]
    assert len({result['receipt_id'] for result in results}) == 1
    with pg_factory() as db:
        order = db.get(PurchaseOrder, oid)
        line = db.scalar(select(OrderItem).where(OrderItem.purchase_order_id == oid))
        assert order.status == 'RECEIVED' and not line.is_open and line.received_quantity == 5
        assert db.get(InventoryItem, item_id).on_hand == 11
        assert db.scalar(select(func.count()).select_from(StockReceipt).where(StockReceipt.purchase_order_id == oid)) == 1
