"""verified, read-only fresh quote comparison."""
from datetime import timedelta
from decimal import Decimal
from sqlalchemy import select

from test_quotation_collection import setup, create_request
from app.core.quotations import utcnow
from app.db.models import PurchaseOrder, VendorQuotation, VendorItem, Vendor


def quote_twice(setup, quotes=(('18.50', '2'), ('19.00', '3'))):
    client, factory, owner, employee = setup
    oid = create_request(setup)
    qs = client.get(f'/api/purchase-requests/{oid}/quotations', headers=owner).json()['quotations']
    for q, (price, days) in zip(qs, quotes):
        r = client.post(f'/api/purchase-requests/{oid}/quotations/{q["id"]}/response',
                        headers=owner, json={'quoted_unit_price': price, 'quoted_lead_time_days': days})
        assert r.status_code == 200, r.text
    return oid, qs


def test_comparison_requires_finished_collection(setup):
    client, factory, owner, employee = setup
    oid = create_request(setup)
    assert client.get(f'/api/purchase-requests/{oid}/compare-quotations', headers=owner).status_code == 409
    qid = client.get(f'/api/purchase-requests/{oid}/quotations', headers=owner).json()['quotations'][0]['id']
    client.post(f'/api/purchase-requests/{oid}/quotations/{qid}/response', headers=owner,
                json={'quoted_unit_price': '18.50', 'quoted_lead_time_days': '2'})
    with factory.begin() as db:
        db.get(PurchaseOrder, oid).quote_deadline_at = utcnow() - timedelta(minutes=1)
    assert client.get(f'/api/purchase-requests/{oid}/compare-quotations', headers=owner).status_code == 409
    assert client.post(f'/api/purchase-requests/{oid}/close-quotations', headers=owner).status_code == 200
    result = client.get(f'/api/purchase-requests/{oid}/compare-quotations', headers=owner)
    assert result.status_code == 200, result.text
    assert result.json()['received_count'] == 1


def test_price_delivery_reliability_and_no_writes(setup):
    client, factory, owner, employee = setup
    oid, qs = quote_twice(setup)
    before = None
    with factory() as db:
        before = [(q.id, q.status) for q in db.scalars(select(VendorQuotation))]
    resp = client.get(f'/api/purchase-requests/{oid}/compare-quotations', headers=owner)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data['comparison_state'] == 'READY' and data['eligible_count'] == 2
    assert {Decimal(str(q['total_quoted_cost'])) for q in data['quotations']} == {Decimal('74.00'), Decimal('76.00')}
    assert data['quotations'][0]['total_quoted_cost'] == '74.00'
    assert data['lowest_cost_quotation_id'] == data['quotations'][0]['quotation_id']
    assert data['fastest_quotation_id'] == data['quotations'][0]['quotation_id']
    assert all(e['reliability']['on_time_rate_percent'] is None for e in data['quotations'])
    assert all(e['reliability']['completed_orders_with_delivery_data'] == 0 for e in data['quotations'])
    with factory() as db:
        po = db.get(PurchaseOrder, oid)
        assert po.status == 'WAITING_FOR_QUOTES' and po.vendor_id is None and po.total_amount is None
        assert [(q.id, q.status) for q in db.scalars(select(VendorQuotation))] == before


def test_minimum_order_quantity_excluded_and_active_vendor_rechecked(setup):
    client, factory, owner, employee = setup
    oid, qs = quote_twice(setup)
    with factory.begin() as db:
        db.query(VendorItem).filter(VendorItem.vendor_id == qs[0]['vendor_id']).update({'min_order_qty': 5})
        db.query(Vendor).filter(Vendor.id == qs[1]['vendor_id']).update({'is_active': False})
    data = client.get(f'/api/purchase-requests/{oid}/compare-quotations', headers=owner).json()
    assert data['comparison_state'] == 'NO_ELIGIBLE_QUOTES'
    assert data['lowest_cost_quotation_id'] is None
    assert set(reason for q in data['quotations'] for reason in q['ineligibility_reasons']) == {
        'BELOW_VENDOR_MINIMUM_ORDER_QUANTITY', 'VENDOR_INACTIVE'
    }


def test_excludes_expired_and_requested_quotes_and_no_valid_result(setup):
    client, factory, owner, employee = setup
    oid = create_request(setup)
    with factory.begin() as db:
        db.get(PurchaseOrder, oid).quote_deadline_at = utcnow() - timedelta(minutes=1)
    assert client.post(f'/api/purchase-requests/{oid}/close-quotations', headers=owner).status_code == 200
    data = client.get(f'/api/purchase-requests/{oid}/compare-quotations', headers=owner).json()
    assert data['comparison_state'] == 'NO_VALID_QUOTES'
    assert data['quotations'] == [] and data['lowest_cost_quotation_id'] is None


def test_role_restriction_and_nonexistent_order(setup):
    client, factory, owner, employee = setup
    oid = create_request(setup)
    assert client.get(f'/api/purchase-requests/{oid}/compare-quotations', headers=employee).status_code == 403
    assert client.get('/api/purchase-requests/999999/compare-quotations', headers=owner).status_code == 404


def test_reference_prices_only_for_comparison(setup):
    client, factory, owner, employee = setup
    oid, qs = quote_twice(setup, (('22.00', '3'), ('17.00', '4')))
    data = client.get(f'/api/purchase-requests/{oid}/compare-quotations', headers=owner).json()
    assert data['quotations'][0]['quoted_unit_price'] == '17.00'
    assert data['quotations'][0]['last_known_unit_price'] == '18.00'
    assert Decimal(data['quotations'][0]['price_change_percent']) == Decimal('-5.56')
    assert Decimal(data['quotations'][0]['total_quoted_cost']) == Decimal('68.00')


def test_reliability_uses_actual_completed_delivery_record_only(setup):
    from app.db.models import OrderItem
    client, factory, owner, employee = setup
    oid, qs = quote_twice(setup)
    winning_vendor_id = qs[0]['vendor_id']
    with factory.begin() as db:
        historical = PurchaseOrder(
            inventory_item_id=1, vendor_id=winning_vendor_id,
            status='RECEIVED', placed_at=utcnow() - timedelta(days=5),
            completed_at=utcnow() - timedelta(days=3), total_amount=Decimal('20.00'),
        )
        db.add(historical)
        db.flush()
        db.add(OrderItem(purchase_order_id=historical.id, inventory_item_id=1,
                         ordered_quantity=1, received_quantity=1,
                         unit_price=Decimal('20.00'), total_price=Decimal('20.00'), is_open=False))
        db.add(VendorQuotation(purchase_order_id=historical.id, inventory_item_id=1,
                               vendor_id=winning_vendor_id, status='SELECTED',
                               quoted_unit_price=Decimal('20.00'),
                               quoted_lead_time_days=Decimal('3'), responded_at=utcnow() - timedelta(days=6)))
    data = client.get(f'/api/purchase-requests/{oid}/compare-quotations', headers=owner).json()
    first = next(q for q in data['quotations'] if q['vendor_id'] == winning_vendor_id)
    other = next(q for q in data['quotations'] if q['vendor_id'] != winning_vendor_id)
    assert first['reliability']['completed_orders_with_delivery_data'] == 1
    assert first['reliability']['on_time_deliveries'] == 1
    assert first['reliability']['on_time_rate_percent'] == '100.00'
    assert other['reliability']['on_time_rate_percent'] is None
