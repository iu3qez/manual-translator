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
