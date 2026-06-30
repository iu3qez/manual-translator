from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

SUFFIX = {"pdf": ".pdf", "docx": ".docx"}


class RenderError(Exception):
    pass


_TOC_DEPTH = 2  # keep the generated index short (chapters + one sub-level)


def build_pandoc_cmd(md_path: Path, out_path: Path, media_dir: Path, toc: bool = False) -> list[str]:
    """Pandoc command for a native output (DOCX): pandoc embeds images itself."""
    cmd = [
        "pandoc",
        str(md_path),
        "--from=markdown+raw_html-implicit_figures",
        f"--resource-path={media_dir}",
        "-o",
        str(out_path),
    ]
    if toc:
        cmd += ["--toc", f"--toc-depth={_TOC_DEPTH}"]
    return cmd


def build_html_cmd(md_path: Path, html_path: Path, media_dir: Path,
                   css: Path | None = None, toc: bool = False,
                   before_body: Path | None = None) -> list[str]:
    """Standalone HTML with images inlined as data URIs.

    weasyprint resolves a relative ``<img src>`` against its own working dir, not
    media/, so a plain HTML loses every image. Inlining via --embed-resources
    (pandoc's own PDF path does NOT do this) makes the HTML self-contained.
    """
    cmd = [
        "pandoc",
        str(md_path),
        "--from=markdown+raw_html-implicit_figures",
        f"--resource-path={media_dir}",
        "--standalone",
        "--embed-resources",
        "-t",
        "html5",
        "-o",
        str(html_path),
    ]
    if css is not None:
        cmd.append(f"--css={css}")
    if before_body is not None:
        # cover injected here so it lands BEFORE the generated TOC (pandoc
        # template order: before-body → toc → body); --embed-resources inlines it.
        cmd += ["--include-before-body", str(before_body)]
    if toc:
        cmd += ["--toc", f"--toc-depth={_TOC_DEPTH}"]
    return cmd


def build_weasyprint_cmd(html_path: Path, pdf_path: Path) -> list[str]:
    return ["weasyprint", str(html_path), str(pdf_path)]


def _run(runner, cmd: list[str], label: str) -> None:
    result = runner(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RenderError(f"{label} failed: {getattr(result, 'stderr', '')}")


def _cover_before_body(cover: Path) -> Path:
    """Temp HTML injected before the body so the cover precedes the TOC (PDF)."""
    fd, name = tempfile.mkstemp(suffix=".html")
    os.close(fd)
    p = Path(name)
    p.write_text(
        f'<img class="cover" src="{cover.name}">\n'
        '<div style="page-break-after: always"></div>\n',
        encoding="utf-8",
    )
    return p


# A page break and a TOC field as raw OpenXML, for in-body placement in DOCX.
# pandoc's --toc emits the index at the very top of the document, ABOVE any cover
# prepended to the body; raw HTML (the page-break div / an <img>) is dropped by
# the docx writer. So when a cover is present we suppress --toc and inject the
# field here, after the cover. Like pandoc's own --toc, it's an unpopulated field
# (w:dirty) that Word fills in on open/update — nothing is lost.
_PAGE_BREAK_OPENXML = (
    '```{=openxml}\n<w:p><w:r><w:br w:type="page"/></w:r></w:p>\n```\n\n'
)


def _toc_openxml(depth: int) -> str:
    return (
        "```{=openxml}\n"
        "<w:sdt><w:sdtPr><w:docPartObj>"
        '<w:docPartGallery w:val="Table of Contents" /><w:docPartUnique />'
        "</w:docPartObj></w:sdtPr><w:sdtContent>"
        '<w:p><w:pPr><w:pStyle w:val="TOCHeading" /></w:pPr>'
        '<w:r><w:t xml:space="preserve">Table of Contents</w:t></w:r></w:p>'
        '<w:p><w:r><w:fldChar w:fldCharType="begin" w:dirty="true" />'
        f'<w:instrText xml:space="preserve">TOC \\o "1-{depth}" \\h \\z \\u</w:instrText>'
        '<w:fldChar w:fldCharType="separate" /><w:fldChar w:fldCharType="end" /></w:r></w:p>'
        "</w:sdtContent></w:sdt>\n"
        "```\n\n"
    )


def _md_with_cover(md_path: Path, cover: Path, toc: bool = False) -> Path:
    """Temp markdown with the cover image prepended (DOCX path: include-before-body
    does not embed raw-HTML images into DOCX, so the cover goes in the body).

    When ``toc`` is set, the index is injected (as a raw-OpenXML field) right after
    the cover so it lands AFTER the cover page — pandoc's own --toc would sit above
    it. Callers must then NOT pass --toc to pandoc."""
    fd, name = tempfile.mkstemp(suffix=".md")
    os.close(fd)
    p = Path(name)
    parts = [f"![cover]({cover.name}){{.cover}}\n\n", _PAGE_BREAK_OPENXML]
    if toc:
        parts.append(_toc_openxml(_TOC_DEPTH))
    parts.append(md_path.read_text(encoding="utf-8"))
    p.write_text("".join(parts), encoding="utf-8")
    return p


def render(
    md_path: str | Path,
    out_base: str | Path,
    formats: list[str],
    media_dir: str | Path,
    runner=subprocess.run,
    css: "Path | None" = None,
    toc: bool = False,
    cover: "Path | None" = None,
) -> list[Path]:
    md_path = Path(md_path)
    out_base = Path(out_base)
    media_dir = Path(media_dir)
    cover = Path(cover) if cover else None
    produced: list[Path] = []
    for fmt in formats:
        if fmt not in SUFFIX:
            raise RenderError(f"unsupported format: {fmt}")
        # append the extension instead of with_suffix: basenames legitimately
        # contain dots (e.g. version numbers like "manual-1.04_001").
        out_path = out_base.parent / (out_base.name + SUFFIX[fmt])
        if fmt == "pdf":
            # two steps: pandoc -> self-contained HTML -> weasyprint -> PDF
            # cover (if any) injected before-body so it precedes the TOC
            fd, tmp_name = tempfile.mkstemp(suffix=".html")
            os.close(fd)
            tmp_html = Path(tmp_name)
            before = _cover_before_body(cover) if cover else None
            try:
                _run(runner, build_html_cmd(md_path, tmp_html, media_dir, css=css, toc=toc,
                                            before_body=before), "pandoc (html)")
                _run(runner, build_weasyprint_cmd(tmp_html, out_path), "weasyprint")
            finally:
                tmp_html.unlink(missing_ok=True)
                if before:
                    before.unlink(missing_ok=True)
        else:
            # with a cover the TOC is injected into the body (after the cover) so
            # it follows the cover page; pandoc's own --toc would sit above it.
            if cover:
                src = _md_with_cover(md_path, cover, toc=toc)
                cmd = build_pandoc_cmd(src, out_path, media_dir, toc=False)
            else:
                src = md_path
                cmd = build_pandoc_cmd(src, out_path, media_dir, toc=toc)
            try:
                _run(runner, cmd, "pandoc")
            finally:
                if cover:
                    src.unlink(missing_ok=True)
        produced.append(out_path)
    return produced
