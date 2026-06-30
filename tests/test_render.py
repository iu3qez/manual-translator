from pathlib import Path

import pytest

from manualtrans.render import (
    build_html_cmd,
    build_pandoc_cmd,
    build_weasyprint_cmd,
    render,
    RenderError,
)


def test_html_cmd_inlines_images(tmp_path: Path):
    # the intermediate HTML for the PDF path must embed images as data URIs
    cmd = build_html_cmd(tmp_path / "in.md", tmp_path / "out.html", tmp_path / "media")
    assert "pandoc" in cmd[0]
    assert "--embed-resources" in cmd
    assert "--standalone" in cmd
    assert any(a.startswith("--resource-path=") for a in cmd)


def test_weasyprint_cmd(tmp_path: Path):
    cmd = build_weasyprint_cmd(tmp_path / "in.html", tmp_path / "out.pdf")
    assert cmd[0] == "weasyprint"
    assert str(tmp_path / "in.html") in cmd
    assert str(tmp_path / "out.pdf") in cmd


def test_docx_cmd_no_pdf_engine(tmp_path: Path):
    cmd = build_pandoc_cmd(tmp_path / "in.md", tmp_path / "out.docx", tmp_path / "media")
    assert not any("pdf-engine" in a for a in cmd)


def test_no_implicit_figure_captions(tmp_path: Path):
    # the image alt (filename) must not render as a visible figcaption
    docx = build_pandoc_cmd(tmp_path / "in.md", tmp_path / "out.docx", tmp_path / "media")
    html = build_html_cmd(tmp_path / "in.md", tmp_path / "out.html", tmp_path / "media")
    assert any("-implicit_figures" in a for a in docx)
    assert any("-implicit_figures" in a for a in html)


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
    # pdf = pandoc(html) + weasyprint (2 calls), docx = pandoc (1 call)
    assert len(calls) == 3
    assert (tmp_path / "out.pdf") in out
    assert (tmp_path / "out.docx") in out
    # the pdf path runs weasyprint
    assert any(c[0] == "weasyprint" for c in calls)


def test_render_preserves_dotted_basename(tmp_path: Path):
    # a version-numbered basename must not be truncated at the first dot
    md = tmp_path / "in.md"
    md.write_text("# hi", encoding="utf-8")

    class Result:
        returncode = 0
        stderr = ""

    def runner(cmd, **kwargs):
        return Result()

    out = render(md, tmp_path / "manual-1.04_001", ["pdf", "docx"], tmp_path / "media", runner=runner)
    assert (tmp_path / "manual-1.04_001.pdf") in out
    assert (tmp_path / "manual-1.04_001.docx") in out


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


def test_html_cmd_css_and_toc(tmp_path):
    from manualtrans.render import build_html_cmd
    cmd = build_html_cmd(tmp_path / "in.md", tmp_path / "o.html", tmp_path / "media",
                         css=tmp_path / "s.css", toc=True)
    assert any(a == f"--css={tmp_path / 's.css'}" for a in cmd)
    assert "--toc" in cmd and "--toc-depth=2" in cmd


def test_pandoc_cmd_toc(tmp_path):
    from manualtrans.render import build_pandoc_cmd
    cmd = build_pandoc_cmd(tmp_path / "in.md", tmp_path / "o.docx", tmp_path / "media", toc=True)
    assert "--toc" in cmd


def test_render_threads_css_and_toc(tmp_path):
    from manualtrans.render import render
    md = tmp_path / "in.md"; md.write_text("# hi", encoding="utf-8")
    calls = []

    class R:
        returncode = 0
        stderr = ""

    def runner(cmd, **k):
        calls.append(cmd)
        return R()

    render(md, tmp_path / "out", ["pdf", "docx"], tmp_path / "media",
           runner=runner, css=tmp_path / "s.css", toc=True)
    flat = [a for c in calls for a in c]
    assert any(a == f"--css={tmp_path / 's.css'}" for a in flat)  # pdf html step
    assert flat.count("--toc") == 2  # pdf html + docx


