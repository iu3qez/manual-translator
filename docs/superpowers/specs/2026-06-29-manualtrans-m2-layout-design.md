# Spec di design — manualtrans M2 (sotto-progetto): blocchi OCR-4 + ricostruzione layout

**Data:** 2026-06-29
**Owner:** IU3QEZ
**Riferimento:** `PRD_traduttore_manuali_pdf.md` (§5 schema, §13 milestone M2); spec M1
`docs/superpowers/specs/2026-06-27-manualtrans-m1-design.md`
**Scope:** primo sotto-progetto di M2 — **estrazione blocchi OCR-4** + **ricostruzione del
layout** (gerarchia heading, CSS adattiva, TOC). Esclusi (sotto-progetti successivi): traduzione
selettiva per tipo di blocco, confidence gate + `review.md`, check §10.3–5, supporto ZH/scansioni.

---

## 1. Contesto e motivazione

L'output tradotto M1 ha tre problemi di layout (osservati su manuali reali): font troppo grande,
indice (TOC) con numeri di pagina dell'originale ormai sbagliati dopo il reflow, formattazione
piatta. Uno spike (2026-06-29, rami geometrico-OCR4 e vision-LLM, vedi memoria
`layout-needs-heading-hierarchy`) ha individuato la causa: **il markdown OCR ha una gerarchia di
heading piatta** — Mistral usa `#` (H1) sia per il titolo di copertina sia per ~20 micro-heading
in-body. La CSS stila *per livello*, quindi qualsiasi foglio di stile che ingrandisce H1 trasforma
ogni intestazione interna in un banner. **La CSS da sola non basta: serve ricostruire i livelli
degli heading**, e il segnale affidabile sono le **dimensioni dei blocchi OCR-4**.

Vincolo di design invariato (PRD §1): **mai layout pixel-perfect**, l'output rifluisce. Obiettivo:
**"paragraph similar"** — stessa scala tipografica e densità dell'originale, contenuto che rifluisce
su più pagine se serve. Non si comprime per stare nelle stesse pagine.

## 2. Decisioni (prese in brainstorming)

- **OCR-4 default.** Il modello OCR predefinito diventa `mistral-ocr-latest` (OCR 4) con
  `include_blocks=True`. OCR-3 (`mistral-ocr-2512`) resta selezionabile via `--ocr-model ocr3` e
  mantiene il comportamento flat M1 (nessuna ricostruzione layout).
- **TOC rigenerato.** L'indice OCR'd dell'originale viene **rimosso** e sostituito da un TOC
  generato da pandoc (`--toc`), con link e numeri di pagina corretti.
- **Riclassificazione sull'EN prima della traduzione.** I livelli heading si correggono sul
  `doc.json` (EN) subito dopo l'OCR; la traduzione preserva i livelli (invariante M1), quindi l'IT
  li eredita.
- **Soglie livelli heading** (default tarabile): rapporto font/body ≥1.7→h1, ≥1.35→h2, ≥1.15→h3,
  altrimenti h4.
- **Callout** riconosciuti per **prefisso testuale** (NOTE/NOTA/WARNING/ATTENZIONE/CAUTION) per ora.

## 3. Architettura / data flow

```
PDF ─[ocr.py OCR-4 +include_blocks]→ doc.json (+blocks, page width/height)
    ─[layout.reclassify_headings]→ doc.json' (livelli # corretti, TOC OCR'd marcato/rimosso)
    ─[translate.py]→ doc_it.json (livelli preservati)
    ─[assemble.py +strip TOC]→ output.md
    ─[render.py +CSS generata +--toc]→ output.pdf / output.docx
```

La CSS adattiva è generata da `layout.style_profile(doc)` (dalle metriche dei blocchi EN) ed è
indipendente dalla traduzione; viene passata al render. Quando i blocchi non ci sono (OCR-3),
`layout` è un no-op e il render usa il percorso flat M1.

## 4. Schema intermedio (`models.py`)

Estensione retro-compatibile dello schema §5:

```python
class Block(BaseModel):
    type: str                         # title|text|list|caption|table|code|equation|...
    bbox: list[float]                 # [x0, y0, x1, y1] nello spazio coordinate pagina
    content: str | None = None

class Page(BaseModel):
    index: int
    markdown: str
    images: list[Image] = []
    tables: list[Table] = []
    blocks: list[Block] = []          # NEW — vuoto su OCR-3
    width: float | None = None        # NEW — dimensione pagina (unità OCR)
    height: float | None = None       # NEW
    header: str | None = None
    footer: str | None = None
```

