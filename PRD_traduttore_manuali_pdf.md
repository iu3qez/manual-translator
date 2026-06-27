# PRD — Traduttore di Manuali PDF (EN/ZH → IT) per documentazione radioamatoriale

**Versione:** 0.1
**Destinatario:** Claude Code
**Owner:** IU3QEZ

---

## 1. Contesto e obiettivo

Tradurre in italiano manuali d'uso di apparati radioamatoriali (PDF nativi o scansionati,
sorgenti spesso EN o ZH di qualità variabile) **preservando la struttura** (titoli, liste,
tabelle spec, immagini), producendo un output editabile e impaginato.

Vincolo di design fondamentale: **la traduzione sostituisce il testo, quindi NON si ricostruisce
il layout pixel-perfect**. L'italiano si allunga ~15–20% rispetto all'inglese; va usato un
formato che **rifluisce** (markdown), non coordinate fisse. Semplicità e correttezza qui
coincidono: la pipeline è OCR → markdown → traduzione del markdown → reiniezione asset → render.

## 2. Scope

**In scope**
- Ingest PDF (nativo o scansione), estrazione strutturata via Mistral OCR.
- Traduzione del markdown verso IT, registro tecnico, dominio ham radio.
- Reinizione di immagini e tabelle estratte.
- Render finale DOCX (revisione editabile) e PDF.
- Glossario / regole do-not-translate configurabili.
- Caching per stadio (OCR e traduzione sono a pagamento: mai rieseguire inutilmente).

**Out of scope (v0.1)**
- Traduzione del testo *dentro* le immagini (callout su pannelli, etichette negli schemi):
  è rasterizzato, resta in originale. Vedi §15.
