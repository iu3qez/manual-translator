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
    assert calls["m1"] == 2  # exhausted attempts before fallback
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
