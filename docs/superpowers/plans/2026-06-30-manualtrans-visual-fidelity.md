# manualtrans — Visual fidelity (text color + cover page) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve the original's red/attention-colored text and keep the first page as the original cover (with a "TRADUZIONE IN ITALIANO" watermark) in the rendered output.

**Architecture:** Rasterize the source PDF pages (pdftoppm, at the OCR's pixel dimensions so block bboxes map 1:1). Detect attention colors per OCR block with a deterministic HSV mask (Pillow `.convert("HSV")` + numpy); store on `Block.color`. In layout, wrap colored title/text blocks' IT markdown in `<span style="color:…">` (per-page, reading-order, with a count guard). The cover is the rasterized source page 1 with a PIL-burned watermark, emitted as a full-page image instead of reflowed page 0.

**Tech Stack:** Python 3.11+, Pillow (already present via weasyprint), numpy (new), pdftoppm (present), pydantic, pytest.

## Global Constraints

- No pixel-perfect layout; reflow preserved. This adds only color preservation + a special-cased cover.
- **Color source = HSV mask on source-page rasters.** OCR exposes no color (verified). Pillow `.convert("HSV")` channels are **0-255 each** (H is 0-255, NOT 0-179). Red ≈ `H<=12 or H>=243`, `S>=90`, `V>=50`.
- **Color scope = `title` + `text` blocks.** Granularity = whole block; inline sub-block color is out of scope.
- **Mapping is per-page, reading-order, guarded:** if a page's colorable-block count ≠ its colorable-markdown-segment count, that page is NOT colored (logged) — never wrong output.
- **Cover:** source page 1 raster + watermark burned into the image (works in PDF and DOCX); page 0 not reflowed/translated.
- Rasterize at the OCR page pixel size (`page.width`×`page.height`) via `pdftoppm -singlefile -scale-to-x W -scale-to-y H`.
- Flags `--no-color` and `--no-cover` disable each; both require OCR-4 blocks (layout active) — OCR-3 stays flat.
- No real API / no real pdftoppm in unit tests (inject runners; build small PIL images in-test). TDD; frequent commits.

## File Structure

```
manualtrans/
  models.py      # MODIFY: Block gains color: str | None
  color.py       # CREATE: HSV attention-color detection (block_color, annotate_block_colors)
  pagerender.py  # CREATE: rasterize_pages (pdftoppm) + make_cover (PIL watermark)
  layout.py      # MODIFY: apply_block_colors (wrap colored spans); build_cover; cover CSS in render_css
  assemble.py    # MODIFY: emit cover image as full-page page-0 replacement
  main.py        # MODIFY: wire rasterize→color→cover into run; --no-color/--no-cover
  config.py      # (no change)
pyproject.toml   # MODIFY: add numpy
tests/
  test_models.py test_color.py test_pagerender.py test_layout.py test_assemble.py test_cli.py
```

---

## Task 1: `Block.color` schema field

**Files:**
- Modify: `manualtrans/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces: `Block` gains `color: str | None = None`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_models.py`)

```python
def test_block_color_field_default_and_roundtrip(tmp_path):
    from manualtrans.models import Doc, Page, Block
    assert Block(type="text", bbox=[0, 0, 1, 1]).color is None
    doc = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="mistral-ocr-latest",
              pages=[Page(index=0, markdown="x",
                          blocks=[Block(type="text", bbox=[0, 0, 1, 1], color="#cc0000")])])
    p = tmp_path / "d.json"
    doc.dump(p)
    assert Doc.load(p).pages[0].blocks[0].color == "#cc0000"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest tests/test_models.py::test_block_color_field_default_and_roundtrip -v`
Expected: FAIL (unexpected keyword `color`)

- [ ] **Step 3: Implement** — in `manualtrans/models.py`, add to `Block`:

```python
class Block(BaseModel):
    type: str
    bbox: list[float]
    content: str | None = None
    color: str | None = None
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --no-sync pytest tests/test_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add manualtrans/models.py tests/test_models.py
git commit -m "feat: Block.color field for attention-color preservation"
```

---

## Task 2: `color.py` — HSV attention-color detection (+ numpy)

**Files:**
- Modify: `pyproject.toml`
- Create: `manualtrans/color.py`, `tests/test_color.py`

