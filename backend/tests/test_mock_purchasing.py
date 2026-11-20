import json
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select

from app.core import mock_purchasing
from app.core.claude_agent import AgentUnavailable, Recommendation
from app.core.config import settings
from app.core.order_decisions import authorize_order, validate_quote
from app.core.order_email_dispatch import record_workflow_mock_dispatch
from app.core.quote_simulation import simulate_pending_quotations
from app.core.sample_vendor_catalogue import SAMPLE_VENDOR_NAMES, sync_sample_quotes
from app.db.models import (InventoryItem, OrderItem, PurchaseOrder, PurchaseOrderEmailDispatch, QuotationEmailDispatch,
                           StockReceipt, Vendor, VendorItem, VendorQuotation)
from test_quotation_collection import create_request, setup
from test_groq_provider import fake_groq


@pytest.fixture
def select_simulated_quote(monkeypatch):
    from app.core.quote_comparison import compare_quotations

    def recommend(db, order):
        comparison = compare_quotations(db, order)
        assert all(q['is_simulated'] for q in comparison['quotations'])
        return Recommendation(
            recommended_quotation_id=comparison['lowest_cost_quotation_id'],
            reason='Lowest valid simulated total for this replenishment.',
        ), comparison

    monkeypatch.setattr(mock_purchasing, 'recommend', recommend)


def enable_mock(monkeypatch):
    monkeypatch.setattr(settings, 'enable_mock_purchasing', True)
    monkeypatch.setattr(settings, 'enable_quote_simulation', True)


def test_checkout_compares_catalogue_vendors_and_auto_places_one_mock_order(setup, monkeypatch):
    client, factory, owner, employee = setup
    enable_mock(monkeypatch)
    monkeypatch.setattr(settings, 'ai_fallback_provider', 'groq')
    monkeypatch.setattr(settings, 'groq_api_key', 'test-groq-key')
    with factory.begin() as db:
        sync_sample_quotes(db, item_id=1)
        db.get(InventoryItem, 1).unit_cost = Decimal("500.00")

    def groq(request, payload, _):
        assert request.url.host == 'api.groq.com'
        assert 'tools' not in payload
        schema = payload['response_format']['json_schema']
        assert payload['response_format']['type'] == 'json_schema' and schema['strict']
        assert schema['schema']['required'] == ['recommended_quotation_id', 'reason']
        quotes = json.loads(payload['messages'][-1]['content'].split('Eligible quotations: ')[1])
        assert {q['vendor_name'] for q in quotes} == set(SAMPLE_VENDOR_NAMES.values())
        assert {q['quoted_unit_price'] for q in quotes} == {'21.00', '22.60'}
        assert {Decimal(q['quoted_delivery_days']) for q in quotes} == {2, 4}
        assert all(q['available_quantity'] == q['required_quantity'] for q in quotes)
        answer = {
            'recommended_quotation_id': quotes[1]['quotation_id'],
            'reason': 'The selected simulation quote is eligible for replenishment.',
        }
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(answer)}}]})

    requests = fake_groq(monkeypatch, groq)
    order_id = create_request(setup)
    assert len(requests) == 1
    base = f'/api/purchase-requests/{order_id}'
    rows = client.get('/api/purchase-requests', headers=owner).json()['items']
    assert rows[0]['status'] == 'PLACED' and rows[0]['mock_order']
    assert client.get(base + '/order-email-dispatch', headers=owner).json()['dispatch_status'] == 'MOCKED'
    assert rows[0]['catalogue_purchase']
    assert rows[0]['vendor_name'] == "NCR Rapid Hardware Traders"
    assert Decimal(rows[0]['unit_price']) == Decimal("22.60")
    assert Decimal(rows[0]['total_amount']) == Decimal("22.60") * rows[0]['ordered_quantity']
    compared = client.get(base + '/mock-comparison', headers=owner)
    assert compared.status_code == 200, compared.text
    assert {q['vendor_name'] for q in compared.json()['quotations']} == set(SAMPLE_VENDOR_NAMES.values())
    assert client.get(base + '/mock-comparison', headers=employee).status_code == 403
    assert client.post(base + '/mock-delivery', headers=owner).status_code == 409
    assert client.post(base + '/mock-process', headers=owner).json()['status'] == 'PLACED'
    assert len(requests) == 1
    with factory() as db:
        assert db.get(InventoryItem, 1).on_hand == 1
        assert db.scalar(select(func.count()).select_from(StockReceipt)) == 0
        assert db.scalar(select(func.count()).select_from(PurchaseOrderEmailDispatch)) == 1
        rfqs = list(db.scalars(select(QuotationEmailDispatch)))
        assert len(rfqs) == 2
        assert {r.recipient for r in rfqs} == set(SAMPLE_VENDOR_NAMES)
        assert all(r.mode == 'mock' and r.status == 'MOCKED' for r in rfqs)
        assert db.scalar(select(func.count()).select_from(VendorQuotation).where(VendorQuotation.status == 'SELECTED')) == 1
    assert rows[0]['tracking_status'] == 'PLACED'
    assert Decimal(rows[0]['expected_delivery_days']) == 2
    for action in ('mock-transit', 'mock-delivery'):
        assert client.post(f'{base}/{action}', headers=employee).status_code == 403
    sent = client.post(base + '/mock-transit', headers=owner)
    assert sent.status_code == 200 and sent.json()['status'] == 'IN_TRANSIT'
    assert not sent.json()['replayed']
    with factory() as db:
        transit_at = db.get(PurchaseOrder, order_id).mock_in_transit_at
        assert transit_at is not None
        assert db.get(InventoryItem, 1).on_hand == 1
        assert db.scalar(select(func.count()).select_from(StockReceipt)) == 0
    assert client.post(base + '/mock-transit', headers=owner).json()['replayed']
    assert client.get('/api/purchase-requests', headers=owner).json()['items'][0]['tracking_status'] == 'IN_TRANSIT'
    with factory() as db:
        assert db.get(PurchaseOrder, order_id).mock_in_transit_at == transit_at
    delivered = client.post(base + '/mock-delivery', headers=owner)
    assert delivered.status_code == 200 and delivered.json()['order_status'] == 'RECEIVED'
    assert delivered.json()['on_hand'] == 1 + rows[0]['ordered_quantity']
    assert client.get('/api/purchase-requests', headers=owner).json()['items'][0]['tracking_status'] == 'RECEIVED'
    assert client.post(base + '/mock-delivery', headers=owner).json()['replayed']
    assert client.post(base + '/mock-transit', headers=owner).status_code == 409
    with factory() as db:
        assert db.get(InventoryItem, 1).on_hand == delivered.json()['on_hand']
        assert db.scalar(select(func.count()).select_from(StockReceipt)) == 1


