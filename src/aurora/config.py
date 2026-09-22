from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    google_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    aurora_domain_db: Path = PROJECT_ROOT / "var" / "aurora.db"
    aurora_session_db: Path = PROJECT_ROOT / "var" / "sessions.db"

    @property
    def data_dir(self) -> Path:
        return PROJECT_ROOT / "dados"

    @property
    def session_db_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.aurora_session_db}"


@lru_cache
def get_settings() -> Settings:
    return Settings()