**Interfaces:**
- Consumes: `Doc` (Task 1).
- Produces:
  - `ATTENTION_COLORS: dict[str, dict]` — hex → `{"hue": [(lo,hi),…], "s_min": int, "v_min": int}` (PIL 0-255 scale).
  - `block_color(hsv: "np.ndarray", bbox) -> str | None` — fraction of ink pixels in bbox matching a target color ≥ `_COLOR_FRACTION` → that hex, else None.
  - `annotate_block_colors(doc: Doc, page_images: dict) -> Doc` — sets `Block.color` on `title|text` blocks from each page's image (`page_images[index]` = PIL.Image or path); pages without an image are left untouched.

- [ ] **Step 1: Add numpy to `pyproject.toml`** then sync

In `[project] dependencies` add `"numpy>=1.26"`. Run: `uv add numpy` (or `uv sync`).
Expected: numpy installed.

- [ ] **Step 2: Write the failing test** (`tests/test_color.py`)

```python
import numpy as np
from PIL import Image

from manualtrans.color import block_color, annotate_block_colors
from manualtrans.models import Doc, Page, Block


def _hsv(img):
    return np.asarray(img.convert("HSV"))


def test_block_color_detects_red_region():
    img = Image.new("RGB", (40, 40), (255, 255, 255))
    for y in range(0, 20):           # top half = red text-ish block
        for x in range(0, 40):
            img.putpixel((x, y), (210, 0, 0))
    hsv = _hsv(img)
    assert block_color(hsv, [0, 0, 40, 20]) == "#cc0000"      # red region
    assert block_color(hsv, [0, 20, 40, 40]) is None           # white region


def test_block_color_ignores_gray_and_black():
    img = Image.new("RGB", (20, 20), (255, 255, 255))
    for y in range(0, 20):
        for x in range(0, 20):
            img.putpixel((x, y), (10, 10, 10))   # near-black text
    assert block_color(_hsv(img), [0, 0, 20, 20]) is None


def test_annotate_block_colors_sets_only_title_text():
    img = Image.new("RGB", (20, 20), (210, 0, 0))   # all red
    doc = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="mistral-ocr-latest",
              pages=[Page(index=0, markdown="x", blocks=[
                  Block(type="text", bbox=[0, 0, 20, 20]),
                  Block(type="image", bbox=[0, 0, 20, 20]),
              ])])
    out = annotate_block_colors(doc, {0: img})
    assert out.pages[0].blocks[0].color == "#cc0000"   # text
    assert out.pages[0].blocks[1].color is None        # image type skipped
```

- [ ] **Step 3: Run to verify it fails**

Run: `uv run --no-sync pytest tests/test_color.py -v`
Expected: FAIL (`No module named 'manualtrans.color'`)

- [ ] **Step 4: Implement** — create `manualtrans/color.py`:

```python
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
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run --no-sync pytest tests/test_color.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock manualtrans/color.py tests/test_color.py
git commit -m "feat: HSV attention-color detection per OCR block (Pillow+numpy)"
```

---

## Task 3: `pagerender.py` — rasterize source pages + cover watermark

**Files:**
- Create: `manualtrans/pagerender.py`, `tests/test_pagerender.py`

**Interfaces:**
- Produces:
  - `build_pdftoppm_cmd(pdf, page_1based, width, height, out_prefix) -> list[str]` — `pdftoppm -png -f N -l N -singlefile -scale-to-x W -scale-to-y H <pdf> <prefix>`.
  - `rasterize_pages(pdf_path, sizes, out_dir, runner=subprocess.run) -> dict[int, Path]` — `sizes` is a list of `(width, height)` per 0-based page index; renders each (skipping falsy sizes) to `out_dir/page-<i>.png`; returns `{index: Path}` for pages whose runner returned 0.
  - `make_cover(page_png, out_path, text="TRADUZIONE IN ITALIANO") -> Path` — burns a large, diagonal, semi-transparent red watermark centered on the image; saves `out_path`; returns it.

- [ ] **Step 1: Write the failing test** (`tests/test_pagerender.py`)

