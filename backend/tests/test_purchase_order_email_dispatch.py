"""final PO email dispatch safety tests; SMTP is always mocked."""
import pytest
from decimal import Decimal
from sqlalchemy import select

from app.core.config import settings
from app.core import order_email_dispatch
from app.db.models import (PurchaseOrder, PurchaseOrderEmailDispatch,
                           VendorQuotation, Vendor, InventoryItem, ApprovalLog)
from test_quotation_collection import setup, create_request
from test_quote_comparison import quote_twice
from test_order_decisions import set_normal


@pytest.fixture
def po_setup(setup, monkeypatch):
    monkeypatch.setattr(settings, 'email_mode', 'mock')
    monkeypatch.setattr(settings, 'enable_real_email_dispatch', False)
    monkeypatch.setattr(settings, 'email_host', '')
    monkeypatch.setattr(settings, 'email_username', '')
    monkeypatch.setattr(settings, 'email_password', '')
    monkeypatch.setattr(settings, 'email_port', 587)
    return setup


def dispatch_url(oid):
    return f'/api/purchase-requests/{oid}/dispatch-order-email'


def make_placed(data, *, force_owner=False):
    client, factory, owner, employee = data
    oid, quotes = quote_twice(data)
    if not force_owner:
        set_normal(factory)
    resp = client.post(f'/api/purchase-requests/{oid}/evaluate', json={}, headers=owner)
    assert resp.status_code == 200, resp.text
    if resp.json()['status'] == 'PENDING_APPROVAL':
        approval = client.post(f'/api/purchase-requests/{oid}/owner-decision',
                               headers=owner, json={'action': 'APPROVED', 'comments': 'Approved'})
        assert approval.status_code == 200, approval.text
    return oid


def enable_smtp(data, monkeypatch):
    _, factory, _, _ = data
    with factory.begin() as db:
        for vendor in db.scalars(select(Vendor)):
            vendor.email = vendor.email.replace('@example.com', '@verified-supplier.com')
    monkeypatch.setattr(settings, 'email_mode', 'smtp')
    monkeypatch.setattr(settings, 'enable_real_email_dispatch', True)
    monkeypatch.setattr(settings, 'email_host', 'smtp.titan.email')
    monkeypatch.setattr(settings, 'email_port', 587)
    monkeypatch.setattr(settings, 'email_username', 'orders@verified-business.com')
    monkeypatch.setattr(settings, 'email_password', 'synthetic-password')


def test_mock_dispatch_safe_and_replay(po_setup, monkeypatch):
    client, factory, owner, employee = po_setup
    oid = make_placed(po_setup)
    monkeypatch.setattr(order_email_dispatch, 'send_smtp',
                        lambda **kw: pytest.fail('mock must never send'))
    first = client.post(dispatch_url(oid), headers=owner)
    assert first.status_code == 200, first.text
    assert first.json()['dispatch_status'] == 'MOCKED'
    assert not first.json()['smtp_accepted'] and not first.json()['replayed']
    second = client.post(dispatch_url(oid), headers=owner)
    assert second.status_code == 200 and second.json()['replayed']
    history = client.get(f'/api/purchase-requests/{oid}/order-email-dispatch', headers=owner)
    assert history.status_code == 200 and history.json()['dispatch_status'] == 'MOCKED'
    with factory() as db:
        order = db.get(PurchaseOrder, oid)
        assert order.status == 'PLACED' and order.total_amount == Decimal('74.00')
        assert db.get(InventoryItem, 1).on_hand >= 0
        assert len(list(db.scalars(select(PurchaseOrderEmailDispatch)))) == 1
        assert db.scalar(select(PurchaseOrderEmailDispatch)).quotation_id == order.backend_candidate_quotation_id