def test_pdf_cover_uses_before_body_and_toc_depth_2(tmp_path):
    from manualtrans.render import render
    md = tmp_path / "in.md"; md.write_text("# H", encoding="utf-8")
    cover = tmp_path / "media" / "cover.png"; cover.parent.mkdir(); cover.write_bytes(b"x")
    calls = []

    class R:
        returncode = 0
        stderr = ""

    def runner(cmd, **k):
        calls.append(cmd)
        return R()

    render(md, tmp_path / "out", ["pdf"], tmp_path / "media", runner=runner, toc=True, cover=cover)
    html_cmd = next(c for c in calls if "html5" in c)
    assert "--include-before-body" in html_cmd            # cover before TOC
    assert "--toc-depth=2" in html_cmd                    # shorter index


def test_docx_cover_prepends_to_body(tmp_path):
    from manualtrans.render import render
    md = tmp_path / "in.md"; md.write_text("# H\n\nbody", encoding="utf-8")
    cover = tmp_path / "media" / "cover.png"; cover.parent.mkdir(); cover.write_bytes(b"x")
    from pathlib import Path
    seen_content = []

    class R:
        returncode = 0
        stderr = ""

    def runner(cmd, **k):
        # docx pandoc input is the 2nd arg; read it NOW (render unlinks it after)
        seen_content.append(Path(cmd[1]).read_text(encoding="utf-8"))
        return R()

    render(md, tmp_path / "out", ["docx"], tmp_path / "media", runner=runner, toc=True, cover=cover)
    assert "![cover](cover.png)" in seen_content[0]   # cover prepended to docx body


def test_docx_cover_precedes_toc(tmp_path):
    # pandoc's --toc emits the index at the document top, above the cover. With a
    # cover, the TOC must instead be injected (as a raw-OpenXML field) into the
    # body AFTER the cover, and pandoc's own --toc suppressed.
    from manualtrans.render import render
    md = tmp_path / "in.md"; md.write_text("# H\n\nbody", encoding="utf-8")
    cover = tmp_path / "media" / "cover.png"; cover.parent.mkdir(); cover.write_bytes(b"x")
    calls = []
    seen_content = []

    class R:
        returncode = 0
        stderr = ""

    def runner(cmd, **k):
        calls.append(cmd)
        seen_content.append(Path(cmd[1]).read_text(encoding="utf-8"))
        return R()

    render(md, tmp_path / "out", ["docx"], tmp_path / "media", runner=runner, toc=True, cover=cover)
    src = seen_content[0]
    # pandoc must NOT add its own top-of-document TOC
    assert "--toc" not in calls[0]
    # the TOC field is injected into the body, after the cover image
    assert "docPartGallery" in src and 'TOC \\o "1-2"' in src
    assert src.index("![cover](cover.png)") < src.index("docPartGallery")


@pytest.mark.skipif(not __import__("shutil").which("pandoc"), reason="pandoc not installed")
def test_docx_cover_before_toc_real_pandoc(tmp_path):
    # end-to-end through real pandoc: the produced .docx must order the cover
    # image before the generated TOC field, with the image embedded.
    import zipfile
    from PIL import Image
    from manualtrans.render import render

    media = tmp_path / "media"; media.mkdir()
    Image.new("RGB", (40, 40), (200, 0, 0)).save(media / "cover.png")
    md = tmp_path / "in.md"
    md.write_text("# Primo\n\ntesto\n\n## Sotto\n\naltro\n", encoding="utf-8")

    (out,) = render(md, tmp_path / "out", ["docx"], media, toc=True, cover=media / "cover.png")

    with zipfile.ZipFile(out) as z:
        xml = z.read("word/document.xml").decode("utf-8")
        assert any(n.startswith("word/media/") for n in z.namelist())  # cover embedded
    assert xml.index("<w:drawing") < xml.index("docPartGallery") < xml.index("Primo")
