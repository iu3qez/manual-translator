# manualtrans M2 — Layout reconstruction (OCR-4 blocks) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Use OCR-4 block geometry to reconstruct heading hierarchy and generate an adaptive stylesheet, so the translated PDF is "paragraph similar" to the original (compact type, correct heading levels, real TOC, styled callouts).

**Architecture:** OCR-4 (`include_blocks=True`) adds per-block type+bbox and page dimensions to `doc.json`. A new `layout.py` derives, deterministically: body font size and per-level heading sizes from block bbox heights, correct heading levels (applied to the translated doc by reading order), removal of the OCR'd TOC, callout wrapping, and a generated CSS. Render passes the CSS to weasyprint and `--toc` to pandoc. No pixel-perfect reconstruction — the document still reflows.

**Tech Stack:** Python 3.11+, pydantic, mistralai 2.5.0 (OCR-4), pandoc + weasyprint, pytest. Reuses M1 modules (models, ocr, translate, assemble, render, main).

## Global Constraints

- **No pixel-perfect layout.** Reflowing markdown; goal is "paragraph similar" (type scale + density resemble the original), content may span more pages.
- **OCR-4 default:** OCR model default = `mistral-ocr-latest`; `include_blocks=True` only on OCR-4. `--ocr-model ocr3` → `mistral-ocr-2512`, no blocks, M1 flat behavior preserved.
- **Reclassification + TOC strip happen on the TRANSLATED (IT) doc**, deriving structure from the EN doc by reading order — so the per-page translation cache (keyed by page index) is never disrupted.
- **Heading level thresholds** (font/body ratio): ≥1.7→h1, ≥1.35→h2, ≥1.15→h3, else h4.
- **bbox units are pixels** at `dimensions.dpi`; pt = px / dpi * 72; mm = px / dpi * 25.4.
- **Layout is a no-op when blocks are absent** (OCR-3 or missing) — graceful fallback to M1 render.
- No real API calls in tests (fake OCR responses, no network). TDD; frequent commits.
- Placeholders preserved byte-for-byte; one input unit = one output unit (M1 invariants hold).

## File Structure

```
manualtrans/
  models.py     # MODIFY: add Block; Page gains blocks/width/height/dpi
  ocr.py        # MODIFY: parse blocks + dimensions; include_blocks on OCR-4
  config.py     # MODIFY: ocr_model default -> mistral-ocr-latest
  layout.py     # CREATE: font-size/levels/reclassify/strip-toc/callouts/css
  render.py     # MODIFY: build cmds accept css + toc
  main.py       # MODIFY: ocr-model alias, wire layout into `run`, --no-layout
tests/
  test_models.py test_ocr.py test_layout.py test_render.py test_cli.py  # MODIFY/extend
```

---

## Task 1: Schema — `Block` and page geometry

**Files:**
- Modify: `manualtrans/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces:
  - `Block(type: str, bbox: list[float], content: str | None = None)`
  - `Page` gains `blocks: list[Block] = []`, `width: float | None = None`, `height: float | None = None`, `dpi: float | None = None`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_models.py`)

```python
def test_block_and_page_geometry_roundtrip(tmp_path):
    from manualtrans.models import Doc, Page, Block
    doc = Doc(
        source_pdf="m.pdf", source_hash="H", ocr_model="mistral-ocr-latest",
        pages=[Page(
            index=0, markdown="# T",
            blocks=[Block(type="title", bbox=[10.0, 20.0, 300.0, 60.0], content="T")],
            width=1654.0, height=2339.0, dpi=200.0,
        )],
    )
    p = tmp_path / "doc.json"
    doc.dump(p)
    loaded = Doc.load(p)
    assert loaded == doc
    assert loaded.pages[0].blocks[0].type == "title"
    assert loaded.pages[0].blocks[0].bbox == [10.0, 20.0, 300.0, 60.0]
    assert loaded.pages[0].dpi == 200.0


def test_page_block_defaults():
    from manualtrans.models import Page
    page = Page(index=0, markdown="x")
    assert page.blocks == []
    assert page.width is None and page.height is None and page.dpi is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest tests/test_models.py -v`
Expected: FAIL (`cannot import name 'Block'`)

- [ ] **Step 3: Implement** — in `manualtrans/models.py`, add `Block` before `Page` and extend `Page`:

