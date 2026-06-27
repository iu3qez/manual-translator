from pathlib import Path

from manualtrans.models import Doc, Page, Image, Table


def make_doc() -> Doc:
    return Doc(
        source_pdf="manuale.pdf",
        source_hash="abc123",
        ocr_model="mistral-ocr-2512",
        pages=[
            Page(
                index=0,
                markdown="# Titolo\n\n![img-0.jpeg](media/img-0.jpeg)\n\n[tbl-0.html](#tbl-0)",
                images=[Image(id="img-0.jpeg", path="media/img-0.jpeg")],
                tables=[Table(id="tbl-0", html="<table><tr><td>1</td></tr></table>")],
                header="Header",
                footer=None,
            )
        ],
    )


def test_roundtrip_dump_load(tmp_path: Path):
    doc = make_doc()
    p = tmp_path / "doc.json"
    doc.dump(p)
    loaded = Doc.load(p)
    assert loaded == doc
    assert loaded.pages[0].images[0].id == "img-0.jpeg"


def test_page_defaults():
    page = Page(index=1, markdown="hi")
    assert page.images == []
    assert page.tables == []
    assert page.header is None
