from __future__ import annotations

import statistics

from .models import Block, Doc


def block_font_size(block: Block) -> float:
    x0, y0, x1, y1 = block.bbox
    n_lines = (block.content.count("\n") + 1) if block.content else 1
    return (y1 - y0) / max(1, n_lines)


def body_font_size(doc: Doc) -> float:
    text_sizes, all_sizes = [], []
    for page in doc.pages:
        for b in page.blocks:
            size = block_font_size(b)
            all_sizes.append(size)
            if b.type == "text":
                text_sizes.append(size)
    pool = text_sizes or all_sizes
    return float(statistics.median(pool)) if pool else 0.0