@pytest.mark.parametrize(('price', 'state'), [('100000.00', 'PLACED'), ('100000.01', 'PENDING_APPROVAL')])
def test_exact_approval_boundary(setup, monkeypatch, select_simulated_quote, price, state):
    client, factory, owner, employee = setup
    order_id = create_request(setup)
    with factory.begin() as db:
        db.scalar(select(OrderItem).where(OrderItem.purchase_order_id == order_id)).ordered_quantity = 1
        sync_sample_quotes(db, item_id=1)
        for terms in db.scalars(select(VendorItem).join(Vendor).where(Vendor.email.in_(SAMPLE_VENDOR_NAMES))):
            terms.last_known_unit_price = Decimal(price)
    enable_mock(monkeypatch)
    base = f'/api/purchase-requests/{order_id}'
    result = client.post(base + '/mock-process', headers=owner)
    assert result.status_code == 200 and result.json()['status'] == state, result.text
    if state == 'PENDING_APPROVAL':
        pending = client.get('/api/purchase-requests?status=PENDING_APPROVAL', headers=owner).json()['items'][0]
        assert pending['vendor_name'] in SAMPLE_VENDOR_NAMES.values()
        assert pending['total_amount'] == '100000.01'
        assert client.get(base + '/order-email-dispatch', headers=owner).json()['dispatch_status'] == 'NOT_SENT'
        assert client.post(base + '/mock-transit', headers=owner).status_code == 409
        assert client.post(base + '/owner-decision', headers=employee, json={'action': 'APPROVED'}).status_code == 403
        decision = client.post(base + '/owner-decision', headers=owner, json={'action': 'APPROVED'})
        assert decision.status_code == 200 and decision.json()['dispatch_status'] == 'MOCKED', decision.text


