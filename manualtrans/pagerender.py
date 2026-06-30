from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def build_pdftoppm_cmd(pdf, page_1based: int, width, height, out_prefix) -> list[str]:
    return [
        "pdftoppm", "-png",
        "-f", str(page_1based), "-l", str(page_1based), "-singlefile",
        "-scale-to-x", str(int(width)), "-scale-to-y", str(int(height)),
        str(pdf), str(out_prefix),
    ]


def rasterize_pages(pdf_path, sizes, out_dir, runner=subprocess.run) -> dict[int, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    result: dict[int, Path] = {}
    for i, (w, h) in enumerate(sizes):
        if not w or not h:
            continue
        prefix = out_dir / f"page-{i}"
        r = runner(build_pdftoppm_cmd(pdf_path, i + 1, w, h, prefix), capture_output=True, text=True)
        if getattr(r, "returncode", 0) == 0:
            result[i] = prefix.with_suffix(".png")
    return result


def make_cover(page_png, out_path, text: str = "TRADUZIONE IN ITALIANO") -> Path:
    base = Image.open(page_png).convert("RGBA")
    w, h = base.size
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    try:
        font = ImageFont.load_default(size=max(28, w // 16))
    except TypeError:  # very old Pillow without size arg
        font = ImageFont.load_default()
    draw.text((w // 2, h // 2), text, fill=(204, 0, 0, 130), anchor="mm", font=font)
    layer = layer.rotate(30, expand=False)
    out = Image.alpha_composite(base, layer).convert("RGB")
    out.save(out_path)
    return Path(out_path)