```python
class Block(BaseModel):
    type: str
    bbox: list[float]
    content: str | None = None
```

Add these fields to `Page` (keep existing fields):
```python
    blocks: list[Block] = []
    width: float | None = None
    height: float | None = None
    dpi: float | None = None
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --no-sync pytest tests/test_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add manualtrans/models.py tests/test_models.py
git commit -m "feat: add Block schema and page geometry (blocks/width/height/dpi)"
```

---

## Task 2: OCR-4 block extraction (`ocr.py` + `config.py`)

**Files:**
- Modify: `manualtrans/ocr.py`, `manualtrans/config.py`
- Test: `tests/test_ocr.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: `Block`, `Page` (Task 1).
- Produces: `parse_ocr_response(...)` now also fills `page.blocks` (from `response.pages[i].blocks`, each with `.type`, `.top_left_x/_y`, `.bottom_right_x/_y`, `.content`) and `page.width/height/dpi` (from `response.pages[i].dimensions.width/height/dpi`). `run_ocr` passes `include_blocks=True` when `ocr_model == "mistral-ocr-latest"`. Settings default `ocr_model = "mistral-ocr-latest"`.

**Note on the real SDK (verified):** `OCRPageObject` has `.dimensions` (`OCRPageDimensions(dpi, height, width)`) and `.blocks` (a list of typed block objects, each with `top_left_x, top_left_y, bottom_right_x, bottom_right_y, content, type`). `ocr.process(..., include_blocks=True)` returns them. The test below uses a fake response, so it does not need the live API; keep `parse_ocr_response`'s field access matching these names.

- [ ] **Step 1: Write the failing test** (append to `tests/test_ocr.py`)

```python
def test_parse_blocks_and_dimensions(tmp_path):
    from types import SimpleNamespace
    import base64
    from manualtrans.ocr import parse_ocr_response

    raw = base64.b64encode(b"jpegbytes").decode()
    resp = SimpleNamespace(pages=[SimpleNamespace(
        markdown="# Big title\n\nbody",
        images=[SimpleNamespace(id="img-0.jpeg", image_base64=raw)],
        tables=[],
        dimensions=SimpleNamespace(dpi=200, width=1654, height=2339),
        blocks=[
            SimpleNamespace(type="title", top_left_x=10, top_left_y=20,
                            bottom_right_x=300, bottom_right_y=70, content="Big title"),
            SimpleNamespace(type="text", top_left_x=10, top_left_y=90,
                            bottom_right_x=300, bottom_right_y=110, content="body"),
        ],
    )])
    doc = parse_ocr_response(resp, "m.pdf", "H", "mistral-ocr-latest", tmp_path / "media")
    pg = doc.pages[0]
    assert pg.dpi == 200 and pg.width == 1654 and pg.height == 2339
    assert [b.type for b in pg.blocks] == ["title", "text"]
    assert pg.blocks[0].bbox == [10, 20, 300, 70]
    assert pg.blocks[0].content == "Big title"


def test_parse_no_blocks_ok(tmp_path):
    # OCR-3 style response: no blocks/dimensions attrs -> empty/None, no crash
    from types import SimpleNamespace
    from manualtrans.ocr import parse_ocr_response
    resp = SimpleNamespace(pages=[SimpleNamespace(markdown="x", images=[], tables=[])])
    doc = parse_ocr_response(resp, "m.pdf", "H", "mistral-ocr-2512", tmp_path / "media")
    assert doc.pages[0].blocks == []
    assert doc.pages[0].dpi is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest tests/test_ocr.py::test_parse_blocks_and_dimensions -v`
Expected: FAIL (blocks/dimensions not populated)

- [ ] **Step 3: Implement in `parse_ocr_response`** — add block + dimension extraction. Import `Block` at top (`from .models import Doc, Image, Page, Table, Block`). Inside the per-page loop, before constructing `Page`, add:

```python
        blocks: list[Block] = []
        for blk in getattr(p, "blocks", None) or []:
            blocks.append(Block(
                type=getattr(blk, "type", "text"),
                bbox=[blk.top_left_x, blk.top_left_y, blk.bottom_right_x, blk.bottom_right_y],
                content=getattr(blk, "content", None),
            ))
        dims = getattr(p, "dimensions", None)
        width = getattr(dims, "width", None) if dims else None
        height = getattr(dims, "height", None) if dims else None
        dpi = getattr(dims, "dpi", None) if dims else None
