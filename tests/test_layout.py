from manualtrans.layout import block_font_size, body_font_size
from manualtrans.models import Doc, Page, Block


def test_block_font_size_single_and_multiline():
    one = Block(type="title", bbox=[0, 0, 100, 40], content="Title")
    assert block_font_size(one) == 40.0
    two = Block(type="text", bbox=[0, 0, 100, 40], content="line1\nline2")
    assert block_font_size(two) == 20.0


def test_body_font_size_low_percentile_of_text_heights():
    # body line height = low percentile of TEXT-block heights (single lines),
    # ignoring title blocks; with [20,22,24] the 15th-pct lands on the smallest.
    doc = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="mistral-ocr-latest",
              pages=[Page(index=0, markdown="x", blocks=[
                  Block(type="title", bbox=[0, 0, 10, 40], content="T"),
                  Block(type="text", bbox=[0, 0, 10, 20], content="a"),
                  Block(type="text", bbox=[0, 0, 10, 22], content="b"),
                  Block(type="text", bbox=[0, 0, 10, 24], content="c"),
              ])])
    assert body_font_size(doc) == 20.0


def test_body_font_size_no_blocks():
    doc = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="x",
              pages=[Page(index=0, markdown="x")])
    assert body_font_size(doc) == 0.0


from manualtrans.layout import title_block_levels, reclassify_headings


def _doc(pages):
    return Doc(source_pdf="m.pdf", source_hash="H", ocr_model="mistral-ocr-latest", pages=pages)


def test_title_block_levels_by_ratio():
    page = Page(index=0, markdown="x", blocks=[
        Block(type="title", bbox=[0, 10, 10, 50], content="A"),   # 40 -> 2.0x body -> h1
        Block(type="title", bbox=[0, 60, 10, 88], content="B"),   # 28 -> 1.4x -> h2
        Block(type="text",  bbox=[0, 90, 10, 110], content="t"),  # 20 = body
    ])
    assert title_block_levels(page, body=20.0) == [1, 2]


def test_reclassify_levels_from_section_numbers():
    # level = numbering depth: "1." → h1, "4.2" → h2, "8.1.2" → h3; survives any
    # input level the OCR/translation produced.
    doc = _doc([Page(index=0, markdown=(
        "# 1. Panoramica\n\n"
        "##### 4.2 Modalità operativa\n\n"
        "# 8.1.2 Dettaglio\n\n"
        "testo"
    ))])
    out = reclassify_headings(doc)
    assert out.pages[0].markdown == (
        "# 1. Panoramica\n\n"
        "## 4.2 Modalità operativa\n\n"
        "### 8.1.2 Dettaglio\n\n"
        "testo"
    )


def test_reclassify_unnumbered_heading_becomes_h4():
    doc = _doc([Page(index=0, markdown="# QMX è altamente portatile\n\ntesto")])
    out = reclassify_headings(doc)
    assert out.pages[0].markdown == "#### QMX è altamente portatile\n\ntesto"


from manualtrans.layout import strip_ocr_toc


def test_strip_ocr_toc_removes_index_block():
    from manualtrans.models import Doc, Page
    toc = ("# Indice\n\n"
           "1. Panoramica....................3\n"
           "2. Connettori...................7\n"
           "3. Display......................11\n\n"
           "Testo reale che resta.")
    doc = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="x",
              pages=[Page(index=0, markdown=toc)])
    out = strip_ocr_toc(doc)
    md = out.pages[0].markdown
    assert "Indice" not in md
    assert "....." not in md
    assert "Testo reale che resta." in md


def test_strip_ocr_toc_leaves_normal_page():
    from manualtrans.models import Doc, Page
    doc = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="x",
              pages=[Page(index=0, markdown="# Capitolo 1\n\nParagrafo normale.")])
    out = strip_ocr_toc(doc)
    assert out.pages[0].markdown == "# Capitolo 1\n\nParagrafo normale."


from manualtrans.layout import wrap_callouts


def test_wrap_callouts_wraps_note_and_warning():
    md = "NOTA: questo è importante.\n\nParagrafo normale.\n\nWARNING attenzione qui."
    out = wrap_callouts(md)
    assert '<div class="callout">\nNOTA: questo è importante.\n</div>' in out
    assert '<div class="callout">\nWARNING attenzione qui.\n</div>' in out
    assert "Paragrafo normale." in out
    assert out.count('<div class="callout">') == 2


