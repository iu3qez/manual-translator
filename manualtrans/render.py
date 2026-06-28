from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

SUFFIX = {"pdf": ".pdf", "docx": ".docx"}


class RenderError(Exception):
    pass


def build_pandoc_cmd(md_path: Path, out_path: Path, media_dir: Path) -> list[str]:
    """Pandoc command for a native output (DOCX): pandoc embeds images itself."""
    return [
        "pandoc",
        str(md_path),
        "--from=markdown+raw_html",
        f"--resource-path={media_dir}",
        "-o",
        str(out_path),
    ]


def build_html_cmd(md_path: Path, html_path: Path, media_dir: Path) -> list[str]:
    """Standalone HTML with images inlined as data URIs.

    weasyprint resolves a relative ``<img src>`` against its own working dir, not
    media/, so a plain HTML loses every image. Inlining via --embed-resources
    (pandoc's own PDF path does NOT do this) makes the HTML self-contained.
    """
    return [
        "pandoc",
        str(md_path),
        "--from=markdown+raw_html",
        f"--resource-path={media_dir}",
        "--standalone",
        "--embed-resources",
        "-t",
        "html5",
        "-o",
        str(html_path),
    ]


def build_weasyprint_cmd(html_path: Path, pdf_path: Path) -> list[str]:
    return ["weasyprint", str(html_path), str(pdf_path)]


def _run(runner, cmd: list[str], label: str) -> None:
    result = runner(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RenderError(f"{label} failed: {getattr(result, 'stderr', '')}")


def render(
    md_path: str | Path,
    out_base: str | Path,
    formats: list[str],
    media_dir: str | Path,
    runner=subprocess.run,
) -> list[Path]:
    md_path = Path(md_path)
    out_base = Path(out_base)
    media_dir = Path(media_dir)
    produced: list[Path] = []
    for fmt in formats:
        if fmt not in SUFFIX:
            raise RenderError(f"unsupported format: {fmt}")
        # append the extension instead of with_suffix: basenames legitimately
        # contain dots (e.g. version numbers like "manual-1.04_001").
        out_path = out_base.parent / (out_base.name + SUFFIX[fmt])
        if fmt == "pdf":
            # two steps: pandoc -> self-contained HTML -> weasyprint -> PDF
            fd, tmp_name = tempfile.mkstemp(suffix=".html")
            os.close(fd)
            tmp_html = Path(tmp_name)
            try:
                _run(runner, build_html_cmd(md_path, tmp_html, media_dir), "pandoc (html)")
                _run(runner, build_weasyprint_cmd(tmp_html, out_path), "weasyprint")
            finally:
                tmp_html.unlink(missing_ok=True)
        else:
            _run(runner, build_pandoc_cmd(md_path, out_path, media_dir), "pandoc")
        produced.append(out_path)
    return produced
