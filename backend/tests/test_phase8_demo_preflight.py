from types import SimpleNamespace
from unittest.mock import Mock

from app.verification.demo_preflight import configuration_findings, inspect_demo


def _config(**overrides):
    data = dict(email_mode="mock", enable_real_email_dispatch=False, enable_quote_simulation=True,
                enable_busy_week_simulation=True, ai_provider="groq", ai_fallback_provider="none",
                claude_api_key="")
    data.update(overrides)
    return SimpleNamespace(**data)


def test_safe_demo_configuration():
    assert configuration_findings(_config()) == []
    assert configuration_findings(_config(ai_provider="claude", ai_fallback_provider="groq")) == []


def test_real_smtp_is_rejected_for_demo():
    assert any("EMAIL_MODE" in msg for msg in configuration_findings(_config(email_mode="smtp")))
    assert any("EMAIL_MODE" in msg for msg in configuration_findings(_config(enable_real_email_dispatch=True)))


def test_demo_requires_explicit_opt_in():
    findings = configuration_findings(_config(enable_quote_simulation=False, enable_busy_week_simulation=False))
    assert len(findings) == 2


def test_demo_preflight_flags_missing_seed_rows():
    session = Mock()
    session.scalars.return_value.all.return_value = []
    findings = inspect_demo(session)
    assert any("HW-SCR-15" in item for item in findings)
    assert any("OWNER" in item for item in findings)
    assert any("EMPLOYEE" in item for item in findings)
