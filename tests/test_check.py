from manualtrans.check import (
    check_document,
    check_placeholder_integrity,
    check_structure,
)
from manualtrans.models import Doc, Image, Page, Table


def one_page(markdown, images=None, tables=None):
    return Doc(source_pdf="a.pdf", source_hash="H", ocr_model="mistral-ocr-2512",
               pages=[Page(index=0, markdown=markdown, images=images or [],
                           tables=tables or [])])


def test_placeholder_integrity_ok():
    d = one_page("![i](media/i.jpeg)", images=[Image(id="i", path="media/i.jpeg")])
    assert check_placeholder_integrity(d) == []


def test_placeholder_integrity_orphan_table():
    d = one_page("[tbl-1.html](#tbl-1)", tables=[])
    problems = check_placeholder_integrity(d)
    assert problems  # non-empty


def test_structure_heading_mismatch():
    en = one_page("# A\n\n## B\n\ntext")
    it = one_page("# A\n\ntesto")  # lost one heading
    problems = check_structure(en, it)
    assert any("heading" in p.lower() for p in problems)


def test_structure_table_row_drop():
    en = one_page("<table><tr><td>1</td></tr><tr><td>2</td></tr></table>")
    it = one_page("<table><tr><td>1</td></tr></table>")  # dropped a row
    problems = check_structure(en, it)
    assert any("row" in p.lower() for p in problems)


def test_check_document_combines():
    en = one_page("# A")
    it = one_page("# A")
    assert check_document(it, en) == []