```

Then pass them into the `Page(...)` constructor (add `blocks=blocks, width=width, height=height, dpi=dpi`).

- [ ] **Step 4: Implement `include_blocks` in `run_ocr`** — change the `client.ocr.process(...)` call to compute and pass the flag:

```python
    response = client.ocr.process(
        model=ocr_model,
        document={"type": "document_url", "document_url": signed.url},
        table_format="html",
        include_image_base64=True,
        extract_header=True,
        extract_footer=True,
        include_blocks=(ocr_model == "mistral-ocr-latest"),
    )
```

- [ ] **Step 5: Change the OCR default in `config.py`**

Change the field default:
```python
    ocr_model: str = "mistral-ocr-latest"
```

- [ ] **Step 6: Update the config default test** — in `tests/test_config.py::test_defaults`, change the assertion:

```python
    assert s.ocr_model == "mistral-ocr-latest"
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run --no-sync pytest tests/test_ocr.py tests/test_config.py -v`
Expected: PASS (both new ocr tests + config tests)

- [ ] **Step 8: Commit**

```bash
git add manualtrans/ocr.py manualtrans/config.py tests/test_ocr.py tests/test_config.py
git commit -m "feat: extract OCR-4 blocks + page dimensions; default to OCR-4"
```

---

## Task 3: `layout.py` — font-size estimation

**Files:**
- Create: `manualtrans/layout.py`
- Test: `tests/test_layout.py`

**Interfaces:**
- Consumes: `Block`, `Doc`, `Page` (Task 1).
- Produces:
  - `block_font_size(block: Block) -> float` — `(y1 - y0) / n_lines`, `n_lines = content.count("\n") + 1` (min 1).
  - `body_font_size(doc: Doc) -> float` — median `block_font_size` over `text` blocks; if none, median over all blocks; if no blocks, returns `0.0`.

- [ ] **Step 1: Write the failing test**

```python
from manualtrans.layout import block_font_size, body_font_size
from manualtrans.models import Doc, Page, Block


def test_block_font_size_single_and_multiline():
    one = Block(type="title", bbox=[0, 0, 100, 40], content="Title")
    assert block_font_size(one) == 40.0
    two = Block(type="text", bbox=[0, 0, 100, 40], content="line1\nline2")
    assert block_font_size(two) == 20.0


def test_body_font_size_median_of_text_blocks():
    doc = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="mistral-ocr-latest",
              pages=[Page(index=0, markdown="x", blocks=[
                  Block(type="title", bbox=[0, 0, 10, 40], content="T"),
                  Block(type="text", bbox=[0, 0, 10, 20], content="a"),
                  Block(type="text", bbox=[0, 0, 10, 22], content="b"),
                  Block(type="text", bbox=[0, 0, 10, 24], content="c"),
              ])])
    assert body_font_size(doc) == 22.0  # median of 20,22,24, ignores the title


def test_body_font_size_no_blocks():
    doc = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="x",
              pages=[Page(index=0, markdown="x")])
    assert body_font_size(doc) == 0.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest tests/test_layout.py -v`
Expected: FAIL (`No module named 'manualtrans.layout'`)

- [ ] **Step 3: Implement** — create `manualtrans/layout.py`:

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --no-sync pytest tests/test_layout.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add manualtrans/layout.py tests/test_layout.py
git commit -m "feat: layout font-size estimation from block bboxes"
```

---

## Task 4: `layout.py` — heading level reconstruction

**Files:**
- Modify: `manualtrans/layout.py`
- Test: `tests/test_layout.py`

**Interfaces:**
- Consumes: `block_font_size`, `body_font_size` (Task 3); `Doc`/`Page`/`Block`.
- Produces:
  - `HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*)$", re.MULTILINE)`
  - `title_block_levels(page, body, thresholds=(1.7, 1.35, 1.15)) -> list[int]` — for each `title` block (sorted by `bbox[1]`, i.e. top y), the level 1..4 from `font/body` ratio.
  - `reclassify_headings(en_doc, it_doc, thresholds=(1.7, 1.35, 1.15)) -> Doc` — returns a copy of `it_doc` where, per page, the i-th markdown heading's `#`-depth is set to the i-th EN title-block level. Skips a page when the heading count and title-block count differ, or when body size is 0.

