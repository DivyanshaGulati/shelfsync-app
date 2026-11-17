from unittest.mock import patch
import smtplib
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.config import settings
from app.api.contact import _attempts
client = TestClient(app)
DATA = dict(name='Visitor', email='visitor@gmail.com', message='Please tell me about ShelfSync.')
@pytest.fixture(autouse=True)
def configure(monkeypatch):
    _attempts.clear()
    for k, v in dict(enable_contact_email=True, email_host='smtp.example.org', email_port=465, email_username='hello@shelfsync.in', email_password='test-only', owner_email='admin@shelfsync.in').items():
        monkeypatch.setattr(settings, k, v)
    yield
    _attempts.clear()
def test_fixed_recipient_with_purchasing_mock():
    with patch('app.api.contact.send_smtp') as send:
        assert client.post('/api/contact', json={**DATA, 'to': 'other@gmail.com'}).status_code == 200
        assert send.call_args.kwargs['to'] == 'admin@shelfsync.in'
        assert DATA['email'] in send.call_args.kwargs['body']
        assert settings.email_mode == 'mock'
        assert settings.enable_real_email_dispatch is False
def test_disabled(monkeypatch):
    monkeypatch.setattr(settings, 'enable_contact_email', False)
    with patch('app.api.contact.send_smtp') as send:
        assert client.post('/api/contact', json=DATA).status_code == 503
        send.assert_not_called()
def test_honeypot():
    with patch('app.api.contact.send_smtp') as send:
        assert client.post('/api/contact', json={**DATA, 'website': 'spam'}).status_code == 200
        send.assert_not_called()
def test_rate_limit():
    with patch('app.api.contact.send_smtp') as send:
        for _ in range(3):
            assert client.post('/api/contact', json=DATA).status_code == 200
        assert client.post('/api/contact', json=DATA, headers={'X-Forwarded-For': '9.9.9.9'}).status_code == 429
        assert send.call_count == 3
@pytest.mark.parametrize('change', [{'email': 'a\r\nBcc:x@gmail.com'}, {'message': 'x'*4001}])
def test_invalid(change):
    with patch('app.api.contact.send_smtp') as send:
        assert client.post('/api/contact', json={**DATA, **change}).status_code == 422
        send.assert_not_called()
def test_failure():
    with patch('app.api.contact.send_smtp', side_effect=smtplib.SMTPException('secret-value')) as send:
        response = client.post('/api/contact', json=DATA)
        assert response.status_code == 502
        assert 'secret-value' not in response.text
        assert send.call_count == 1
