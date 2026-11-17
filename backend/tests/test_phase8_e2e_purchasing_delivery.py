"""multi-route purchasing-to-delivery journeys.

All external AI and SMTP transports are mocked. No live provider calls or email.
The fixture creates a fresh database for each test. This is an API integration
suite, not a replacement for dedicated PostgreSQL concurrency tests.
"""
import json
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.db.models import (ApprovalLog, InventoryItem, OrderItem, PurchaseOrder,
                           PurchaseOrderEmailDispatch, StockReceipt, VendorQuotation)
from test_quotation_collection import setup, create_request
from test_order_decisions import set_normal


@pytest.fixture
def journey(setup, monkeypatch):
    monkeypatch.setattr(settings, 'email_mode', 'mock')
    monkeypatch.setattr(settings, 'enable_real_email_dispatch', False)
    monkeypatch.setattr(settings, 'enable_quote_simulation', True)
    return setup


def simulate(journey):
    client, _, owner, _ = journey
    oid = create_request(journey)
    result = client.post(f'/api/purchase-requests/{oid}/simulate-vendor-quote-replies', headers=owner)
    assert result.status_code == 200, result.text
    assert result.json()['collection']['collection_state'] == 'READY_FOR_COMPARISON'
    assert result.json()['new_simulated_replies'] == 2
    comparison = client.get(f'/api/purchase-requests/{oid}/compare-quotations', headers=owner)
    assert comparison.status_code == 200, comparison.text
    assert comparison.json()['eligible_count'] == 2
    assert all(q['is_simulated'] for q in comparison.json()['quotations'])
    return oid, comparison.json()


def fake_ai(client, owner, oid, quote_id, monkeypatch):
    from app.core import claude_agent
    def fake_messages(payload):
        return {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': json.dumps({
            'recommended_quotation_id': quote_id,
            'reason': 'Fresh eligible quotation with a competitive total and delivery time.'
        })}]}
    monkeypatch.setattr(claude_agent, 'call_messages', fake_messages)
    result = client.post(f'/api/purchase-requests/{oid}/ai-recommendation', headers=owner)
    assert result.status_code == 200, result.text
    assert result.json()['authorization_status'] == 'NOT_AUTHORIZED_BY_AI'
    return result.json()


def test_mark_delivered_reconciles_partial_receipt_and_step3_tracking(journey):
    client, factory, owner, employee = journey
    oid, _ = simulate(journey)
    base = f'/api/purchase-requests/{oid}'
    assert client.post(base + '/mock-delivery', headers=owner).status_code == 409
    set_normal(factory)
    assert client.post(base + '/evaluate', headers=owner, json={}).json()['status'] == 'PLACED'
    with factory() as db:
        stock = db.get(InventoryItem, 1).on_hand
    assert client.post(base + '/mock-delivery', headers=owner).status_code == 409
    assert client.post(base + '/mock-transit', headers=owner).json()['status'] == 'IN_TRANSIT'
    with factory.begin() as db:
        order = db.get(PurchaseOrder, oid)
        order.mock_delivered_at = order.mock_in_transit_at  # Step 3 tracking-only record
    listing = client.get('/api/purchase-requests', headers=owner).json()['items'][0]
    assert listing['tracking_status'] == 'IN_TRANSIT'
    qty = listing['ordered_quantity']
    assert qty > 1
    partial = client.post(base + '/receive-stock', headers=owner, json={
        'quantity': 1, 'idempotency_key': 'partial-before-delivery',
    })
    assert partial.status_code == 200 and partial.json()['on_hand'] == stock + 1
    assert client.post(base + '/mock-delivery', headers=employee).status_code == 403
    received = client.post(base + '/mock-delivery', headers=owner)
    assert received.status_code == 200, received.text
    assert received.json()['quantity_received'] == qty - 1
    assert received.json()['on_hand'] == stock + qty
    assert received.json()['order_status'] == 'RECEIVED'
    replay = client.post(base + '/mock-delivery', headers=owner)
    assert replay.json()['replayed'] and replay.json()['receipt_id'] == received.json()['receipt_id']
    assert client.post(base + '/mock-transit', headers=owner).status_code == 409
    details = client.get('/api/purchase-requests', headers=owner).json()['items'][0]
    assert details['tracking_status'] == 'RECEIVED' and details['received_quantity'] == qty
    assert client.get('/api/inventory/1', headers=owner).json()['on_hand'] == stock + qty
    with factory() as db:
        order = db.get(PurchaseOrder, oid)
        assert order.completed_at is not None and order.mock_delivered_at is not None
        line = db.scalar(select(OrderItem).where(OrderItem.purchase_order_id == oid))
        assert not line.is_open and line.received_quantity == qty
        assert db.scalar(select(func.count()).select_from(StockReceipt)) == 2


