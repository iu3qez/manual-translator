from pathlib import Path

from typer.testing import CliRunner

from manualtrans.main import app
from manualtrans.models import Doc, Page

runner = CliRunner()


def write_doc(path: Path, markdown: str):
    Doc(source_pdf="a.pdf", source_hash="H", ocr_model="mistral-ocr-2512",
        pages=[Page(index=0, markdown=markdown)]).dump(path)


def test_check_passes(tmp_path: Path):
    it = tmp_path / "doc_it.json"
    write_doc(it, "# Titolo")
    result = runner.invoke(app, ["check", str(it)])
    assert result.exit_code == 0


def test_check_fails_on_structure(tmp_path: Path):
    en = tmp_path / "doc.json"
    it = tmp_path / "doc_it.json"
    write_doc(en, "# A\n\n## B")
    write_doc(it, "# A")  # lost a heading
    result = runner.invoke(app, ["check", str(it), "--source", str(en)])
    assert result.exit_code != 0
    assert "heading" in result.stdout.lower()


def test_assemble_command(tmp_path: Path):
    it = tmp_path / "doc_it.json"
    out = tmp_path / "out.md"
    write_doc(it, "# Titolo\n\nTesto")
    result = runner.invoke(app, ["assemble", str(it), "--out", str(out)])
    assert result.exit_code == 0
    assert out.read_text(encoding="utf-8").startswith("# Titolo")
