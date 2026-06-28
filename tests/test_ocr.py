import base64
from pathlib import Path
from types import SimpleNamespace

from manualtrans.cache import Cache
from manualtrans.ocr import parse_ocr_response, run_ocr


def fake_response():
    raw = base64.b64encode(b"\xff\xd8jpegbytes").decode()
    return SimpleNamespace(
        pages=[
            SimpleNamespace(
                markdown="# T\n\n![img-0.jpeg](img-0.jpeg)\n\n[tbl-0.html](#tbl-0)",
                images=[SimpleNamespace(id="img-0.jpeg", image_base64=raw)],
                tables=[SimpleNamespace(id="tbl-0", html="<table><tr><td>1</td></tr></table>")],
            )
        ]
    )


def test_parse_writes_media_and_builds_doc(tmp_path: Path):
    media = tmp_path / "media"
    doc = parse_ocr_response(
        fake_response(), "m.pdf", "HASH", "mistral-ocr-2512", media
    )
    assert doc.source_hash == "HASH"
    assert doc.pages[0].images[0].id == "img-0.jpeg"
    assert doc.pages[0].tables[0].html.startswith("<table>")
    # image bytes were written to media/
    assert (media / "img-0.jpeg").read_bytes() == b"\xff\xd8jpegbytes"


def test_run_ocr_uses_cache(tmp_path: Path):
    pdf = tmp_path / "m.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    cache = Cache(tmp_path / "c")
    calls = {"n": 0}

    class FakeClient:
        class _OCR:
            def process(self, **kwargs):
                calls["n"] += 1
                return fake_response()
        def __init__(self):
            self.ocr = self._OCR()
        # mimic mistralai file upload surface no-ops
        class _Files:
            def upload(self, **kwargs):
                return SimpleNamespace(id="fileid")
            def get_signed_url(self, **kwargs):
                return SimpleNamespace(url="https://signed")
        files = _Files()

    out = tmp_path / "doc.json"
    media = tmp_path / "media"
    doc1 = run_ocr(pdf, out, media, "mistral-ocr-2512", "key", cache, client=FakeClient())
    doc2 = run_ocr(pdf, out, media, "mistral-ocr-2512", "key", cache, client=FakeClient())
    assert calls["n"] == 1  # second call served from cache
    assert doc1 == doc2
    assert out.exists()