```python
from pathlib import Path

from PIL import Image

from manualtrans.pagerender import build_pdftoppm_cmd, rasterize_pages, make_cover


def test_build_pdftoppm_cmd(tmp_path):
    cmd = build_pdftoppm_cmd(tmp_path / "in.pdf", 1, 720, 1018, tmp_path / "page-0")
    assert cmd[0] == "pdftoppm"
    assert "-singlefile" in cmd
    assert "-scale-to-x" in cmd and "720" in cmd
    assert "-scale-to-y" in cmd and "1018" in cmd


def test_rasterize_pages_uses_runner_and_skips_empty(tmp_path):
    calls = []

    class R:
        returncode = 0

    def runner(cmd, **k):
        calls.append(cmd)
        return R()

    out = rasterize_pages(tmp_path / "in.pdf", [(720, 1018), (None, None), (600, 800)],
                          tmp_path / "raster", runner=runner)
    assert set(out.keys()) == {0, 2}            # page 1 (None size) skipped
    assert out[0] == tmp_path / "raster" / "page-0.png"
    assert len(calls) == 2


def test_make_cover_adds_watermark(tmp_path):
    src = tmp_path / "p1.png"
    Image.new("RGB", (300, 400), (255, 255, 255)).save(src)
    out = make_cover(src, tmp_path / "cover.png")
    assert out.exists()
    cov = Image.open(out)
    assert cov.size == (300, 400)
    # watermark changed some pixels away from pure white
    assert any(px != (255, 255, 255) for px in cov.convert("RGB").getdata())
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest tests/test_pagerender.py -v`
Expected: FAIL (`No module named 'manualtrans.pagerender'`)

- [ ] **Step 3: Implement** — create `manualtrans/pagerender.py`:

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --no-sync pytest tests/test_pagerender.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add manualtrans/pagerender.py tests/test_pagerender.py
git commit -m "feat: rasterize source pages and build watermarked cover"
```

---

## Task 4: `layout.apply_block_colors` — wrap colored blocks in spans

**Files:**
- Modify: `manualtrans/layout.py`
- Test: `tests/test_layout.py`

**Interfaces:**
- Consumes: `Doc`/`Block`; `HEADING_RE` (existing in layout.py).
- Produces:
  - `apply_block_colors(en_doc: Doc, it_doc: Doc) -> Doc` — returns a deep copy of `it_doc`; per page, takes EN `title|text` blocks in reading order (sorted by `bbox[1]`) and the IT markdown's *colorable segments* (blank-line-separated segments that are a heading or a plain paragraph — NOT image `![`, table HTML `<table`, table placeholder `[…](…html)`, list, or `<div`). If the two counts match, wraps each segment whose EN block has a `color` in `<span style="color:HEX">…</span>` (for a heading, only the text after the `#`s). Counts differ → page left unchanged. Logs reconstructed/skipped counts.

- [ ] **Step 1: Write the failing test** (append to `tests/test_layout.py`)

```python
from manualtrans.layout import apply_block_colors
from manualtrans.models import Block as _B


def test_apply_block_colors_wraps_red_paragraph_and_heading():
    from manualtrans.models import Doc, Page
    en = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="mistral-ocr-latest",
             pages=[Page(index=0, markdown="# T\n\npara", blocks=[
                 _B(type="title", bbox=[0, 0, 10, 10], color="#cc0000"),
                 _B(type="text", bbox=[0, 20, 10, 30], color=None),
             ])])
    it = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="mistral-ocr-latest",
             pages=[Page(index=0, markdown="# Titolo\n\nparagrafo")])
    out = apply_block_colors(en, it)
    assert out.pages[0].markdown == '# <span style="color:#cc0000">Titolo</span>\n\nparagrafo'


def test_apply_block_colors_skips_on_count_mismatch():
    from manualtrans.models import Doc, Page
    en = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="mistral-ocr-latest",
             pages=[Page(index=0, markdown="# T", blocks=[
                 _B(type="title", bbox=[0, 0, 10, 10], color="#cc0000"),
                 _B(type="text", bbox=[0, 20, 10, 30], color="#cc0000"),
             ])])
    it = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="mistral-ocr-latest",
             pages=[Page(index=0, markdown="# Solo")])   # 1 segment vs 2 blocks
    out = apply_block_colors(en, it)
    assert out.pages[0].markdown == "# Solo"   # unchanged
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest tests/test_layout.py -v`
Expected: FAIL (`cannot import name 'apply_block_colors'`)

- [ ] **Step 3: Implement** — add to `manualtrans/layout.py`:

```python
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
        return f"{m.group(1)} {span(m.group(2))}"
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
```

Note: `HEADING_RE` is `^(#{1,6})[ \t]+(.*)$` with MULTILINE; `HEADING_RE.match(seg)` matches a heading segment, group(1)=`#`s, group(2)=text.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --no-sync pytest tests/test_layout.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add manualtrans/layout.py tests/test_layout.py
git commit -m "feat: wrap colored title/text blocks in colored spans (guarded)"
```

---

## Task 5: Cover page — `build_cover` + assemble replacement + CSS

**Files:**
- Modify: `manualtrans/layout.py`, `manualtrans/assemble.py`
- Test: `tests/test_assemble.py`, `tests/test_layout.py`

**Interfaces:**
- Consumes: `pagerender.rasterize_pages`/`make_cover` (Task 3); `Doc`.
- Produces:
  - `layout.cover_markdown(cover_filename: str) -> str` — returns the full-page cover image markdown + a page break, e.g. `![cover](cover.png)\n\n<div style="page-break-after:always"></div>`.
  - `assemble(doc, header_footer_policy="keep_once", cover: str | None = None) -> str` — when `cover` (a filename) is given, the output starts with `cover_markdown(cover)` and page index 0's reflowed body is **omitted**; pages 1+ assembled as before.
  - `render_css` (existing) gains a `.cover` / first-image rule so the cover image fills the page (`img.cover { width: 100%; }` plus an `@page` note is optional).

- [ ] **Step 1: Write the failing test** (append to `tests/test_assemble.py`)

```python
def test_assemble_with_cover_replaces_page0():
    from manualtrans.assemble import assemble
    from manualtrans.models import Doc, Page
    doc = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="mistral-ocr-latest",
              pages=[Page(index=0, markdown="# Copertina testo OCR"),
                     Page(index=1, markdown="Contenuto vero.")])
    out = assemble(doc, cover="cover.png")
    assert "![cover](cover.png)" in out
    assert "Copertina testo OCR" not in out      # page 0 body dropped
    assert "Contenuto vero." in out              # page 1 kept


