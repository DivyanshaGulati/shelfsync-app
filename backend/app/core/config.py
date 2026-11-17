"""Environment-based configuration; secrets never belong in frontend code."""
from pathlib import Path
from decimal import Decimal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://shelfsync:shelfsync_local_password@localhost:5432/shelfsync"
    jwt_secret: str = ""  # Required for signed access tokens; validated on token creation/decoding.
    ai_provider: str = "claude"
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-20b"
    groq_timeout_seconds: int = Field(default=15, ge=1, le=120)
    ai_fallback_provider: str = ""  # Blank defaults to Groq for Claude, or no fallback otherwise.
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:7b"
    ollama_timeout_seconds: int = Field(default=45, ge=5, le=600)
    ollama_max_output_tokens: int = Field(default=350, ge=64, le=4096)
    ollama_keep_alive: str = "30s"
    claude_api_key: str = ""
    claude_model: str = "claude-sonnet-5-5"
    enable_busy_week_simulation: bool = False  # Owner-only opt-in; requires mock email mode.
    enable_quote_simulation: bool = False  # Explicit demo-only opt-in; never used for real SMTP workflows.
    enable_mock_purchasing: bool = False  # Automatic AI ordering and simulated delivery, mock-only.
    email_mode: str = "mock"  # mock or smtp; mock is the safe default
    enable_real_email_dispatch: bool = False
    email_timeout_seconds: int = Field(default=12, ge=1, le=120)
    email_host: str = ""
    email_port: int = 587
    email_username: str = ""
    email_password: str = ""
    enable_contact_email: bool = False  # Independent of purchasing email dispatch.
    owner_email: str = ""
    quote_collection_window_minutes: int = Field(default=60, ge=1, le=10080)
    max_acceptable_lead_time_days: int = Field(default=7, ge=0)
    auto_order_limit: Decimal = Field(default=Decimal("2000"), ge=0)
    service_level_z: float = Field(default=1.65, gt=0)
    review_period_days: int = Field(default=7, ge=0)
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
