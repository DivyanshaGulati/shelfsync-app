"""No network calls or credits: exercise provider routing and Ollama native protocol."""
import json

import httpx
import pytest
from sqlalchemy import select

from app.core import claude_agent
from app.core.config import Settings, settings
from app.db.models import PurchaseOrder, VendorQuotation
from test_quotation_collection import setup
from test_quote_comparison import quote_twice


def fake_ollama_client(monkeypatch, reply):
    requests = []
    original = httpx.Client

    def client(*args, **kwargs):
        def handler(request):
            requests.append((str(request.url), json.loads(request.content)))
            return reply(request, requests)
        return original(transport=httpx.MockTransport(handler), *args, **kwargs)

    monkeypatch.setattr(claude_agent.httpx, 'Client', client)
    return requests


def test_ollama_without_claude_key_uses_tools_and_preserves_order(setup, monkeypatch):
    client, factory, owner, employee = setup
    oid, quotes = quote_twice(setup)
    monkeypatch.setattr(settings, 'ai_provider', 'ollama')
    monkeypatch.setattr(settings, 'claude_api_key', '')
    monkeypatch.setattr(settings, 'ollama_model', 'qwen2.5:7b')

    def reply(request, calls):
        assert request.url.path == '/api/chat'
        payload = calls[-1][1]
        assert payload['model'] == 'qwen2.5:7b'
        assert payload['stream'] is False
        assert 'x-api-key' not in request.headers
        if len(calls) == 1:
            assert payload['tools'][0]['type'] == 'function'
            return httpx.Response(200, json={'message': {'role': 'assistant', 'content': '',
                 'tool_calls': [{'function': {'name': 'compare_received_quotations', 'arguments': {}}}]}})
        assert payload['messages'][-1]['role'] == 'tool'
        assert payload['messages'][-1]['tool_name'] == 'compare_received_quotations'
        assert 'quotations' in json.loads(payload['messages'][-1]['content'])
        return httpx.Response(200, json={'message': {'role': 'assistant', 'content': json.dumps({
            'recommended_quotation_id': quotes[0]['id'],
            'reason': 'Competitive price and acceptable delivery from a current received quotation.',
            'comparison_summary': [{'vendor_id': quotes[0]['vendor_id'], 'reason': 'Good terms'}]
        })}})
    requests = fake_ollama_client(monkeypatch, reply)
    response = client.post(f'/api/purchase-requests/{oid}/ai-recommendation', headers=owner)
    assert response.status_code == 200, response.text
    assert response.json()['recommended_quotation_id'] == quotes[0]['id']
    assert response.json()['authorization_status'] == 'NOT_AUTHORIZED_BY_AI'
    assert len(requests) == 2
    with factory() as db:
        order = db.get(PurchaseOrder, oid)
        assert order.status == 'WAITING_FOR_QUOTES'
        assert order.vendor_id is None and order.total_amount is None
        assert order.backend_candidate_quotation_id is None
        assert all(q.status == 'RECEIVED' for q in db.scalars(select(VendorQuotation)))


def test_ollama_invalid_quote_is_rejected_without_mutations(setup, monkeypatch):
    client, factory, owner, employee = setup
    oid, _ = quote_twice(setup)
    monkeypatch.setattr(settings, 'ai_provider', 'ollama')
    fake_ollama_client(monkeypatch, lambda *_: httpx.Response(200, json={'message': {
        'content': json.dumps({'recommended_quotation_id': 999999, 'reason': 'Invalid unrelated quote'})}}))
    response = client.post(f'/api/purchase-requests/{oid}/ai-recommendation', headers=owner)
    assert response.status_code == 409
    with factory() as db:
        order = db.get(PurchaseOrder, oid)
        assert order.ai_recommended_quotation_id is None
        assert order.status == 'WAITING_FOR_QUOTES'