`Doc` invariato. File OCR-3 esistenti restano validi (campi nuovi con default).

## 5. `ocr.py` — estrazione blocchi (OCR-4)

- Default `OCR_MODEL=mistral-ocr-latest`; `--ocr-model ocr3|ocr4` mappa su
  `mistral-ocr-2512`/`mistral-ocr-latest`.
- `run_ocr`: se modello = OCR-4, chiama `client.ocr.process(..., include_blocks=True)`.
- `parse_ocr_response`: oltre a markdown/images/tables (già fatto), popola per pagina
  `blocks` (type, bbox, content) e `width`/`height` dalle dimensioni pagina.
- La cache key include già il modello OCR → cambiare default a OCR-4 produce un nuovo artefatto
  (re-OCR una tantum).
- **Verifica SDK (rischio):** i nomi reali in `mistralai 2.5.0` per i blocchi e le dimensioni
  pagina (`OCRPageObject.blocks`, `dimensions`; campi del blocco type/bbox) e le **unità** del bbox
  vanno confermati ispezionando i modelli installati (`OCRPageObject`, `OCRPageDimensions`, i vari
  `OCR*Block`) — come fatto per `OCRTableObject`. Se un campo differisce, si adatta solo
  `parse_ocr_response`; la firma resta stabile. I test usano response finte e non dipendono dall'API.

## 6. `layout.py` (nuovo modulo) — ricostruzione deterministica

Responsabilità unica: dato un `Doc` con blocchi, derivare livelli heading e profilo di stile.
Nessuna chiamata di rete; testabile in isolamento.

### 6.1 Stima dimensione font
`block_font_size(block) = bbox_height / max(1, n_righe(content))`, dove `bbox_height = y1 - y0` e
`n_righe = content.count("\n") + 1` (con fallback se `content` è vuoto). Valori nelle stesse unità
del bbox.

### 6.2 Metriche pagina
- `body_size` = mediana di `block_font_size` sui blocchi `text` (fallback: mediana su tutti).
- `page_width/height` dalle dimensioni pagina (mediana sulle pagine).
- margini: stimati dall'estensione dell'area-testo (min/max bbox dei blocchi) rispetto al bordo
  pagina; arrotondati a valori tipografici sensati.

### 6.3 Riclassificazione heading — `reclassify_headings(doc) -> doc`
Per ogni riga heading del markdown (`^#{1,6}\s+(.*)`), trova il blocco `title` il cui `content`
corrisponde al testo (match esatto/normalizzato); calcola `ratio = font/body` e assegna il livello
per soglie (§2). Riscrive il prefisso `#` al livello derivato. Heading senza blocco corrispondente:
lasciati invariati. Ritorna un nuovo `Doc` (i `markdown` di pagina aggiornati). Deterministico.

### 6.4 Callout — `wrap_callouts(markdown) -> markdown`
Righe/paragrafi che iniziano con `NOTE|NOTA|WARNING|ATTENZIONE|CAUTION|AVVERTENZA` (case-insensitive,
eventuale `:`) → wrappati in `<div class="callout">…</div>` (HTML raw, che pandoc preserva), stilato
dalla CSS. Conservativo: solo il paragrafo del prefisso.

### 6.5 TOC OCR'd — `strip_ocr_toc(doc) -> doc`
Individua il blocco indice: una sezione che inizia con heading `Contents|Indice|Sommario` seguito da
≥3 righe con leader puntati e numero finale (`^.*\.{3,}\s*\d+\s*$`). Rimuove l'heading-indice e
quelle righe. Conservativo: se non trova il pattern, non tocca nulla.

### 6.6 Profilo e CSS — `style_profile(doc) -> dict` / `render_css(profile) -> str`
Profilo: `{page: {width_mm,height_mm,margin_mm}, body_pt, line_height, headings:{h1..h4 pt},
table, callout}`. `render_css` riempie un template CSS:
- `@page { size: …; margin: … }`
- `body { font-family; font-size: body_pt; line-height }`
- `h1..h4 { font-size: … }` dai pt derivati
- `table, th, td { … }` bordi/padding compatti
- `.callout { … }` sfondo/bordo per i NOTE box
La conversione unità-OCR → pt/mm usa le dimensioni pagina (se la pagina è ~A4 si normalizza ad A4).

