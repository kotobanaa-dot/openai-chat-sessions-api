from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_timeout_seconds: float = 60.0
    openai_max_retries: int = 3
    openai_max_output_tokens: int = 512

    # Database
    database_url: str = "postgresql+asyncpg://chat:chat@localhost:5433/chat"

    # Application
    app_env: str = "local"
    log_level: str = "INFO"
    context_max_messages: int = 20
    max_message_chars: int = 8000
    max_messages_per_session: int = 100

    # Optional shared secret for a public deployment. Empty means auth is off.
    api_key: str = ""

    pricing_file: Path = BASE_DIR / "pricing.yaml"

    @property
    def auth_enabled(self) -> bool:
        return bool(self.api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