def test_assemble_without_cover_unchanged():
    from manualtrans.assemble import assemble
    from manualtrans.models import Doc, Page
    doc = Doc(source_pdf="m.pdf", source_hash="H", ocr_model="mistral-ocr-latest",
              pages=[Page(index=0, markdown="Pagina zero."),
                     Page(index=1, markdown="Pagina uno.")])
    out = assemble(doc)
    assert "Pagina zero." in out and "Pagina uno." in out
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest tests/test_assemble.py -v`
Expected: FAIL (`assemble() got an unexpected keyword argument 'cover'`)

- [ ] **Step 3: Implement**

In `manualtrans/layout.py` add:
```python
def cover_markdown(cover_filename: str) -> str:
    return (f"![cover]({cover_filename}){{.cover}}\n\n"
            '<div style="page-break-after: always"></div>')
```

In `manualtrans/assemble.py`, change `assemble` to accept `cover` and skip page 0's body when set. Locate the page loop; build per-page bodies into `parts` as today, but when `cover` is set, prepend the cover and skip the page with `index == 0`:
```python
def assemble(doc: Doc, header_footer_policy: str = "keep_once", cover: str | None = None) -> str:
    from .layout import cover_markdown
    parts: list[str] = []
    if cover:
        parts.append(cover_markdown(cover))
    for page in doc.pages:
        if cover and page.index == 0:
            continue
        # … existing per-page placeholder-resolution / validation logic, appending to parts …
    # … existing header/footer policy handling and return …
```
(Keep all existing placeholder-integrity validation for the pages that ARE emitted.)

In `render_css` (layout.py) add a rule so the cover image fills the page width:
```css
img.cover { width: 100%; display: block; }
```
(append inside the returned CSS string.)

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --no-sync pytest tests/test_assemble.py tests/test_layout.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add manualtrans/layout.py manualtrans/assemble.py tests/test_assemble.py tests/test_layout.py
git commit -m "feat: emit original cover image (page 0) with full-page CSS"
```

---

## Task 6: Wire color + cover into the `run` CLI

