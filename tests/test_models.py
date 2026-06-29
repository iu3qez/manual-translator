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


def test_block_and_page_geometry_roundtrip(tmp_path):
    from manualtrans.models import Doc, Page, Block
    doc = Doc(
        source_pdf="m.pdf", source_hash="H", ocr_model="mistral-ocr-latest",
        pages=[Page(
            index=0, markdown="# T",
            blocks=[Block(type="title", bbox=[10.0, 20.0, 300.0, 60.0], content="T")],
            width=1654.0, height=2339.0, dpi=200.0,
        )],
    )
    p = tmp_path / "doc.json"
    doc.dump(p)
    loaded = Doc.load(p)
    assert loaded == doc
    assert loaded.pages[0].blocks[0].type == "title"
    assert loaded.pages[0].blocks[0].bbox == [10.0, 20.0, 300.0, 60.0]
    assert loaded.pages[0].dpi == 200.0


def test_page_block_defaults():
    from manualtrans.models import Page
    page = Page(index=0, markdown="x")
    assert page.blocks == []
    assert page.width is None and page.height is None and page.dpi is None