def test_provider_failure_keeps_checkout_and_request_retriable(setup, monkeypatch):
    client, factory, owner, _ = setup
    enable_mock(monkeypatch)

    def unavailable(*_):
        raise AgentUnavailable("Provider unavailable")

    monkeypatch.setattr(mock_purchasing, 'recommend', unavailable)
    order_id = create_request(setup)
    with factory() as db:
        assert db.get(InventoryItem, 1).on_hand == 1
        assert db.scalar(select(func.count()).select_from(PurchaseOrderEmailDispatch)) == 0
    result = client.post(f'/api/purchase-requests/{order_id}/mock-process', headers=owner)
    assert result.status_code == 503
    monkeypatch.setattr(settings, 'enable_real_email_dispatch', True)
    assert not client.get('/api/purchase-requests/mock-workflow', headers=owner).json()['enabled']
    assert client.post(f'/api/purchase-requests/{order_id}/mock-process', headers=owner).status_code == 409


def test_ai_cannot_choose_vendor_unavailable_for_required_quantity(setup, monkeypatch):
    client, factory, owner, _ = setup
    order_id = create_request(setup)
    with factory.begin() as db:
        sync_sample_quotes(db, item_id=1)
        terms = db.scalar(select(VendorItem).join(Vendor).where(
            Vendor.email == "quotes@westdelhi-industrial.example"
        ))
        terms.min_order_qty = 100000
    enable_mock(monkeypatch)
    monkeypatch.setattr(settings, 'ai_fallback_provider', 'groq')
    monkeypatch.setattr(settings, 'groq_api_key', 'test-groq-key')

    def groq(request, payload, _):
        candidates = json.loads(payload['messages'][-1]['content'].split('Eligible quotations: ')[1])
        unavailable = next(q for q in candidates if not q['available_quantity'])
        answer = {'recommended_quotation_id': unavailable['quotation_id'], 'reason': 'This selection lacks stock.'}
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(answer)}}]})

    fake_groq(monkeypatch, groq)
    result = client.post(f'/api/purchase-requests/{order_id}/mock-process', headers=owner)
    assert result.status_code == 409
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(PurchaseOrderEmailDispatch)) == 0


def test_existing_non_catalogue_mock_order_keeps_legacy_delivery_actions(setup, monkeypatch):
    client, factory, owner, _ = setup
    order_id = create_request(setup)
    enable_mock(monkeypatch)
    with factory.begin() as db:
        order = db.get(PurchaseOrder, order_id)
        simulate_pending_quotations(db, order)
        quote_id = db.scalar(select(VendorQuotation.id).where(VendorQuotation.purchase_order_id == order_id))
        quote, entry = validate_quote(db, order, quote_id)
        order.mock_order = True
        authorize_order(db, order, quote, entry)
        record_workflow_mock_dispatch(db, order)

    base = f'/api/purchase-requests/{order_id}'
    assert client.post(base + '/mock-transit', headers=owner).json()['status'] == 'IN_TRANSIT'
    receipt = client.post(base + '/mock-delivery', headers=owner).json()
    assert receipt['order_status'] == 'RECEIVED' and receipt['on_hand'] > 1
    replay = client.post(base + '/mock-delivery', headers=owner).json()
    assert replay['replayed'] and replay['on_hand'] == receipt['on_hand']


