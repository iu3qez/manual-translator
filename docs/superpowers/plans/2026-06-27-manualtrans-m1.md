# manualtrans M1 (MVP) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the M1 CLI `manualtrans` that translates ham-radio PDF manuals (EN/ZH) into Italian through a 4-stage pipeline (OCR → translate → assemble → render) producing PDF + DOCX.

**Architecture:** Each stage reads/writes a disk artifact and runs in isolation. A normalized `doc.json` (pydantic) decouples translate/assemble/render from the OCR model. Mistral OCR 3 produces markdown + images; a single OpenRouter client translates page-by-page with an ordered model-fallback list; pandoc renders PDF (weasyprint) + DOCX. OCR and translation results are cached on disk so a second run makes zero API calls.

**Tech Stack:** Python 3.11+, `uv`, `typer`, `pydantic` + `pydantic-settings`, `pyyaml`, `httpx`, `mistralai`, `pandoc` + `weasyprint` (system), `pytest`.

## Global Constraints

- Python **3.11+**, managed with `uv`; package name `manualtrans`; console entry point `manualtrans`.
- All configuration comes from `.env` (via `pydantic-settings`). **No `config.yaml` in M1.**
- Translation provider is **OpenRouter only** (OpenAI-compatible, called over `httpx`). No Anthropic/Google SDKs. No abstract `Translator` interface — one concrete class.
- `OPENROUTER_MODELS` is an **ordered preference list**; per page each model gets `MODEL_ATTEMPTS` (default 2) attempts with exponential backoff, then fall back to the next model; exhausting the list fails the page with an explicit error naming the page.
- OCR model fixed to `mistral-ocr-2512` (OCR 3). **No** `include_blocks`, `blocks`, or `confidence` fields in M1.
- Render produces **PDF (primary, `--pdf-engine=weasyprint`) and DOCX**, both via `pandoc`, default `OUTPUT_FORMATS=pdf,docx`.
- Image/table **placeholders preserved byte-for-byte** through translation: `![...](...)` and `[...](...html)`.
- **No silent degradation:** assemble/check fail loudly on orphan placeholders, in≠out asset counts, or heading/table-row count mismatch between EN and IT.
- Translation temperature = **0**. Unit of translation = one page.
- **No real API calls in tests.** OpenRouter mocked at the HTTP layer; Mistral OCR behind a thin injectable boundary.
- TDD throughout: failing test first, minimal implementation, frequent commits.

---

## File Structure

```
manualtrans/
  __init__.py
  models.py        # pydantic schemas Doc/Page/Image/Table + load/dump
  config.py        # Settings from .env (pydantic-settings)
  cache.py         # disk cache (SHA-256 keyed) + file_hash helper
  glossary.py      # load/validate glossary.yaml + render prompt block
  prompt.py        # system prompt template (§6) + builder
  ocr.py           # Mistral OCR wrapper -> Doc + media/ extraction
  translate.py     # OpenRouterTranslator + translate_document (cache, fallback)
  assemble.py      # resolve placeholders, merge pages -> markdown (+ validation)
  check.py         # placeholder integrity + EN/IT structure parity
  render.py        # markdown -> pdf/docx via pandoc
  main.py          # Typer CLI: run/ocr/translate/assemble/render/check
glossary.yaml      # domain glossary (PRD §7 values)
tests/
  test_models.py test_config.py test_cache.py test_glossary.py
  test_prompt.py test_ocr.py test_translate.py test_assemble.py
  test_check.py test_render.py test_cli.py
  fixtures/
pyproject.toml
.env.example
```

---

## Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`, `manualtrans/__init__.py`, `tests/__init__.py`, `.env.example`, `.gitignore`

**Interfaces:**
- Produces: installable package `manualtrans`, `pytest` runnable, `uv run manualtrans` entry point resolving to `manualtrans.main:app`.

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "manualtrans"
version = "0.1.0"
description = "Translate ham-radio PDF manuals (EN/ZH) to Italian, preserving structure"
requires-python = ">=3.11"
dependencies = [
    "typer>=0.12",
    "pydantic>=2.7",
    "pydantic-settings>=2.3",
    "pyyaml>=6.0",
    "httpx>=0.27",
    "mistralai>=1.0",
]

[project.scripts]
manualtrans = "manualtrans.main:app"