- [ ] **Step 1: Write the failing test**

```python
from manualtrans.layout import title_block_levels, reclassify_headings


def _doc(pages):
    from manualtrans.models import Doc
    return Doc(source_pdf="m.pdf", source_hash="H", ocr_model="mistral-ocr-latest", pages=pages)


def test_title_block_levels_by_ratio():
    from manualtrans.models import Page, Block
    page = Page(index=0, markdown="x", blocks=[
        Block(type="title", bbox=[0, 10, 10, 50], content="A"),   # 40 -> 2.0x body -> h1
        Block(type="title", bbox=[0, 60, 10, 88], content="B"),   # 28 -> 1.4x -> h2
        Block(type="text",  bbox=[0, 90, 10, 110], content="t"),  # 20 = body
    ])
    assert title_block_levels(page, body=20.0) == [1, 2]


def test_reclassify_applies_en_levels_to_it():
    from manualtrans.models import Page, Block
    en = _doc([Page(index=0, markdown="# A\n\n# B\n\nbody", blocks=[
        Block(type="title", bbox=[0, 10, 10, 50], content="A"),    # h1
        Block(type="title", bbox=[0, 60, 10, 84], content="B"),    # 24 -> 1.2x -> h3
        Block(type="text",  bbox=[0, 90, 10, 110], content="body"),
    ])])
    it = _doc([Page(index=0, markdown="# A-it\n\n# B-it\n\ncorpo")])
    out = reclassify_headings(en, it)
    assert out.pages[0].markdown == "# A-it\n\n### B-it\n\ncorpo"


def test_reclassify_skips_on_count_mismatch():
    from manualtrans.models import Page, Block
    en = _doc([Page(index=0, markdown="# A", blocks=[
        Block(type="title", bbox=[0, 0, 10, 40], content="A"),
        Block(type="title", bbox=[0, 50, 10, 90], content="extra"),
        Block(type="text", bbox=[0, 0, 10, 20], content="b"),
    ])])
    it = _doc([Page(index=0, markdown="# A-it")])  # 1 heading vs 2 title blocks
    out = reclassify_headings(en, it)
    assert out.pages[0].markdown == "# A-it"  # unchanged
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest tests/test_layout.py -v`
Expected: FAIL (`cannot import name 'title_block_levels'`)

- [ ] **Step 3: Implement** — add to `manualtrans/layout.py` (add `import re` and `HEADING_RE`):

```python
import re

HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*)$", re.MULTILINE)


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
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --no-sync pytest tests/test_layout.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add manualtrans/layout.py tests/test_layout.py
git commit -m "feat: reconstruct heading levels from OCR-4 title-block sizes"
```

---

## Task 5: `layout.py` — strip the OCR'd TOC

**Files:**
- Modify: `manualtrans/layout.py`
- Test: `tests/test_layout.py`

**Interfaces:**
- Produces: `strip_ocr_toc(doc: Doc) -> Doc` — returns a copy where, on any page, a heading line whose text matches `Contents|Indice|Sommario|Table of Contents` (case-insensitive) AND that is followed (within the page) by ≥3 dotted-leader lines (`^.*\.{3,}\s*\d+\s*$`) has the heading line and all those leader lines removed. Pages without the pattern are untouched.

- [ ] **Step 1: Write the failing test**

```python
from manualtrans.layout import strip_ocr_toc


def test_strip_ocr_toc_removes_index_block():
    from manualtrans.models import Doc, Page
    toc = ("# Indice\n\n"
           "1. Panoramica....................3\n"
           "2. Connettori...................7\n"
           "3. Display......................11\n\n"
           "Testo reale che resta.")
    doc = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="x",
              pages=[Page(index=0, markdown=toc)])
    out = strip_ocr_toc(doc)
    md = out.pages[0].markdown
    assert "Indice" not in md
    assert "....." not in md
    assert "Testo reale che resta." in md


def test_strip_ocr_toc_leaves_normal_page():
    from manualtrans.models import Doc, Page
    doc = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="x",
              pages=[Page(index=0, markdown="# Capitolo 1\n\nParagrafo normale.")])
    out = strip_ocr_toc(doc)
    assert out.pages[0].markdown == "# Capitolo 1\n\nParagrafo normale."
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest tests/test_layout.py -v`
Expected: FAIL (`cannot import name 'strip_ocr_toc'`)

