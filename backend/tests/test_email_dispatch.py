"""RFQ dispatch tests: no real SMTP calls are made."""
import pytest
from sqlalchemy import select
from app.core.config import settings
from app.core import email_service
from app.db.models import PurchaseOrder, QuotationEmailDispatch, Vendor, VendorQuotation
from test_quotation_collection import setup, create_request


@pytest.fixture
def email_setup(setup, monkeypatch):
    monkeypatch.setattr(settings, 'email_mode', 'mock')
    monkeypatch.setattr(settings, 'enable_real_email_dispatch', False)
    monkeypatch.setattr(settings, 'email_host', '')
    monkeypatch.setattr(settings, 'email_username', '')
    monkeypatch.setattr(settings, 'email_password', '')
    monkeypatch.setattr(settings, 'email_port', 587)
    return setup


def endpoint(oid):
    return f'/api/purchase-requests/{oid}/dispatch-quotation-requests'


def test_mock_dispatch_persists_and_preserves_request(email_setup, monkeypatch):
    client, factory, owner, employee = email_setup
    oid = create_request(email_setup)
    monkeypatch.setattr(email_service, 'send_smtp', lambda **kw: pytest.fail('mock must not send SMTP'))
    r = client.post(endpoint(oid), headers=owner)
    assert r.status_code == 200, r.text
    assert r.json()['email_mode'] == 'mock'
    assert {d['status'] for d in r.json()['dispatches']} == {'MOCKED'}
    assert not r.json()['final_purchase_order_email_sent']
    assert {q['dispatch_status'] for q in client.get(f'/api/purchase-requests/{oid}/quotations', headers=owner).json()['quotations']} == {'MOCKED'}
    with factory() as db:
        po = db.get(PurchaseOrder, oid)
        assert po.status == 'WAITING_FOR_QUOTES' and po.total_amount is None and po.vendor_id is None
        assert all(q.status == 'REQUESTED' and q.quoted_unit_price is None for q in db.scalars(select(VendorQuotation)))
        attempts = list(db.scalars(select(QuotationEmailDispatch)))
        assert len(attempts) == 2
        assert all(a.status == 'MOCKED' and a.completed_at for a in attempts)


def test_repeated_dispatch_idempotent(email_setup, monkeypatch):
    client, factory, owner, employee = email_setup
    oid = create_request(email_setup)
    assert client.post(endpoint(oid), headers=owner).status_code == 200
    r = client.post(endpoint(oid), headers=owner)
    assert r.status_code == 200, r.text
    assert all(d['replayed'] for d in r.json()['dispatches'])
    with factory() as db:
        assert len(list(db.scalars(select(QuotationEmailDispatch)))) == 2


def test_employee_and_anonymous_cannot_dispatch(email_setup):
    client, factory, owner, employee = email_setup
    oid = create_request(email_setup)
    assert client.post(endpoint(oid)).status_code == 401
    assert client.post(endpoint(oid), headers=employee).status_code == 403
    with factory() as db:
        assert list(db.scalars(select(QuotationEmailDispatch))) == []


def test_live_smtp_requires_explicit_opt_in_and_credentials(email_setup, monkeypatch):
    client, factory, owner, employee = email_setup
    oid = create_request(email_setup)
    monkeypatch.setattr(settings, 'email_mode', 'smtp')
    r = client.post(endpoint(oid), headers=owner)
    assert r.status_code == 409 and 'disabled' in r.text
    monkeypatch.setattr(settings, 'enable_real_email_dispatch', True)
    r = client.post(endpoint(oid), headers=owner)
    assert r.status_code == 409 and 'incomplete' in r.text
    with factory() as db:
        assert list(db.scalars(select(QuotationEmailDispatch))) == []


def configure_smtp(email_setup, monkeypatch):
    _, factory, _, _ = email_setup
    with factory.begin() as db:
        for v in db.scalars(select(Vendor)):
            v.email = v.email.replace('@example.com', '@supplier-company.com')
    monkeypatch.setattr(settings, 'email_mode', 'smtp')
    monkeypatch.setattr(settings, 'enable_real_email_dispatch', True)
    monkeypatch.setattr(settings, 'email_host', 'smtp.titan.email')
    monkeypatch.setattr(settings, 'email_username', 'orders@mybusiness.com')
    monkeypatch.setattr(settings, 'email_password', 'test-not-a-real-password')


def test_live_smtp_rejects_demo_vendor_addresses(email_setup, monkeypatch):
    client, factory, owner, employee = email_setup
    oid = create_request(email_setup)
    monkeypatch.setattr(settings, 'email_mode', 'smtp')
    monkeypatch.setattr(settings, 'enable_real_email_dispatch', True)
    monkeypatch.setattr(settings, 'email_host', 'smtp.titan.email')
    monkeypatch.setattr(settings, 'email_username', 'orders@mybusiness.com')
    monkeypatch.setattr(settings, 'email_password', 'fake')
    r = client.post(endpoint(oid), headers=owner)
    assert r.status_code == 409 and 'demo-only' in r.text
    with factory() as db:
        assert list(db.scalars(select(QuotationEmailDispatch))) == []


