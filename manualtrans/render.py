from __future__ import annotations

import subprocess
from pathlib import Path

SUFFIX = {"pdf": ".pdf", "docx": ".docx"}


class RenderError(Exception):
    pass


def build_pandoc_cmd(md_path: Path, out_path: Path, media_dir: Path) -> list[str]:
    cmd = [
        "pandoc",
        str(md_path),
        "--from=markdown+raw_html",
        f"--resource-path={media_dir}",
        "-o",
        str(out_path),
    ]
    if out_path.suffix == ".pdf":
        cmd.append("--pdf-engine=weasyprint")
    return cmd


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
        out_path = out_base.with_suffix(SUFFIX[fmt])
        cmd = build_pandoc_cmd(md_path, out_path, media_dir)
        result = runner(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RenderError(f"pandoc failed for {fmt}: {getattr(result, 'stderr', '')}")
        produced.append(out_path)
    return produced