def test_mark_delivered_rolls_back_stock_receipt_and_status_together(journey, monkeypatch):
    from app.core import stock_receipts

    client, factory, owner, _ = journey
    oid, _ = simulate(journey)
    base = f'/api/purchase-requests/{oid}'
    set_normal(factory)
    assert client.post(base + '/evaluate', headers=owner, json={}).json()['status'] == 'PLACED'
    assert client.post(base + '/mock-transit', headers=owner).status_code == 200
    with factory() as db:
        stock = db.get(InventoryItem, 1).on_hand

    def fail_after_writes(*args, **kwargs):
        raise RuntimeError('test rollback after staged inventory and receipt writes')

    with monkeypatch.context() as patcher:
        patcher.setattr(stock_receipts, 'calculate_forecast', fail_after_writes)
        with pytest.raises(RuntimeError, match='test rollback'):
            client.post(base + '/mock-delivery', headers=owner)
    with factory() as db:
        assert db.get(InventoryItem, 1).on_hand == stock
        order = db.get(PurchaseOrder, oid)
        assert order.status == 'PLACED' and order.completed_at is None
        line = db.scalar(select(OrderItem).where(OrderItem.purchase_order_id == oid))
        assert line.is_open and line.received_quantity == 0
        assert db.scalar(select(func.count()).select_from(StockReceipt)) == 0
    result = client.post(base + '/mock-delivery', headers=owner)
    assert result.status_code == 200 and result.json()['on_hand'] > stock


def test_simulated_quotes_ai_auto_authorization_mock_dispatch_partial_full_receipt(journey, monkeypatch):
    client, factory, owner, employee = journey
    oid, comparison = simulate(journey)
    set_normal(factory)
    chosen = comparison['lowest_cost_quotation_id']
    fake_ai(client, owner, oid, chosen, monkeypatch)
    with factory() as db:
        po = db.get(PurchaseOrder, oid)
        assert po.status == 'WAITING_FOR_QUOTES' and po.vendor_id is None
        assert po.ai_recommended_quotation_id == chosen
        before_stock = db.get(InventoryItem, 1).on_hand
    result = client.post(f'/api/purchase-requests/{oid}/evaluate', headers=owner, json={})
    assert result.status_code == 200, result.text
    assert result.json()['outcome'] == 'AUTO_AUTHORIZED'
    assert result.json()['status'] == 'PLACED'
    with patch('app.core.order_email_dispatch.send_smtp', side_effect=AssertionError('SMTP forbidden')):
        sent = client.post(f'/api/purchase-requests/{oid}/dispatch-order-email', headers=owner)
        replay = client.post(f'/api/purchase-requests/{oid}/dispatch-order-email', headers=owner)
    assert sent.status_code == 200 and sent.json()['dispatch_status'] == 'MOCKED'
    assert replay.status_code == 200 and replay.json()['replayed']
    assert not sent.json()['smtp_accepted']
    with factory() as db:
        qty = db.scalar(select(OrderItem).where(OrderItem.purchase_order_id == oid)).ordered_quantity
        assert qty > 1
        assert db.get(InventoryItem, 1).on_hand == before_stock
        assert db.scalar(select(func.count()).select_from(PurchaseOrderEmailDispatch)) == 1
    first = client.post(f'/api/purchase-requests/{oid}/receive-stock', headers=owner,
                        json={'quantity': 1, 'idempotency_key': 'p8s3-partial'})
    assert first.status_code == 200, first.text
    assert first.json()['order_status'] == 'PARTIALLY_RECEIVED'
    assert first.json()['on_order'] == qty - 1
    repeat = client.post(f'/api/purchase-requests/{oid}/receive-stock', headers=owner,
                         json={'quantity': 1, 'idempotency_key': 'p8s3-partial'})
    assert repeat.status_code == 200 and repeat.json()['replayed']
    final = client.post(f'/api/purchase-requests/{oid}/receive-stock', headers=owner,
                        json={'quantity': qty - 1, 'idempotency_key': 'p8s3-final'})
    assert final.status_code == 200, final.text
    assert final.json()['order_status'] == 'RECEIVED'
    delivered_again = client.post(f'/api/purchase-requests/{oid}/mock-delivery', headers=owner)
    assert delivered_again.status_code == 200
    assert delivered_again.json()['replayed'] and delivered_again.json()['receipt_id'] == final.json()['receipt_id']
    assert final.json()['on_order'] == 0 and final.json()['actual_lead_time_days'] is not None
    with factory() as db:
        assert db.get(InventoryItem, 1).on_hand == before_stock + qty
        assert db.scalar(select(func.count()).select_from(StockReceipt)) == 2
        assert db.get(PurchaseOrder, oid).status == 'RECEIVED'
    assert client.post(f'/api/purchase-requests/{oid}/receive-stock', headers=employee,
                       json={'quantity': 1, 'idempotency_key': 'employee-forbidden'}).status_code == 403