def test_wrap_callouts_ignores_placeholders_and_plain():
    md = "![img-0.jpeg](img-0.jpeg)\n\nTesto semplice."
    assert wrap_callouts(md) == md


from manualtrans.layout import style_profile, render_css, write_css


def test_style_profile_from_blocks():
    # body text blocks ~20px tall at 200dpi -> 20/200*72 = 7.2pt -> clamped to 8.0
    doc = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="mistral-ocr-latest",
              pages=[Page(index=0, markdown="x", dpi=200, width=1654, height=2339, blocks=[
                  Block(type="text", bbox=[0, 0, 10, 20], content="a"),
                  Block(type="text", bbox=[0, 0, 10, 20], content="b"),
              ])])
    prof = style_profile(doc)
    assert prof["body_pt"] == 8.0
    assert prof["page_w_mm"] == 210 and prof["page_h_mm"] == 297  # snapped to A4
    assert prof["headings"][1] > prof["headings"][2] > prof["body_pt"]


def test_style_profile_no_blocks_fallback():
    doc = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="x",
              pages=[Page(index=0, markdown="x")])
    prof = style_profile(doc)
    assert prof["body_pt"] == 10.5
    assert prof["page_w_mm"] == 210


def test_render_and_write_css(tmp_path):
    doc = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="x",
              pages=[Page(index=0, markdown="x")])
    css = render_css(style_profile(doc))
    assert "@page" in css and "body" in css and ".callout" in css and "h1" in css
    p = write_css(style_profile(doc), tmp_path / "style.css")
    assert p.read_text(encoding="utf-8") == css


def test_css_caps_image_height_to_one_page():
    # Oversized images must not overflow/clip: cap at the page content height
    # (A4 297mm - 2*18mm margin = 261mm). Also exposes a .fullpage class.
    doc = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="x",
              pages=[Page(index=0, markdown="x")])
    css = render_css(style_profile(doc))
    assert "max-height: 261.0mm" in css
    assert "img.fullpage" in css


from manualtrans.layout import apply_block_colors
from manualtrans.models import Block as _B


def test_apply_block_colors_wraps_red_paragraph_and_heading():
    from manualtrans.models import Doc, Page
    en = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="mistral-ocr-latest",
             pages=[Page(index=0, markdown="# T\n\npara", blocks=[
                 _B(type="title", bbox=[0, 0, 10, 10], color="#cc0000"),
                 _B(type="text", bbox=[0, 20, 10, 30], color=None),
             ])])
    it = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="mistral-ocr-latest",
             pages=[Page(index=0, markdown="# Titolo\n\nparagrafo")])
    out = apply_block_colors(en, it)
    assert out.pages[0].markdown == '# <span style="color:#cc0000">Titolo</span>\n\nparagrafo'


def test_apply_block_colors_multiline_heading_keeps_trailing_line():
    """_wrap_segment must not drop lines after the first heading line."""
    from manualtrans.models import Doc, Page
    en = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="mistral-ocr-latest",
             pages=[Page(index=0, markdown="# T", blocks=[
                 _B(type="title", bbox=[0, 0, 10, 10], color="#cc0000"),
             ])])
    it = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="mistral-ocr-latest",
             pages=[Page(index=0, markdown="# Titolo\nsottotitolo")])
    out = apply_block_colors(en, it)
    result = out.pages[0].markdown
    assert '# <span style="color:#cc0000">Titolo</span>' in result
    assert "sottotitolo" in result


def test_apply_block_colors_skips_on_count_mismatch():
    from manualtrans.models import Doc, Page
    en = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="mistral-ocr-latest",
             pages=[Page(index=0, markdown="# T", blocks=[
                 _B(type="title", bbox=[0, 0, 10, 10], color="#cc0000"),
                 _B(type="text", bbox=[0, 20, 10, 30], color="#cc0000"),
             ])])
    it = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="mistral-ocr-latest",
             pages=[Page(index=0, markdown="# Solo")])   # 1 segment vs 2 blocks
    out = apply_block_colors(en, it)
    assert out.pages[0].markdown == "# Solo"   # unchanged


