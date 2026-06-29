from __future__ import annotations

import re
import statistics

from .models import Block, Doc

HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*)$", re.MULTILINE)


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


def _level_for_ratio(ratio: float, thresholds: tuple[float, float, float]) -> int:
    t1, t2, t3 = thresholds
    if ratio >= t1:
        return 1
    if ratio >= t2:
        return 2
    if ratio >= t3:
        return 3
    return 4


def title_block_levels(page, body, thresholds=(1.7, 1.35, 1.15)) -> list[int]:
    if not body:
        return []
    titles = sorted((b for b in page.blocks if b.type == "title"), key=lambda b: b.bbox[1])
    return [_level_for_ratio(block_font_size(b) / body, thresholds) for b in titles]


def reclassify_headings(en_doc: Doc, it_doc: Doc, thresholds=(1.7, 1.35, 1.15)) -> Doc:
    out = it_doc.model_copy(deep=True)
    body = body_font_size(en_doc)
    if not body:
        return out
    for en_page, it_page in zip(en_doc.pages, out.pages):
        levels = title_block_levels(en_page, body, thresholds)
        headings = HEADING_RE.findall(it_page.markdown)
        if not levels or len(levels) != len(headings):
            continue
        seq = iter(levels)

        def _sub(m: "re.Match") -> str:
            return f"{'#' * next(seq)} {m.group(2)}"

        it_page.markdown = HEADING_RE.sub(_sub, it_page.markdown)
    return out