**Files:**
- Modify: `manualtrans/main.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `pagerender.rasterize_pages`/`make_cover`, `color.annotate_block_colors`, `layout.apply_block_colors`, `assemble(cover=…)` (Tasks 2-5).
- Produces: `run` gains `--no-color` and `--no-cover`. When layout is active (OCR-4 blocks present): rasterize source pages at OCR sizes; unless `--no-color`, `annotate_block_colors` then `apply_block_colors(doc, doc_it)`; unless `--no-cover`, build `media/cover.png` from page-0 raster + watermark and pass `cover="cover.png"` to assemble.

- [ ] **Step 1: Write the failing test** (append to `tests/test_cli.py`)

```python
def test_run_has_no_color_and_no_cover_flags():
    from manualtrans.main import run
    import inspect
    params = inspect.signature(run).parameters
    assert "no_color" in params
    assert "no_cover" in params
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest tests/test_cli.py::test_run_has_no_color_and_no_cover_flags -v`
Expected: FAIL (KeyError / params missing)

- [ ] **Step 3: Implement** — in `manualtrans/main.py`:

Add imports: `from . import color, pagerender`.

Add to the `run` signature (with the other options):
```python
    no_color: bool = typer.Option(False, "--no-color", help="skip attention-color preservation"),
    no_cover: bool = typer.Option(False, "--no-cover", help="skip original-cover page"),
```

Inside the `use_layout` block (where `reclassify_headings`/`strip_ocr_toc` already run), add color + cover. Use `doc` (EN, has blocks + dpi + width/height) and `input_pdf`:
```python
    cover_name = None
    if use_layout:
        doc_it = layout.reclassify_headings(doc, doc_it)
        doc_it = layout.strip_ocr_toc(doc_it)
        sizes = [(p.width, p.height) for p in doc.pages]
        rasters = pagerender.rasterize_pages(input_pdf, sizes, media)
        if not no_color and rasters:
            doc = color.annotate_block_colors(doc, rasters)
            doc_it = layout.apply_block_colors(doc, doc_it)
        if not no_cover and rasters.get(0):
            cover_path = media / "cover.png"
            pagerender.make_cover(rasters[0], cover_path)
            cover_name = "cover.png"
        css_path = layout.write_css(layout.style_profile(doc), base.with_name(base.name + ".style.css"))
        toc = True
        typer.echo("      layout: heading levels + adaptive CSS"
                   + ("" if no_color else " + colors")
                   + ("" if no_cover else " + cover"), err=True)

    typer.echo(f"[3/4] Assembling → {md_path}", err=True)
    md = assemble_doc(doc_it, header_footer_policy=gloss.header_footer_policy, cover=cover_name)
    if use_layout:
        md = layout.wrap_callouts(md)
    md_path.write_text(md, encoding="utf-8")
```
(Replace the existing assemble line with the `cover=cover_name` version; keep everything else — render call etc. — unchanged. `rasterize_pages` writes PNGs into `media/`, already passed to render via `--resource-path=media`.)

- [ ] **Step 4: Run the CLI test + full suite**

Run: `uv run --no-sync pytest tests/test_cli.py -v`
Expected: PASS
Run: `uv run --no-sync pytest -q`
Expected: ALL pass.

- [ ] **Step 5: Commit**

```bash
git add manualtrans/main.py tests/test_cli.py
git commit -m "feat: wire color preservation + original cover into run (--no-color/--no-cover)"
```

---

## Task 7: Docs

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a "Color & cover" note to `README.md`** under the layout section:

```markdown
### Color & cover (OCR-4)

With OCR-4 the renderer also preserves attention-colored text (e.g. red "important"
paragraphs) by sampling the source page rasters, and replaces the first page with the
original cover image stamped "TRADUZIONE IN ITALIANO". Disable with `--no-color` or
`--no-cover`.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: document color preservation and cover page"
```

---

## Final verification

- [ ] **Full suite:** `uv run --no-sync pytest -q` — all green.
- [ ] **Real smoke (OCR-4 cached):** `uv run manualtrans run sample.pdf --out out --ocr-model ocr4` →
  open `out.pdf`: red paragraphs/headings appear colored; page 1 is the original cover with the
  "TRADUZIONE IN ITALIANO" watermark; content starts page 2; `--no-color`/`--no-cover` disable each.
  Check the log line `layout: colored N block(s), M page(s) skipped` to gauge coverage.
- [ ] **Note:** absolute color/cover fidelity is validated by eye on a real manual; the per-page count
  guard guarantees no wrong coloring (worst case: a page is left uncolored).
```
