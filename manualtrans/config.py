from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _split_csv(value):
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mistral_api_key: str = ""
    openrouter_api_key: str = ""
    # NoDecode stops pydantic-settings from JSON-decoding these from env/.env
    # sources, so _parse_lists can split the comma-separated string instead.
    openrouter_models: Annotated[list[str], NoDecode] = []
    ocr_model: str = "mistral-ocr-2512"
    output_formats: Annotated[list[str], NoDecode] = ["pdf", "docx"]
    header_footer_policy: str = "keep_once"
    model_attempts: int = 2
    translate_concurrency: int = 8
    cache_dir: Path = Path(".cache")

    @field_validator("openrouter_models", "output_formats", mode="before")
    @classmethod
    def _parse_lists(cls, v):
        return _split_csv(v)


def get_settings(**overrides) -> Settings:
    return Settings(**overrides)
