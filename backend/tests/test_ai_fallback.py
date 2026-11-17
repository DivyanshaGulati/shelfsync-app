"""Cloud provider fallback: opt-in, safe, no API requests to live providers."""
import json
import httpx
import pytest
from app.core import claude_agent
from app.core.config import settings
from app.db.models import PurchaseOrder
from test_quote_comparison import quote_twice
from test_quotation_collection import setup


def set_providers(monkeypatch, primary='groq', fallback='claude'):
    monkeypatch.setattr(settings, 'ai_provider', primary)
    monkeypatch.setattr(settings, 'ai_fallback_provider', fallback)
    monkeypatch.setattr(settings, 'groq_api_key', 'mock-groq')
    monkeypatch.setattr(settings, 'claude_api_key', 'mock-claude')


def payload():
    return {'model':'dummy', 'max_tokens':100, 'system':'Read only',
            'messages':[{'role':'user','content':'Recommend'}], 'tools':[]}


def complete():
    return {'stop_reason':'end_turn','content':[{'type':'text','text':'{}'}]}


def test_default_off_no_fallback(monkeypatch):
    set_providers(monkeypatch, fallback='none')
    called=[]
    def transport(p, data):
        called.append(p)
        raise claude_agent.AgentUnavailable('outage')
    monkeypatch.setattr(claude_agent, '_provider_transport', transport)
    with pytest.raises(claude_agent.AgentUnavailable):
        claude_agent.call_provider_messages(payload())
    assert called==['groq']


def test_failover_once_on_outage_and_reverse(monkeypatch):
    for primary, secondary in [('groq','claude'), ('claude','groq')]:
        set_providers(monkeypatch, primary, secondary)
        calls=[]
        def transport(p, data):
            calls.append(p)
            if p==primary:
                raise claude_agent.AgentUnavailable('outage')
            return complete()
        monkeypatch.setattr(claude_agent, '_provider_transport', transport)
        assert claude_agent.call_provider_messages(payload()) == complete()
        assert calls == [primary, secondary]


def test_both_providers_down_stops_after_second(monkeypatch):
    set_providers(monkeypatch)
    calls=[]
    def transport(p,data):
        calls.append(p)
        raise claude_agent.AgentUnavailable('outage')
    monkeypatch.setattr(claude_agent, '_provider_transport', transport)
    with pytest.raises(claude_agent.AgentUnavailable):
        claude_agent.call_provider_messages(payload())
    assert calls==['groq','claude']


def test_no_fallback_for_missing_key_or_invalid_ai_output(monkeypatch):
    set_providers(monkeypatch)
    for exception in [claude_agent.AgentConfigurationError('missing key'),
                      claude_agent.AgentInvalidResponse('invalid tool')]:
        calls=[]
        def transport(p,data):
            calls.append(p)
            raise exception
        monkeypatch.setattr(claude_agent, '_provider_transport', transport)
        with pytest.raises(type(exception)):
            claude_agent.call_provider_messages(payload())
        assert calls==['groq']


def test_invalid_fallback_configuration_never_calls_any_provider(monkeypatch):
    set_providers(monkeypatch, primary='groq', fallback='groq')
    monkeypatch.setattr(claude_agent, '_provider_transport', lambda *_: pytest.fail('should not call'))
    with pytest.raises(claude_agent.AgentConfigurationError):
        claude_agent.call_provider_messages(payload())
    monkeypatch.setattr(settings,'ai_fallback_provider','ollama')
    with pytest.raises(claude_agent.AgentConfigurationError):
        claude_agent.call_provider_messages(payload())


def test_http_auth_errors_do_not_failover(monkeypatch):
    set_providers(monkeypatch)
    count=[]
    original = httpx.Client
    def client(*args, **kwargs):
        def handler(req):
            count.append(str(req.url))
            return httpx.Response(401,json={'error':'invalid key'})
        return original(transport=httpx.MockTransport(handler), *args, **kwargs)
    monkeypatch.setattr(claude_agent.httpx, 'Client', client)
    with pytest.raises(claude_agent.AgentConfigurationError):
        claude_agent.call_provider_messages(payload())
    assert len(count)==1 and 'groq' in count[0]


