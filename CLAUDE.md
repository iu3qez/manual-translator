# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Implemented and on `master`: M1 (full pipeline), M2 layout (OCR-4 blocks → heading levels +
adaptive CSS + generated TOC), visual fidelity (red/attention text color + original cover page).
~87 pytest tests pass. PRD (`PRD_traduttore_manuali_pdf.md`, §N) is the original spec; design
docs/plans live in `docs/superpowers/{specs,plans}/`. Where reality diverged from the PRD, the
notes below win.

## Commands & gotchas (learned)

- **Tests:** `uv run --no-sync pytest -q`. The `--no-sync` is mandatory — plain `uv run` tries to
  re-sync and times out (no network in the sandbox).
- **Network:** sandboxed Bash has NO outbound network; API calls (Mistral/OpenRouter) and `uv add`
  need `dangerouslyDisableSandbox: true`. `pdftoppm`/`pandoc`/`weasyprint` are local (fine sandboxed).
- **mistralai is 2.5.0:** `from mistralai.client import Mistral`; OCR-4 (`include_blocks=True`) is slow
  → client needs `timeout_ms=600000`; OCR tables use `tbl.content` (not `.html`); blocks expose
  `top_left_x/y,bottom_right_x/y,type,content` + `dimensions(dpi,width,height)` — NO color.
- **Translation = OpenRouter only** (httpx, OpenAI-compatible), `OPENROUTER_MODELS` ordered with
  per-page fallback; NOT Anthropic/Google SDKs. `--ocr-model ocr3|ocr4` aliases map to the model ids.
- **Render:** PDF is two-step pandoc→self-contained HTML→weasyprint (`-implicit_figures` off,
  `--embed-resources`); cover via `--include-before-body` (PDF) / body prepend (DOCX); colored
  `<span>` renders in PDF only (DOCX keeps text, drops color).
- **TOC order:** `pandoc --toc` emits the index at the document TOP, above the cover. PDF is fine
  (`--include-before-body` precedes the toc nav in the template). DOCX with a cover: `--toc` is
  suppressed and the TOC injected as a raw-OpenXML field in the body AFTER the cover (pandoc's docx
  TOC is itself an unpopulated `w:dirty` field Word fills in, so nothing is lost). See `render.py`.