- Ricostruzione layout fedele via bounding box.
- UI web (eventuale fase 2, vedi §13).
- Impaginazione tipografica fine (l'output è "buono e leggibile", non print-ready).

## 3. Architettura (pipeline a stadi)

```
PDF ──▶ [1 OCR] ──▶ doc.json ──▶ [2 TRANSLATE] ──▶ doc_it.json ──▶ [3 ASSEMBLE] ──▶ *.md + media/ ──▶ [4 RENDER] ──▶ *.docx / *.pdf
            │                          │                                 │
          cache                      cache                          placeholder check
```

Ogni stadio legge/scrive un artefatto su disco ed è eseguibile in isolamento (riproducibilità,
debug, ripresa dopo errore). Lo **schema intermedio normalizzato** (§5) disaccoppia il resto
della pipeline dal modello OCR usato (OCR 3 vs OCR 4): gli stadi 2–4 non sanno quale modello
ha prodotto il `doc.json`.

## 4. Moduli

### 4.1 `ocr.py`
- Wrappa l'SDK `mistralai`. Upload del PDF, chiamata `client.ocr.process`.
- Parametri chiamata:
  - `model`: selezionabile — `mistral-ocr-2512` (OCR 3, **default**, più economico) oppure
    `mistral-ocr-latest` (OCR 4, opzionale).
  - `table_format="html"` (preserva colspan/rowspan delle tabelle spec).
  - `include_image_base64=True`.
  - `extract_header=True`, `extract_footer=True`.
  - `include_blocks=True` **solo se** modello = OCR 4 (abilita classificazione per blocco +
    confidence per-parola; usato per traduzione selettiva e QA, vedi §6/§10).
- Output: `doc.json` (schema §5). Le immagini base64 vengono **estratte su `media/`** come file
  (`img-0.jpeg`, …) già qui, e nel markdown il placeholder punta al path relativo (pandoc embeda
  da file, non da data-URI).

### 4.2 `translate.py`
- Traduce il campo `markdown` di ogni pagina (unità = pagina, `temperature=0`).
- Provider LLM **astratto dietro interfaccia** (`Translator.translate(md, context) -> md`),
  con implementazioni Anthropic e Google; default configurabile. Tradeoff costo/qualità su
  manuali lunghi → vedi config (§9).
- Inietta nel system prompt il glossario (§7) e applica le regole do-not-translate.
- **Traduzione selettiva (se `blocks` presenti, OCR 4):** traduce solo blocchi di tipo
  `text|title|list|caption`; salta `code|equation|table` (solo numeri) e li ricopia tali e quali.
  Senza `blocks` (OCR 3) opera sull'intero markdown di pagina, delegando le esclusioni al prompt.
- **Coerenza terminologica** cross-pagina: glossario statico + temp 0. (Estrazione automatica
  termini → glossario dinamico = fase 2.)
- Preserva **byte-for-byte** i placeholder `![...](...)` e `[...](...html)`.

### 4.3 `glossary.py` + `glossary.yaml`
Carica e valida il glossario (§7). Espone:
- `do_not_translate`: lista regex/stringhe da non toccare.
- `preferred`: mappa termine→traduzione IT preferita.
- Iniettato nel prompt; non fa sostituzione meccanica (l'LLM decide in contesto), tranne i
  placeholder che sono protetti a valle dal check (§10).

### 4.4 `assemble.py`
- Risolve i placeholder: immagini → path in `media/`; tabelle → HTML inline (markdown ammette
  HTML raw embedded).
- Riunisce le pagine tradotte in un unico `*.md` (header/footer gestiti: opzione "scarta" o
  "mantieni una volta").
- **Fallisce se restano placeholder orfani** o se il count tabelle/immagini in≠out.

### 4.5 `render.py`
- `*.md` → DOCX e/o PDF via **pandoc** (`--resource-path=media`).
- DOCX: target primario per revisione umana in Word. PDF via engine LaTeX o weasyprint.
- Verifica resa tabelle HTML in DOCX (colspan/rowspan); logga warning se la struttura degrada.

### 4.6 `main.py` (CLI)
Typer. Comandi per stadio + comando `run` end-to-end. Vedi §8.

### 4.7 `cache.py`
- Chiave = hash SHA-256 del file sorgente **+ parametri rilevanti** (modello OCR; modello/prompt
  versione per la traduzione).
- Store su disco (`.cache/`). OCR e traduzione consultano la cache prima di chiamare le API.

## 5. Schema intermedio (`doc.json`)

```json
{
  "source_pdf": "manuale.pdf",
  "source_hash": "…",
  "ocr_model": "mistral-ocr-2512",
  "pages": [
    {
      "index": 0,
      "markdown": "…  ![img-0.jpeg](media/img-0.jpeg)  …  [tbl-0.html](#tbl-0)  …",
      "images": [{ "id": "img-0.jpeg", "path": "media/img-0.jpeg" }],
      "tables": [{ "id": "tbl-0", "html": "<table>…</table>" }],
      "blocks": [{ "type": "title", "bbox": [x0,y0,x1,y1], "content": "…" }],
      "header": "…|null",
      "footer": "…|null",
      "confidence": { "page": 0.97, "words": [ … ] }
    }
  ]
}
```

Il file tradotto `doc_it.json` ha lo stesso schema con `markdown` (e `blocks[].content`) in IT;
`images`/`tables`/`bbox` invariati.

## 6. Specifica del prompt di traduzione (cuore del progetto)

System prompt del `Translator` (parametrizzato con `{glossary}`):

```
Sei un traduttore tecnico EN/ZH→IT specializzato in manuali di apparati radioamatoriali.
Traduci il MARKDOWN fornito in italiano, registro tecnico-manualistico.

REGOLE RIGIDE
1. Restituisci SOLO il markdown tradotto. Nessun preambolo, nessun ```fence``` aggiunto.
2. Preserva ESATTAMENTE la sintassi markdown: heading, liste, grassetti, tabelle, code fence.
3. Preserva BYTE-PER-BYTE i placeholder immagine/tabella:  ![...](...)  e  [...](...html).
   Non tradurli, non spostarli, non alterarne il testo interno.
4. NON tradurre:
   - sigle/acronimi di settore (SSB, CW, FM, AM, VFO, PTT, CTCSS, DCS, RIT, NB, AGC, S-meter…)
   - modelli e codici prodotto (es. UV-K5, IC-7300, FT-991A)
   - frequenze, valori numerici, unità (Hz, kHz, MHz, dB, dBm, W, V, mAh, ppm…)
   - blocchi di codice ed equazioni
5. STRINGHE DI DISPLAY/MENU dell'apparato → lascia in INGLESE come appaiono sul dispositivo,
   ma traduci il testo descrittivo attorno.
   Esempio: "Press [MENU], select SET > VFO > SPLIT to enable split operation"
   →        "Premere [MENU], selezionare SET > VFO > SPLIT per attivare il funzionamento split"
6. TABELLE: traduci solo le celle di intestazione e le celle descrittive; lascia invariate
   le celle numeriche e le unità.
7. Una unità in ingresso = una unità in uscita. Non aggiungere, riassumere o omettere contenuto.
8. Usa le traduzioni preferite del glossario quando applicabili.

GLOSSARIO
{glossary}
```

User message = markdown della pagina (o `content` del singolo blocco in modalità selettiva).

## 7. Glossario / regole do-not-translate (`glossary.yaml`)

```yaml
do_not_translate:
  acronyms: [SSB, CW, FM, AM, VFO, PTT, CTCSS, DCS, RIT, XIT, NB, NR, AGC, ATU, SWR]
  patterns:
    - '\b\d+(\.\d+)?\s?(Hz|kHz|MHz|GHz|dB|dBm|W|mW|V|mAh|ppm)\b'   # valori+unità
    - '\b[A-Z]{1,3}-?[A-Z0-9]{2,}\b'                               # codici modello (euristica)
preferred:                       # term EN/ZH -> IT preferito (l'LLM decide in contesto)
  squelch: squelch
  frequency: frequenza
  channel: canale
  scan: scansione
  memory channel: canale di memoria
  dual watch: dual watch
  battery save: risparmio batteria
  busy channel lockout: blocco canale occupato
header_footer_policy: keep_once   # keep_once | drop | keep_all
```

## 8. Interfaccia CLI

```
# end-to-end
manualtrans run input.pdf --out output.docx \
    --ocr-model ocr3 \          # ocr3 (default) | ocr4
    --to docx,pdf \
    --glossary glossary.yaml \
    --provider anthropic        # anthropic | google

# per stadio (debug / ripresa)
manualtrans ocr       input.pdf            -> doc.json + media/
manualtrans translate doc.json             -> doc_it.json
manualtrans assemble  doc_it.json          -> output.md
manualtrans render    output.md --to docx  -> output.docx

# QA
manualtrans check doc_it.json   # validazioni §10, exit code ≠0 se fallisce
```

## 9. Configurazione (`config.yaml` + env)

- Env: `MISTRAL_API_KEY`, `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY`.
- `config.yaml`: provider e modello di traduzione di default, formati output, cartella cache,
  policy header/footer, soglia confidence per il flag di revisione (§10).
- Tradeoff costo/qualità documentato: per manuali lunghi, modello "economico" di default;
  override per documenti critici.

## 10. Validazione & QA

Il comando `check` (e l'assemble) impongono:
1. **Placeholder integrity:** nessun placeholder orfano; count immagini/tabelle in = out.
2. **Struttura markdown:** heading/lista/tabella della pagina IT coerenti con la EN
   (stesso numero di heading e di righe-tabella per pagina). Questo intercetta il classico
   **silent row-drop** ai confini di split pagina di Mistral.
3. **Block-count (OCR 4):** numero di blocchi `table` atteso per pagina = quello prodotto;
   discrepanza → fail con indicazione pagina.
4. **Confidence gate (OCR 4):** parole sotto soglia → marcate `⟨?…⟩` nell'output e listate in
   un report `review.md`, per revisione umana (cruciale su sorgenti scansionate/ZH).
5. **Leakage check:** euristica anti-traduzione di token protetti (sigle/unità) presenti in EN
   ma assenti/alterati in IT → warning.

## 11. Gestione errori & casi limite

- PDF nativo con layer testo (Icom/Yaesu/Kenwood ufficiali): OCR comunque utile per markdown
  pulito; default OCR 3 sufficiente.
- Scansioni / sorgenti ZH sporche: consigliare OCR 4 (estrazione multilingua + confidence).
- Pagina che eccede il context del traduttore: split per blocchi (OCR 4) o per sezioni heading.
- Rate limit / 429 API: retry con backoff esponenziale; cache parziale per ripresa.
- Tabella HTML che non rende bene in DOCX: logga warning, mantieni HTML (no downgrade silenzioso).

## 12. Stack tecnico & dipendenze

- **Python 3.11+**
- `mistralai` (OCR), provider LLM SDK (`anthropic` / `google-genai`)
- `typer` (CLI), `pydantic` (schemi/config), `pyyaml`
- `pandoc` (binario di sistema; documentare come prerequisito), engine PDF (xelatex o weasyprint)
- `httpx` con retry/backoff
- Struttura: package `manualtrans/` con i moduli §4; test `pytest`.

## 13. Fasi / milestone

- **M1 — MVP CLI (OCR 3):** ocr→translate→assemble→render DOCX, glossario statico, caching,
  check §10 punti 1–2. *Definition of done:* un manuale EN nativo tradotto end-to-end.
- **M2 — OCR 4:** `include_blocks`, traduzione selettiva per tipo, confidence gate + `review.md`,
  check punti 3–5. Supporto sorgenti ZH/scansioni.
- **M3 (opzionale) — Review UI:** thin frontend (SvelteKit adapter-static + FastAPI) per
  revisione affiancata EN/IT con i flag di confidence. Riuso pattern CPVI.
- **M4 (opzionale) — Glossario dinamico:** pass di estrazione termini → glossario per coerenza.

## 14. Criteri di accettazione (MVP / M1)

- `manualtrans run manuale_en.pdf --out out.docx` produce un DOCX leggibile in IT con immagini
  e tabelle al loro posto.
- Tutti i placeholder risolti; nessun orfano (check passa).
- Sigle, modelli, unità e stringhe di menu **non** tradotti.
- Una seconda esecuzione usa la cache (nessuna nuova chiamata OCR/LLM).
- Output riproducibile a partire dai `*.json` intermedi senza ricontattare le API.

## 15. Limiti noti (v0.1)

- Testo dentro le immagini non tradotto (rasterizzato). Mitigazione futura: `bbox_annotation`
  per generare una didascalia IT sotto la figura — non in scope qui.
- Layout non pixel-fedele: l'output rifluisce, l'impaginazione differisce dall'originale (atteso
  e desiderato, dato che il testo è sostituito).
- Tabelle molto complesse (multi-livello, celle unite estese) possono degradare nel passaggio
  HTML→DOCX: gestite con warning, non con downgrade silenzioso.
