from __future__ import annotations

import logging
import re
import statistics
from pathlib import Path

from .models import Block, Doc

logger = logging.getLogger(__name__)

HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*)$", re.MULTILINE)
_TOC_TITLE_RE = re.compile(r"^#{1,6}\s+(contents|table of contents|indice|sommario)\s*$",
                           re.IGNORECASE)
_LEADER_RE = re.compile(r"^.*\.{3,}\s*\d+\s*$")


def block_font_size(block: Block) -> float:
    x0, y0, x1, y1 = block.bbox
    n_lines = (block.content.count("\n") + 1) if block.content else 1
    return (y1 - y0) / max(1, n_lines)


_BODY_LINE_PCT = 0.15


def _percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = max(0, min(len(sorted_vals) - 1, round(q * (len(sorted_vals) - 1))))
    return float(sorted_vals[idx])


def body_font_size(doc: Doc) -> float:
    """Height (px) of ONE body text line.

    Mistral OCR packs each text block's content onto a single line (no newlines),
    so block_height/n_lines wildly overestimates multi-line paragraphs. Instead
    take a low percentile of text-block heights: genuine single-line blocks sit at
    the bottom of the height distribution and approximate one line. Used both as
    the body line height and as the denominator for heading-level ratios.
    """
    heights = [b.bbox[3] - b.bbox[1] for p in doc.pages for b in p.blocks if b.type == "text"]
    if not heights:
        heights = [b.bbox[3] - b.bbox[1] for p in doc.pages for b in p.blocks]
    heights.sort()
    return _percentile(heights, _BODY_LINE_PCT)


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
    reconstructed = skipped = 0
    for en_page, it_page in zip(en_doc.pages, out.pages):
        levels = title_block_levels(en_page, body, thresholds)
        if not levels:
            continue
        headings = HEADING_RE.findall(it_page.markdown)
        if len(levels) != len(headings):
            # title-block count != heading count → reading-order match is unsafe,
            # leave the page's levels untouched (and make the skip observable)
            skipped += 1
            continue
        seq = iter(levels)

        def _sub(m: "re.Match") -> str:
            return f"{'#' * next(seq)} {m.group(2)}"

        it_page.markdown = HEADING_RE.sub(_sub, it_page.markdown)
        reconstructed += 1
    logger.info(
        "layout: heading levels reconstructed on %d page(s), %d skipped (title/heading count mismatch)",
        reconstructed, skipped,
    )
    return out


# A TOC may span several pages: the first carries the "Contents/Indice" heading,
# continuation pages carry only dotted-leader lines. Treat a page as TOC when it
# has a title + a few leaders, OR enough leaders on their own (continuation page).
_MIN_TOC_LEADERS = 5


def strip_ocr_toc(doc: Doc) -> Doc:
    out = doc.model_copy(deep=True)
    for page in out.pages:
        lines = page.markdown.splitlines()
        leader_idx = [i for i, ln in enumerate(lines) if _LEADER_RE.match(ln)]
        title_idx = next((i for i, ln in enumerate(lines) if _TOC_TITLE_RE.match(ln)), None)
        is_toc = (title_idx is not None and len(leader_idx) >= 3) or len(leader_idx) >= _MIN_TOC_LEADERS
        if not is_toc:
            continue
        drop = set(leader_idx) | ({title_idx} if title_idx is not None else set())
        kept = [ln for i, ln in enumerate(lines) if i not in drop]
        page.markdown = "\n".join(kept).strip()
    return out