- **Heading levels** come from the section NUMBER (1.→h1, 5.2→h2), not font size.
- **Pillow HSV is 0-255 per channel** (not OpenCV's 0-179).
- **Generated artifacts are gitignored** (`media/`, `*.doc*.json`, `*.docx`, `*.pdf`, `*.style.css`);
  never commit them. `pdftoppm` (poppler-utils) is a prerequisite for OCR-4 color/cover.
- Modules now include `layout.py`, `color.py`, `pagerender.py` beyond the PRD's planned set.

## What this is

A CLI tool that translates ham-radio equipment manuals (PDF, native or scanned, source EN/ZH)
into Italian **while preserving structure** (headings, lists, spec tables, images), emitting an
editable DOCX (and optionally PDF).

## Core design constraint (don't violate)

Translation **replaces** text, so the layout is deliberately **not** reconstructed pixel-perfect.
Italian runs ~15–20% longer than English, so the pipeline targets a **reflowing** format
(markdown), never fixed coordinates. The whole architecture follows from this: OCR → markdown →
translate the markdown → re-inject assets → render. If a proposed change reintroduces bounding-box
layout reconstruction, it's fighting the design.

## Architecture: a 4-stage pipeline over on-disk artifacts

```
PDF ─[ocr]→ doc.json ─[translate]→ doc_it.json ─[assemble]→ *.md + media/ ─[render]→ *.docx/*.pdf
        cache              cache                  placeholder check
```

Each stage reads/writes a disk artifact and is runnable in isolation (reproducibility, debug,
resume-after-failure). The key decoupling: a **normalized intermediate schema** (`doc.json`, §5)
means stages 2–4 don't know which OCR model produced it. When touching one stage, preserve this
boundary — don't let OCR-model specifics leak past `ocr.py`.

Planned package layout (`manualtrans/`, §4):
- `ocr.py` — wraps `mistralai` SDK; extracts base64 images to `media/` as files here (pandoc
  embeds from file paths, not data-URIs); placeholders in markdown point to relative paths.
- `translate.py` — single `OpenRouterTranslator` (httpx); ordered `OPENROUTER_MODELS` with per-page
  fallback + retry; unit = one page, `temperature=0`. (No Anthropic/Google SDKs — PRD diverged.)
- `glossary.py` + `glossary.yaml` — `do_not_translate` (regex/strings) and `preferred` term map,
  injected into the prompt. The LLM decides in context; **no mechanical substitution** except
  placeholder protection downstream.
- `assemble.py` — resolves placeholders, merges pages into one `*.md`. **Must fail** on orphan
  placeholders or when in/out image/table counts differ.
- `render.py` — `*.md` → DOCX/PDF via **pandoc** (`--resource-path=media`). DOCX is primary.
- `cache.py` — key = SHA-256 of source file **+ relevant params** (OCR model; translation
  model/prompt version). OCR and translation consult cache before hitting paid APIs.
- `main.py` — Typer CLI: per-stage commands + `run` end-to-end.

## Invariants that must hold (these are the point of the project)

- **Placeholders preserved byte-for-byte** through translation: `![...](...)` and `[...](...html)`.
  Never translate, move, or alter their inner text.
- **One input unit = one output unit.** Translation must not add, summarize, or omit content.
- **Cache is mandatory, not optional.** OCR and LLM translation are paid; never re-run
  unnecessarily. A second run of the same input must make zero API calls (acceptance criterion).
- **Reproducible from the intermediate `*.json`** without re-contacting any API.
- **No silent degradation.** Validation failures (orphan placeholders, table HTML that renders
  badly in DOCX, dropped table rows) must surface as errors/warnings, never be swallowed.

The `check` command and `assemble` enforce: placeholder integrity, markdown-structure parity
EN↔IT (same heading/table-row counts per page — catches Mistral's silent row-drop at page-split
boundaries), and (OCR 4 only) block-count and confidence gating (§10).

## OCR model split

Two Mistral models via `--ocr-model ocr3|ocr4`: `mistral-ocr-2512` (OCR 3, flat) and
`mistral-ocr-latest` (OCR 4, **default in config**, enables layout). NB: a stale `OCR_MODEL` in a
local `.env` overrides the config default. `include_blocks=True` only on OCR 4 — it enables per-block
classification + per-word confidence, which drive **selective translation** (translate only
`text|title|list|caption`; copy `code|equation|table` verbatim) and the confidence gate. Without
blocks (OCR 3), translate the whole page markdown and delegate exclusions to the prompt. M1 is
OCR-3-only; OCR-4 features land in M2 (§13).

## The translation prompt is the heart of the project (§6)

Don't casually rewrite it. It's a system prompt parameterized with `{glossary}` enforcing: return
only translated markdown (no preamble/fences), preserve markdown syntax and placeholders, never
translate acronyms/model codes/frequencies/units/code/equations, keep device menu strings in
English while translating surrounding prose, translate only header/descriptive table cells.

## Stack & prerequisites (§12)

Python 3.11+; `mistralai`, `anthropic`/`google-genai`, `typer`, `pydantic`, `pyyaml`, `httpx`
(retry/backoff). **`pandoc` is a system binary prerequisite**, plus a PDF engine (xelatex or
weasyprint). **`pdftoppm` (poppler-utils) is a system prerequisite** for the OCR-4 layout
color/cover features. Tests via `pytest`. Env: `MISTRAL_API_KEY`, `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY`.

## Intended CLI (§8)

```
manualtrans run input.pdf --out output.docx --ocr-model ocr3 --to docx,pdf \
    --glossary glossary.yaml --provider anthropic
manualtrans ocr       input.pdf      # -> doc.json + media/
manualtrans translate doc.json       # -> doc_it.json
manualtrans assemble  doc_it.json    # -> output.md
manualtrans render    output.md --to docx
manualtrans check     doc_it.json    # validations §10, non-zero exit on failure
```
