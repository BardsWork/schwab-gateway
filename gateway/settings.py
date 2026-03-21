"""Gateway configuration via pydantic-settings (loads from .env)."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    schwab_app_key: str
    schwab_api_secret: str
    schwab_callback_url: str

    token_path: Path = Path.home() / ".schwab" / "token.json"
    host: str = "0.0.0.0"
    port: int = 8182
    alert_threshold_days: float = 2.0


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]
    return _settings