def test_existing_waiting_order_recovers_after_invalid_provider_output(setup, monkeypatch):
    client, factory, owner, _ = setup
    order_id = create_request(setup)
    with factory.begin() as db:
        db.scalar(select(OrderItem).where(OrderItem.purchase_order_id == order_id)).ordered_quantity = 52
        sync_sample_quotes(db, item_id=1)
        for terms, vendor in db.execute(select(VendorItem, Vendor).join(Vendor).where(
            Vendor.email.in_(SAMPLE_VENDOR_NAMES)
        )):
            terms.last_known_unit_price = (
                Decimal('157.50') if vendor.email.startswith('quotes@westdelhi') else Decimal('169.50')
            )
    enable_mock(monkeypatch)
    monkeypatch.setattr(settings, 'ai_fallback_provider', 'groq')
    monkeypatch.setattr(settings, 'groq_api_key', 'test-groq-key')

    def groq(request, payload, attempt):
        prompt = payload['messages'][-1]['content']
        offers = json.loads(prompt.split('Eligible quotations: ')[1])
        assert [Decimal(row['total_quoted_cost']) for row in offers] == [Decimal('8190.00'), Decimal('8814.00')]
        assert ('Use exactly the two JSON fields' in prompt) == (attempt % 2 == 0)
        assert not payload.get('tools')
        if attempt < 4:
            content = 'I would choose West Delhi Industrial Supply.'
        else:
            content = json.dumps({
                'recommended_quotation_id': offers[0]['quotation_id'],
                'reason': 'It has all 52 units and costs less, with a four-day delivery estimate.',
            })
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': content}}]})

    requests = fake_groq(monkeypatch, groq)
    base = f'/api/purchase-requests/{order_id}'
    failed = client.post(base + '/mock-process', headers=owner)
    assert failed.status_code == 409 and len(requests) == 2
    before = client.get(base + '/mock-comparison', headers=owner).json()['quotations']
    assert len(before) == 2
    assert client.get(base + '/order-email-dispatch', headers=owner).json()['dispatch_status'] == 'NOT_SENT'

    restarted = client.post(base + '/mock-process', headers=owner)
    assert restarted.status_code == 200 and restarted.json()['status'] == 'PLACED', restarted.text
    assert len(requests) == 4
    assert client.get(base + '/mock-comparison', headers=owner).json()['quotations'] == before
    rows = client.get('/api/purchase-requests', headers=owner).json()['items']
    assert rows[0]['vendor_name'] == 'West Delhi Industrial Supply'
    assert Decimal(rows[0]['total_amount']) == Decimal('8190.00')
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(PurchaseOrderEmailDispatch)) == 1
        assert db.scalar(select(func.count()).select_from(QuotationEmailDispatch)) == 2
        assert db.get(InventoryItem, 1).on_hand == 1


@pytest.mark.parametrize(('outcome', 'check_status'), [
    ('match', 'MATCHED'), ('mismatch', 'MISMATCH'), ('offline', 'UNAVAILABLE'),
])
def test_delivered_mock_bill_preview(setup, monkeypatch, select_simulated_quote, outcome, check_status):
    from base64 import b64decode
    from datetime import timedelta
    from io import BytesIO

    from PIL import Image

    from app.core import vendor_bills
    from app.db.models import VendorBill

    client, factory, owner, employee = setup
    enable_mock(monkeypatch)
    with factory.begin() as db:
        sync_sample_quotes(db, item_id=1)
    order_id = create_request(setup)
    base = f'/api/purchase-requests/{order_id}'
    assert client.post(base + '/mock-bill', headers=owner).status_code == 409
    assert client.post(base + '/mock-transit', headers=owner).status_code == 200
    assert client.post(base + '/mock-bill', headers=owner).status_code == 409
    delivered = client.post(base + '/mock-delivery', headers=owner)
    assert delivered.status_code == 200, delivered.text
    assert client.post(base + '/mock-bill', headers=employee).status_code == 403

    with factory() as db:
        order = db.get(PurchaseOrder, order_id)
        total = order.total_amount
        invoice_date = (order.completed_at + timedelta(hours=5, minutes=30)).date().isoformat()
    calls = []

    def read_invoice(payload, *, api_version):
        assert api_version == '2023-06-01'
        image = payload['messages'][0]['content'][0]
        assert image['source']['media_type'] == 'image/png'
        calls.append(image['source']['data'])
        if outcome == 'offline':
            raise AgentUnavailable('Claude unavailable')
        return {'content': [{'type': 'text', 'text': json.dumps({
            'invoice_number': f'INV-{order_id:06d}',
            'invoice_date': invoice_date,
            'total': str(total + (1 if outcome == 'mismatch' else 0)),
        })}]}

    monkeypatch.setattr(vendor_bills, 'call_messages', read_invoice)
    preview = client.post(base + '/mock-bill', headers=owner)
    assert preview.status_code == 200, preview.text
    data = preview.json()
    assert list(data) == ['image']
    assert Image.open(BytesIO(b64decode(data['image']))).format == 'PNG'
    assert client.post(base + '/mock-bill', headers=owner).json() == data
    assert calls == [data['image']]
    with factory() as db:
        assert db.get(VendorBill, order_id).check_status == check_status
        assert db.get(InventoryItem, 1).on_hand == delivered.json()['on_hand']
        assert db.scalar(select(func.count()).select_from(StockReceipt)) == 1
