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


def test_assemble_with_cover_replaces_page0():
    from manualtrans.assemble import assemble
    from manualtrans.models import Doc, Page
    doc = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="mistral-ocr-latest",
              pages=[Page(index=0, markdown="# Copertina testo OCR"),
                     Page(index=1, markdown="Contenuto vero.")])
    out = assemble(doc, cover="cover.png")
    assert "Copertina testo OCR" not in out      # page 0 body dropped
    assert "Contenuto vero." in out              # page 1 kept
    # the cover image is injected by render (per-format), NOT in the assembled body
    assert "![cover]" not in out


def test_assemble_without_cover_unchanged():
    from manualtrans.assemble import assemble
    from manualtrans.models import Doc, Page
    doc = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="mistral-ocr-latest",
              pages=[Page(index=0, markdown="Pagina zero."),
                     Page(index=1, markdown="Pagina uno.")])
    out = assemble(doc)
    assert "Pagina zero." in out and "Pagina uno." in out


# --- Mistral-OCR list-marker recovery (see CLAUDE.md "OCR-dependent workarounds") ---

def test_dropped_first_bullet_marker_is_restored():
    # Mistral OCR drops the marker on the first list item, gluing a bare line
    # directly above the `- ` items (no blank line). pandoc would then collapse
    # the whole list into one paragraph. assemble must promote the bare line.
    d = doc_with("Tre versioni: 80, 60m\n- 5W da 9V\n- Segnale pulito")
    out = assemble(d)
    assert "- Tre versioni: 80, 60m" in out


def test_lead_in_paragraph_gets_blank_line_not_a_bullet():
    # A genuine lead-in sentence (ends with ':') glued to a list must NOT become a
    # bullet; instead a blank line is inserted so the list still renders.
    d = doc_with("Caratteristiche principali:\n- prima\n- seconda")
    out = assemble(d)
    assert "- Caratteristiche principali:" not in out
    assert "Caratteristiche principali:\n\n- prima" in out


def test_plain_paragraph_above_non_list_untouched():
    d = doc_with("Solo un paragrafo.\nUn altro paragrafo.")
    out = assemble(d)
    assert out.strip() == "Solo un paragrafo.\nUn altro paragrafo."


def test_existing_list_item_not_double_marked():
    d = doc_with("- gia lista\n- seconda")
    out = assemble(d)
    assert "- - gia lista" not in out