- [ ] **Step 3: Implement** — add to `manualtrans/layout.py`:

```python
_TOC_TITLE_RE = re.compile(r"^#{1,6}\s+(contents|table of contents|indice|sommario)\s*$",
                           re.IGNORECASE)
_LEADER_RE = re.compile(r"^.*\.{3,}\s*\d+\s*$")


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
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --no-sync pytest tests/test_layout.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add manualtrans/layout.py tests/test_layout.py
git commit -m "feat: strip OCR'd table-of-contents (stale page numbers)"
```

---

## Task 6: `layout.py` — wrap callouts

**Files:**
- Modify: `manualtrans/layout.py`
- Test: `tests/test_layout.py`

**Interfaces:**
- Produces: `wrap_callouts(markdown: str) -> str` — wraps any paragraph (block of non-blank lines) whose first line starts with `NOTE|NOTA|WARNING|ATTENZIONE|CAUTION|AVVERTENZA` (case-insensitive, optional `:`/space) in `<div class="callout">\n...\n</div>`. Other paragraphs unchanged. Image/table placeholder paragraphs (starting with `![` or `[`) are never wrapped.

- [ ] **Step 1: Write the failing test**

```python
from manualtrans.layout import wrap_callouts


def test_wrap_callouts_wraps_note_and_warning():
    md = "NOTA: questo è importante.\n\nParagrafo normale.\n\nWARNING attenzione qui."
    out = wrap_callouts(md)
    assert '<div class="callout">\nNOTA: questo è importante.\n</div>' in out
    assert '<div class="callout">\nWARNING attenzione qui.\n</div>' in out
    assert "Paragrafo normale." in out
    assert out.count('<div class="callout">') == 2


def test_wrap_callouts_ignores_placeholders_and_plain():
    md = "![img-0.jpeg](img-0.jpeg)\n\nTesto semplice."
    assert wrap_callouts(md) == md
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest tests/test_layout.py -v`
Expected: FAIL (`cannot import name 'wrap_callouts'`)

- [ ] **Step 3: Implement** — add to `manualtrans/layout.py`:

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --no-sync pytest tests/test_layout.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add manualtrans/layout.py tests/test_layout.py
git commit -m "feat: wrap NOTE/WARNING callouts for styling"
```

---

## Task 7: `layout.py` — style profile and CSS generation

**Files:**
- Modify: `manualtrans/layout.py`
- Test: `tests/test_layout.py`

**Interfaces:**
- Consumes: `body_font_size` (Task 3); `Doc`/`Page`.
- Produces:
  - `style_profile(doc: Doc) -> dict` — `{body_pt, line_height, page_w_mm, page_h_mm, margin_mm, headings: {1,2,3,4}}`. Uses median page `dpi` (fallback 200) to convert px→pt/mm. `body_pt = clamp(body_px/dpi*72, 8.0, 13.0)`, fallback 10.5 when no blocks. Heading pts = `body_pt * {1: 1.9, 2: 1.5, 3: 1.25, 4: 1.1}`. Page size from median width/height px→mm; if within 8mm of A4 (210×297) snap to A4; margin fixed 18.0 mm. `line_height = 1.25`.
  - `render_css(profile: dict) -> str` — a CSS string with `@page`, `body`, `h1..h4`, `table/th/td`, `.callout`.
  - `write_css(profile: dict, path) -> Path` — writes the CSS, returns the path.

- [ ] **Step 1: Write the failing test**

```python
from manualtrans.layout import style_profile, render_css, write_css
from manualtrans.models import Doc, Page, Block


def test_style_profile_from_blocks():
    # body text blocks ~20px tall at 200dpi -> 20/200*72 = 7.2pt -> clamped to 8.0
    doc = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="mistral-ocr-latest",
              pages=[Page(index=0, markdown="x", dpi=200, width=1654, height=2339, blocks=[
                  Block(type="text", bbox=[0, 0, 10, 20], content="a"),
                  Block(type="text", bbox=[0, 0, 10, 20], content="b"),
              ])])
    prof = style_profile(doc)
    assert prof["body_pt"] == 8.0
    assert prof["page_w_mm"] == 210 and prof["page_h_mm"] == 297  # snapped to A4
    assert prof["headings"][1] > prof["headings"][2] > prof["body_pt"]