def test_no_dispatch_before_authorization_or_after_rejection(po_setup):
    client, factory, owner, employee = po_setup
    oid, quotes = quote_twice(po_setup, (('25.00','2'),('26.00','3')))
    assert client.post(dispatch_url(oid), headers=owner).status_code == 409
    evaluated = client.post(f'/api/purchase-requests/{oid}/evaluate', json={}, headers=owner)
    assert evaluated.status_code == 200 and evaluated.json()['status'] == 'PENDING_APPROVAL'
    assert client.post(dispatch_url(oid), headers=owner).status_code == 409
    rejected = client.post(f'/api/purchase-requests/{oid}/owner-decision',
                           json={'action':'REJECTED'}, headers=owner)
    assert rejected.status_code == 200
    assert client.post(dispatch_url(oid), headers=owner).status_code == 409
    with factory() as db:
        assert db.scalar(select(PurchaseOrderEmailDispatch)) is None


def test_owner_approved_selected_alternative_can_send_mock(po_setup):
    client, factory, owner, employee = po_setup
    oid, quotes = quote_twice(po_setup, (('25.00','2'),('26.00','3')))
    assert client.post(f'/api/purchase-requests/{oid}/evaluate', json={}, headers=owner).status_code == 200
    selected = quotes[1]['id']
    approved = client.post(f'/api/purchase-requests/{oid}/owner-decision',
                           json={'action':'APPROVED','quotation_id':selected}, headers=owner)
    assert approved.status_code == 200, approved.text
    result = client.post(dispatch_url(oid), headers=owner)
    assert result.status_code == 200, result.text
    assert result.json()['selected_quotation_id'] == selected
    with factory() as db:
        assert db.scalar(select(PurchaseOrderEmailDispatch)).quotation_id == selected
        assert db.scalar(select(ApprovalLog)).action == 'APPROVED'


def test_owner_only_and_missing_order(po_setup):
    client, factory, owner, employee = po_setup
    oid = make_placed(po_setup)
    assert client.post(dispatch_url(oid)).status_code == 401
    assert client.post(dispatch_url(oid), headers=employee).status_code == 403
    assert client.get(f'/api/purchase-requests/{oid}/order-email-dispatch', headers=employee).status_code == 403
    assert client.post(dispatch_url(999999), headers=owner).status_code == 409
    assert client.get('/api/purchase-requests/999999/order-email-dispatch', headers=owner).status_code == 404
    with factory() as db:
        assert db.scalar(select(PurchaseOrderEmailDispatch)) is None


def test_smtp_requires_explicit_optin_and_real_vendor_email(po_setup, monkeypatch):
    client, factory, owner, employee = po_setup
    oid = make_placed(po_setup)
    monkeypatch.setattr(settings,'email_mode','smtp')
    assert client.post(dispatch_url(oid), headers=owner).status_code == 409
    monkeypatch.setattr(settings,'enable_real_email_dispatch',True)
    monkeypatch.setattr(settings,'email_host','smtp.titan.email')
    monkeypatch.setattr(settings,'email_username','orders@verified-business.com')
    monkeypatch.setattr(settings,'email_password','synthetic-password')
    response=client.post(dispatch_url(oid),headers=owner)
    assert response.status_code == 409 and 'demo-only' in response.text
    with factory() as db:
        assert db.scalar(select(PurchaseOrderEmailDispatch)) is None


def test_authorized_genuine_smtp_uses_fresh_selected_price_and_is_idempotent(po_setup, monkeypatch):
    client, factory, owner, employee = po_setup
    oid = make_placed(po_setup)
    enable_smtp(po_setup, monkeypatch)
    sent=[]
    monkeypatch.setattr(order_email_dispatch,'send_smtp', lambda **kw: sent.append(kw))
    response=client.post(dispatch_url(oid),headers=owner)
    assert response.status_code == 200, response.text
    assert response.json()['dispatch_status'] == 'SENT' and response.json()['smtp_accepted']
    assert len(sent) == 1
    assert 'INR 18.50' in sent[0]['body'] and 'INR 74.00' in sent[0]['body']
    assert sent[0]['to'].endswith('@verified-supplier.com')
    assert 'PO-' in sent[0]['subject']
    again=client.post(dispatch_url(oid),headers=owner)
    assert again.status_code == 200 and again.json()['replayed']
    assert len(sent) == 1
    with factory() as db:
        assert db.scalar(select(PurchaseOrderEmailDispatch)).status == 'SENT'


