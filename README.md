# manualtrans

Translate ham-radio equipment manuals (PDF, EN/ZH) into Italian, preserving
structure (headings, lists, spec tables, images). Output: PDF (primary) + DOCX.

## Prerequisites

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- `pandoc` and `weasyprint` installed on the system (PDF engine)

## Setup

```bash
uv sync
cp .env.example .env   # fill MISTRAL_API_KEY and OPENROUTER_API_KEY
```

## Usage

```bash
# end-to-end
uv run manualtrans run input.pdf --out output --to pdf,docx

# per stage (debug / resume; both stages are cached)
uv run manualtrans ocr       input.pdf --out doc.json --media media
uv run manualtrans translate doc.json  --out doc_it.json
uv run manualtrans assemble  doc_it.json --out output.md
uv run manualtrans render    output.md --out output --to pdf,docx
uv run manualtrans check     doc_it.json --source doc.json
```

Configuration is read from `.env` (see `.env.example`). `OPENROUTER_MODELS` is an
ordered fallback list; translation tries each model in turn.

## Layout reconstruction (OCR-4)

By default the pipeline uses Mistral OCR-4 (`mistral-ocr-latest`), which returns
per-block geometry. `manualtrans` uses it to rebuild heading levels, generate an
adaptive stylesheet (compact body font, page size/margins matched to the
original), drop the original table-of-contents (its page numbers are stale after
reflow) and generate a fresh one, and style NOTE/WARNING callouts.

Use `--ocr-model ocr3` for the cheaper flat OCR-3 path (no layout), or `--no-layout`
to skip reconstruction while still using OCR-4.

### Color & cover (OCR-4)

With OCR-4 the renderer also preserves attention-colored text (e.g. red "important"
paragraphs) by sampling the source page rasters, and replaces the first page with the
original cover image stamped "TRADUZIONE IN ITALIANO". Disable with `--no-color` or
`--no-cover`.