def test_strip_ocr_toc_removes_titleless_continuation_page():
    # a TOC continuation page has many dotted-leader lines but NO "Contents" title
    from manualtrans.models import Doc, Page
    cont = ("7. Firmware Update...95\n"
            "8. Terminal Applications...98\n"
            "8.1 PC terminal emulator...98\n"
            "8.2 Web-based terminal...99\n"
            "9. Troubleshooting...101\n\n"
            "Testo reale di pagina.")
    doc = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="x",
              pages=[Page(index=0, markdown=cont)])
    out = strip_ocr_toc(doc)
    md = out.pages[0].markdown
    assert "....." not in md and "...95" not in md
    assert "Testo reale di pagina." in md


# --- Mistral-OCR full-page-graphic recovery (see CLAUDE.md "OCR-dependent workarounds") ---

from manualtrans.layout import is_full_page_graphic, apply_full_page_rasters
from manualtrans.models import Image


def _img_block(area_frac, w=1000.0, h=1000.0):
    # one image block covering `area_frac` of a w*h page
    import math
    side = (area_frac * w * h) ** 0.5
    return _B(type="image", bbox=[0, 0, side, side])


def test_full_page_graphic_detected_for_image_only_page():
    # OCR fragments a full-page diagram into several tall images + tiny labels
    p = Page(index=3, markdown="![a](a)\n![b](b)", width=1000, height=1000,
             blocks=[_B(type="image", bbox=[0, 0, 700, 700]),
                     _B(type="image", bbox=[0, 700, 300, 1000]),
                     _B(type="text", bbox=[0, 0, 50, 20], content="Rev 2")])
    assert is_full_page_graphic(p) is True


def test_page_with_captions_and_prose_not_a_graphic():
    # real figure page: title captions + a paragraph -> NOT rasterized
    p = Page(index=4, markdown="x", width=1000, height=1000,
             blocks=[_B(type="title", bbox=[0, 0, 200, 30], content="Trace layout:"),
                     _B(type="image", bbox=[0, 30, 600, 430]),
                     _B(type="text", bbox=[0, 440, 900, 470],
                        content="LCD and controls board are on the following page; "
                                "there are no SMD components on the bottom side.")])
    assert is_full_page_graphic(p) is False


def test_text_page_not_a_graphic():
    p = Page(index=1, markdown="x", width=1000, height=1000,
             blocks=[_B(type="text", bbox=[0, 0, 900, 800], content="lots of prose " * 40)])
    assert is_full_page_graphic(p) is False


def test_apply_full_page_rasters_replaces_only_graphic_pages(tmp_path):
    png = tmp_path / "page-3.png"
    png.write_bytes(b"\x89PNG fake")
    en = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="mistral-ocr-latest",
             pages=[Page(index=0, markdown="testo", width=1000, height=1000,
                         blocks=[_B(type="text", bbox=[0, 0, 900, 800], content="prose " * 50)]),
                    Page(index=3, markdown="![a](a)\n![b](b)", width=1000, height=1000,
                         blocks=[_B(type="image", bbox=[0, 0, 700, 700]),
                                 _B(type="image", bbox=[0, 700, 300, 1000])])])
    it = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="mistral-ocr-latest",
             pages=[Page(index=0, markdown="testo tradotto"),
                    Page(index=3, markdown="![a](a)\n![b](b)",
                         images=[Image(id="a", path="media/a"), Image(id="b", path="media/b")])])
    out, replaced = apply_full_page_rasters(en, it, {3: png})
    assert replaced == [3]
    assert out.pages[0].markdown == "testo tradotto"           # text page untouched
    assert "page-3.png" in out.pages[1].markdown               # graphic page replaced
    assert "{.fullpage}" in out.pages[1].markdown
    assert len(out.pages[1].images) == 1                       # one placeholder => one image
    assert out.pages[1].images[0].id == "page-3.png"


def test_apply_full_page_rasters_skips_indices(tmp_path):
    png = tmp_path / "page-0.png"
    png.write_bytes(b"x")
    en = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="mistral-ocr-latest",
             pages=[Page(index=0, markdown="![a](a)", width=1000, height=1000,
                         blocks=[_B(type="image", bbox=[0, 0, 900, 900])])])
    it = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="mistral-ocr-latest",
             pages=[Page(index=0, markdown="![a](a)", images=[Image(id="a", path="media/a")])])
    out, replaced = apply_full_page_rasters(en, it, {0: png}, skip_indices={0})
    assert replaced == []
