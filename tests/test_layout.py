from manualtrans.layout import block_font_size, body_font_size
from manualtrans.models import Doc, Page, Block


def test_block_font_size_single_and_multiline():
    one = Block(type="title", bbox=[0, 0, 100, 40], content="Title")
    assert block_font_size(one) == 40.0
    two = Block(type="text", bbox=[0, 0, 100, 40], content="line1\nline2")
    assert block_font_size(two) == 20.0


def test_body_font_size_median_of_text_blocks():
    doc = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="mistral-ocr-latest",
              pages=[Page(index=0, markdown="x", blocks=[
                  Block(type="title", bbox=[0, 0, 10, 40], content="T"),
                  Block(type="text", bbox=[0, 0, 10, 20], content="a"),
                  Block(type="text", bbox=[0, 0, 10, 22], content="b"),
                  Block(type="text", bbox=[0, 0, 10, 24], content="c"),
              ])])
    assert body_font_size(doc) == 22.0  # median of 20,22,24, ignores the title


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
