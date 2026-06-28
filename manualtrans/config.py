from __future__ import annotations

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, EnvSettingsSource, SettingsConfigDict


def _split_csv(value):
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class CustomEnvSettingsSource(EnvSettingsSource):
    def prepare_field_value(self, field_name, field, value, value_is_complex):
        if field_name in ("openrouter_models", "output_formats"):
            return _split_csv(value) if isinstance(value, str) else value
        return super().prepare_field_value(field_name, field, value, value_is_complex)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore"
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        return (
            init_settings,
            CustomEnvSettingsSource(settings_cls),
            dotenv_settings,
            file_secret_settings,
        )

    mistral_api_key: str = ""
    openrouter_api_key: str = ""
    openrouter_models: list[str] = []
    ocr_model: str = "mistral-ocr-2512"
    output_formats: list[str] = ["pdf", "docx"]
    header_footer_policy: str = "keep_once"
    model_attempts: int = 2
    cache_dir: Path = Path(".cache")

    @field_validator("openrouter_models", "output_formats", mode="before")
    @classmethod
    def _parse_lists(cls, v):
        return _split_csv(v)


def get_settings(**overrides) -> Settings:
    return Settings(**overrides)