def test_high_value_owner_approval_alternative_quote_and_mock_order(journey):
    client, factory, owner, employee = journey
    oid, comparison = simulate(journey)
    set_normal(factory)
    with factory.begin() as db:
        # Simulated prices remain labeled, but a high total must not auto-authorize.
        for q in db.scalars(select(VendorQuotation).where(VendorQuotation.purchase_order_id == oid)):
            q.quoted_unit_price = Decimal('1000.00') if q.vendor_id == 1 else Decimal('1100.00')
    updated = client.get(f'/api/purchase-requests/{oid}/compare-quotations', headers=owner)
    assert updated.status_code == 200
    choices = updated.json()['quotations']
    assert len(choices) == 2
    evaluate = client.post(f'/api/purchase-requests/{oid}/evaluate', headers=owner, json={})
    assert evaluate.status_code == 200, evaluate.text
    assert evaluate.json()['outcome'] == 'OWNER_APPROVAL_REQUIRED'
    assert 'ORDER_EXCEEDS_AUTO_ORDER_LIMIT' in evaluate.json()['failure_reasons']
    assert client.post(f'/api/purchase-requests/{oid}/dispatch-order-email', headers=owner).status_code == 409
    alternative = next(q for q in choices if q['quotation_id'] != updated.json()['lowest_cost_quotation_id'])
    approve = client.post(f'/api/purchase-requests/{oid}/owner-decision', headers=owner,
                          json={'action': 'APPROVED', 'quotation_id': alternative['quotation_id'], 'comments': 'Owner chose alternate vendor'})
    assert approve.status_code == 200, approve.text
    assert approve.json()['status'] == 'PLACED'
    with factory() as db:
        po = db.get(PurchaseOrder, oid)
        assert po.vendor_id == alternative['vendor_id']
        assert po.total_amount == Decimal(str(alternative['total_quoted_cost']))
        assert db.scalar(select(ApprovalLog)).action == 'APPROVED'
    mocked = client.post(f'/api/purchase-requests/{oid}/dispatch-order-email', headers=owner)
    assert mocked.status_code == 200 and mocked.json()['dispatch_status'] == 'MOCKED'
    assert mocked.json()['selected_quotation_id'] == alternative['quotation_id']


def test_simulated_quote_never_goes_to_live_smtp(journey, monkeypatch):
    client, factory, owner, employee = journey
    oid, _ = simulate(journey)
    set_normal(factory)
    assert client.post(f'/api/purchase-requests/{oid}/evaluate', headers=owner, json={}).status_code == 200
    monkeypatch.setattr(settings, 'email_mode', 'smtp')
    monkeypatch.setattr(settings, 'enable_real_email_dispatch', True)
    with patch('app.core.order_email_dispatch.send_smtp', side_effect=AssertionError('SMTP must not run')):
        blocked = client.post(f'/api/purchase-requests/{oid}/dispatch-order-email', headers=owner)
    assert blocked.status_code == 409
    with factory() as db:
        assert db.scalar(select(PurchaseOrderEmailDispatch)) is None


def test_ai_unavailable_does_not_prevent_backend_authorization(journey, monkeypatch):
    from app.core import claude_agent
    client, factory, owner, employee = journey
    oid, _ = simulate(journey)
    set_normal(factory)
    monkeypatch.setattr(claude_agent, 'call_messages', lambda payload: (_ for _ in ()).throw(claude_agent.AgentUnavailable('offline')))
    unavailable = client.post(f'/api/purchase-requests/{oid}/ai-recommendation', headers=owner)
    assert unavailable.status_code == 503
    result = client.post(f'/api/purchase-requests/{oid}/evaluate', headers=owner, json={})
    assert result.status_code == 200 and result.json()['outcome'] == 'AUTO_AUTHORIZED'
    with factory() as db:
        po = db.get(PurchaseOrder, oid)
        assert po.status == 'PLACED' and po.ai_recommended_quotation_id is None


def test_rejection_and_employee_denial_never_send_or_receive(journey):
    client, factory, owner, employee = journey
    oid, _ = simulate(journey)
    with factory.begin() as db:
        db.get(InventoryItem, 1).low_confidence = True
    assert client.post(f'/api/purchase-requests/{oid}/evaluate', headers=employee, json={}).status_code == 403
    evaluated = client.post(f'/api/purchase-requests/{oid}/evaluate', headers=owner, json={})
    assert evaluated.status_code == 200 and evaluated.json()['status'] == 'PENDING_APPROVAL'
    assert client.post(f'/api/purchase-requests/{oid}/owner-decision', headers=employee,
                       json={'action': 'APPROVED'}).status_code == 403
    rejected = client.post(f'/api/purchase-requests/{oid}/owner-decision', headers=owner,
                           json={'action': 'REJECTED', 'comments': 'Not approved'})
    assert rejected.status_code == 200 and rejected.json()['status'] == 'REJECTED'
    assert client.post(f'/api/purchase-requests/{oid}/dispatch-order-email', headers=owner).status_code == 409
    assert client.post(f'/api/purchase-requests/{oid}/receive-stock', headers=owner,
                       json={'quantity': 1, 'idempotency_key': 'rejected-receipt'}).status_code == 409
    with factory() as db:
        assert db.scalar(select(PurchaseOrderEmailDispatch)) is None
        assert db.scalar(select(StockReceipt)) is None
