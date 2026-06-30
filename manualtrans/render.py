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


def _md_with_cover(md_path: Path, cover: Path) -> Path:
    """Temp markdown with the cover image prepended (DOCX path: include-before-body
    does not embed raw-HTML images into DOCX, so the cover goes in the body)."""
    fd, name = tempfile.mkstemp(suffix=".md")
    os.close(fd)
    p = Path(name)
    p.write_text(
        f"![cover]({cover.name}){{.cover}}\n\n"
        '<div style="page-break-after: always"></div>\n\n'
        + md_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
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
            src = _md_with_cover(md_path, cover) if cover else md_path
            try:
                _run(runner, build_pandoc_cmd(src, out_path, media_dir, toc=toc), "pandoc")
            finally:
                if cover:
                    src.unlink(missing_ok=True)
        produced.append(out_path)
    return produced