def test_style_profile_no_blocks_fallback():
    doc = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="x",
              pages=[Page(index=0, markdown="x")])
    prof = style_profile(doc)
    assert prof["body_pt"] == 10.5
    assert prof["page_w_mm"] == 210


def test_render_and_write_css(tmp_path):
    doc = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="x",
              pages=[Page(index=0, markdown="x")])
    css = render_css(style_profile(doc))
    assert "@page" in css and "body" in css and ".callout" in css and "h1" in css
    p = write_css(style_profile(doc), tmp_path / "style.css")
    assert p.read_text(encoding="utf-8") == css
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest tests/test_layout.py -v`
Expected: FAIL (`cannot import name 'style_profile'`)

- [ ] **Step 3: Implement** — add to `manualtrans/layout.py` (add `from pathlib import Path`):

```python
from pathlib import Path

_HEADING_MULT = {1: 1.9, 2: 1.5, 3: 1.25, 4: 1.1}


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def style_profile(doc: Doc) -> dict:
    dpis = [p.dpi for p in doc.pages if p.dpi]
    dpi = statistics.median(dpis) if dpis else 200.0
    body_px = body_font_size(doc)
    body_pt = round(_clamp(body_px / dpi * 72, 8.0, 13.0), 1) if body_px else 10.5
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
"""


def write_css(profile: dict, path) -> Path:
    path = Path(path)
    path.write_text(render_css(profile), encoding="utf-8")
    return path
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --no-sync pytest tests/test_layout.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add manualtrans/layout.py tests/test_layout.py
git commit -m "feat: generate adaptive CSS from measured page/block metrics"
```

---

## Task 8: `render.py` — CSS and TOC options

**Files:**
- Modify: `manualtrans/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `build_pandoc_cmd(md_path, out_path, media_dir, toc: bool = False) -> list[str]` — adds `--toc --toc-depth=3` when `toc`.
  - `build_html_cmd(md_path, html_path, media_dir, css: Path | None = None, toc: bool = False) -> list[str]` — adds `--css=<css>` when given, `--toc --toc-depth=3` when `toc`.
  - `render(md_path, out_base, formats, media_dir, runner=subprocess.run, css: Path | None = None, toc: bool = False) -> list[Path]` — threads `css`/`toc` into the pdf (html) and docx commands.

- [ ] **Step 1: Write the failing test** (append/adjust in `tests/test_render.py`)

```python
def test_html_cmd_css_and_toc(tmp_path):
    from manualtrans.render import build_html_cmd
    cmd = build_html_cmd(tmp_path / "in.md", tmp_path / "o.html", tmp_path / "media",
                         css=tmp_path / "s.css", toc=True)
    assert any(a == f"--css={tmp_path / 's.css'}" for a in cmd)
    assert "--toc" in cmd and "--toc-depth=3" in cmd


def test_pandoc_cmd_toc(tmp_path):
    from manualtrans.render import build_pandoc_cmd
    cmd = build_pandoc_cmd(tmp_path / "in.md", tmp_path / "o.docx", tmp_path / "media", toc=True)
    assert "--toc" in cmd


def test_render_threads_css_and_toc(tmp_path):
    from manualtrans.render import render
    md = tmp_path / "in.md"; md.write_text("# hi", encoding="utf-8")
    calls = []

    class R:
        returncode = 0
        stderr = ""

    def runner(cmd, **k):
        calls.append(cmd)
        return R()

    render(md, tmp_path / "out", ["pdf", "docx"], tmp_path / "media",
           runner=runner, css=tmp_path / "s.css", toc=True)
    flat = [a for c in calls for a in c]
    assert any(a == f"--css={tmp_path / 's.css'}" for a in flat)  # pdf html step
    assert flat.count("--toc") == 2  # pdf html + docx
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest tests/test_render.py -v`
Expected: FAIL (unexpected `css`/`toc` kwargs)

- [ ] **Step 3: Implement** — update `manualtrans/render.py`:

Replace `build_pandoc_cmd` signature/body:
```python
def build_pandoc_cmd(md_path: Path, out_path: Path, media_dir: Path, toc: bool = False) -> list[str]:
    cmd = [
        "pandoc",
        str(md_path),
        "--from=markdown+raw_html-implicit_figures",
        f"--resource-path={media_dir}",
        "-o",
        str(out_path),
    ]
    if toc:
        cmd += ["--toc", "--toc-depth=3"]
    return cmd
```

Replace `build_html_cmd`:
```python
def build_html_cmd(md_path: Path, html_path: Path, media_dir: Path,
                   css: Path | None = None, toc: bool = False) -> list[str]:
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
    if toc:
        cmd += ["--toc", "--toc-depth=3"]
    return cmd
```

Update `render(...)` signature and the two call sites:
```python
def render(
    md_path: str | Path,
    out_base: str | Path,
    formats: list[str],
    media_dir: str | Path,
    runner=subprocess.run,
    css: "Path | None" = None,
    toc: bool = False,
) -> list[Path]:
    md_path = Path(md_path)
    out_base = Path(out_base)
    media_dir = Path(media_dir)
    produced: list[Path] = []
    for fmt in formats:
        if fmt not in SUFFIX:
            raise RenderError(f"unsupported format: {fmt}")
        out_path = out_base.parent / (out_base.name + SUFFIX[fmt])
        if fmt == "pdf":
            fd, tmp_name = tempfile.mkstemp(suffix=".html")
            os.close(fd)
            tmp_html = Path(tmp_name)
            try:
                _run(runner, build_html_cmd(md_path, tmp_html, media_dir, css=css, toc=toc),
                     "pandoc (html)")
                _run(runner, build_weasyprint_cmd(tmp_html, out_path), "weasyprint")
            finally:
                tmp_html.unlink(missing_ok=True)
        else:
            _run(runner, build_pandoc_cmd(md_path, out_path, media_dir, toc=toc), "pandoc")
        produced.append(out_path)
    return produced
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --no-sync pytest tests/test_render.py -v`
Expected: PASS (existing + new tests)

- [ ] **Step 5: Commit**

```bash
git add manualtrans/render.py tests/test_render.py
git commit -m "feat: render accepts adaptive CSS and generated TOC"
```

---

## Task 9: Wire layout into the CLI (`main.py`)

**Files:**
- Modify: `manualtrans/main.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `layout.reclassify_headings/strip_ocr_toc/wrap_callouts/style_profile/write_css` (Tasks 4-7); `render(..., css=, toc=)` (Task 8).
- Produces: `run` resolves `--ocr-model ocr3|ocr4` aliases (default from settings = OCR-4); applies layout when the EN doc has blocks and `--no-layout` is not set; passes generated CSS + `toc=True` to render. Helper `_resolve_ocr_model(flag: str | None, default: str) -> str` maps `ocr3→mistral-ocr-2512`, `ocr4→mistral-ocr-latest`, else returns the flag verbatim, else the default.

- [ ] **Step 1: Write the failing test** (append to `tests/test_cli.py`)

```python
def test_resolve_ocr_model_aliases():
    from manualtrans.main import _resolve_ocr_model
    assert _resolve_ocr_model("ocr3", "mistral-ocr-latest") == "mistral-ocr-2512"
    assert _resolve_ocr_model("ocr4", "mistral-ocr-2512") == "mistral-ocr-latest"
    assert _resolve_ocr_model(None, "mistral-ocr-latest") == "mistral-ocr-latest"
    assert _resolve_ocr_model("custom/model", "x") == "custom/model"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest tests/test_cli.py::test_resolve_ocr_model_aliases -v`
Expected: FAIL (`cannot import name '_resolve_ocr_model'`)

- [ ] **Step 3: Implement** — in `manualtrans/main.py`:

Add import: `from . import layout` (top, with the other imports).

Add the helper near the top (after `app = typer.Typer(...)`):
```python
_OCR_ALIASES = {"ocr3": "mistral-ocr-2512", "ocr4": "mistral-ocr-latest"}


def _resolve_ocr_model(flag: str | None, default: str) -> str:
    if flag is None:
        return default
    return _OCR_ALIASES.get(flag, flag)
```

In the `run` command, add a `--no-layout` option to the signature:
```python
    no_layout: bool = typer.Option(False, "--no-layout", help="skip layout reconstruction"),
```

Change the model resolution line in `run` from `model = ocr_model or s.ocr_model` to:
```python
    model = _resolve_ocr_model(ocr_model, s.ocr_model)
```

After `doc_it = translate_document(...)` / `doc_it.dump(...)` and the existing `check_document` block, and BEFORE the assemble call, insert layout handling. Replace the existing assemble+render tail of `run` with:
```python
    css_path = None
    toc = False
    use_layout = (not no_layout) and any(p.blocks for p in doc.pages)
    if use_layout:
        doc_it = layout.reclassify_headings(doc, doc_it)
        doc_it = layout.strip_ocr_toc(doc_it)
        css_path = layout.write_css(layout.style_profile(doc), base.with_name(base.name + ".style.css"))
        toc = True
        typer.echo("      layout: reconstructed heading levels + adaptive CSS", err=True)

    typer.echo(f"[3/4] Assembling → {md_path}", err=True)
    md = assemble_doc(doc_it, header_footer_policy=gloss.header_footer_policy)
    if use_layout:
        md = layout.wrap_callouts(md)
    md_path.write_text(md, encoding="utf-8")

    formats = [f.strip() for f in to.split(",")] if to else s.output_formats
    typer.echo(f"[4/4] Rendering {', '.join(formats)} via pandoc…", err=True)
    produced = render_md(md_path, base, formats, media, css=css_path, toc=toc)
    for p in produced:
        typer.echo(f"wrote {p}")
```

(Keep the earlier parts of `run` — settings, cache, paths, ocr, translate, check — unchanged except the two edits above. `render_md` is the existing alias `from .render import render as render_md`.)

Also resolve the alias in the standalone `ocr` command: change its `model = ocr_model or s.ocr_model` to `model = _resolve_ocr_model(ocr_model, s.ocr_model)`.

- [ ] **Step 4: Run the CLI test + full suite**

Run: `uv run --no-sync pytest tests/test_cli.py -v`
Expected: PASS

Run: `uv run --no-sync pytest -q`
Expected: ALL pass.

- [ ] **Step 5: Commit**

```bash
git add manualtrans/main.py tests/test_cli.py
git commit -m "feat: wire layout reconstruction into run; ocr3/ocr4 aliases; --no-layout"
```

---

## Task 10: Update README + .env.example

**Files:**
- Modify: `README.md`, `.env.example`

- [ ] **Step 1: Update `.env.example`** — change the OCR line and comment:

```bash
# OCR model: ocr4 (mistral-ocr-latest, default — gives blocks for layout) or ocr3 (cheaper, flat)
OCR_MODEL=mistral-ocr-latest
```

- [ ] **Step 2: Add a "Layout (OCR-4)" note to `README.md`** under Usage:

```markdown
## Layout reconstruction (OCR-4)

By default the pipeline uses Mistral OCR-4 (`mistral-ocr-latest`), which returns
per-block geometry. `manualtrans` uses it to rebuild heading levels, generate an
adaptive stylesheet (compact body font, page size/margins matched to the
original), drop the original table-of-contents (its page numbers are stale after
reflow) and generate a fresh one, and style NOTE/WARNING callouts.

Use `--ocr-model ocr3` for the cheaper flat OCR-3 path (no layout), or `--no-layout`
to skip reconstruction while still using OCR-4.
```

- [ ] **Step 3: Commit**

```bash
git add README.md .env.example
git commit -m "docs: document OCR-4 layout reconstruction"
```

---

## Final verification

- [ ] **Full suite:** `uv run --no-sync pytest -q` — all green.
- [ ] **SDK field confirmation (live, once):** before the first real OCR-4 run, confirm against the installed `mistralai` that `ocr.process(include_blocks=True)` populates `pages[].blocks` (typed blocks with `top_left_x/y`, `bottom_right_x/y`, `content`, `type`) and `pages[].dimensions` (`dpi/width/height`). If any field name differs, adjust only `parse_ocr_response` (Task 2). The bbox unit is pixels at `dpi`.
- [ ] **Manual smoke (real keys + small PDF):** `uv run manualtrans run sample.pdf --out out` →
  open `out.pdf`: heading hierarchy correct (cover title ≫ sections ≫ subsections, no giant in-body
  banners), generated TOC present with correct links, original TOC gone, compact body, callouts
  styled, images present. Compare against the original for "paragraph similar" feel.
- [ ] **OCR-3 regression:** `uv run manualtrans run sample.pdf --out out3 --ocr-model ocr3` → still
  renders (flat, no CSS), M1 behavior intact.
```
