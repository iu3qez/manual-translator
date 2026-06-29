import json

import httpx
import pytest

from manualtrans.cache import Cache
from manualtrans.models import Doc, Page
from manualtrans.translate import (
    OpenRouterTranslator,
    TranslationError,
    translate_document,
)


def _response(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


def make_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_translate_page_success(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        assert "m1" in body  # first model used
        return httpx.Response(200, json=_response("CIAO"))

    t = OpenRouterTranslator("k", ["m1", "m2"], "sys", attempts=2, client=make_client(handler))
    assert t.translate_page("hello") == "CIAO"
    assert t.last_model == "m1"


def test_fallback_to_second_model(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    calls = {"m1": 0, "m2": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        model = "m1" if '"m1"' in body else "m2"
        calls[model] += 1
        if model == "m1":
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(200, json=_response("DAL SECONDO"))

    t = OpenRouterTranslator("k", ["m1", "m2"], "sys", attempts=2, client=make_client(handler))
    assert t.translate_page("hi") == "DAL SECONDO"
    assert calls["m1"] == 1  # failure → immediate fallback, no wasted retry on m1
    assert t.last_model == "m2"


def test_rate_limit_falls_back_immediately(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    calls = {"m1": 0, "m2": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content.decode())["model"]
        calls[model] += 1
        if model == "m1":
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(200, json=_response("OK2"))

    t = OpenRouterTranslator("k", ["m1", "m2"], "sys", attempts=2, client=make_client(handler))
    assert t.translate_page("hi") == "OK2"
    assert calls["m1"] == 1  # rate-limited model not retried before falling back
    assert calls["m2"] == 1
    assert t.last_model == "m2"


def test_all_models_fail_raises(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "down"})

    t = OpenRouterTranslator("k", ["m1", "m2"], "sys", attempts=1, client=make_client(handler))
    with pytest.raises(TranslationError):
        t.translate_page("hi")


def test_translate_document_uses_cache(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    cache = Cache(tmp_path / "c")
    doc = Doc(source_pdf="a.pdf", source_hash="H", ocr_model="mistral-ocr-2512",
              pages=[Page(index=0, markdown="hello")])
    count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        count["n"] += 1
        return httpx.Response(200, json=_response("CIAO"))

    t = OpenRouterTranslator("k", ["m1"], "sys", attempts=1, client=make_client(handler))
    out1 = translate_document(doc, t, cache)
    assert out1.pages[0].markdown == "CIAO"
    # second run: same translator, cache should prevent a second HTTP call
    out2 = translate_document(doc, t, cache)
    assert out2.pages[0].markdown == "CIAO"
    assert count["n"] == 1


def test_translate_document_different_prompt_is_cache_miss(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    cache = Cache(tmp_path / "c")
    doc = Doc(source_pdf="a.pdf", source_hash="H", ocr_model="mistral-ocr-2512",
              pages=[Page(index=0, markdown="hello")])
    count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        count["n"] += 1
        return httpx.Response(200, json=_response("CIAO"))

    shared_client = make_client(handler)
    t_a = OpenRouterTranslator("k", ["m1"], "system prompt A", attempts=1, client=shared_client)
    t_b = OpenRouterTranslator("k", ["m1"], "system prompt B", attempts=1, client=shared_client)

    out1 = translate_document(doc, t_a, cache)
    assert out1.pages[0].markdown == "CIAO"
    # translator B has a different system prompt → different cache key → must NOT reuse A's entry
    out2 = translate_document(doc, t_b, cache)
    assert out2.pages[0].markdown == "CIAO"
    assert count["n"] == 2


def test_structure_mismatch_uses_second_model(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    cache = Cache(tmp_path / "c")
    # EN page has one heading; the first model drops it, the second keeps it.
    doc = Doc(source_pdf="a.pdf", source_hash="H", ocr_model="mistral-ocr-2512",
              pages=[Page(index=0, markdown="# Title\n\nbody")])
    calls = {"weak": 0, "strong": 0}

    def handler(request):
        model = json.loads(request.content.decode())["model"]
        calls[model] += 1
        if model == "weak":
            return httpx.Response(200, json=_response("testo senza titolo"))  # 0 headings
        return httpx.Response(200, json=_response("# Titolo\n\ntesto"))  # 1 heading

    t = OpenRouterTranslator("k", ["weak", "strong"], "sys", attempts=1, client=make_client(handler))
    out = translate_document(doc, t, cache)
    assert out.pages[0].markdown == "# Titolo\n\ntesto"  # second model's output kept
    assert calls["weak"] == 1 and calls["strong"] == 1


def test_dropped_image_placeholder_is_restored(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    cache = Cache(tmp_path / "c")
    # EN page references two images; the (only) model drops one placeholder.
    # Restoration is deterministic — it must not depend on a second model.
    en = "intro\n\n![img-0.jpeg](img-0.jpeg)\n\n![img-1.jpeg](img-1.jpeg)"
    doc = Doc(source_pdf="a.pdf", source_hash="H", ocr_model="mistral-ocr-2512",
              pages=[Page(index=0, markdown=en)])

    def handler(request):
        return httpx.Response(200, json=_response("intro\n\n![img-0.jpeg](img-0.jpeg)"))  # drops img-1

    t = OpenRouterTranslator("k", ["m1"], "sys", attempts=1, client=make_client(handler))
    out = translate_document(doc, t, cache)
    assert "![img-1.jpeg](img-1.jpeg)" in out.pages[0].markdown  # re-appended
    assert out.pages[0].markdown.count("![") == 2


def test_dropped_table_placeholder_is_restored(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    cache = Cache(tmp_path / "c")
    en = "spec\n\n[tbl-0.html](tbl-0.html)"
    doc = Doc(source_pdf="a.pdf", source_hash="H", ocr_model="mistral-ocr-2512",
              pages=[Page(index=0, markdown=en)])

    def handler(request):
        return httpx.Response(200, json=_response("spec tradotto"))  # drops the table placeholder

    t = OpenRouterTranslator("k", ["m1"], "sys", attempts=1, client=make_client(handler))
    out = translate_document(doc, t, cache)
    assert "[tbl-0.html](tbl-0.html)" in out.pages[0].markdown


def test_structure_ok_does_not_call_second_model(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    cache = Cache(tmp_path / "c")
    doc = Doc(source_pdf="a.pdf", source_hash="H", ocr_model="mistral-ocr-2512",
              pages=[Page(index=0, markdown="# Title\n\nbody")])
    calls = {"weak": 0, "strong": 0}

    def handler(request):
        model = json.loads(request.content.decode())["model"]
        calls[model] += 1
        return httpx.Response(200, json=_response("# Titolo\n\ntesto"))  # 1 heading, matches

    t = OpenRouterTranslator("k", ["weak", "strong"], "sys", attempts=1, client=make_client(handler))
    out = translate_document(doc, t, cache)
    assert out.pages[0].markdown == "# Titolo\n\ntesto"
    assert calls["weak"] == 1 and calls["strong"] == 0  # second model untouched


def test_translate_document_parallel_preserves_mapping(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    cache = Cache(tmp_path / "c")
    pages = [Page(index=k, markdown=f"page-{k}") for k in range(6)]
    doc = Doc(source_pdf="a.pdf", source_hash="H", ocr_model="mistral-ocr-2512", pages=pages)

    def handler(request: httpx.Request) -> httpx.Response:
        # echo the page's own content back, so a mis-mapped result is detectable
        user = json.loads(request.content.decode())["messages"][1]["content"]
        return httpx.Response(200, json=_response(f"IT::{user}"))

    t = OpenRouterTranslator("k", ["m1"], "sys", attempts=1, client=make_client(handler))
    out = translate_document(doc, t, cache, concurrency=4)
    # each translated page must correspond to its own source page despite parallelism
    for k in range(6):
        assert out.pages[k].markdown == f"IT::page-{k}"