@pytest.mark.parametrize('provider', ['ollama', 'claude'])
def test_missing_provider_credential_or_model_is_unavailable(setup, monkeypatch, provider):
    client, factory, owner, _ = setup
    oid, _ = quote_twice(setup)
    monkeypatch.setattr(settings, 'ai_provider', provider)
    if provider == 'claude':
        monkeypatch.setattr(settings, 'claude_api_key', '')
    else:
        monkeypatch.setattr(settings, 'ollama_model', '')
        monkeypatch.setattr(settings, 'claude_api_key', '')
    response = client.post(f'/api/purchase-requests/{oid}/ai-recommendation', headers=owner)
    assert response.status_code == 503
    assert response.json()['detail']['ai_status'] == 'UNAVAILABLE'
    with factory() as db:
        assert db.get(PurchaseOrder, oid).ai_recommended_quotation_id is None


def test_ollama_connection_failure_returns_unavailable(setup, monkeypatch):
    client, factory, owner, _ = setup
    oid, _ = quote_twice(setup)
    monkeypatch.setattr(settings, 'ai_provider', 'ollama')
    def fail(*_):
        raise httpx.ConnectError('offline')
    fake_ollama_client(monkeypatch, fail)
    response = client.post(f'/api/purchase-requests/{oid}/ai-recommendation', headers=owner)
    assert response.status_code == 503
    with factory() as db:
        assert db.get(PurchaseOrder, oid).status == 'WAITING_FOR_QUOTES'


def test_provider_switch_preserves_claude_transport(monkeypatch):
    monkeypatch.setattr(settings, 'ai_provider', 'claude')
    monkeypatch.setattr(settings, 'claude_api_key', 'secret-key')
    monkeypatch.setattr(settings, 'claude_model', 'test-model')
    urls = []
    def reply(request, _calls):
        urls.append(str(request.url))
        assert request.headers['x-api-key'] == 'secret-key'
        return httpx.Response(200, json={'stop_reason': 'end_turn', 'content': []})
    fake_ollama_client(monkeypatch, reply)
    result = claude_agent.call_provider_messages({'model': 'test-model', 'messages': [], 'tools': []})
    assert result['stop_reason'] == 'end_turn'
    assert urls == ['https://api.anthropic.com/v1/messages']


def test_unsupported_provider_is_unavailable(monkeypatch):
    monkeypatch.setattr(settings, 'ai_provider', 'unknown')
    with pytest.raises(claude_agent.AgentUnavailable):
        claude_agent.call_provider_messages({})


def test_ollama_failing_http_and_invalid_tool_arguments(monkeypatch):
    monkeypatch.setattr(settings, 'ai_provider', 'ollama')
    monkeypatch.setattr(settings, 'ollama_model', 'test-model')
    payload = {'model': 'ignored', 'system': 'Safe AI', 'tools': claude_agent.TOOLS,
               'messages': [{'role': 'user', 'content': 'Inspect'}]}
    with monkeypatch.context() as patch:
        fake_ollama_client(patch, lambda *_: httpx.Response(404, json={'error':'model missing'}))
        with pytest.raises(claude_agent.AgentUnavailable):
            claude_agent.call_provider_messages(payload)
    with monkeypatch.context() as patch:
        fake_ollama_client(patch, lambda *_: httpx.Response(200, json={'message': {
            'tool_calls': [{'function': {'name': 'get_inventory_context', 'arguments': '{bad'}}]}}))
        with pytest.raises(claude_agent.AgentInvalidResponse):
            claude_agent.call_provider_messages(payload)


def test_settings_accept_ollama_without_claude_key(monkeypatch):
    monkeypatch.setenv('AI_PROVIDER', 'ollama')
    monkeypatch.setenv('OLLAMA_MODEL', 'qwen2.5:7b')
    monkeypatch.setenv('CLAUDE_API_KEY', '')
    config = Settings(_env_file=None)
    assert config.ai_provider == 'ollama'
    assert config.claude_api_key == ''
    assert config.ollama_model == 'qwen2.5:7b'