_HEADING_MULT = {1: 1.9, 2: 1.5, 3: 1.25, 4: 1.1}
# a text-line bbox is taller than the glyphs (leading/line-box); map line-box → font
_GLYPH_FACTOR = 0.72


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def style_profile(doc: Doc) -> dict:
    dpis = [p.dpi for p in doc.pages if p.dpi]
    dpi = statistics.median(dpis) if dpis else 200.0
    body_px = body_font_size(doc)  # one body line height (px)
    body_pt = round(_clamp(body_px / dpi * 72 * _GLYPH_FACTOR, 8.0, 12.0), 1) if body_px else 10.5
    widths = [p.width for p in doc.pages if p.width]
    heights = [p.height for p in doc.pages if p.height]
    if widths and heights:
        w_mm = statistics.median(widths) / dpi * 25.4
        h_mm = statistics.median(heights) / dpi * 25.4
        if abs(w_mm - 210) <= 8 and abs(h_mm - 297) <= 8:
            w_mm, h_mm = 210.0, 297.0
        else:
            w_mm, h_mm = round(w_mm), round(h_mm)
    else:
        w_mm, h_mm = 210.0, 297.0
    return {
        "body_pt": body_pt,
        "line_height": 1.25,
        "page_w_mm": w_mm,
        "page_h_mm": h_mm,
        "margin_mm": 18.0,
        "headings": {n: round(body_pt * m, 1) for n, m in _HEADING_MULT.items()},
    }


def render_css(profile: dict) -> str:
    h = profile["headings"]
    return f"""@page {{ size: {profile['page_w_mm']}mm {profile['page_h_mm']}mm; margin: {profile['margin_mm']}mm; }}
body {{ font-family: "DejaVu Sans", Arial, sans-serif; font-size: {profile['body_pt']}pt; line-height: {profile['line_height']}; }}
h1 {{ font-size: {h[1]}pt; }}
h2 {{ font-size: {h[2]}pt; }}
h3 {{ font-size: {h[3]}pt; }}
h4 {{ font-size: {h[4]}pt; }}
table {{ border-collapse: collapse; font-size: {profile['body_pt']}pt; }}
th, td {{ border: 0.5pt solid #888; padding: 2pt 4pt; }}
.callout {{ border-left: 3pt solid #36c; background: #eef3ff; padding: 4pt 8pt; margin: 6pt 0; }}
img {{ max-width: 100%; }}
img.cover {{ width: 100%; display: block; }}
"""


def cover_markdown(cover_filename: str) -> str:
    return (f"![cover]({cover_filename}){{.cover}}\n\n"
            '<div style="page-break-after: always"></div>')


def write_css(profile: dict, path) -> Path:
    path = Path(path)
    path.write_text(render_css(profile), encoding="utf-8")
    return path


_LIST_RE = re.compile(r"^\s*([-*+]\s|\d+\.\s)")
_TABLE_PLACEHOLDER_RE = re.compile(r"^\[[^\]]+\.html\]\([^)]*\)\s*$")


def _is_colorable_segment(seg: str) -> bool:
    s = seg.strip()
    if not s:
        return False
    first = s.splitlines()[0].lstrip()
    if first.startswith(("![", "<table", "<div")):
        return False
    if _TABLE_PLACEHOLDER_RE.match(first):
        return False
    if _LIST_RE.match(first):
        return False
    return True


def _wrap_segment(seg: str, hex_color: str) -> str:
    m = HEADING_RE.match(seg)
    span = lambda txt: f'<span style="color:{hex_color}">{txt}</span>'
    if m:
        return f"{m.group(1)} {span(m.group(2))}{seg[m.end():]}"
    return span(seg)


def apply_block_colors(en_doc: Doc, it_doc: Doc) -> Doc:
    out = it_doc.model_copy(deep=True)
    colored = skipped = 0
    for en_page, it_page in zip(en_doc.pages, out.pages):
        blocks = [b for b in sorted(en_page.blocks, key=lambda b: b.bbox[1])
                  if b.type in ("title", "text")]
        segments = it_page.markdown.split("\n\n")
        colorable_idx = [i for i, s in enumerate(segments) if _is_colorable_segment(s)]
        if not blocks or len(blocks) != len(colorable_idx):
            if any(b.color for b in blocks):
                skipped += 1
            continue
        for blk, idx in zip(blocks, colorable_idx):
            if blk.color:
                segments[idx] = _wrap_segment(segments[idx], blk.color)
                colored += 1
        it_page.markdown = "\n\n".join(segments)
    logger.info("layout: colored %d block(s), %d page(s) skipped (count mismatch)", colored, skipped)
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
