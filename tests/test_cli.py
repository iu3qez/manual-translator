from pathlib import Path

from typer.testing import CliRunner

from manualtrans.main import app
from manualtrans.models import Doc, Page

runner = CliRunner()


def _make_doc(markdown: str) -> Doc:
    return Doc(
        source_pdf="a.pdf",
        source_hash="H",
        ocr_model="m",
        pages=[Page(index=0, markdown=markdown)],
    )


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


def test_run_fails_loudly_on_structure_mismatch(tmp_path: Path, monkeypatch):
    from manualtrans.config import Settings

    # EN doc has a heading; IT doc is missing it → structure mismatch
    en_doc = _make_doc("# Heading\n\nText")
    it_doc = _make_doc("Text only")

    render_called = {"v": False}

    monkeypatch.setattr("manualtrans.main.run_ocr", lambda *a, **k: en_doc)
    monkeypatch.setattr("manualtrans.main.translate_document", lambda *a, **k: it_doc)
    monkeypatch.setattr(
        "manualtrans.main.render_md",
        lambda *a, **k: render_called.update({"v": True}) or [],
    )
    monkeypatch.setattr(
        "manualtrans.main.get_settings",
        lambda: Settings(openrouter_models=["m"], cache_dir=tmp_path / "cache"),
    )

    out = tmp_path / "result"

    # Without --force: must exit non-zero, print FAIL, and NOT call render
    result = runner.invoke(app, ["run", "input.pdf", "--out", str(out)])
    assert result.exit_code != 0
    assert "FAIL" in result.output
    assert not render_called["v"]

    # With --force: must call render and exit 0
    result2 = runner.invoke(app, ["run", "input.pdf", "--out", str(out), "--force"])
    assert result2.exit_code == 0
    assert render_called["v"]


def test_assemble_command(tmp_path: Path):
    it = tmp_path / "doc_it.json"
    out = tmp_path / "out.md"
    write_doc(it, "# Titolo\n\nTesto")
    result = runner.invoke(app, ["assemble", str(it), "--out", str(out)])
    assert result.exit_code == 0
    assert out.read_text(encoding="utf-8").startswith("# Titolo")


def test_resolve_ocr_model_aliases():
    from manualtrans.main import _resolve_ocr_model
    assert _resolve_ocr_model("ocr3", "mistral-ocr-latest") == "mistral-ocr-2512"
    assert _resolve_ocr_model("ocr4", "mistral-ocr-2512") == "mistral-ocr-latest"
    assert _resolve_ocr_model(None, "mistral-ocr-latest") == "mistral-ocr-latest"
    assert _resolve_ocr_model("custom/model", "x") == "custom/model"
