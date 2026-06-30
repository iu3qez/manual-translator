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


def test_reclassify_applies_en_levels_to_it():
    en = _doc([Page(index=0, markdown="# A\n\n# B\n\nbody", blocks=[
        Block(type="title", bbox=[0, 10, 10, 50], content="A"),    # h1
        Block(type="title", bbox=[0, 60, 10, 84], content="B"),    # 24 -> 1.2x -> h3
        Block(type="text",  bbox=[0, 90, 10, 110], content="body"),
    ])])
    it = _doc([Page(index=0, markdown="# A-it\n\n# B-it\n\ncorpo")])
    out = reclassify_headings(en, it)
    assert out.pages[0].markdown == "# A-it\n\n### B-it\n\ncorpo"


def test_reclassify_skips_on_count_mismatch():
    en = _doc([Page(index=0, markdown="# A", blocks=[
        Block(type="title", bbox=[0, 0, 10, 40], content="A"),
        Block(type="title", bbox=[0, 50, 10, 90], content="extra"),
        Block(type="text", bbox=[0, 0, 10, 20], content="b"),
    ])])
    it = _doc([Page(index=0, markdown="# A-it")])  # 1 heading vs 2 title blocks
    out = reclassify_headings(en, it)
    assert out.pages[0].markdown == "# A-it"  # unchanged


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
