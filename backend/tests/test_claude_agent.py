"""Fake the external Claude response; no live key, vendor emails, or purchases."""
import json
from sqlalchemy import select
from test_quotation_collection import setup
from test_quote_comparison import quote_twice
from app.core.config import settings
from app.core.claude_agent import run_tool, AgentInvalidResponse
from app.db.models import PurchaseOrder, VendorQuotation


def test_no_key_is_explicit_and_no_database_change(setup, monkeypatch):
    client, factory, owner, employee = setup
    oid, quotes = quote_twice(setup)
    monkeypatch.setattr(settings, 'claude_api_key', '')
    response = client.post(f'/api/purchase-requests/{oid}/ai-recommendation', headers=owner)
    assert response.status_code == 503 and response.json()['detail']['ai_status'] == 'UNAVAILABLE'
    with factory() as db:
        po = db.get(PurchaseOrder, oid)
        assert po.status == 'WAITING_FOR_QUOTES'
        assert po.ai_recommended_quotation_id is None
        assert po.vendor_id is None and po.total_amount is None


def test_tool_use_and_structured_recommendation_is_only_advisory(setup, monkeypatch):
    from app.core import claude_agent
    client, factory, owner, employee = setup
    oid, quotes = quote_twice(setup)
    chosen = quotes[0]['id']
    monkeypatch.setattr(settings, 'claude_api_key', 'fake-test-key')
    calls = []
    def reply(payload):
        calls.append(payload)
        if len(calls) == 1:
            return {'stop_reason':'tool_use', 'content':[
                {'type':'tool_use','id':'toolu_a','name':'compare_received_quotations','input':{}}]}
        return {'stop_reason':'end_turn','content':[{'type':'text','text':json.dumps({
            'recommended_quotation_id': chosen,
            'reason':'Good current price and acceptable quoted delivery time.',
            'comparison_summary': [{'vendor_id':quotes[0]['vendor_id'], 'reason':'Competitive pricing'}]
        })}]}
    monkeypatch.setattr(claude_agent,'call_messages',reply)
    r = client.post(f'/api/purchase-requests/{oid}/ai-recommendation', headers=owner)
    assert r.status_code == 200, r.text
    assert r.json()['recommended_quotation_id'] == chosen
    assert r.json()['authorization_status'] == 'NOT_AUTHORIZED_BY_AI'
    assert len(calls) == 2
    assert calls[1]['messages'][-1]['content'][0]['type'] == 'tool_result'
    with factory() as db:
        po=db.get(PurchaseOrder,oid)
        assert po.ai_recommended_quotation_id == chosen
        assert po.backend_candidate_quotation_id is None
        assert po.vendor_id is None and po.status == 'WAITING_FOR_QUOTES'
        assert all(q.status=='RECEIVED' for q in db.scalars(select(VendorQuotation)))
    get = client.get(f'/api/purchase-requests/{oid}/ai-recommendation',headers=owner)
    assert get.json()['recommended_quotation_id'] == chosen


def test_reject_hallucinated_quote_and_owner_only(setup, monkeypatch):
    from app.core import claude_agent
    client, factory, owner, employee = setup
    oid, quotes = quote_twice(setup)
    monkeypatch.setattr(settings, 'claude_api_key', 'fake-test-key')
    monkeypatch.setattr(claude_agent,'call_messages',lambda payload: {'stop_reason':'end_turn','content':[
        {'type':'text','text':json.dumps({'recommended_quotation_id':999999,'reason':'Cannot select this vendor'})}]})
    assert client.post(f'/api/purchase-requests/{oid}/ai-recommendation',headers=owner).status_code == 409
    assert client.post(f'/api/purchase-requests/{oid}/ai-recommendation',headers=employee).status_code == 403
    with factory() as db:
        assert db.get(PurchaseOrder,oid).ai_recommended_quotation_id is None


def test_tool_sandbox_rejects_unmapped_vendor_and_unknown_tool(setup):
    client, factory, owner, employee = setup
    oid, quotes = quote_twice(setup)
    with factory() as db:
        po = db.get(PurchaseOrder,oid)
        try:
            run_tool(db,po,'get_vendor_performance',{'vendor_id':999999})
            assert False, 'expected rejection'
        except AgentInvalidResponse:
            pass
        try:
            run_tool(db,po,'authorize_order',{})
            assert False, 'expected rejection'
        except AgentInvalidResponse:
            pass
