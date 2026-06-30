from __future__ import annotations

import numpy as np
from PIL import Image

from .models import Doc

# PIL HSV channels are 0-255 each. Red wraps around H=0.
ATTENTION_COLORS: dict[str, dict] = {
    "#cc0000": {"hue": [(0, 12), (243, 255)], "s_min": 90, "v_min": 50},
}
_COLOR_FRACTION = 0.5   # share of ink pixels matching a color to call the block that color
_BG_S_MAX = 40          # background = unsaturated …
_BG_V_MIN = 200         # … and bright


def block_color(hsv: np.ndarray, bbox) -> str | None:
    x0, y0, x1, y1 = (int(round(v)) for v in bbox)
    crop = hsv[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
    if crop.size == 0:
        return None
    H, S, V = crop[..., 0], crop[..., 1], crop[..., 2]
    ink = ~((S < _BG_S_MAX) & (V > _BG_V_MIN))
    ink_n = int(ink.sum())
    if ink_n == 0:
        return None
    best, best_frac = None, 0.0
    for hexv, spec in ATTENTION_COLORS.items():
        hue = np.zeros(H.shape, dtype=bool)
        for lo, hi in spec["hue"]:
            hue |= (H >= lo) & (H <= hi)
        mask = hue & (S >= spec["s_min"]) & (V >= spec["v_min"]) & ink
        frac = int(mask.sum()) / ink_n
        if frac > best_frac:
            best, best_frac = hexv, frac
    return best if best_frac >= _COLOR_FRACTION else None


def annotate_block_colors(doc: Doc, page_images: dict) -> Doc:
    for page in doc.pages:
        img = page_images.get(page.index)
        if img is None:
            continue
        if not hasattr(img, "convert"):
            img = Image.open(img)
        hsv = np.asarray(img.convert("HSV"))
        for b in page.blocks:
            if b.type in ("title", "text"):
                b.color = block_color(hsv, b.bbox)
    return doc
