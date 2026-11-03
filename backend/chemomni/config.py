from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_base_url: str | None = Field(default=None, alias="OPENAI_BASE_URL")

    provider: Literal["openai", "fake"] = Field(default="openai", alias="CHEMOMNI_PROVIDER")
    model: str = Field(default="gpt-6.1-sol", alias="CHEMOMNI_MODEL")
    reasoning_effort: str | None = Field(default="low", alias="CHEMOMNI_REASONING_EFFORT")
    db_path: Path = Field(default=Path("./data/chemomni.sqlite"), alias="CHEMOMNI_DB")
    context_budget: int = Field(default=24000, alias="CHEMOMNI_CONTEXT_BUDGET")
    static_dir: Path | None = Field(default=None, alias="CHEMOMNI_STATIC_DIR")

    @property
    def db_url(self) -> str:
        if str(self.db_path) == ":memory:":
            return "sqlite+aiosqlite:///:memory:"
        return f"sqlite+aiosqlite:///{self.db_path}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
