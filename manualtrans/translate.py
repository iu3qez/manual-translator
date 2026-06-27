from __future__ import annotations

import logging
import time

import httpx

from .cache import Cache
from .models import Doc

logger = logging.getLogger(__name__)


class TranslationError(Exception):
    pass


class OpenRouterTranslator:
    def __init__(
        self,
        api_key: str,
        models: list[str],
        system_prompt: str,
        attempts: int = 2,
        client: httpx.Client | None = None,
        base_url: str = "https://openrouter.ai/api/v1",
    ):
        if not models:
            raise TranslationError("no models configured")
        self.api_key = api_key
        self.models = models
        self.system_prompt = system_prompt
        self.attempts = attempts
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=120.0)
        self.last_model: str | None = None

    def _call(self, model: str, markdown: str) -> str:
        resp = self._client.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": markdown},
                ],
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    def translate_page(self, markdown: str) -> str:
        last_exc: Exception | None = None
        for model in self.models:
            for attempt in range(1, self.attempts + 1):
                try:
                    result = self._call(model, markdown)
                    self.last_model = model
                    return result
                except Exception as exc:  # noqa: BLE001 - retry/fallback on any failure
                    last_exc = exc
                    logger.warning("model %s attempt %d failed: %s", model, attempt, exc)
                    if attempt < self.attempts:
                        time.sleep(2 ** (attempt - 1))
            logger.warning("model %s exhausted, falling back", model)
        raise TranslationError(f"all models failed; last error: {last_exc}")


def translate_document(
    doc: Doc,
    translator: OpenRouterTranslator,
    cache: Cache,
    prompt_version: str = "v1",
) -> Doc:
    out = doc.model_copy(deep=True)
    models_key = ",".join(translator.models)
    for page in out.pages:
        key = cache.key(
            doc.source_hash, "translate", models_key, prompt_version, str(page.index)
        )
        cached = cache.get(key)
        if cached is not None:
            page.markdown = cached
            continue
        translated = translator.translate_page(page.markdown)
        cache.set(key, translated)
        page.markdown = translated
    return out
