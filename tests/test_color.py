import numpy as np
from PIL import Image

from manualtrans.color import block_color, annotate_block_colors
from manualtrans.models import Doc, Page, Block


def _hsv(img):
    return np.asarray(img.convert("HSV"))


def test_block_color_detects_red_region():
    img = Image.new("RGB", (40, 40), (255, 255, 255))
    for y in range(0, 20):           # top half = red text-ish block
        for x in range(0, 40):
            img.putpixel((x, y), (210, 0, 0))
    hsv = _hsv(img)
    assert block_color(hsv, [0, 0, 40, 20]) == "#cc0000"      # red region
    assert block_color(hsv, [0, 20, 40, 40]) is None           # white region


def test_block_color_ignores_gray_and_black():
    img = Image.new("RGB", (20, 20), (255, 255, 255))
    for y in range(0, 20):
        for x in range(0, 20):
            img.putpixel((x, y), (10, 10, 10))   # near-black text
    assert block_color(_hsv(img), [0, 0, 20, 20]) is None


def test_annotate_block_colors_sets_only_title_text():
    img = Image.new("RGB", (20, 20), (210, 0, 0))   # all red
    doc = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="mistral-ocr-latest",
              pages=[Page(index=0, markdown="x", blocks=[
                  Block(type="text", bbox=[0, 0, 20, 20]),
                  Block(type="image", bbox=[0, 0, 20, 20]),
              ])])
    out = annotate_block_colors(doc, {0: img})
    assert out.pages[0].blocks[0].color == "#cc0000"   # text
    assert out.pages[0].blocks[1].color is None        # image type skipped