[dependency-groups]
dev = ["pytest>=8.0"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Create package and test init files**

`manualtrans/__init__.py`:
```python
"""manualtrans: translate ham-radio PDF manuals to Italian."""

__version__ = "0.1.0"
```

`tests/__init__.py`: (empty file)

- [ ] **Step 3: Create `.env.example`**

```bash
MISTRAL_API_KEY=
OPENROUTER_API_KEY=
OPENROUTER_MODELS=anthropic/claude-3.5-sonnet, google/gemini-2.0-flash-001
OCR_MODEL=mistral-ocr-2512
OUTPUT_FORMATS=pdf,docx
HEADER_FOOTER_POLICY=keep_once
MODEL_ATTEMPTS=2
CACHE_DIR=.cache
```

- [ ] **Step 4: Create `.gitignore`**

```gitignore
__pycache__/
*.pyc
.venv/
.cache/
.env
dist/
*.egg-info/
```

- [ ] **Step 5: Install and verify**

Run: `uv sync`
Expected: resolves and creates `.venv` with no errors.

Run: `uv run python -c "import manualtrans; print(manualtrans.__version__)"`
Expected: prints `0.1.0`

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml manualtrans/__init__.py tests/__init__.py .env.example .gitignore
git commit -m "chore: scaffold manualtrans package with uv"
```

---

## Task 2: Intermediate schema (`models.py`)

**Files:**
- Create: `manualtrans/models.py`, `tests/test_models.py`

**Interfaces:**
- Produces:
  - `Image(id: str, path: str)`
  - `Table(id: str, html: str)`
  - `Page(index: int, markdown: str, images: list[Image] = [], tables: list[Table] = [], header: str | None = None, footer: str | None = None)`
  - `Doc(source_pdf: str, source_hash: str, ocr_model: str, pages: list[Page])`
  - `Doc.load(path: Path) -> Doc` (classmethod), `Doc.dump(self, path: Path) -> None`

- [ ] **Step 1: Write the failing test**

`tests/test_models.py`:
```python
from pathlib import Path

from manualtrans.models import Doc, Page, Image, Table


def make_doc() -> Doc:
    return Doc(
        source_pdf="manuale.pdf",
        source_hash="abc123",
        ocr_model="mistral-ocr-2512",
        pages=[
            Page(
                index=0,
                markdown="# Titolo\n\n![img-0.jpeg](media/img-0.jpeg)\n\n[tbl-0.html](#tbl-0)",
                images=[Image(id="img-0.jpeg", path="media/img-0.jpeg")],
                tables=[Table(id="tbl-0", html="<table><tr><td>1</td></tr></table>")],
                header="Header",
                footer=None,
            )
        ],
    )


def test_roundtrip_dump_load(tmp_path: Path):
    doc = make_doc()
    p = tmp_path / "doc.json"
    doc.dump(p)
    loaded = Doc.load(p)
    assert loaded == doc
    assert loaded.pages[0].images[0].id == "img-0.jpeg"


def test_page_defaults():
    page = Page(index=1, markdown="hi")
    assert page.images == []
    assert page.tables == []
    assert page.header is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'manualtrans.models'`

- [ ] **Step 3: Write minimal implementation**

`manualtrans/models.py`:
```python
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class Image(BaseModel):
    id: str
    path: str


class Table(BaseModel):
    id: str
    html: str


class Page(BaseModel):
    index: int
    markdown: str
    images: list[Image] = []
    tables: list[Table] = []
    header: str | None = None
    footer: str | None = None


class Doc(BaseModel):
    source_pdf: str
    source_hash: str
    ocr_model: str
    pages: list[Page]

    @classmethod
    def load(cls, path: str | Path) -> "Doc":
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    def dump(self, path: str | Path) -> None:
        Path(path).write_text(self.model_dump_json(indent=2), encoding="utf-8")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add manualtrans/models.py tests/test_models.py
git commit -m "feat: add doc.json intermediate schema"
```

---

## Task 3: Settings from `.env` (`config.py`)

**Files:**
- Create: `manualtrans/config.py`, `tests/test_config.py`

**Interfaces:**
- Produces:
  - `Settings` (pydantic-settings) with fields: `mistral_api_key: str = ""`, `openrouter_api_key: str = ""`, `openrouter_models: list[str] = []`, `ocr_model: str = "mistral-ocr-2512"`, `output_formats: list[str] = ["pdf", "docx"]`, `header_footer_policy: str = "keep_once"`, `model_attempts: int = 2`, `cache_dir: Path = Path(".cache")`
  - Comma-separated env values for `OPENROUTER_MODELS` and `OUTPUT_FORMATS` parse into trimmed lists.
  - `get_settings(**overrides) -> Settings`

- [ ] **Step 1: Write the failing test**

`tests/test_config.py`:
```python
from pathlib import Path

from manualtrans.config import Settings, get_settings


def test_env_parsing(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "mk")
    monkeypatch.setenv("OPENROUTER_API_KEY", "ok")
    monkeypatch.setenv("OPENROUTER_MODELS", "a/one, b/two ,c/three")
    monkeypatch.setenv("OUTPUT_FORMATS", "pdf, docx")
    monkeypatch.setenv("MODEL_ATTEMPTS", "3")
    s = Settings(_env_file=None)
    assert s.openrouter_models == ["a/one", "b/two", "c/three"]
    assert s.output_formats == ["pdf", "docx"]
    assert s.model_attempts == 3
    assert s.cache_dir == Path(".cache")


def test_defaults(monkeypatch):
    for k in ["MISTRAL_API_KEY", "OPENROUTER_API_KEY", "OPENROUTER_MODELS",
              "OUTPUT_FORMATS", "MODEL_ATTEMPTS", "OCR_MODEL", "HEADER_FOOTER_POLICY",
              "CACHE_DIR"]:
        monkeypatch.delenv(k, raising=False)
    s = Settings(_env_file=None)
    assert s.ocr_model == "mistral-ocr-2512"
    assert s.output_formats == ["pdf", "docx"]
    assert s.header_footer_policy == "keep_once"


def test_get_settings_overrides(monkeypatch):
    monkeypatch.delenv("OCR_MODEL", raising=False)
    s = get_settings(ocr_model="mistral-ocr-latest")
    assert s.ocr_model == "mistral-ocr-latest"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'manualtrans.config'`

- [ ] **Step 3: Write minimal implementation**

`manualtrans/config.py`:
```python
from __future__ import annotations

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _split_csv(value):
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mistral_api_key: str = ""
    openrouter_api_key: str = ""
    openrouter_models: list[str] = []
    ocr_model: str = "mistral-ocr-2512"
    output_formats: list[str] = ["pdf", "docx"]
    header_footer_policy: str = "keep_once"
    model_attempts: int = 2
    cache_dir: Path = Path(".cache")

    @field_validator("openrouter_models", "output_formats", mode="before")
    @classmethod
    def _parse_lists(cls, v):
        return _split_csv(v)


def get_settings(**overrides) -> Settings:
    return Settings(**overrides)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add manualtrans/config.py tests/test_config.py
git commit -m "feat: load settings from .env"
```

---

## Task 4: Disk cache (`cache.py`)

**Files:**
- Create: `manualtrans/cache.py`, `tests/test_cache.py`

**Interfaces:**
- Produces:
  - `file_hash(path: Path) -> str` — SHA-256 hex of file bytes.
  - `Cache(cache_dir: Path)` with:
    - `key(*parts: str) -> str` — SHA-256 hex of the parts joined by `\x00`.
    - `get(key: str) -> str | None` — returns cached text or `None`.
    - `set(key: str, value: str) -> None` — writes text, creating `cache_dir`.

- [ ] **Step 1: Write the failing test**

`tests/test_cache.py`:
```python
from pathlib import Path

from manualtrans.cache import Cache, file_hash


def test_file_hash_stable(tmp_path: Path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello")
    assert file_hash(f) == file_hash(f)
    g = tmp_path / "b.bin"
    g.write_bytes(b"world")
    assert file_hash(f) != file_hash(g)


def test_key_depends_on_all_parts():
    c = Cache(Path("/tmp/unused"))
    assert c.key("h", "ocr3") == c.key("h", "ocr3")
    assert c.key("h", "ocr3") != c.key("h", "ocr4")


def test_get_miss_then_set_hit(tmp_path: Path):
    c = Cache(tmp_path / "cache")
    k = c.key("h", "translate", "page0")
    assert c.get(k) is None
    c.set(k, "tradotto")
    assert c.get(k) == "tradotto"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'manualtrans.cache'`

- [ ] **Step 3: Write minimal implementation**

`manualtrans/cache.py`:
```python
from __future__ import annotations

import hashlib
from pathlib import Path


def file_hash(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class Cache:
    def __init__(self, cache_dir: str | Path):
        self.cache_dir = Path(cache_dir)

    def key(self, *parts: str) -> str:
        joined = "\x00".join(parts).encode("utf-8")
        return hashlib.sha256(joined).hexdigest()

    def _path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.txt"

    def get(self, key: str) -> str | None:
        p = self._path(key)
        if p.exists():
            return p.read_text(encoding="utf-8")
        return None

    def set(self, key: str, value: str) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._path(key).write_text(value, encoding="utf-8")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cache.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add manualtrans/cache.py tests/test_cache.py
git commit -m "feat: add disk cache keyed by sha-256"
```

---

## Task 5: Glossary (`glossary.py` + `glossary.yaml`)

**Files:**
- Create: `manualtrans/glossary.py`, `glossary.yaml`, `tests/test_glossary.py`

**Interfaces:**
- Produces:
  - `Glossary` (pydantic) with `do_not_translate: dict`, `preferred: dict[str, str]`, `header_footer_policy: str = "keep_once"`.
  - `Glossary.load(path: Path) -> Glossary` (classmethod).
  - `Glossary.render(self) -> str` — human-readable block injected into the prompt, listing do-not-translate acronyms/patterns and preferred terms.

- [ ] **Step 1: Write the failing test**

`tests/test_glossary.py`:
```python
from pathlib import Path

from manualtrans.glossary import Glossary

YAML = """
do_not_translate:
  acronyms: [SSB, CW, VFO]
  patterns:
    - '\\\\b\\\\d+\\\\s?(Hz|MHz)\\\\b'
preferred:
  squelch: squelch
  channel: canale
header_footer_policy: keep_once
"""


def test_load_and_fields(tmp_path: Path):
    p = tmp_path / "glossary.yaml"
    p.write_text(YAML, encoding="utf-8")
    g = Glossary.load(p)
    assert "SSB" in g.do_not_translate["acronyms"]
    assert g.preferred["channel"] == "canale"
    assert g.header_footer_policy == "keep_once"


def test_render_contains_terms(tmp_path: Path):
    p = tmp_path / "glossary.yaml"
    p.write_text(YAML, encoding="utf-8")
    rendered = Glossary.load(p).render()
    assert "SSB" in rendered
    assert "squelch" in rendered
    assert "canale" in rendered
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_glossary.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'manualtrans.glossary'`

- [ ] **Step 3: Write minimal implementation**

`manualtrans/glossary.py`:
```python
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel


class Glossary(BaseModel):
    do_not_translate: dict = {}
    preferred: dict[str, str] = {}
    header_footer_policy: str = "keep_once"

    @classmethod
    def load(cls, path: str | Path) -> "Glossary":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls.model_validate(data)

    def render(self) -> str:
        lines: list[str] = []
        acronyms = self.do_not_translate.get("acronyms", [])
        if acronyms:
            lines.append("NON tradurre questi acronimi: " + ", ".join(acronyms))
        patterns = self.do_not_translate.get("patterns", [])
        if patterns:
            lines.append("NON tradurre i token che corrispondono a questi pattern regex:")
            lines.extend(f"  - {p}" for p in patterns)
        if self.preferred:
            lines.append("Traduzioni preferite (term -> IT):")
            lines.extend(f"  - {k} -> {v}" for k, v in self.preferred.items())
        return "\n".join(lines)
```

- [ ] **Step 4: Create the repo `glossary.yaml` (PRD §7 values)**

`glossary.yaml`:
```yaml
do_not_translate:
  acronyms: [SSB, CW, FM, AM, VFO, PTT, CTCSS, DCS, RIT, XIT, NB, NR, AGC, ATU, SWR]
  patterns:
    - '\b\d+(\.\d+)?\s?(Hz|kHz|MHz|GHz|dB|dBm|W|mW|V|mAh|ppm)\b'
    - '\b[A-Z]{1,3}-?[A-Z0-9]{2,}\b'
preferred:
  squelch: squelch
  frequency: frequenza
  channel: canale
  scan: scansione
  memory channel: canale di memoria
  dual watch: dual watch
  battery save: risparmio batteria
  busy channel lockout: blocco canale occupato
header_footer_policy: keep_once
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_glossary.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add manualtrans/glossary.py glossary.yaml tests/test_glossary.py
git commit -m "feat: load and render translation glossary"
```

---

## Task 6: Translation system prompt (`prompt.py`)

**Files:**
- Create: `manualtrans/prompt.py`, `tests/test_prompt.py`

**Interfaces:**
- Produces:
  - `SYSTEM_PROMPT_TEMPLATE: str` — contains the literal token `{glossary}`.
  - `build_system_prompt(glossary_text: str) -> str` — returns the template with `{glossary}` replaced (no other `{...}` substitution).

- [ ] **Step 1: Write the failing test**

`tests/test_prompt.py`:
```python
from manualtrans.prompt import SYSTEM_PROMPT_TEMPLATE, build_system_prompt


def test_template_has_glossary_slot():
    assert "{glossary}" in SYSTEM_PROMPT_TEMPLATE


def test_build_injects_glossary():
    out = build_system_prompt("ACRONIMI: SSB")
    assert "ACRONIMI: SSB" in out
    assert "{glossary}" not in out
    # Core rules are present
    assert "BYTE-PER-BYTE" in out
    assert "markdown" in out.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_prompt.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'manualtrans.prompt'`

- [ ] **Step 3: Write minimal implementation**

`manualtrans/prompt.py` (text is PRD §6 verbatim):
```python
SYSTEM_PROMPT_TEMPLATE = """\
Sei un traduttore tecnico EN/ZH->IT specializzato in manuali di apparati radioamatoriali.
Traduci il MARKDOWN fornito in italiano, registro tecnico-manualistico.

REGOLE RIGIDE
1. Restituisci SOLO il markdown tradotto. Nessun preambolo, nessun fence aggiunto.
2. Preserva ESATTAMENTE la sintassi markdown: heading, liste, grassetti, tabelle, code fence.
3. Preserva BYTE-PER-BYTE i placeholder immagine/tabella:  ![...](...)  e  [...](...html).
   Non tradurli, non spostarli, non alterarne il testo interno.
4. NON tradurre:
   - sigle/acronimi di settore (SSB, CW, FM, AM, VFO, PTT, CTCSS, DCS, RIT, NB, AGC, S-meter...)
   - modelli e codici prodotto (es. UV-K5, IC-7300, FT-991A)
   - frequenze, valori numerici, unita (Hz, kHz, MHz, dB, dBm, W, V, mAh, ppm...)
   - blocchi di codice ed equazioni
5. STRINGHE DI DISPLAY/MENU dell'apparato -> lascia in INGLESE come appaiono sul dispositivo,
   ma traduci il testo descrittivo attorno.
   Esempio: "Press [MENU], select SET > VFO > SPLIT to enable split operation"
   ->        "Premere [MENU], selezionare SET > VFO > SPLIT per attivare il funzionamento split"
6. TABELLE: traduci solo le celle di intestazione e le celle descrittive; lascia invariate
   le celle numeriche e le unita.
7. Una unita in ingresso = una unita in uscita. Non aggiungere, riassumere o omettere contenuto.
8. Usa le traduzioni preferite del glossario quando applicabili.

GLOSSARIO
{glossary}
"""


def build_system_prompt(glossary_text: str) -> str:
    return SYSTEM_PROMPT_TEMPLATE.replace("{glossary}", glossary_text)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_prompt.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add manualtrans/prompt.py tests/test_prompt.py
git commit -m "feat: add translation system prompt template"
```

---

## Task 7: OpenRouter translator with model fallback (`translate.py`)

**Files:**
- Create: `manualtrans/translate.py`, `tests/test_translate.py`

**Interfaces:**
- Consumes: `Doc`/`Page` (Task 2), `Cache` (Task 4), `build_system_prompt` (Task 6).
- Produces:
  - `TranslationError(Exception)`.
  - `OpenRouterTranslator(api_key: str, models: list[str], system_prompt: str, attempts: int = 2, client: httpx.Client | None = None, base_url: str = "https://openrouter.ai/api/v1")`
    - `translate_page(markdown: str) -> str` — tries each model up to `attempts` times with exponential backoff; on exhausting a model moves to the next; raises `TranslationError` if all models fail. Sets `self.last_model` to the model that produced the result.
  - `translate_document(doc: Doc, translator: OpenRouterTranslator, cache: Cache, prompt_version: str = "v1") -> Doc` — copies the doc, translates each page's `markdown`, caching per page keyed on `(doc.source_hash, "translate", model_list, prompt_version, page.index)`. Cache hit ⇒ no translator call.

**Note on backoff:** use `time.sleep`; tests inject `attempts` and a fake client and patch `time.sleep` to a no-op so they run instantly.

- [ ] **Step 1: Write the failing test**

`tests/test_translate.py`:
```python
import httpx
import pytest

from manualtrans.cache import Cache
from manualtrans.models import Doc, Page
from manualtrans.translate import (
    OpenRouterTranslator,
    TranslationError,
    translate_document,
)


def _response(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


def make_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_translate_page_success(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        assert "m1" in body  # first model used
        return httpx.Response(200, json=_response("CIAO"))

    t = OpenRouterTranslator("k", ["m1", "m2"], "sys", attempts=2, client=make_client(handler))
    assert t.translate_page("hello") == "CIAO"
    assert t.last_model == "m1"


def test_fallback_to_second_model(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    calls = {"m1": 0, "m2": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        model = "m1" if '"m1"' in body else "m2"
        calls[model] += 1
        if model == "m1":
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(200, json=_response("DAL SECONDO"))

    t = OpenRouterTranslator("k", ["m1", "m2"], "sys", attempts=2, client=make_client(handler))
    assert t.translate_page("hi") == "DAL SECONDO"
    assert calls["m1"] == 2  # exhausted attempts before fallback
    assert t.last_model == "m2"


def test_all_models_fail_raises(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "down"})

    t = OpenRouterTranslator("k", ["m1", "m2"], "sys", attempts=1, client=make_client(handler))
    with pytest.raises(TranslationError):
        t.translate_page("hi")


def test_translate_document_uses_cache(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    cache = Cache(tmp_path / "c")
    doc = Doc(source_pdf="a.pdf", source_hash="H", ocr_model="mistral-ocr-2512",
              pages=[Page(index=0, markdown="hello")])
    count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        count["n"] += 1
        return httpx.Response(200, json=_response("CIAO"))

    t = OpenRouterTranslator("k", ["m1"], "sys", attempts=1, client=make_client(handler))
    out1 = translate_document(doc, t, cache)
    assert out1.pages[0].markdown == "CIAO"
    # second run: same translator, cache should prevent a second HTTP call
    out2 = translate_document(doc, t, cache)
    assert out2.pages[0].markdown == "CIAO"
    assert count["n"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_translate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'manualtrans.translate'`

- [ ] **Step 3: Write minimal implementation**

`manualtrans/translate.py`:
```python
from __future__ import annotations

import logging
import time

import httpx

from .cache import Cache
from .models import Doc

logger = logging.getLogger(__name__)


class TranslationError(Exception):
    pass


class OpenRouterTranslator:
    def __init__(
        self,
        api_key: str,
        models: list[str],
        system_prompt: str,
        attempts: int = 2,
        client: httpx.Client | None = None,
        base_url: str = "https://openrouter.ai/api/v1",
    ):
        if not models:
            raise TranslationError("no models configured")
        self.api_key = api_key
        self.models = models
        self.system_prompt = system_prompt
        self.attempts = attempts
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=120.0)
        self.last_model: str | None = None

    def _call(self, model: str, markdown: str) -> str:
        resp = self._client.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": markdown},
                ],
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    def translate_page(self, markdown: str) -> str:
        last_exc: Exception | None = None
        for model in self.models:
            for attempt in range(1, self.attempts + 1):
                try:
                    result = self._call(model, markdown)
                    self.last_model = model
                    return result
                except Exception as exc:  # noqa: BLE001 - retry/fallback on any failure
                    last_exc = exc
                    logger.warning("model %s attempt %d failed: %s", model, attempt, exc)
                    if attempt < self.attempts:
                        time.sleep(2 ** (attempt - 1))
            logger.warning("model %s exhausted, falling back", model)
        raise TranslationError(f"all models failed; last error: {last_exc}")


def translate_document(
    doc: Doc,
    translator: OpenRouterTranslator,
    cache: Cache,
    prompt_version: str = "v1",
) -> Doc:
    out = doc.model_copy(deep=True)
    models_key = ",".join(translator.models)
    for page in out.pages:
        key = cache.key(
            doc.source_hash, "translate", models_key, prompt_version, str(page.index)
        )
        cached = cache.get(key)
        if cached is not None:
            page.markdown = cached
            continue
        translated = translator.translate_page(page.markdown)
        cache.set(key, translated)
        page.markdown = translated
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_translate.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add manualtrans/translate.py tests/test_translate.py
git commit -m "feat: openrouter translator with per-page cache and model fallback"
```

---

## Task 8: OCR wrapper (`ocr.py`)

**Files:**
- Create: `manualtrans/ocr.py`, `tests/test_ocr.py`

**Interfaces:**
- Consumes: `Doc`/`Page`/`Image`/`Table` (Task 2), `Cache` + `file_hash` (Task 4).
- Produces:
  - `parse_ocr_response(response, source_pdf: str, source_hash: str, ocr_model: str, media_dir: Path) -> Doc` — pure function turning a Mistral OCR response object into a `Doc`, writing each page image's base64 bytes to `media_dir/<image_id>` and leaving the markdown placeholders untouched. Tables present in `response.pages[i].tables` (id/html) become `Table` entries.
  - `run_ocr(pdf_path: Path, out_json: Path, media_dir: Path, ocr_model: str, api_key: str, cache: Cache, client=None) -> Doc` — checks cache (key = `(file_hash, "ocr", ocr_model)`); on miss calls Mistral, parses, extracts media, writes `out_json`, caches the doc JSON. `client` is injectable for tests.

**Note:** The exact `mistralai` response field names must be confirmed against the installed SDK version (use the Context7 docs for `mistralai`). The parser below assumes `response.pages[i].markdown`, `response.pages[i].images[j].id`, `response.pages[i].images[j].image_base64` (a data-URI or raw base64), and an optional `response.pages[i].tables[j]` with `.id`/`.html`. Adjust field access in `parse_ocr_response` only; keep the function signature stable. Tests use a fake response object so they don't depend on the SDK.

- [ ] **Step 1: Write the failing test**

`tests/test_ocr.py`:
```python
import base64
from pathlib import Path
from types import SimpleNamespace

from manualtrans.cache import Cache
from manualtrans.ocr import parse_ocr_response, run_ocr


def fake_response():
    raw = base64.b64encode(b"\xff\xd8jpegbytes").decode()
    return SimpleNamespace(
        pages=[
            SimpleNamespace(
                markdown="# T\n\n![img-0.jpeg](img-0.jpeg)\n\n[tbl-0.html](#tbl-0)",
                images=[SimpleNamespace(id="img-0.jpeg", image_base64=raw)],
                tables=[SimpleNamespace(id="tbl-0", html="<table><tr><td>1</td></tr></table>")],
            )
        ]
    )


def test_parse_writes_media_and_builds_doc(tmp_path: Path):
    media = tmp_path / "media"
    doc = parse_ocr_response(
        fake_response(), "m.pdf", "HASH", "mistral-ocr-2512", media
    )
    assert doc.source_hash == "HASH"
    assert doc.pages[0].images[0].id == "img-0.jpeg"
    assert doc.pages[0].tables[0].html.startswith("<table>")
    # image bytes were written to media/
    assert (media / "img-0.jpeg").read_bytes() == b"\xff\xd8jpegbytes"


def test_run_ocr_uses_cache(tmp_path: Path):
    pdf = tmp_path / "m.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    cache = Cache(tmp_path / "c")
    calls = {"n": 0}

    class FakeClient:
        class _OCR:
            def process(self, **kwargs):
                calls["n"] += 1
                return fake_response()
        def __init__(self):
            self.ocr = self._OCR()
        # mimic mistralai file upload surface no-ops
        class _Files:
            def upload(self, **kwargs):
                return SimpleNamespace(id="fileid")
            def get_signed_url(self, **kwargs):
                return SimpleNamespace(url="https://signed")
        files = _Files()

    out = tmp_path / "doc.json"
    media = tmp_path / "media"
    doc1 = run_ocr(pdf, out, media, "mistral-ocr-2512", "key", cache, client=FakeClient())
    doc2 = run_ocr(pdf, out, media, "mistral-ocr-2512", "key", cache, client=FakeClient())
    assert calls["n"] == 1  # second call served from cache
    assert doc1 == doc2
    assert out.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ocr.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'manualtrans.ocr'`

- [ ] **Step 3: Write minimal implementation**

`manualtrans/ocr.py`:
```python
from __future__ import annotations

import base64
from pathlib import Path

from mistralai import Mistral

from .cache import Cache, file_hash
from .models import Doc, Image, Page, Table


def _decode_base64(data: str) -> bytes:
    # tolerate data-URI prefixes like "data:image/jpeg;base64,...."
    if "," in data and data.strip().startswith("data:"):
        data = data.split(",", 1)[1]
    return base64.b64decode(data)


def parse_ocr_response(
    response,
    source_pdf: str,
    source_hash: str,
    ocr_model: str,
    media_dir: Path,
) -> Doc:
    media_dir = Path(media_dir)
    media_dir.mkdir(parents=True, exist_ok=True)
    pages: list[Page] = []
    for i, p in enumerate(response.pages):
        images: list[Image] = []
        for img in getattr(p, "images", []) or []:
            img_bytes = _decode_base64(img.image_base64)
            (media_dir / img.id).write_bytes(img_bytes)
            images.append(Image(id=img.id, path=f"media/{img.id}"))
        tables: list[Table] = []
        for tbl in getattr(p, "tables", []) or []:
            tables.append(Table(id=tbl.id, html=tbl.html))
        pages.append(
            Page(
                index=i,
                markdown=p.markdown,
                images=images,
                tables=tables,
                header=getattr(p, "header", None),
                footer=getattr(p, "footer", None),
            )
        )
    return Doc(
        source_pdf=source_pdf,
        source_hash=source_hash,
        ocr_model=ocr_model,
        pages=pages,
    )


def run_ocr(
    pdf_path: str | Path,
    out_json: str | Path,
    media_dir: str | Path,
    ocr_model: str,
    api_key: str,
    cache: Cache,
    client=None,
) -> Doc:
    pdf_path = Path(pdf_path)
    out_json = Path(out_json)
    source_hash = file_hash(pdf_path)
    key = cache.key(source_hash, "ocr", ocr_model)

    cached = cache.get(key)
    if cached is not None:
        doc = Doc.model_validate_json(cached)
        out_json.write_text(cached, encoding="utf-8")
        return doc

    client = client or Mistral(api_key=api_key)
    uploaded = client.files.upload(
        file={"file_name": pdf_path.name, "content": pdf_path.read_bytes()},
        purpose="ocr",
    )
    signed = client.files.get_signed_url(file_id=uploaded.id)
    response = client.ocr.process(
        model=ocr_model,
        document={"type": "document_url", "document_url": signed.url},
        table_format="html",
        include_image_base64=True,
        extract_header=True,
        extract_footer=True,
    )
    doc = parse_ocr_response(
        response, pdf_path.name, source_hash, ocr_model, media_dir
    )
    payload = doc.model_dump_json(indent=2)
    cache.set(key, payload)
    out_json.write_text(payload, encoding="utf-8")
    return doc
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_ocr.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Confirm SDK field names**

Use the Context7 docs for `mistralai` (`resolve-library-id` → `query-docs`) to verify the request params (`files.upload`, `files.get_signed_url`, `ocr.process`, `table_format`, `extract_header/footer`) and response shape (`pages[].markdown`, `pages[].images[].image_base64`, table extraction). If a field differs, adjust only inside `run_ocr`/`parse_ocr_response`; keep signatures unchanged. Re-run the test after any change.

- [ ] **Step 6: Commit**

```bash
git add manualtrans/ocr.py tests/test_ocr.py
git commit -m "feat: mistral ocr wrapper with media extraction and cache"
```

---

## Task 9: Assemble (`assemble.py`)

**Files:**
- Create: `manualtrans/assemble.py`, `tests/test_assemble.py`

**Interfaces:**
- Consumes: `Doc`/`Page` (Task 2).
- Produces:
  - `AssembleError(Exception)`.
  - `assemble(doc: Doc, header_footer_policy: str = "keep_once") -> str` — concatenates page markdown into one document; replaces each table placeholder `[<id>.html](#<id>)` with the table's inline HTML; applies header/footer policy (`keep_once`: emit header once at top, footer once at bottom; `drop`: omit; `keep_all`: per page). Raises `AssembleError` if a placeholder references an unknown id (orphan) or if the per-page count of image placeholders ≠ `len(page.images)` or table placeholders ≠ `len(page.tables)`.
  - Regexes: image placeholder `!\[[^\]]*\]\([^)]*\)`; table placeholder `\[([^\]]+\.html)\]\(#([^)]+)\)`.

- [ ] **Step 1: Write the failing test**

`tests/test_assemble.py`:
```python
import pytest

from manualtrans.assemble import assemble, AssembleError
from manualtrans.models import Doc, Image, Page, Table


def doc_with(markdown, images=None, tables=None, header=None, footer=None):
    return Doc(
        source_pdf="a.pdf", source_hash="H", ocr_model="mistral-ocr-2512",
        pages=[Page(index=0, markdown=markdown, images=images or [],
                    tables=tables or [], header=header, footer=footer)],
    )


def test_table_placeholder_resolved_to_html():
    d = doc_with(
        "Spec:\n\n[tbl-0.html](#tbl-0)\n",
        tables=[Table(id="tbl-0", html="<table><tr><td>1</td></tr></table>")],
    )
    out = assemble(d)
    assert "<table><tr><td>1</td></tr></table>" in out
    assert "[tbl-0.html](#tbl-0)" not in out


def test_image_placeholder_kept():
    d = doc_with(
        "![img-0.jpeg](media/img-0.jpeg)",
        images=[Image(id="img-0.jpeg", path="media/img-0.jpeg")],
    )
    out = assemble(d)
    assert "![img-0.jpeg](media/img-0.jpeg)" in out


def test_orphan_table_placeholder_fails():
    d = doc_with("[tbl-9.html](#tbl-9)", tables=[])
    with pytest.raises(AssembleError):
        assemble(d)


def test_image_count_mismatch_fails():
    # one placeholder but zero declared images
    d = doc_with("![x](media/x.jpeg)", images=[])
    with pytest.raises(AssembleError):
        assemble(d)


def test_header_footer_keep_once():
    d = Doc(
        source_pdf="a.pdf", source_hash="H", ocr_model="mistral-ocr-2512",
        pages=[
            Page(index=0, markdown="Pagina 1", header="HEAD", footer="FOOT"),
            Page(index=1, markdown="Pagina 2", header="HEAD", footer="FOOT"),
        ],
    )
    out = assemble(d, header_footer_policy="keep_once")
    assert out.count("HEAD") == 1
    assert out.count("FOOT") == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_assemble.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'manualtrans.assemble'`

- [ ] **Step 3: Write minimal implementation**

`manualtrans/assemble.py`:
```python
from __future__ import annotations

import re

from .models import Doc

IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
TABLE_RE = re.compile(r"\[([^\]]+\.html)\]\(#([^)]+)\)")


class AssembleError(Exception):
    pass


def assemble(doc: Doc, header_footer_policy: str = "keep_once") -> str:
    parts: list[str] = []

    for page in doc.pages:
        image_count = len(IMAGE_RE.findall(page.markdown))
        if image_count != len(page.images):
            raise AssembleError(
                f"page {page.index}: {image_count} image placeholders but "
                f"{len(page.images)} declared images"
            )

        table_ids = {t.id: t.html for t in page.tables}
        table_matches = TABLE_RE.findall(page.markdown)
        if len(table_matches) != len(page.tables):
            raise AssembleError(
                f"page {page.index}: {len(table_matches)} table placeholders but "
                f"{len(page.tables)} declared tables"
            )

        def _resolve(m: re.Match) -> str:
            tbl_id = m.group(2)
            if tbl_id not in table_ids:
                raise AssembleError(
                    f"page {page.index}: orphan table placeholder #{tbl_id}"
                )
            return table_ids[tbl_id]

        body = TABLE_RE.sub(_resolve, page.markdown)
        parts.append(body)

    document = "\n\n".join(parts)

    if header_footer_policy == "keep_all":
        # headers/footers are already embedded per page by OCR markdown; nothing to add
        pass
    elif header_footer_policy == "keep_once":
        first_header = next((p.header for p in doc.pages if p.header), None)
        last_footer = next((p.footer for p in reversed(doc.pages) if p.footer), None)
        if first_header:
            document = f"{first_header}\n\n{document}"
        if last_footer:
            document = f"{document}\n\n{last_footer}"
    elif header_footer_policy == "drop":
        pass
    else:
        raise AssembleError(f"unknown header_footer_policy: {header_footer_policy}")

    return document
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_assemble.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add manualtrans/assemble.py tests/test_assemble.py
git commit -m "feat: assemble translated pages into markdown with validation"
```

---

## Task 10: Validation checks (`check.py`)

**Files:**
- Create: `manualtrans/check.py`, `tests/test_check.py`

**Interfaces:**
- Consumes: `Doc`/`Page` (Task 2), `IMAGE_RE`/`TABLE_RE` (Task 9).
- Produces:
  - `check_placeholder_integrity(doc: Doc) -> list[str]` — problems if any page's image-placeholder count ≠ `len(images)`, table-placeholder count ≠ `len(tables)`, or a table placeholder references an unknown id.
  - `check_structure(en: Doc, it: Doc) -> list[str]` — problems if page counts differ, or for any page the count of markdown headings (lines matching `^#{1,6}\s`) or the count of table rows (`<tr`) differ between EN and IT. (Catches Mistral silent row-drop.)
  - `check_document(it: Doc, en: Doc | None = None) -> list[str]` — runs placeholder integrity on `it`, plus structure parity when `en` is provided. Returns the combined problem list (empty = OK).

- [ ] **Step 1: Write the failing test**

`tests/test_check.py`:
```python
from manualtrans.check import (
    check_document,
    check_placeholder_integrity,
    check_structure,
)
from manualtrans.models import Doc, Image, Page, Table


def one_page(markdown, images=None, tables=None):
    return Doc(source_pdf="a.pdf", source_hash="H", ocr_model="mistral-ocr-2512",
               pages=[Page(index=0, markdown=markdown, images=images or [],
                           tables=tables or [])])


def test_placeholder_integrity_ok():
    d = one_page("![i](media/i.jpeg)", images=[Image(id="i", path="media/i.jpeg")])
    assert check_placeholder_integrity(d) == []


def test_placeholder_integrity_orphan_table():
    d = one_page("[tbl-1.html](#tbl-1)", tables=[])
    problems = check_placeholder_integrity(d)
    assert problems  # non-empty


def test_structure_heading_mismatch():
    en = one_page("# A\n\n## B\n\ntext")
    it = one_page("# A\n\ntesto")  # lost one heading
    problems = check_structure(en, it)
    assert any("heading" in p.lower() for p in problems)


def test_structure_table_row_drop():
    en = one_page("<table><tr><td>1</td></tr><tr><td>2</td></tr></table>")
    it = one_page("<table><tr><td>1</td></tr></table>")  # dropped a row
    problems = check_structure(en, it)
    assert any("row" in p.lower() for p in problems)


def test_check_document_combines():
    en = one_page("# A")
    it = one_page("# A")
    assert check_document(it, en) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_check.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'manualtrans.check'`

- [ ] **Step 3: Write minimal implementation**

`manualtrans/check.py`:
```python
from __future__ import annotations

import re

from .assemble import IMAGE_RE, TABLE_RE
from .models import Doc

HEADING_RE = re.compile(r"^#{1,6}\s", re.MULTILINE)
TR_RE = re.compile(r"<tr\b", re.IGNORECASE)


def check_placeholder_integrity(doc: Doc) -> list[str]:
    problems: list[str] = []
    for page in doc.pages:
        n_img = len(IMAGE_RE.findall(page.markdown))
        if n_img != len(page.images):
            problems.append(
                f"page {page.index}: {n_img} image placeholders vs {len(page.images)} images"
            )
        table_ids = {t.id for t in page.tables}
        matches = TABLE_RE.findall(page.markdown)
        if len(matches) != len(page.tables):
            problems.append(
                f"page {page.index}: {len(matches)} table placeholders vs "
                f"{len(page.tables)} tables"
            )
        for _, tbl_id in matches:
            if tbl_id not in table_ids:
                problems.append(f"page {page.index}: orphan table placeholder #{tbl_id}")
    return problems


def check_structure(en: Doc, it: Doc) -> list[str]:
    problems: list[str] = []
    if len(en.pages) != len(it.pages):
        problems.append(f"page count differs: EN {len(en.pages)} vs IT {len(it.pages)}")
        return problems
    for ep, ip in zip(en.pages, it.pages):
        en_h = len(HEADING_RE.findall(ep.markdown))
        it_h = len(HEADING_RE.findall(ip.markdown))
        if en_h != it_h:
            problems.append(f"page {ep.index}: heading count EN {en_h} vs IT {it_h}")
        en_r = len(TR_RE.findall(ep.markdown))
        it_r = len(TR_RE.findall(ip.markdown))
        if en_r != it_r:
            problems.append(f"page {ep.index}: table row count EN {en_r} vs IT {it_r}")
    return problems


def check_document(it: Doc, en: Doc | None = None) -> list[str]:
    problems = check_placeholder_integrity(it)
    if en is not None:
        problems += check_structure(en, it)
    return problems
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_check.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add manualtrans/check.py tests/test_check.py
git commit -m "feat: placeholder integrity and EN/IT structure checks"
```

---

## Task 11: Render (`render.py`)

**Files:**
- Create: `manualtrans/render.py`, `tests/test_render.py`

**Interfaces:**
- Produces:
  - `RenderError(Exception)`.
  - `build_pandoc_cmd(md_path: Path, out_path: Path, media_dir: Path) -> list[str]` — returns the pandoc argv; for `.pdf` outputs includes `--pdf-engine=weasyprint`; always includes `--resource-path=<media_dir>` and `--from=markdown+raw_html`.
  - `render(md_path: Path, out_base: Path, formats: list[str], media_dir: Path, runner=subprocess.run) -> list[Path]` — for each format builds the cmd and invokes `runner`; returns the list of produced output paths (`out_base.with_suffix(".pdf")`, `.docx`). Raises `RenderError` if `runner` returns non-zero. `runner` is injectable for tests.

- [ ] **Step 1: Write the failing test**

`tests/test_render.py`:
```python
from pathlib import Path

import pytest

from manualtrans.render import build_pandoc_cmd, render, RenderError


def test_pdf_cmd_uses_weasyprint(tmp_path: Path):
    cmd = build_pandoc_cmd(tmp_path / "in.md", tmp_path / "out.pdf", tmp_path / "media")
    assert "pandoc" in cmd[0]
    assert "--pdf-engine=weasyprint" in cmd
    assert any(a.startswith("--resource-path=") for a in cmd)


def test_docx_cmd_no_pdf_engine(tmp_path: Path):
    cmd = build_pandoc_cmd(tmp_path / "in.md", tmp_path / "out.docx", tmp_path / "media")
    assert not any("pdf-engine" in a for a in cmd)


def test_render_invokes_runner_per_format(tmp_path: Path):
    md = tmp_path / "in.md"
    md.write_text("# hi", encoding="utf-8")
    calls = []

    class Result:
        returncode = 0
        stderr = ""

    def runner(cmd, **kwargs):
        calls.append(cmd)
        return Result()

    out = render(md, tmp_path / "out", ["pdf", "docx"], tmp_path / "media", runner=runner)
    assert len(calls) == 2
    assert (tmp_path / "out.pdf") in out
    assert (tmp_path / "out.docx") in out


def test_render_raises_on_failure(tmp_path: Path):
    md = tmp_path / "in.md"
    md.write_text("# hi", encoding="utf-8")

    class Result:
        returncode = 1
        stderr = "boom"

    def runner(cmd, **kwargs):
        return Result()

    with pytest.raises(RenderError):
        render(md, tmp_path / "out", ["pdf"], tmp_path / "media", runner=runner)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_render.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'manualtrans.render'`

- [ ] **Step 3: Write minimal implementation**

`manualtrans/render.py`:
```python
from __future__ import annotations

import subprocess
from pathlib import Path

SUFFIX = {"pdf": ".pdf", "docx": ".docx"}


class RenderError(Exception):
    pass


def build_pandoc_cmd(md_path: Path, out_path: Path, media_dir: Path) -> list[str]:
    cmd = [
        "pandoc",
        str(md_path),
        "--from=markdown+raw_html",
        f"--resource-path={media_dir}",
        "-o",
        str(out_path),
    ]
    if out_path.suffix == ".pdf":
        cmd.append("--pdf-engine=weasyprint")
    return cmd


def render(
    md_path: str | Path,
    out_base: str | Path,
    formats: list[str],
    media_dir: str | Path,
    runner=subprocess.run,
) -> list[Path]:
    md_path = Path(md_path)
    out_base = Path(out_base)
    media_dir = Path(media_dir)
    produced: list[Path] = []
    for fmt in formats:
        if fmt not in SUFFIX:
            raise RenderError(f"unsupported format: {fmt}")
        out_path = out_base.with_suffix(SUFFIX[fmt])
        cmd = build_pandoc_cmd(md_path, out_path, media_dir)
        result = runner(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RenderError(f"pandoc failed for {fmt}: {getattr(result, 'stderr', '')}")
        produced.append(out_path)
    return produced
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_render.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add manualtrans/render.py tests/test_render.py
git commit -m "feat: render markdown to pdf/docx via pandoc"
```

---

## Task 12: CLI wiring (`main.py`)

**Files:**
- Create: `manualtrans/main.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: every prior module.
- Produces a Typer `app` with commands:
  - `ocr INPUT_PDF [--out doc.json] [--media media] [--ocr-model ...]`
  - `translate DOC_JSON [--out doc_it.json] [--glossary glossary.yaml]`
  - `assemble DOC_IT_JSON [--out output.md]`
  - `render OUTPUT_MD --out OUT_BASE [--to pdf,docx] [--media media]`
  - `check DOC_IT_JSON [--source doc.json]` — prints problems, exits non-zero if any.
  - `run INPUT_PDF --out OUT_BASE [--ocr-model ...] [--to pdf,docx] [--glossary glossary.yaml]` — full pipeline writing intermediates next to `OUT_BASE`.
- The CLI reads defaults from `get_settings()`; flags override.

**Testing approach:** Test the `check` command end-to-end via Typer's `CliRunner` (pure, no network). The translate/ocr/render commands are covered by their module tests; the CLI test focuses on wiring `check` and on `run` with the network stages monkeypatched.

- [ ] **Step 1: Write the failing test**

`tests/test_cli.py`:
```python
from pathlib import Path

from typer.testing import CliRunner

from manualtrans.main import app
from manualtrans.models import Doc, Page

runner = CliRunner()


def write_doc(path: Path, markdown: str):
    Doc(source_pdf="a.pdf", source_hash="H", ocr_model="mistral-ocr-2512",
        pages=[Page(index=0, markdown=markdown)]).dump(path)


def test_check_passes(tmp_path: Path):
    it = tmp_path / "doc_it.json"
    write_doc(it, "# Titolo")
    result = runner.invoke(app, ["check", str(it)])
    assert result.exit_code == 0


def test_check_fails_on_structure(tmp_path: Path):
    en = tmp_path / "doc.json"
    it = tmp_path / "doc_it.json"
    write_doc(en, "# A\n\n## B")
    write_doc(it, "# A")  # lost a heading
    result = runner.invoke(app, ["check", str(it), "--source", str(en)])
    assert result.exit_code != 0
    assert "heading" in result.stdout.lower()


def test_assemble_command(tmp_path: Path):
    it = tmp_path / "doc_it.json"
    out = tmp_path / "out.md"
    write_doc(it, "# Titolo\n\nTesto")
    result = runner.invoke(app, ["assemble", str(it), "--out", str(out)])
    assert result.exit_code == 0
    assert out.read_text(encoding="utf-8").startswith("# Titolo")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'manualtrans.main'`

- [ ] **Step 3: Write minimal implementation**

`manualtrans/main.py`:
```python
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from .assemble import assemble as assemble_doc
from .cache import Cache
from .check import check_document
from .config import get_settings
from .glossary import Glossary
from .models import Doc
from .ocr import run_ocr
from .prompt import build_system_prompt
from .render import render as render_md
from .translate import OpenRouterTranslator, translate_document

app = typer.Typer(help="Translate ham-radio PDF manuals (EN/ZH) to Italian.")


@app.command()
def ocr(
    input_pdf: Path,
    out: Path = typer.Option(Path("doc.json"), "--out"),
    media: Path = typer.Option(Path("media"), "--media"),
    ocr_model: Optional[str] = typer.Option(None, "--ocr-model"),
):
    s = get_settings()
    model = ocr_model or s.ocr_model
    cache = Cache(s.cache_dir)
    run_ocr(input_pdf, out, media, model, s.mistral_api_key, cache)
    typer.echo(f"wrote {out}")


@app.command()
def translate(
    doc_json: Path,
    out: Path = typer.Option(Path("doc_it.json"), "--out"),
    glossary: Path = typer.Option(Path("glossary.yaml"), "--glossary"),
):
    s = get_settings()
    doc = Doc.load(doc_json)
    gloss = Glossary.load(glossary)
    system_prompt = build_system_prompt(gloss.render())
    translator = OpenRouterTranslator(
        s.openrouter_api_key, s.openrouter_models, system_prompt, attempts=s.model_attempts
    )
    cache = Cache(s.cache_dir)
    out_doc = translate_document(doc, translator, cache)
    out_doc.dump(out)
    typer.echo(f"wrote {out}")


@app.command()
def assemble(
    doc_it_json: Path,
    out: Path = typer.Option(Path("output.md"), "--out"),
    glossary: Path = typer.Option(Path("glossary.yaml"), "--glossary"),
):
    gloss = Glossary.load(glossary)
    doc = Doc.load(doc_it_json)
    md = assemble_doc(doc, header_footer_policy=gloss.header_footer_policy)
    Path(out).write_text(md, encoding="utf-8")
    typer.echo(f"wrote {out}")


@app.command()
def render(
    output_md: Path,
    out: Path = typer.Option(..., "--out", help="output basename, no extension"),
    to: Optional[str] = typer.Option(None, "--to"),
    media: Path = typer.Option(Path("media"), "--media"),
):
    s = get_settings()
    formats = [f.strip() for f in to.split(",")] if to else s.output_formats
    produced = render_md(output_md, out, formats, media)
    for p in produced:
        typer.echo(f"wrote {p}")


@app.command()
def check(
    doc_it_json: Path,
    source: Optional[Path] = typer.Option(None, "--source", help="EN doc.json for structure parity"),
):
    it = Doc.load(doc_it_json)
    en = Doc.load(source) if source else None
    problems = check_document(it, en)
    if problems:
        for p in problems:
            typer.echo(f"FAIL: {p}")
        raise typer.Exit(code=1)
    typer.echo("OK")


@app.command()
def run(
    input_pdf: Path,
    out: Path = typer.Option(..., "--out", help="output basename, no extension"),
    ocr_model: Optional[str] = typer.Option(None, "--ocr-model"),
    to: Optional[str] = typer.Option(None, "--to"),
    glossary: Path = typer.Option(Path("glossary.yaml"), "--glossary"),
):
    s = get_settings()
    cache = Cache(s.cache_dir)
    base = out
    doc_json = base.with_name(base.name + ".doc.json")
    doc_it_json = base.with_name(base.name + ".doc_it.json")
    md_path = base.with_suffix(".md")
    media = base.with_name("media")

    model = ocr_model or s.ocr_model
    doc = run_ocr(input_pdf, doc_json, media, model, s.mistral_api_key, cache)

    gloss = Glossary.load(glossary)
    system_prompt = build_system_prompt(gloss.render())
    translator = OpenRouterTranslator(
        s.openrouter_api_key, s.openrouter_models, system_prompt, attempts=s.model_attempts
    )
    doc_it = translate_document(doc, translator, cache)
    doc_it.dump(doc_it_json)

    problems = check_document(doc_it, doc)
    if problems:
        for p in problems:
            typer.echo(f"WARN: {p}", err=True)

    md = assemble_doc(doc_it, header_footer_policy=gloss.header_footer_policy)
    md_path.write_text(md, encoding="utf-8")

    formats = [f.strip() for f in to.split(",")] if to else s.output_formats
    produced = render_md(md_path, base, formats, media)
    for p in produced:
        typer.echo(f"wrote {p}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -v`
Expected: ALL tests pass.

- [ ] **Step 6: Commit**

```bash
git add manualtrans/main.py tests/test_cli.py
git commit -m "feat: typer CLI wiring run/ocr/translate/assemble/render/check"
```

---

## Task 13: README and end-to-end smoke documentation

**Files:**
- Create: `README.md`

**Interfaces:** none (docs only).

- [ ] **Step 1: Write `README.md`**

```markdown
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
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add README with setup and usage"
```

---

## Final verification

- [ ] **Run the whole suite:** `uv run pytest -v` — all green.
- [ ] **Manual smoke (requires real keys + a small EN PDF, pandoc, weasyprint):**
  `uv run manualtrans run sample_en.pdf --out out` → produces `out.pdf` + `out.docx`;
  a second run makes **zero** API calls (watch logs / cache dir).
- [ ] **Acceptance check:** open `out.docx`, confirm Italian text with images and tables in place,
  acronyms/model codes/units/menu strings untranslated.
