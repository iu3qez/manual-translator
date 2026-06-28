import pytest

from manualtrans.assemble import assemble, AssembleError
from manualtrans.models import Doc, Image, Page, Table


def doc_with(markdown, images=None, tables=None, header=None, footer=None):
    return Doc(
        source_pdf="a.pdf", source_hash="H", ocr_model="mistral-ocr-2512",
        pages=[Page(index=0, markdown=markdown, images=images or [],
                    tables=tables or [], header=header, footer=footer)],
    )


def test_table_placeholder_resolved_to_html():
    # Mistral OCR's real placeholder syntax: [tbl-0.html](tbl-0.html)
    d = doc_with(
        "Spec:\n\n[tbl-0.html](tbl-0.html)\n",
        tables=[Table(id="tbl-0.html", html="<table><tr><td>1</td></tr></table>")],
    )
    out = assemble(d)
    assert "<table><tr><td>1</td></tr></table>" in out
    assert "[tbl-0.html](tbl-0.html)" not in out


def test_image_placeholder_kept():
    # Mistral emits a bare-filename href; assemble must not rewrite it.
    d = doc_with(
        "![img-0.jpeg](img-0.jpeg)",
        images=[Image(id="img-0.jpeg", path="media/img-0.jpeg")],
    )
    out = assemble(d)
    assert "![img-0.jpeg](img-0.jpeg)" in out


def test_table_count_mismatch_fails():
    # one placeholder but zero declared tables
    d = doc_with("[tbl-9.html](tbl-9.html)", tables=[])
    with pytest.raises(AssembleError):
        assemble(d)


def test_orphan_table_id_fails():
    # count matches (1 placeholder, 1 table) but the referenced id is unknown
    d = doc_with(
        "[tbl-9.html](tbl-9.html)",
        tables=[Table(id="tbl-0.html", html="<table></table>")],
    )
    with pytest.raises(AssembleError):
        assemble(d)


def test_image_count_mismatch_fails():
    # one placeholder but zero declared images
    d = doc_with("![x](media/x.jpeg)", images=[])
    with pytest.raises(AssembleError):
        assemble(d)


def test_header_footer_keep_once():
    d = Doc(
        source_pdf="a.pdf", source_hash="H", ocr_model="mistral-ocr-2512",
        pages=[
            Page(index=0, markdown="Pagina 1", header="HEAD", footer="FOOT"),
            Page(index=1, markdown="Pagina 2", header="HEAD", footer="FOOT"),
        ],
    )
    out = assemble(d, header_footer_policy="keep_once")
    assert out.count("HEAD") == 1
    assert out.count("FOOT") == 1
