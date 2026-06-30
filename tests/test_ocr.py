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


def test_parse_blocks_and_dimensions(tmp_path):
    from types import SimpleNamespace
    import base64
    from manualtrans.ocr import parse_ocr_response

    raw = base64.b64encode(b"jpegbytes").decode()
    resp = SimpleNamespace(pages=[SimpleNamespace(
        markdown="# Big title\n\nbody",
        images=[SimpleNamespace(id="img-0.jpeg", image_base64=raw)],
        tables=[],
        dimensions=SimpleNamespace(dpi=200, width=1654, height=2339),
        blocks=[
            SimpleNamespace(type="title", top_left_x=10, top_left_y=20,
                            bottom_right_x=300, bottom_right_y=70, content="Big title"),
            SimpleNamespace(type="text", top_left_x=10, top_left_y=90,
                            bottom_right_x=300, bottom_right_y=110, content="body"),
        ],
    )])
    doc = parse_ocr_response(resp, "m.pdf", "H", "mistral-ocr-latest", tmp_path / "media")
    pg = doc.pages[0]
    assert pg.dpi == 200 and pg.width == 1654 and pg.height == 2339
    assert [b.type for b in pg.blocks] == ["title", "text"]
    assert pg.blocks[0].bbox == [10, 20, 300, 70]
    assert pg.blocks[0].content == "Big title"


def test_parse_no_blocks_ok(tmp_path):
    # OCR-3 style response: no blocks/dimensions attrs -> empty/None, no crash
    from types import SimpleNamespace
    from manualtrans.ocr import parse_ocr_response
    resp = SimpleNamespace(pages=[SimpleNamespace(markdown="x", images=[], tables=[])])
    doc = parse_ocr_response(resp, "m.pdf", "H", "mistral-ocr-2512", tmp_path / "media")
    assert doc.pages[0].blocks == []
    assert doc.pages[0].dpi is None


def test_parse_strips_dataless_icon_placeholders(tmp_path):
    # Mistral OCR-4 represents small inline icon glyphs (e.g. a lock icon in
    # an icon legend) as markdown images with an empty href and no backing
    # image data: ![lock icon](). They never appear in the page's images
    # list, so they must not be counted as real image placeholders downstream
    # (see CLAUDE.md "OCR-model-dependent workarounds").
    resp = SimpleNamespace(pages=[SimpleNamespace(
        markdown="**VFO Icons:** ![lock icon]() Shows locked.\n\n"
                  "![img-8.jpeg](img-8.jpeg)\n\n"
                  "![lock icon]() Message playback.",
        images=[SimpleNamespace(
            id="img-8.jpeg",
            image_base64=base64.b64encode(b"\xff\xd8jpegbytes").decode(),
        )],
        tables=[],
    )])
    doc = parse_ocr_response(resp, "m.pdf", "H", "mistral-ocr-latest", tmp_path / "media")
    pg = doc.pages[0]
    assert "![lock icon]()" not in pg.markdown
    assert "![img-8.jpeg](img-8.jpeg)" in pg.markdown
    import re
    assert len(re.findall(r"!\[[^\]]*\]\([^)]*\)", pg.markdown)) == len(pg.images) == 1
