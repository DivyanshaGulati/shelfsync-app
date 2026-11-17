"""safety-critical transitions."""
from decimal import Decimal
from sqlalchemy import select
from test_quotation_collection import setup
from test_quote_comparison import quote_twice
from app.core.config import settings
from app.db.models import PurchaseOrder, VendorQuotation, VendorItem, ApprovalLog, InventoryItem, OrderItem, Vendor


def set_normal(factory):
    with factory.begin() as db:
        item = db.get(InventoryItem, 1)
        item.low_confidence = False
        item.avg_daily_demand = Decimal('5')
        item.avg_lead_time_days = Decimal('3')
        for vendor in db.scalars(select(Vendor)):
            vendor.is_new_vendor = False
            vendor.is_slow = False


def test_auto_authorization_fresh_price_and_no_email(setup):
    client, factory, owner, employee = setup
    oid, quotes = quote_twice(setup)
    set_normal(factory)
    r = client.post(f'/api/purchase-requests/{oid}/evaluate', headers=owner, json={})
    assert r.status_code == 200, r.text
    assert r.json()['outcome'] == 'AUTO_AUTHORIZED'
    assert r.json()['dispatch_status'].startswith('NOT_SENT')
    with factory() as db:
        po = db.get(PurchaseOrder, oid)
        assert po.status == 'PLACED' and po.vendor_id is not None
        assert po.total_amount == Decimal('74')
        line = db.scalar(select(OrderItem).where(OrderItem.purchase_order_id == oid))
        assert line.unit_price == Decimal('18.50') and line.total_price == Decimal('74')
        selected = db.scalar(select(VendorQuotation).where(VendorQuotation.status == 'SELECTED'))
        assert selected.id == po.backend_candidate_quotation_id
        m = db.scalar(select(VendorItem).where(VendorItem.vendor_id == selected.vendor_id))
        assert m.last_known_unit_price == Decimal('18.50')
    assert client.post(f'/api/purchase-requests/{oid}/evaluate', headers=owner, json={}).status_code == 409


def test_price_spike_requires_owner_approval_then_alternative_quote(setup):
    client, factory, owner, employee = setup
    oid, quotes = quote_twice(setup, (('25.00', '2'), ('19.00', '3')))
    set_normal(factory)
    # explicitly inspect expensive quote to require approval (the default cheap quote passes)
    candidate = next(q for q in quotes if q['vendor_name'] == 'Bharat')
    response = client.post(f'/api/purchase-requests/{oid}/evaluate', headers=owner,
                           json={'quotation_id': candidate['id']})
    assert response.status_code == 200, response.text
    assert response.json()['outcome'] == 'OWNER_APPROVAL_REQUIRED'
    assert 'PRICE_INCREASE_OVER_10_PERCENT' in response.json()['failure_reasons']
    with factory() as db:
        po = db.get(PurchaseOrder, oid)
        assert po.status == 'PENDING_APPROVAL' and po.vendor_id is None
        assert po.backend_candidate_quotation_id == candidate['id']
    alternative = next(q for q in quotes if q['id'] != candidate['id'])
    comparison = client.get(f'/api/purchase-requests/{oid}/compare-quotations', headers=owner)
    assert comparison.status_code == 200, comparison.text
    assert {q['quotation_id'] for q in comparison.json()['quotations'] if q['eligible_for_comparison']} == {
        candidate['id'], alternative['id'],
    }
    assert client.post(f'/api/purchase-requests/{oid}/ai-recommendation', headers=owner).status_code == 409
    approval = client.post(f'/api/purchase-requests/{oid}/owner-decision', headers=owner, json={
        'action': 'APPROVED', 'quotation_id': alternative['id'], 'comments': 'Selected alternative vendor'})
    assert approval.status_code == 200, approval.text
    assert approval.json()['status'] == 'PLACED'
    with factory() as db:
        po = db.get(PurchaseOrder, oid)
        assert po.vendor_id == alternative['vendor_id'] and po.total_amount == Decimal('76')
        assert db.scalar(select(ApprovalLog)).action == 'APPROVED'
    history = client.get(f'/api/purchase-requests/{oid}/approval-history', headers=owner)
    assert history.status_code == 200 and len(history.json()['approvals']) == 1
    assert client.post(f'/api/purchase-requests/{oid}/owner-decision', headers=owner,
                       json={'action': 'APPROVED'}).status_code == 409


def test_reject_creates_audit_no_stock_change(setup):
    client, factory, owner, employee = setup
    oid, quotes = quote_twice(setup, (('25.00', '2'), ('26.00', '3')))
    set_normal(factory)
    assert client.post(f'/api/purchase-requests/{oid}/evaluate', headers=owner, json={}).status_code == 200
    with factory() as db:
        stock = db.get(InventoryItem, 1).on_hand
    response = client.post(f'/api/purchase-requests/{oid}/owner-decision', headers=owner,
                           json={'action': 'REJECTED', 'comments': 'Too expensive'})
    assert response.status_code == 200
    with factory() as db:
        po = db.get(PurchaseOrder, oid)
        assert po.status == 'REJECTED' and po.vendor_id is None and po.total_amount is None
        assert db.get(InventoryItem, 1).on_hand == stock
        assert db.scalar(select(ApprovalLog)).action == 'REJECTED'


def test_low_confidence_and_high_value_require_approval(setup):
    client, factory, owner, employee = setup
    oid, _ = quote_twice(setup, (('1000.00', '2'), ('1100.00', '3')))
    r = client.post(f'/api/purchase-requests/{oid}/evaluate', headers=owner, json={})
    assert r.status_code == 200
    assert 'LOW_CONFIDENCE_ITEM' in r.json()['failure_reasons']
    assert 'ORDER_EXCEEDS_AUTO_ORDER_LIMIT' in r.json()['failure_reasons']


def test_unfinished_quote_collection_and_wrong_quote_rejected(setup):
    from test_quotation_collection import create_request
    client, factory, owner, employee = setup
    oid = create_request(setup)
    assert client.post(f'/api/purchase-requests/{oid}/evaluate', headers=owner, json={}).status_code == 409
    assert client.post(f'/api/purchase-requests/{oid}/evaluate', headers=owner,
                       json={'quotation_id': 999999}).status_code == 409


def test_employee_forbidden_and_unknown_order(setup):
    client, factory, owner, employee = setup
    oid, _ = quote_twice(setup)
    assert client.post(f'/api/purchase-requests/{oid}/evaluate', headers=employee, json={}).status_code == 403
    assert client.post(f'/api/purchase-requests/{oid}/owner-decision', headers=employee,
                       json={'action': 'APPROVED'}).status_code == 403
    assert client.get(f'/api/purchase-requests/{oid}/approval-history', headers=employee).status_code == 403
    assert client.post('/api/purchase-requests/999999/evaluate', headers=owner, json={}).status_code == 404


def test_missing_reference_and_vendor_flag_require_approval(setup):
    client, factory, owner, employee = setup
    oid, quotes = quote_twice(setup)
    set_normal(factory)
    with factory.begin() as db:
        mapping = db.scalar(select(VendorItem).where(VendorItem.vendor_id == quotes[0]['vendor_id']))
        mapping.last_known_unit_price = None
    r = client.post(f'/api/purchase-requests/{oid}/evaluate', headers=owner,
                    json={'quotation_id': quotes[0]['id']})
    assert r.status_code == 200, r.text
    assert 'MISSING_PRIOR_VENDOR_PRICE' in r.json()['failure_reasons']
