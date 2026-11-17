"""Groq wire format, provider switching and purchasing guardrails; no real API calls."""
import json
import httpx
import pytest
from sqlalchemy import select

from app.core import claude_agent
from app.core.config import settings, Settings
from app.db.models import PurchaseOrder, VendorQuotation
from test_quotation_collection import setup, create_request
from test_quote_comparison import quote_twice


def fake_groq(monkeypatch, responder):
    requests = []
    original = httpx.Client
    def client(*args, **kwargs):
        def handler(request):
            payload = json.loads(request.content)
            requests.append((request, payload))
            return responder(request, payload, len(requests))
        return original(transport=httpx.MockTransport(handler), *args, **kwargs)
    monkeypatch.setattr(claude_agent.httpx, 'Client', client)
    return requests


def settings_groq(monkeypatch):
    monkeypatch.setattr(settings, 'ai_provider', 'groq')
    monkeypatch.setattr(settings, 'groq_api_key', 'mock-test-token')
    monkeypatch.setattr(settings, 'groq_model', 'openai/gpt-oss-20b')


def test_groq_tool_roundtrip_recommendation_and_order_is_unchanged(setup, monkeypatch):
    client, factory, owner, employee = setup
    oid, quotes = quote_twice(setup)
    settings_groq(monkeypatch)
    monkeypatch.setattr(settings, 'ai_provider', 'claude')
    monkeypatch.setattr(settings, 'ai_fallback_provider', 'groq')
    monkeypatch.setattr(settings, 'claude_api_key', '  ')
    def responder(req, payload, n):
        assert str(req.url) == 'https://api.groq.com/openai/v1/chat/completions'
        assert req.headers['Authorization'] == 'Bearer mock-test-token'
        assert 'x-api-key' not in req.headers
        assert payload['model'] == 'openai/gpt-oss-20b'
        assert payload['reasoning_effort'] == 'low'
        assert payload['reasoning_format'] == 'hidden'
        assert payload['parallel_tool_calls'] is False
        assert payload['tools'][0]['function']['parameters']['type'] == 'object'
        if n == 1:
            return httpx.Response(200, json={'choices':[{'finish_reason':'tool_calls', 'message': {
                'tool_calls':[{'id':'call_test_1','type':'function','function': {
                    'name':'compare_received_quotations','arguments':'{}'}}]}}]})
        assert payload['messages'][-2]['tool_calls'][0]['id'] == 'call_test_1'
        assert payload['messages'][-2]['content'] == ''
        assert payload['messages'][-1]['role'] == 'tool'
        assert payload['messages'][-1]['tool_call_id'] == 'call_test_1'
        assert 'quotations' in json.loads(payload['messages'][-1]['content'])
        return httpx.Response(200, json={'choices':[{'finish_reason':'stop', 'message': {
            'content': json.dumps({'recommended_quotation_id': quotes[0]['id'],
             'reason':'Best eligible quote based on current price and delivery.',
             'comparison_summary':[{'vendor_id': quotes[0]['vendor_id'], 'reason': 'Good pricing'}]})}}]})
    requests = fake_groq(monkeypatch, responder)
    response = client.post(f'/api/purchase-requests/{oid}/ai-recommendation', headers=owner)
    assert response.status_code == 200, response.text
    assert response.json()['authorization_status'] == 'NOT_AUTHORIZED_BY_AI'
    assert len(requests) == 2
    with factory() as db:
        order = db.get(PurchaseOrder, oid)
        assert order.status == 'WAITING_FOR_QUOTES'
        assert order.vendor_id is None and order.total_amount is None
        assert order.backend_candidate_quotation_id is None
        assert all(q.status == 'RECEIVED' for q in db.scalars(select(VendorQuotation)))


def test_groq_draft_tool_use_and_authoritative_terms(setup, monkeypatch):
    client, factory, owner, employee = setup
    oid = create_request(setup)
    settings_groq(monkeypatch)
    def responder(req, payload, n):
        if n == 1:
            assert [t['function']['name'] for t in payload['tools']] == ['request_vendor_quotations']
            return httpx.Response(200, json={'choices':[{'finish_reason':'tool_calls','message':{
                'tool_calls':[{'id':'draft_1','type':'function','function':{
                    'name':'request_vendor_quotations','arguments':'{}'}}]}}]})
        assert payload['messages'][-1]['tool_call_id'] == 'draft_1'
        return httpx.Response(200, json={'choices':[{'finish_reason':'stop','message':{
            'content':'{"note":"Please let us know if you need clarification."}'}}]})
    fake_groq(monkeypatch, responder)
    r = client.post(f'/api/purchase-requests/{oid}/draft-quotation-requests',headers=owner,json={'use_ai':True})
    assert r.status_code == 200, r.text
    assert r.json()['ai_status']=='AVAILABLE'
    assert all(d['dispatch_status']=='NOT_SENT_PREVIEW_ONLY' for d in r.json()['drafts'])
    with factory() as db:
        order = db.get(PurchaseOrder,oid)
        assert order.status == 'WAITING_FOR_QUOTES'
        assert all(q.status == 'REQUESTED' for q in db.scalars(select(VendorQuotation)))


