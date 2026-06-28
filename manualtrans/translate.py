from __future__ import annotations

import hashlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor

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
        text, model = self._translate_page_with_model(markdown)
        self.last_model = model
        return text

    def _translate_page_with_model(self, markdown: str) -> tuple[str, str]:
        """Translate one page, returning (text, model_used).

        Any failure on a model (rate limit / 5xx / timeout) moves immediately to
        the NEXT model — no point retrying a model that just rate-limited us.
        Only when a full pass over every model fails do we back off and retry the
        whole list, up to ``attempts`` passes.
        """
        last_exc: Exception | None = None
        for attempt in range(self.attempts):
            for model in self.models:
                try:
                    return self._call(model, markdown), model
                except Exception as exc:  # noqa: BLE001 - fall back on any failure
                    last_exc = exc
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                    reason = "rate limit (429)" if status == 429 else f"error ({status or exc})"
                    logger.warning("model %s %s — trying next model", model, reason)
            if attempt < self.attempts - 1:
                time.sleep(2 ** attempt)
        raise TranslationError(f"all models failed; last error: {last_exc}")


def translate_document(
    doc: Doc,
    translator: OpenRouterTranslator,
    cache: Cache,
    prompt_version: str = "v1",
    concurrency: int = 8,
) -> Doc:
    out = doc.model_copy(deep=True)
    models_key = ",".join(translator.models)
    prompt_hash = hashlib.sha256(translator.system_prompt.encode("utf-8")).hexdigest()
    total = len(out.pages)

    # First pass (cheap, in order): serve cached pages, collect the rest.
    pending: list[tuple[int, object, str]] = []
    for i, page in enumerate(out.pages, start=1):
        key = cache.key(
            doc.source_hash, "translate", models_key, prompt_version, prompt_hash, str(page.index)
        )
        cached = cache.get(key)
        if cached is not None:
            logger.info("translate: page %d/%d (cached)", i, total)
            page.markdown = cached
        else:
            pending.append((i, page, key))

    if not pending:
        return out

    def _work(item: tuple[int, object, str]) -> None:
        i, page, key = item
        logger.info("translate: page %d/%d translating…", i, total)
        text, model = translator._translate_page_with_model(page.markdown)
        cache.set(key, text)  # durable before assignment: a crash keeps the result
        page.markdown = text
        logger.info("translate: page %d/%d done via %s", i, total, model)

    # Each thread writes its own page object and a distinct cache file → no races.
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        # list() forces consumption so any worker exception propagates here.
        list(ex.map(_work, pending))
    return out
