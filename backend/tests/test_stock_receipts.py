"""Physical stock receipt tests. No real SMTP or AI network calls."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.db.models import AIThresholdLog, InventoryItem, OrderItem, PurchaseOrder, StockReceipt
from test_purchase_order_email_dispatch import po_setup, make_placed
from test_quotation_collection import setup


def url(oid):
    return f'/api/purchase-requests/{oid}/receive-stock'


def payload(quantity, key='receipt-1'):
    return {'quantity': quantity, 'idempotency_key': key}


def test_partial_then_full_reconciles_all_and_logs(po_setup):
    client, factory, owner, employee = po_setup
    oid = make_placed(po_setup)
    with factory() as db:
        line = db.scalar(select(OrderItem).where(OrderItem.purchase_order_id == oid))
        qty = line.ordered_quantity
        assert qty > 1
        initial_on_hand = db.get(InventoryItem, 1).on_hand
        old_logs = db.scalar(select(func.count()).select_from(AIThresholdLog))
    first = client.post(url(oid), json=payload(1), headers=owner)
    assert first.status_code == 200, first.text
    data = first.json()
    assert data['order_status'] == 'PARTIALLY_RECEIVED'
    assert data['on_hand'] == initial_on_hand + 1
    assert data['on_order'] == qty - 1
    assert data['first_received_at'] and not data['completed_at']
    assert data['actual_lead_time_days'] is None
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(AIThresholdLog)) == old_logs + 1
    second = client.post(url(oid), json=payload(qty - 1, 'receipt-2'), headers=owner)
    assert second.status_code == 200, second.text
    final = second.json()
    assert final['order_status'] == 'RECEIVED'
    assert final['on_hand'] == initial_on_hand + qty
    assert final['on_order'] == 0
    assert final['received_quantity'] == qty
    assert final['completed_at'] and final['actual_lead_time_days'] is not None
    with factory() as db:
        assert db.get(PurchaseOrder, oid).first_received_at is not None
        assert db.scalar(select(OrderItem).where(OrderItem.purchase_order_id == oid)).is_open is False
        assert db.scalar(select(func.count()).select_from(AIThresholdLog)) == old_logs + 2
        assert db.scalar(select(func.count()).select_from(StockReceipt)) == 2
    history = client.get(f'/api/purchase-requests/{oid}/stock-receipts', headers=owner)
    assert history.status_code == 200 and len(history.json()['receipts']) == 2


def test_replay_preserves_stock_and_logs_after_completion(po_setup):
    client, factory, owner, employee = po_setup
    oid = make_placed(po_setup)
    with factory() as db:
        qty = db.scalar(select(OrderItem).where(OrderItem.purchase_order_id == oid)).ordered_quantity
    first = client.post(url(oid), json=payload(qty), headers=owner)
    assert first.status_code == 200
    with factory() as db:
        on_hand = db.get(InventoryItem, 1).on_hand
        logs = db.scalar(select(func.count()).select_from(AIThresholdLog))
    again = client.post(url(oid), json=payload(qty), headers=owner)
    assert again.status_code == 200 and again.json()['replayed'] is True
    with factory() as db:
        assert db.get(InventoryItem, 1).on_hand == on_hand
        assert db.scalar(select(func.count()).select_from(AIThresholdLog)) == logs
        assert db.scalar(select(func.count()).select_from(StockReceipt)) == 1


def test_same_key_different_payload_is_rejected(po_setup):
    client, factory, owner, employee = po_setup
    oid = make_placed(po_setup)
    assert client.post(url(oid), json=payload(1), headers=owner).status_code == 200
    response = client.post(url(oid), json=payload(2), headers=owner)
    assert response.status_code == 409 and 'Idempotency' in response.text


def test_excess_qty_and_zero_qty_no_mutation(po_setup):
    client, factory, owner, employee = po_setup
    oid = make_placed(po_setup)
    with factory() as db:
        qty = db.scalar(select(OrderItem).where(OrderItem.purchase_order_id == oid)).ordered_quantity
        initial = db.get(InventoryItem, 1).on_hand
    assert client.post(url(oid), json=payload(0), headers=owner).status_code == 422
    assert client.post(url(oid), json=payload(-1), headers=owner).status_code == 422
    assert client.post(url(oid), json=payload(qty + 1), headers=owner).status_code == 409
    with factory() as db:
        assert db.get(InventoryItem, 1).on_hand == initial
        assert db.scalar(select(StockReceipt)) is None


def test_only_authorized_placed_orders_can_receive(po_setup):
    client, factory, owner, employee = po_setup
    oid = make_placed(po_setup)
    with factory.begin() as db:
        db.get(PurchaseOrder, oid).status = 'REJECTED'
    assert client.post(url(oid), json=payload(1), headers=owner).status_code == 409
    with factory() as db:
        assert db.scalar(select(StockReceipt)) is None


def test_owner_required_and_missing_order(po_setup):
    client, factory, owner, employee = po_setup
    oid = make_placed(po_setup)
    assert client.post(url(oid), json=payload(1)).status_code == 401
    assert client.post(url(oid), json=payload(1), headers=employee).status_code == 403
    assert client.get(f'/api/purchase-requests/{oid}/stock-receipts', headers=employee).status_code == 403
    assert client.post(url(999999), json=payload(1), headers=owner).status_code == 409
    assert client.get('/api/purchase-requests/999999/stock-receipts', headers=owner).status_code == 404


def test_lead_time_uses_placed_to_completed_not_first_delivery(po_setup):
    client, factory, owner, employee = po_setup
    oid = make_placed(po_setup)
    with factory.begin() as db:
        db.get(PurchaseOrder, oid).placed_at = datetime.now(timezone.utc) - timedelta(days=3)
    with factory() as db:
        qty = db.scalar(select(OrderItem).where(OrderItem.purchase_order_id == oid)).ordered_quantity
    assert client.post(url(oid), json=payload(1), headers=owner).status_code == 200
    completed = client.post(url(oid), json=payload(qty - 1, 'receipt-last'), headers=owner)
    assert completed.status_code == 200
    assert 2.99 <= completed.json()['actual_lead_time_days'] <= 3.01


def test_repeated_full_receipt_with_new_key_is_blocked(po_setup):
    client, factory, owner, employee = po_setup
    oid = make_placed(po_setup)
    with factory() as db:
        qty = db.scalar(select(OrderItem).where(OrderItem.purchase_order_id == oid)).ordered_quantity
    assert client.post(url(oid), json=payload(qty), headers=owner).status_code == 200
    assert client.post(url(oid), json=payload(1, 'new-key'), headers=owner).status_code == 409


def test_forecast_on_order_reduction_after_partial_receipt(po_setup):
    client, factory, owner, employee = po_setup
    oid = make_placed(po_setup)
    initial = client.get('/api/inventory/1/forecast', headers=owner)
    assert initial.status_code == 200
    before = initial.json()
    assert before['on_order'] > 1
    assert client.post(url(oid), json=payload(1), headers=owner).status_code == 200
    after = client.get('/api/inventory/1/forecast', headers=owner).json()
    assert after['on_order'] == before['on_order'] - 1
    assert after['on_hand'] == before['on_hand'] + 1
    assert after['inventory_position'] == before['inventory_position']


def test_failed_forecast_rolls_back_receipt_and_stock(po_setup, monkeypatch):
    from app.core import stock_receipts
    client, factory, owner, employee = po_setup
    oid = make_placed(po_setup)
    with factory() as db:
        initial = db.get(InventoryItem, 1).on_hand
    def broken_forecast(*args, **kwargs):
        raise RuntimeError('forecast unavailable during receipt')
    monkeypatch.setattr(stock_receipts, 'calculate_forecast', broken_forecast)
    with factory() as db:
        with pytest.raises(RuntimeError):
            stock_receipts.record_receipt(db, order_id=oid, quantity=1,
                                          idempotency_key='rollback-test', owner_id=1)
    with factory() as db:
        assert db.get(InventoryItem, 1).on_hand == initial
        assert db.scalar(select(StockReceipt)) is None
        assert db.get(PurchaseOrder, oid).status == 'PLACED'
        assert db.scalar(select(OrderItem).where(OrderItem.purchase_order_id == oid)).received_quantity == 0


def test_partial_receipt_cannot_exceed_remaining(po_setup):
    client, factory, owner, employee = po_setup
    oid = make_placed(po_setup)
    with factory() as db:
        qty = db.scalar(select(OrderItem).where(OrderItem.purchase_order_id == oid)).ordered_quantity
    assert client.post(url(oid), json=payload(1), headers=owner).status_code == 200
    response = client.post(url(oid), json=payload(qty, 'too-many'), headers=owner)
    assert response.status_code == 409
    with factory() as db:
        assert db.scalar(select(OrderItem).where(OrderItem.purchase_order_id == oid)).received_quantity == 1
        assert db.scalar(select(func.count()).select_from(StockReceipt)) == 1
