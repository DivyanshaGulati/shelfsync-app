"""Drafts use either provider, are never sent, and cannot mutate purchasing state."""
import json
import httpx
import pytest
from sqlalchemy import select
from app.core import claude_agent
from app.core.config import settings
from app.db.models import PurchaseOrder, VendorQuotation
from test_quotation_collection import setup, create_request
from test_ai_providers import fake_ollama_client


def test_local_draft_without_ai_requires_no_llm_or_claude_key(setup, monkeypatch):
    client, factory, owner, employee = setup
    oid = create_request(setup)
    monkeypatch.setattr(settings, 'ai_provider', 'ollama')
    monkeypatch.setattr(settings, 'claude_api_key', '')
    monkeypatch.setattr(settings, 'ollama_model', '')
    result = client.post(f'/api/purchase-requests/{oid}/draft-quotation-requests', headers=owner, json={})
    assert result.status_code == 200
    assert result.json()['ai_status'] == 'NOT_REQUESTED'
    assert len(result.json()['drafts']) == 2
    assert all(d['dispatch_status'] == 'NOT_SENT_PREVIEW_ONLY' for d in result.json()['drafts'])


def test_ollama_tool_use_draft_without_claude_credit(setup, monkeypatch):
    client, factory, owner, employee = setup
    oid = create_request(setup)
    monkeypatch.setattr(settings, 'ai_provider', 'ollama')
    monkeypatch.setattr(settings, 'claude_api_key', '')
    monkeypatch.setattr(settings, 'ollama_model', 'test-local')
    def reply(request, requests):
        payload = requests[-1][1]
        assert payload['model'] == 'test-local'
        assert [t['function']['name'] for t in payload['tools']] == ['request_vendor_quotations']
        if len(requests) == 1:
            return httpx.Response(200, json={'message': {'content':'', 'tool_calls':[
                {'function': {'name': 'request_vendor_quotations', 'arguments': {}}}]}})
        assert payload['messages'][-1]['role'] == 'tool'
        return httpx.Response(200, json={'message': {'content': json.dumps({'note': 'Please advise if you require clarification.'})}})
    requests=fake_ollama_client(monkeypatch,reply)
    response=client.post(f'/api/purchase-requests/{oid}/draft-quotation-requests',headers=owner,json={'use_ai':True})
    assert response.status_code==200,response.text
    assert response.json()['ai_status']=='AVAILABLE'
    assert len(requests)==2
    assert all(d['ai_suggested_note']=='Please advise if you require clarification.' for d in response.json()['drafts'])
    with factory() as db:
        assert db.get(PurchaseOrder,oid).status=='WAITING_FOR_QUOTES'
        assert all(q.status=='REQUESTED' for q in db.scalars(select(VendorQuotation)))


def test_claude_provider_optional_draft_uses_existing_transport(setup, monkeypatch):
    client,factory,owner,employee=setup
    oid=create_request(setup)
    monkeypatch.setattr(settings,'ai_provider','claude')
    monkeypatch.setattr(settings,'claude_api_key','mock-only')
    calls=[]
    def fake(payload):
        calls.append(payload)
        return {'stop_reason':'end_turn','content':[{'type':'text','text':json.dumps({'note':'Thank you for your quotation.'})}]}
    monkeypatch.setattr(claude_agent,'call_messages',fake)
    r=client.post(f'/api/purchase-requests/{oid}/draft-quotation-requests',headers=owner,json={'use_ai':True})
    assert r.status_code==200,r.text
    assert len(calls)==1 and r.json()['ai_status']=='AVAILABLE'


@pytest.mark.parametrize('provider', ['ollama','claude'])
def test_unavailable_draft_fails_closed_without_creating_records(setup, monkeypatch, provider):
    client,factory,owner,employee=setup
    oid=create_request(setup)
    monkeypatch.setattr(settings,'ai_provider',provider)
    if provider=='ollama':
        monkeypatch.setattr(settings,'ollama_model','')
        monkeypatch.setattr(settings,'claude_api_key','')
    else:
        monkeypatch.setattr(settings,'claude_api_key','')
    result=client.post(f'/api/purchase-requests/{oid}/draft-quotation-requests',headers=owner,json={'use_ai':True})
    assert result.status_code==503
    assert result.json()['detail']['ai_status']=='UNAVAILABLE'
    with factory() as db:
        assert db.get(PurchaseOrder,oid).status=='WAITING_FOR_QUOTES'
        assert all(q.status=='REQUESTED' for q in db.scalars(select(VendorQuotation)))


def test_ollama_forbidden_tool_cannot_approve_order(setup, monkeypatch):
    client,factory,owner,employee=setup
    oid=create_request(setup)
    monkeypatch.setattr(settings,'ai_provider','ollama')
    def bad(*_):
        return httpx.Response(200,json={'message':{'content':'','tool_calls':[
            {'function':{'name':'approve_purchase_order','arguments':{}}}]}})
    fake_ollama_client(monkeypatch,bad)
    result=client.post(f'/api/purchase-requests/{oid}/draft-quotation-requests',headers=owner,json={'use_ai':True})
    assert result.status_code==409
    with factory() as db:
        order=db.get(PurchaseOrder,oid)
        assert order.status=='WAITING_FOR_QUOTES'
        assert order.vendor_id is None
