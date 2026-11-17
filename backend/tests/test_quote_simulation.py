"""safety, real/simulated separation and comparison integration."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.quotations import utcnow
from app.db.models import (OrderItem, PurchaseOrder, Vendor, VendorQuotation,
                           QuotationEmailDispatch)
from test_quotation_collection import setup, create_request


@pytest.fixture
def demo(setup):
    before = settings.enable_quote_simulation, settings.email_mode, settings.enable_real_email_dispatch
    settings.enable_quote_simulation = True
    settings.email_mode = 'mock'
    settings.enable_real_email_dispatch = False
    try:
        yield setup
    finally:
        settings.enable_quote_simulation, settings.email_mode, settings.enable_real_email_dispatch = before


def url(oid):
    return f'/api/purchase-requests/{oid}/simulate-vendor-quote-replies'


def test_simulation_generates_labeled_quotes_and_comparison_without_ordering(demo):
    client, factory, owner, employee = demo
    oid = create_request(demo)
    with patch('app.core.email_service.send_smtp', side_effect=AssertionError('SMTP must not run')):
        response = client.post(url(oid), headers=owner)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['new_simulated_replies'] == 2
    assert result['simulation'] is True and not result['purchase_order_authorized']
    assert not result['email_sent']
    assert result['collection']['collection_state'] == 'READY_FOR_COMPARISON'
    assert result['collection']['requested_count'] == 0
    assert result['collection']['received_count'] == 2
    assert all(q['is_simulated'] is True for q in result['collection']['quotations'])
    assert result['comparison']['comparison_state'] == 'READY'
    assert len(result['comparison']['quotations']) == 2
    assert all(q['is_simulated'] is True for q in result['comparison']['quotations'])
    prices = {q['quoted_unit_price'] for q in result['comparison']['quotations']}
    assert len(prices) == 2
    with factory() as db:
        order = db.get(PurchaseOrder, oid)
        line = db.scalar(select(OrderItem).where(OrderItem.purchase_order_id == oid))
        assert order.status == 'WAITING_FOR_QUOTES'
        assert order.vendor_id is None and order.total_amount is None
        assert order.backend_candidate_quotation_id is None
        assert order.ai_recommended_quotation_id is None
        assert line.unit_price is None and line.total_price is None


def test_idempotent_second_click_does_not_overwrite_quotes(demo):
    client, factory, owner, employee = demo
    oid = create_request(demo)
    assert client.post(url(oid), headers=owner).status_code == 200
    with factory() as db:
        old = [(q.id, q.quoted_unit_price, q.responded_at) for q in db.scalars(select(VendorQuotation).order_by(VendorQuotation.id))]
    again = client.post(url(oid), headers=owner)
    assert again.status_code == 200, again.text
    assert again.json()['new_simulated_replies'] == 0
    with factory() as db:
        new = [(q.id, q.quoted_unit_price, q.responded_at) for q in db.scalars(select(VendorQuotation).order_by(VendorQuotation.id))]
    assert old == new


def test_preserves_genuine_received_quote_and_only_fills_pending(demo):
    client, factory, owner, employee = demo
    oid = create_request(demo)
    first_quote = client.get(f'/api/purchase-requests/{oid}/quotations', headers=owner).json()['quotations'][0]['id']
    r = client.post(f'/api/purchase-requests/{oid}/quotations/{first_quote}/response', headers=owner,
                    json={'quoted_unit_price': '19.87', 'quoted_lead_time_days': '4'})
    assert r.status_code == 200, r.text
    simulated = client.post(url(oid), headers=owner)
    assert simulated.status_code == 200, simulated.text
    assert simulated.json()['new_simulated_replies'] == 1
    with factory() as db:
        genuine = db.get(VendorQuotation, first_quote)
        assert genuine.quoted_unit_price == Decimal('19.87')
        assert genuine.is_simulated is False
    assert sum(q['is_simulated'] for q in simulated.json()['collection']['quotations']) == 1


def test_disabled_by_default_and_owner_only(demo):
    client, factory, owner, employee = demo
    oid = create_request(demo)
    assert client.post(url(oid), headers=employee).status_code == 403
    settings.enable_quote_simulation = False
    disabled = client.post(url(oid), headers=owner)
    assert disabled.status_code == 409
    with factory() as db:
        assert all(q.status == 'REQUESTED' for q in db.scalars(select(VendorQuotation)))


def test_requires_mock_email_and_real_dispatch_disabled(demo):
    client, factory, owner, employee = demo
    oid = create_request(demo)
    settings.email_mode = 'smtp'
    assert client.post(url(oid), headers=owner).status_code == 409
    settings.email_mode = 'mock'
    settings.enable_real_email_dispatch = True
    assert client.post(url(oid), headers=owner).status_code == 409


def test_expired_window_cannot_be_simulated(demo):
    client, factory, owner, employee = demo
    oid = create_request(demo)
    with factory.begin() as db:
        db.get(PurchaseOrder, oid).quote_deadline_at = utcnow() - timedelta(minutes=1)
    assert client.post(url(oid), headers=owner).status_code == 409
    with factory() as db:
        assert all(q.status == 'REQUESTED' for q in db.scalars(select(VendorQuotation)))


def test_inactive_vendor_rejects_all_changes_atomically(demo):
    client, factory, owner, employee = demo
    oid = create_request(demo)
    with factory.begin() as db:
        db.get(Vendor, 2).is_active = False
    assert client.post(url(oid), headers=owner).status_code == 409
    with factory() as db:
        assert all(q.status == 'REQUESTED' for q in db.scalars(select(VendorQuotation)))


def test_actual_or_uncertain_smtp_dispatch_blocks_simulation(demo):
    client, factory, owner, employee = demo
    oid = create_request(demo)
    with factory.begin() as db:
        quote = db.scalar(select(VendorQuotation).order_by(VendorQuotation.id))
        db.add(QuotationEmailDispatch(quotation_id=quote.id, mode='smtp', status='UNCERTAIN',
                                      recipient='supplier@domain.com', message_id='<test@domain.com>'))
    response = client.post(url(oid), headers=owner)
    assert response.status_code == 409
    with factory() as db:
        assert all(q.status == 'REQUESTED' for q in db.scalars(select(VendorQuotation)))


def test_simulated_quoted_prices_are_not_selected_for_purchasing(demo):
    client, factory, owner, employee = demo
    oid = create_request(demo)
    assert client.post(url(oid), headers=owner).status_code == 200
    cmp = client.get(f'/api/purchase-requests/{oid}/compare-quotations', headers=owner)
    assert cmp.status_code == 200
    assert cmp.json()['eligible_count'] == 2
    assert all(e['is_simulated'] for e in cmp.json()['quotations'])
