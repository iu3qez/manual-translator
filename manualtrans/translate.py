from __future__ import annotations

import hashlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

from .cache import Cache
from .check import HEADING_RE, TR_RE
from .models import Doc

logger = logging.getLogger(__name__)


def _structure_sig(markdown: str) -> tuple[int, int]:
    """Structural fingerprint of a page: (heading count, table-row count).

    A translated page whose fingerprint differs from its source page lost or
    gained a heading/row — the model garbled the structure.
    """
    return (len(HEADING_RE.findall(markdown)), len(TR_RE.findall(markdown)))


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

    def _call_with_retry(self, model: str, markdown: str) -> str:
        """Call ONE specific model, retrying transient failures with backoff."""
        last_exc: Exception | None = None
        for attempt in range(self.attempts):
            try:
                return self._call(model, markdown)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < self.attempts - 1:
                    time.sleep(2 ** attempt)
        raise TranslationError(f"model {model} failed: {last_exc}")


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
    second = translator.models[1] if len(translator.models) >= 2 else None

    # First pass (cheap, in order): serve cached pages whose structure matches the
    # source; collect the rest. A cached page whose structure is WRONG (the model
    # dropped a heading/row) is re-translated with the second model — only those.
    pending: list[tuple[int, object, str, str, str | None]] = []
    for i, page in enumerate(out.pages, start=1):
        en_md = page.markdown  # out is a deep copy of the EN doc; still EN here
        key = cache.key(
            doc.source_hash, "translate", models_key, prompt_version, prompt_hash, str(page.index)
        )
        cached = cache.get(key)
        if cached is not None:
            if second is None or _structure_sig(cached) == _structure_sig(en_md):
                logger.info("translate: page %d/%d (cached)", i, total)
                page.markdown = cached
                continue
            # cached but structurally wrong → retry this page with the second model
            pending.append((i, page, key, en_md, cached))
        else:
            pending.append((i, page, key, en_md, None))

    if not pending:
        return out

    def _work(item: tuple[int, object, str, str, str | None]) -> None:
        i, page, key, en_md, cached_bad = item
        target = _structure_sig(en_md)
        if cached_bad is not None:
            # already have the (structurally wrong) first-model output cached
            text, model = cached_bad, translator.models[0]
        else:
            logger.info("translate: page %d/%d translating…", i, total)
            text, model = translator._translate_page_with_model(en_md)

        if second is not None and _structure_sig(text) != target:
            try:
                text, model = translator._call_with_retry(second, en_md), second
                logger.info(
                    "translate: page %d/%d structure mismatch → re-translated with %s",
                    i, total, second,
                )
            except TranslationError as exc:
                logger.warning(
                    "translate: page %d/%d second model failed (%s); keeping first output",
                    i, total, exc,
                )

        cache.set(key, text)  # durable before assignment: a crash keeps the result
        page.markdown = text
        logger.info("translate: page %d/%d via %s", i, total, model)

    # Each thread writes its own page object and a distinct cache file → no races.
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        # list() forces consumption so any worker exception propagates here.
        list(ex.map(_work, pending))
    return out