def test_endpoint_failover_preserves_purchasing_boundaries(setup, monkeypatch):
    client, factory, owner, employee=setup
    order_id, quotes=quote_twice(setup)
    set_providers(monkeypatch)
    calls=[]
    def transport(p, data):
        calls.append(p)
        if p=='groq':
            raise claude_agent.AgentUnavailable('rate limit')
        return {'stop_reason':'end_turn','content':[{'type':'text','text':json.dumps({
            'recommended_quotation_id':quotes[0]['id'],
            'reason':'Valid comparison based on price and delivery.',
            'comparison_summary':[]})}]}
    monkeypatch.setattr(claude_agent,'_provider_transport', transport)
    r=client.post(f'/api/purchase-requests/{order_id}/ai-recommendation',headers=owner)
    assert r.status_code == 200, r.text
    assert r.json()['authorization_status'] == 'NOT_AUTHORIZED_BY_AI'
    assert calls==['groq','claude']
    with factory() as db:
        order=db.get(PurchaseOrder,order_id)
        assert order.status=='WAITING_FOR_QUOTES'
        assert order.vendor_id is None and order.total_amount is None


def test_endpoint_both_down_does_not_save_recommendation(setup, monkeypatch):
    client, factory, owner, employee=setup
    order_id,_=quote_twice(setup)
    set_providers(monkeypatch)
    calls=[]
    def transport(p, data):
        calls.append(p)
        raise claude_agent.AgentUnavailable('offline')
    monkeypatch.setattr(claude_agent, '_provider_transport', transport)
    r=client.post(f'/api/purchase-requests/{order_id}/ai-recommendation',headers=owner)
    assert r.status_code == 503
    assert calls==['groq','claude']
    with factory() as db:
        order=db.get(PurchaseOrder,order_id)
        assert order.ai_recommended_quotation_id is None
        assert order.status=='WAITING_FOR_QUOTES'


def test_employee_denied_before_fallback(setup, monkeypatch):
    client, factory, owner, employee=setup
    order_id,_=quote_twice(setup)
    set_providers(monkeypatch)
    monkeypatch.setattr(claude_agent, '_provider_transport', lambda *_: pytest.fail('should not call provider'))
    r=client.post(f'/api/purchase-requests/{order_id}/ai-recommendation',headers=employee)
    assert r.status_code==403


def test_claude_tool_round_outage_restarts_exchange_on_groq(setup, monkeypatch):
    client, factory, owner, _ = setup
    order_id, quotes = quote_twice(setup)
    set_providers(monkeypatch, primary='claude', fallback='groq')
    requests = []
    real_client = httpx.Client

    def handler(request):
        body = json.loads(request.content)
        messages = body['messages']
        host = request.url.host
        requests.append(host)
        if host == 'api.anthropic.com':
            assert request.headers['x-api-key'] == 'mock-claude'
            if len(messages) > 1:
                return httpx.Response(503)
            return httpx.Response(200, json={'stop_reason': 'tool_use', 'content': [
                {'type': 'tool_use', 'id': 'toolu_claude', 'name': 'compare_received_quotations', 'input': {}}
            ]})

        assert request.headers['Authorization'] == 'Bearer mock-groq'
        if len(messages) == 2:
            assert [m['role'] for m in messages] == ['system', 'user']
            return httpx.Response(200, json={'choices': [{'finish_reason': 'tool_calls', 'message': {
                'tool_calls': [{'id': 'call_groq', 'type': 'function', 'function': {
                    'name': 'compare_received_quotations', 'arguments': '{}',
                }}],
            }}]})
        assert messages[-1]['tool_call_id'] == 'call_groq'
        result = {'recommended_quotation_id': quotes[0]['id'], 'reason': 'Eligible based on cost and delivery.'}
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
            'content': json.dumps(result),
        }}]})

    monkeypatch.setattr(claude_agent.httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    response = client.post(f'/api/purchase-requests/{order_id}/ai-recommendation', headers=owner)
    assert response.status_code == 200, response.text
    assert response.json()['authorization_status'] == 'NOT_AUTHORIZED_BY_AI'
    assert requests == ['api.anthropic.com', 'api.anthropic.com', 'api.groq.com', 'api.groq.com']
    with factory() as db:
        order = db.get(PurchaseOrder, order_id)
        assert order.status == 'WAITING_FOR_QUOTES'
        assert order.vendor_id is None and order.total_amount is None


@pytest.mark.parametrize('primary,fallback,host', [
    ('claude', 'groq', 'api.anthropic.com'),
    ('groq', 'claude', 'api.groq.com'),
])
def test_malformed_success_response_does_not_fail_over(monkeypatch, primary, fallback, host):
    set_providers(monkeypatch, primary, fallback)
    real_client = httpx.Client

    def handler(request):
        assert request.url.host == host
        return httpx.Response(200, text='broken JSON')

    monkeypatch.setattr(claude_agent.httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(claude_agent.AgentInvalidResponse, match='malformed JSON'):
        claude_agent.call_provider_messages(payload())
