"""App settings, read from environment variables and the .env file."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    anthropic_api_key: str = ""
    claude_model: str = "claude-haiku-5-5"
    backend_url: str = "http://localhost:8000"
    database_url: str = "sqlite:///data/tarnishedbot.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
