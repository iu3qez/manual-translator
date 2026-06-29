from __future__ import annotations

import re
import statistics

from .models import Block, Doc

HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*)$", re.MULTILINE)
_TOC_TITLE_RE = re.compile(r"^#{1,6}\s+(contents|table of contents|indice|sommario)\s*$",
                           re.IGNORECASE)
_LEADER_RE = re.compile(r"^.*\.{3,}\s*\d+\s*$")


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


def strip_ocr_toc(doc: Doc) -> Doc:
    out = doc.model_copy(deep=True)
    for page in out.pages:
        lines = page.markdown.splitlines()
        leader_idx = [i for i, ln in enumerate(lines) if _LEADER_RE.match(ln)]
        if len(leader_idx) < 3:
            continue
        title_idx = next((i for i, ln in enumerate(lines) if _TOC_TITLE_RE.match(ln)), None)
        if title_idx is None:
            continue
        drop = set(leader_idx) | {title_idx}
        kept = [ln for i, ln in enumerate(lines) if i not in drop]
        page.markdown = "\n".join(kept).strip()
    return out


_CALLOUT_RE = re.compile(r"^(note|nota|warning|attenzione|caution|avvertenza)\b[:\s]",
                         re.IGNORECASE)


def wrap_callouts(markdown: str) -> str:
    paragraphs = markdown.split("\n\n")
    out = []
    for para in paragraphs:
        first = para.lstrip().splitlines()[0] if para.strip() else ""
        if first.startswith(("![", "[")):
            out.append(para)
        elif _CALLOUT_RE.match(first):
            out.append(f'<div class="callout">\n{para.strip()}\n</div>')
        else:
            out.append(para)
    return "\n\n".join(out)
