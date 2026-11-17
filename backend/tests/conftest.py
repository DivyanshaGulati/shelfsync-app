import pytest

from app.core.config import Settings, settings


@pytest.fixture(autouse=True)
def isolate_external_service_settings(monkeypatch):
    """Keep tests independent of developer keys, providers and mail settings."""
    prefixes = ('ai_provider', 'ai_fallback_provider', 'claude_', 'groq_', 'ollama_', 'email_', 'enable_')
    for name, field in Settings.model_fields.items():
        if name.startswith(prefixes):
            monkeypatch.setattr(settings, name, field.default)
    monkeypatch.setattr(settings, 'ai_fallback_provider', 'none')
