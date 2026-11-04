from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    # Read `.env` from the repository root and from `backend/` (the latter wins), regardless of
    # the directory the server is started from. Real environment variables override both.
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_base_url: str | None = Field(default=None, alias="OPENAI_BASE_URL")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")

    provider: Literal["openai", "fake"] = Field(default="openai", alias="CHEMOMNI_PROVIDER")
    model: str = Field(default="gpt-6.1-sol", alias="CHEMOMNI_MODEL")
    # Models offered in the UI's model picker (comma-separated). CHEMOMNI_MODEL is the initial one.
    models: str = Field(
        default="gpt-6.1-sol,gpt-6.1-terra,gpt-6-astra,gpt-6-luna,claude-haiku-5-5",
        alias="CHEMOMNI_MODELS",
    )
    # Which OpenAI API to call. "auto": Responses API for api.openai.com (required for tools on
    # reasoning models such as gpt-6.1-sol), Chat Completions when OPENAI_BASE_URL points elsewhere
    # (Ollama, vLLM, LM Studio).
    openai_api: Literal["auto", "responses", "chat"] = Field(default="auto", alias="CHEMOMNI_OPENAI_API")
    # Leave empty to omit the parameter (local OpenAI-compatible servers).
    reasoning_effort: str | None = Field(default="low", alias="CHEMOMNI_REASONING_EFFORT")

    @property
    def model_list(self) -> list[str]:
        """Selectable models; the default model is always included."""
        names = [m.strip() for m in self.models.split(",") if m.strip()]
        return list(dict.fromkeys([self.model, *names]))

    @staticmethod
    def is_anthropic(model: str) -> bool:
        return model.startswith("claude-")

    @property
    def use_responses_api(self) -> bool:
        if self.openai_api != "auto":
            return self.openai_api == "responses"
        return not self.openai_base_url

    db_path: Path = Field(default=BACKEND_DIR / "data" / "chemomni.sqlite", alias="CHEMOMNI_DB")
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
