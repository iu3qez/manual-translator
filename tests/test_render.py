from pathlib import Path

import pytest

from manualtrans.render import build_pandoc_cmd, render, RenderError


def test_pdf_cmd_uses_weasyprint(tmp_path: Path):
    cmd = build_pandoc_cmd(tmp_path / "in.md", tmp_path / "out.pdf", tmp_path / "media")
    assert "pandoc" in cmd[0]
    assert "--pdf-engine=weasyprint" in cmd
    assert any(a.startswith("--resource-path=") for a in cmd)


def test_docx_cmd_no_pdf_engine(tmp_path: Path):
    cmd = build_pandoc_cmd(tmp_path / "in.md", tmp_path / "out.docx", tmp_path / "media")
    assert not any("pdf-engine" in a for a in cmd)


def test_render_invokes_runner_per_format(tmp_path: Path):
    md = tmp_path / "in.md"
    md.write_text("# hi", encoding="utf-8")
    calls = []

    class Result:
        returncode = 0
        stderr = ""

    def runner(cmd, **kwargs):
        calls.append(cmd)
        return Result()

    out = render(md, tmp_path / "out", ["pdf", "docx"], tmp_path / "media", runner=runner)
    assert len(calls) == 2
    assert (tmp_path / "out.pdf") in out
    assert (tmp_path / "out.docx") in out


def test_render_raises_on_failure(tmp_path: Path):
    md = tmp_path / "in.md"
    md.write_text("# hi", encoding="utf-8")

    class Result:
        returncode = 1
        stderr = "boom"

    def runner(cmd, **kwargs):
        return Result()

    with pytest.raises(RenderError):
        render(md, tmp_path / "out", ["pdf"], tmp_path / "media", runner=runner)