def test_smtp_failure_uncertain_and_never_retries(po_setup, monkeypatch):
    client, factory, owner, employee = po_setup
    oid = make_placed(po_setup)
    enable_smtp(po_setup, monkeypatch)
    calls=[]
    def fail(**kw):
        calls.append(kw)
        raise TimeoutError('simulated network timeout')
    monkeypatch.setattr(order_email_dispatch,'send_smtp',fail)
    first=client.post(dispatch_url(oid),headers=owner)
    assert first.status_code == 200 and first.json()['dispatch_status'] == 'UNCERTAIN'
    assert first.json()['manual_reconciliation_required']
    second=client.post(dispatch_url(oid),headers=owner)
    assert second.status_code == 200 and second.json()['replayed']
    assert len(calls) == 1
    with factory() as db:
        assert db.scalar(select(PurchaseOrderEmailDispatch)).error_code == 'SMTP_OUTCOME_UNKNOWN'


def test_mock_then_smtp_upgrade_only_for_genuine_quote(po_setup, monkeypatch):
    client, factory, owner, employee = po_setup
    oid = make_placed(po_setup)
    assert client.post(dispatch_url(oid),headers=owner).status_code == 200
    enable_smtp(po_setup,monkeypatch)
    calls=[]
    monkeypatch.setattr(order_email_dispatch,'send_smtp',lambda **kw:calls.append(kw))
    response=client.post(dispatch_url(oid),headers=owner)
    assert response.status_code == 200 and response.json()['dispatch_status'] == 'SENT'
    assert len(calls) == 1
    with factory() as db:
        assert len(list(db.scalars(select(PurchaseOrderEmailDispatch)))) == 1


def test_simulated_quote_never_goes_to_real_smtp(po_setup, monkeypatch):
    client, factory, owner, employee = po_setup
    oid = make_placed(po_setup)
    with factory.begin() as db:
        q=db.scalar(select(VendorQuotation).where(VendorQuotation.status=='SELECTED'))
        q.is_simulated=True
    mocked=client.post(dispatch_url(oid),headers=owner)
    assert mocked.status_code == 200 and mocked.json()['dispatch_status'] == 'MOCKED'
    enable_smtp(po_setup,monkeypatch)
    called=[]
    monkeypatch.setattr(order_email_dispatch,'send_smtp',lambda **kw:called.append(kw))
    response=client.post(dispatch_url(oid),headers=owner)
    assert response.status_code == 409
    assert 'Simulated quotations' in response.text
    assert called == []
    with factory() as db:
        assert db.scalar(select(PurchaseOrderEmailDispatch)).status == 'MOCKED'


def test_tampered_total_and_vendor_block_send(po_setup, monkeypatch):
    client, factory, owner, employee = po_setup
    oid = make_placed(po_setup)
    enable_smtp(po_setup,monkeypatch)
    called=[]
    monkeypatch.setattr(order_email_dispatch,'send_smtp',lambda **kw:called.append(kw))
    with factory.begin() as db:
        db.get(PurchaseOrder,oid).total_amount=Decimal('999.00')
    assert client.post(dispatch_url(oid),headers=owner).status_code == 409
    with factory.begin() as db:
        db.get(PurchaseOrder,oid).total_amount=Decimal('74.00')
        vendor=db.get(Vendor,db.get(PurchaseOrder,oid).vendor_id)
        vendor.is_active=False
    assert client.post(dispatch_url(oid),headers=owner).status_code == 409
    assert called==[]


def test_no_unexpected_ai_or_inventory_side_effects(po_setup, monkeypatch):
    client, factory, owner, employee=po_setup
    oid=make_placed(po_setup)
    monkeypatch.setattr(order_email_dispatch,'send_smtp',lambda **kw:pytest.fail('not expected'))
    with factory() as db:
        stock=db.get(InventoryItem,1).on_hand
        candidate=db.get(PurchaseOrder,oid).ai_recommended_quotation_id
    assert client.post(dispatch_url(oid),headers=owner).status_code==200
    with factory() as db:
        assert db.get(InventoryItem,1).on_hand==stock
        assert db.get(PurchaseOrder,oid).ai_recommended_quotation_id==candidate