def test_smtp_uses_current_backend_draft_and_is_idempotent(email_setup, monkeypatch):
    client, factory, owner, employee = email_setup
    oid = create_request(email_setup)
    configure_smtp(email_setup, monkeypatch)
    called=[]
    monkeypatch.setattr(email_service, 'send_smtp', lambda **kw: called.append(kw))
    r=client.post(endpoint(oid), headers=owner)
    assert r.status_code == 200, r.text
    assert len(called) == 2
    assert all(c['to'].endswith('@supplier-company.com') for c in called)
    assert all('current unit price' in c['body'] and 'RFQ-' in c['body'] for c in called)
    assert all(d['status'] == 'SENT' for d in r.json()['dispatches'])
    r2=client.post(endpoint(oid), headers=owner)
    assert r2.status_code == 200 and all(d['replayed'] for d in r2.json()['dispatches'])
    assert len(called) == 2
    with factory() as db:
        assert all(a.status == 'SENT' for a in db.scalars(select(QuotationEmailDispatch)))
        assert db.get(PurchaseOrder, oid).vendor_id is None


def test_smtp_failure_marks_uncertain_no_automatic_resend(email_setup, monkeypatch):
    client, factory, owner, employee = email_setup
    oid = create_request(email_setup)
    configure_smtp(email_setup, monkeypatch)
    sent=[]
    def fail(**kw):
        sent.append(kw)
        raise OSError('synthetic private network error')
    monkeypatch.setattr(email_service, 'send_smtp', fail)
    first = client.post(endpoint(oid), headers=owner)
    assert first.status_code == 200, first.text
    assert {d['status'] for d in first.json()['dispatches']} == {'UNCERTAIN'}
    replay = client.post(endpoint(oid), headers=owner)
    assert replay.status_code == 200
    assert len(sent) == 2
    assert all(d['replayed'] for d in replay.json()['dispatches'])
    with factory() as db:
        assert all(a.error_code == 'SMTP_OUTCOME_UNKNOWN' for a in db.scalars(select(QuotationEmailDispatch)))


def test_mock_then_explicit_smtp_dispatch_allowed(email_setup, monkeypatch):
    client, factory, owner, employee = email_setup
    oid = create_request(email_setup)
    assert client.post(endpoint(oid), headers=owner).status_code == 200
    configure_smtp(email_setup, monkeypatch)
    sends=[]
    monkeypatch.setattr(email_service, 'send_smtp', lambda **kw: sends.append(kw))
    r=client.post(endpoint(oid), headers=owner)
    assert r.status_code == 200 and all(d['status'] == 'SENT' for d in r.json()['dispatches'])
    assert len(sends)==2
    with factory() as db:
        assert len(list(db.scalars(select(QuotationEmailDispatch)))) == 2


def test_expired_or_received_quotes_never_dispatched(email_setup):
    client, factory, owner, employee = email_setup
    oid = create_request(email_setup)
    quotes=client.get(f'/api/purchase-requests/{oid}/quotations', headers=owner).json()['quotations']
    r=client.post(f'/api/purchase-requests/{oid}/quotations/{quotes[0]["id"]}/response', headers=owner,
                  json={'quoted_unit_price':'19','quoted_lead_time_days':'2'})
    assert r.status_code == 200
    sent=client.post(endpoint(oid), headers=owner)
    assert sent.status_code == 200 and len(sent.json()['dispatches']) == 1
    with factory() as db:
        assert {a.quotation_id for a in db.scalars(select(QuotationEmailDispatch))} == {quotes[1]['id']}


def test_bad_mode_rejected_without_side_effects(email_setup, monkeypatch):
    client, factory, owner, employee = email_setup
    oid=create_request(email_setup)
    monkeypatch.setattr(settings,'email_mode','random')
    r=client.post(endpoint(oid),headers=owner)
    assert r.status_code==409
    with factory() as db:
        assert list(db.scalars(select(QuotationEmailDispatch))) == []


def test_smtp_uses_starttls_on_587(monkeypatch):
    seen = []
    class FakeSMTP:
        def __init__(self, host, port, timeout):
            seen.append(('connect', host, port, timeout))
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def ehlo(self): seen.append('ehlo')
        def starttls(self, context): seen.append('starttls')
        def login(self, username, password): seen.append(('login', username))
        def send_message(self, msg):
            assert msg['Subject'] == 'Fresh RFQ'
            assert msg['To'] == 'vendor@supplier-company.com'
            seen.append('send')
    monkeypatch.setattr(settings, 'email_host', 'smtp.titan.email')
    monkeypatch.setattr(settings, 'email_port', 587)
    monkeypatch.setattr(settings, 'email_username', 'orders@mybusiness.com')
    monkeypatch.setattr(settings, 'email_password', 'not-a-real-password')
    monkeypatch.setattr(email_service.smtplib, 'SMTP', FakeSMTP)
    email_service.send_smtp(to='vendor@supplier-company.com',subject='Fresh RFQ',body='Reference RFQ-1',message_id='<rfq@mybusiness.com>')
    assert 'starttls' in seen and 'send' in seen
    assert seen.index('starttls') < seen.index(('login', 'orders@mybusiness.com'))


def test_smtp_uses_ssl_on_465(monkeypatch):
    seen=[]
    class FakeSMTP:
        def __init__(self, host, port, timeout, context): seen.append((host, port))
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def login(self, *args): seen.append('login')
        def send_message(self, message): seen.append('send')
    monkeypatch.setattr(settings, 'email_host', 'smtp.titan.email')
    monkeypatch.setattr(settings, 'email_port', 465)
    monkeypatch.setattr(settings, 'email_username', 'orders@mybusiness.com')
    monkeypatch.setattr(settings, 'email_password', 'not-a-real-password')
    monkeypatch.setattr(email_service.smtplib, 'SMTP_SSL', FakeSMTP)
    email_service.send_smtp(to='vendor@supplier-company.com',subject='Fresh RFQ',body='Reference RFQ-1',message_id='<rfq@mybusiness.com>')
    assert seen == [('smtp.titan.email',465), 'login','send']
