"""safe draft-only Claude and backend validations."""
from decimal import Decimal
from sqlalchemy import select
from test_quotation_collection import setup, create_request
from test_quote_comparison import quote_twice
from app.db.models import PurchaseOrder, VendorQuotation, Vendor, VendorItem, OrderItem
from app.core.config import settings
from app.core.claude_agent import run_tool, AgentInvalidResponse
from app.core import claude_agent


def test_quote_previews_include_every_active_mapped_pending_vendor_and_never_dispatch(setup):
    client,factory,owner,employee=setup
    oid=create_request(setup)
    r=client.post(f'/api/purchase-requests/{oid}/draft-quotation-requests',json={},headers=owner)
    assert r.status_code==200,r.text
    data=r.json()
    assert len(data['drafts'])==2
    assert {d['to'] for d in data['drafts']}=={'shreeji@example.com','bharat@example.com'}
    assert all('PL-TEF-01' in d['body'] and 'INR' in d['body'] and 'RFQ-' in d['body'] for d in data['drafts'])
    assert all(d['dispatch_status']=='NOT_SENT_PREVIEW_ONLY' for d in data['drafts'])
    assert client.post(f'/api/purchase-requests/{oid}/draft-quotation-requests',json={},headers=employee).status_code==403
    with factory() as db:
        assert all(q.status=='REQUESTED' for q in db.scalars(select(VendorQuotation)))
        assert db.get(PurchaseOrder,oid).vendor_id is None


def test_previews_require_pending_active_mapped_vendor(setup):
    client,factory,owner,employee=setup
    oid=create_request(setup)
    with factory.begin() as db:
        db.query(Vendor).filter(Vendor.email=='shreeji@example.com').update({'is_active':False})
    r=client.post(f'/api/purchase-requests/{oid}/draft-quotation-requests',json={},headers=owner)
    assert r.status_code==200 and len(r.json()['drafts'])==1
    with factory.begin() as db:
        db.query(Vendor).filter(Vendor.email=='bharat@example.com').update({'is_active':False})
    assert client.post(f'/api/purchase-requests/{oid}/draft-quotation-requests',json={},headers=owner).status_code==409


def test_final_draft_requires_authorized_order_fresh_selected_quote(setup):
    client,factory,owner,employee=setup
    oid, quotes=quote_twice(setup)
    assert client.post(f'/api/purchase-requests/{oid}/draft-order-email',json={},headers=owner).status_code==409
    evaluate=client.post(f'/api/purchase-requests/{oid}/evaluate',json={},headers=owner)
    assert evaluate.status_code==200,evaluate.text
    if evaluate.json()['status']=='PENDING_APPROVAL':
        r=client.post(f'/api/purchase-requests/{oid}/owner-decision',headers=owner,
            json={'action':'APPROVED','quotation_id':quotes[0]['id']})
        assert r.status_code==200,r.text
    r=client.post(f'/api/purchase-requests/{oid}/draft-order-email',json={},headers=owner)
    assert r.status_code==200,r.text
    draft=r.json()['draft']
    with factory() as db:
        po=db.get(PurchaseOrder,oid)
        line=db.scalar(select(OrderItem).where(OrderItem.purchase_order_id==oid))
        quote=db.scalar(select(VendorQuotation).where(VendorQuotation.status=='SELECTED'))
        assert draft['quotation_id']==quote.id
        assert f'INR {quote.quoted_unit_price}' in draft['body']
        assert f'INR {po.total_amount}' in draft['body']
        assert line.total_price==po.total_amount
        assert po.status=='PLACED'
    assert draft['dispatch_status']=='NOT_SENT_PREVIEW_ONLY'
    assert client.post(f'/api/purchase-requests/{oid}/draft-order-email',json={},headers=employee).status_code==403
    with factory.begin() as db:
        db.get(PurchaseOrder,oid).total_amount=Decimal('9999.00')
    assert client.post(f'/api/purchase-requests/{oid}/draft-order-email',json={},headers=owner).status_code==409


def test_claude_draft_tool_sandbox_and_no_api_key_fallback(setup,monkeypatch):
    client,factory,owner,employee=setup
    oid=create_request(setup)
    with factory() as db:
        po=db.get(PurchaseOrder,oid)
        context=run_tool(db,po,'request_vendor_quotations',{})
        assert len(context['vendors'])==2
        try:
            run_tool(db,po,'draft_vendor_order_email',{})
            assert False
        except AgentInvalidResponse: pass
    monkeypatch.setattr(settings,'claude_api_key','')
    response=client.post(f'/api/purchase-requests/{oid}/draft-quotation-requests',headers=owner,json={'use_ai':True})
    assert response.status_code==503
    assert response.json()['detail']['ai_status']=='UNAVAILABLE'
    assert client.post(f'/api/purchase-requests/{oid}/draft-quotation-requests',headers=owner,json={'use_ai':False}).status_code==200


def test_claude_tool_use_note_is_separate_from_canonical_commercial_terms(setup,monkeypatch):
    client,factory,owner,employee=setup
    oid=create_request(setup)
    monkeypatch.setattr(settings,'claude_api_key','fake-key')
    messages=[]
    def fake(payload):
        messages.append(payload)
        if len(messages)==1:
            return {'stop_reason':'tool_use','content':[{'type':'tool_use','name':'request_vendor_quotations','id':'call1','input':{}}]}
        return {'stop_reason':'end_turn','content':[{'type':'text','text':'{"note":"Thank you for reviewing this request."}'}]}
    monkeypatch.setattr(claude_agent,'call_messages',fake)
    response=client.post(f'/api/purchase-requests/{oid}/draft-quotation-requests',headers=owner,json={'use_ai':True})
    assert response.status_code==200,response.text
    assert response.json()['ai_status']=='AVAILABLE'
    assert all(d['ai_suggested_note']=='Thank you for reviewing this request.' for d in response.json()['drafts'])
    assert all('Thank you for reviewing' not in d['body'] for d in response.json()['drafts'])
    assert messages[1]['messages'][-1]['content'][0]['type']=='tool_result'
    with factory() as db:
        assert all(q.status=='REQUESTED' for q in db.scalars(select(VendorQuotation)))