def test_groq_invalid_quote_cannot_mutate_purchase(setup, monkeypatch):
    client, factory, owner, employee = setup
    oid, _ = quote_twice(setup)
    settings_groq(monkeypatch)
    fake_groq(monkeypatch,lambda *_: httpx.Response(200,json={'choices':[{'finish_reason':'stop', 'message':{
        'content':json.dumps({'recommended_quotation_id':999999,'reason':'Out of scope supplier ID'})}}]}))
    r=client.post(f'/api/purchase-requests/{oid}/ai-recommendation',headers=owner)
    assert r.status_code==409
    with factory() as db:
        order=db.get(PurchaseOrder,oid)
        assert order.status=='WAITING_FOR_QUOTES'
        assert order.ai_recommended_quotation_id is None


def test_groq_forbidden_tool_fails_closed(setup, monkeypatch):
    client, factory, owner, employee = setup
    oid = create_request(setup)
    settings_groq(monkeypatch)
    fake_groq(monkeypatch,lambda *_: httpx.Response(200,json={'choices':[{'finish_reason':'tool_calls','message':{
        'tool_calls':[{'id':'bad_1','type':'function','function':{'name':'approve_purchase_order','arguments':'{}'}}]}}]}))
    r=client.post(f'/api/purchase-requests/{oid}/draft-quotation-requests',headers=owner,json={'use_ai':True})
    assert r.status_code==409
    with factory() as db:
        assert db.get(PurchaseOrder,oid).status=='WAITING_FOR_QUOTES'


def test_groq_missing_key_and_outage_do_not_fallback(setup, monkeypatch):
    client, factory, owner, employee = setup
    oid, _ = quote_twice(setup)
    settings_groq(monkeypatch)
    monkeypatch.setattr(settings,'groq_api_key','')
    monkeypatch.setattr(settings,'claude_api_key','not-used')
    r=client.post(f'/api/purchase-requests/{oid}/ai-recommendation',headers=owner)
    assert r.status_code==503
    monkeypatch.setattr(settings,'groq_api_key','mock-token')
    fake_groq(monkeypatch,lambda *_: httpx.Response(429,json={'error':'rate limit'}))
    r=client.post(f'/api/purchase-requests/{oid}/ai-recommendation',headers=owner)
    assert r.status_code==503
    with factory() as db:
        assert db.get(PurchaseOrder,oid).ai_recommended_quotation_id is None


def test_groq_bad_tool_args_and_bad_response(monkeypatch):
    settings_groq(monkeypatch)
    payload={'model':'ignored','system':'read only','max_tokens':100,'tools':claude_agent.TOOLS,
             'messages':[{'role':'user','content':'Get context'}]}
    with monkeypatch.context() as m:
        fake_groq(m,lambda *_: httpx.Response(200,json={'choices':[{'finish_reason':'tool_calls','message':{
            'tool_calls':[{'id':'bad','function':{'name':'get_inventory_context','arguments':'{not-json'}}]}}]}))
        with pytest.raises(claude_agent.AgentInvalidResponse):
            claude_agent.call_provider_messages(payload)
    with monkeypatch.context() as m:
        fake_groq(m,lambda *_: httpx.Response(200,json={'choices':[{'finish_reason':'length','message':{'content':'{}'}}]}))
        with pytest.raises(claude_agent.AgentInvalidResponse):
            claude_agent.call_provider_messages(payload)


def test_groq_employee_rejected_before_provider_call(setup, monkeypatch):
    client, factory, owner, employee = setup
    oid, _ = quote_twice(setup)
    settings_groq(monkeypatch)
    fake_groq(monkeypatch,lambda *_: pytest.fail('Groq should not be called'))
    r=client.post(f'/api/purchase-requests/{oid}/ai-recommendation',headers=employee)
    assert r.status_code==403


def test_env_config_accepts_groq_without_claude(monkeypatch):
    monkeypatch.delenv('AI_FALLBACK_PROVIDER', raising=False)
    monkeypatch.setenv('AI_PROVIDER','groq')
    monkeypatch.setenv('GROQ_API_KEY','test-key')
    monkeypatch.setenv('CLAUDE_API_KEY','')
    config=Settings(_env_file=None)
    assert config.ai_provider=='groq'
    assert config.groq_api_key=='test-key'
    assert config.claude_api_key==''
    assert claude_agent.provider_route(config) == ('groq', 'none')