## 7. Integrazione `assemble.py` / `render.py` / `main.py`

- **assemble:** invariato. Il markdown ricevuto ha già livelli corretti e TOC OCR'd rimosso —
  entrambi fatti da `layout` sull'EN prima della traduzione (punto unico; `assemble` non tocca
  heading né TOC).
- **render:** `build_html_cmd` e `build_pandoc_cmd` accettano `css: Path | None` e `toc: bool`.
  Con layout attivo: `--css=<generata>` (per il PDF/HTML) e `--toc --toc-depth=3`. Per il DOCX la
  CSS non si applica direttamente; in M2 il DOCX usa `--toc` e la gerarchia corretta (lo stile fine
  del DOCX via `--reference-doc` è un miglioramento successivo, fuori scope qui).
- **main `run`:** default ocr4. Dopo l'OCR, se i blocchi sono presenti: `doc = layout.reclassify_headings(layout.strip_ocr_toc(doc))`; calcola `css = layout.write_css(layout.style_profile(doc), path)`; passa `css` e `toc=True` al render. Flag `--no-layout` per saltare la ricostruzione (render flat). OCR-3 (nessun blocco) → percorso flat.

## 8. Gestione errori / casi limite

- **Blocchi assenti** (OCR-3 o risposta priva di blocchi): `layout` è no-op, nessuna CSS, render
  flat M1. Nessun errore.
- **Heading senza blocco corrispondente:** livello invariato (non si indovina).
- **TOC non riconosciuto:** non si rimuove nulla (meglio un indice vecchio che cancellare contenuto).
- **Dimensioni pagina anomale / unità impreviste:** fallback a A4 + body 10.5pt di default, con
  `WARN` a log; non si blocca il render.
- **Callout falsi positivi:** solo prefissi noti; conservativo.

## 9. Testing (`pytest`, nessuna API)

- `models`: round-trip di `Block` + nuovi campi `Page`; retro-compat OCR-3 (blocchi vuoti).
- `ocr`: `parse_ocr_response` con response finta che include blocchi+dimensioni → popola schema.
- `layout`:
  - `block_font_size` su bbox/contenuti sintetici;
  - `reclassify_headings`: blocchi con font grande/medio/piccolo → `#`/`##`/`###` corretti; heading
    senza blocco invariato;
  - `wrap_callouts`: NOTE/WARNING wrappati, testo normale no;
  - `strip_ocr_toc`: rimuove indice con leader puntati; non tocca pagine senza pattern;
  - `style_profile`/`render_css`: profilo coerente, CSS contiene i valori attesi (@page, body_pt,
    h1..h4).
- `render`: `build_html_cmd`/`build_pandoc_cmd` includono `--css` e `--toc` quando richiesti.
- Golden leggero: doc sintetico con blocchi → reclassify → assemble → (cmd costruito) verificato.

## 10. Criteri di accettazione

1. `manualtrans run input.pdf --out out` (default OCR-4) produce PDF/DOCX dove gli heading interni
   **non** sono banner: titolo copertina ≫ sezioni ≫ sotto-sezioni, coerente con l'originale.
2. Indice generato (pandoc `--toc`) con numeri/link corretti; l'indice OCR'd originale **assente**.
3. Body font compatto e margini coerenti con l'originale ("paragraph similar"), documento leggibile.
4. Callout NOTE/WARNING visivamente distinti.
5. Con `--ocr-model ocr3` il comportamento M1 (flat) è invariato.
6. Riproducibile dai `*.json`; nessuna API nei test; seconda esecuzione usa la cache.

## 11. Fasi successive (fuori scope, annotate)

- DOCX styling fine via `--reference-doc` generato dal profilo.
- Traduzione selettiva per tipo di blocco (PRD M2 §6).
- Confidence gate + `review.md` + check §10.3–5.
- Supporto ZH/scansioni.
- Vision-LLM come *assist* per casi che la geometria non risolve (lo spike B ha mostrato che da
  sola non basta, ma può integrare la classificazione).
