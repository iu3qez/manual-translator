# Spec di design — `manualtrans` M1 (MVP)

**Data:** 2026-06-27
**Owner:** IU3QEZ
**Riferimento:** `PRD_traduttore_manuali_pdf.md` (i `§N` puntano a sezioni del PRD)
**Scope:** Milestone **M1** del PRD (MVP, OCR 3). M2–M4 fuori scope.

---

## 1. Obiettivo

Tradurre in italiano manuali d'uso di apparati radioamatoriali (PDF nativo o scansionato,
sorgente EN/ZH) **preservando la struttura** (titoli, liste, tabelle spec, immagini), producendo
un output editabile (DOCX) e impaginato (PDF).

Vincolo di design fondamentale (§1): la traduzione **sostituisce** il testo, quindi **non** si
ricostruisce il layout pixel-perfect. L'italiano si allunga ~15–20%; si usa un formato che
**rifluisce** (markdown), non coordinate fisse. Pipeline: OCR → markdown → traduzione del markdown
→ reiniezione asset → render.

## 2. Criteri di accettazione (M1)

1. `manualtrans run manuale_en.pdf --out out` produce **out.pdf** e **out.docx** leggibili in IT,
   con immagini e tabelle al loro posto.
2. Tutti i placeholder risolti; nessun orfano (il comando `check` passa).
3. Sigle, modelli, unità e stringhe di menu del dispositivo **non** tradotti.
4. Una seconda esecuzione usa la cache: **nessuna** nuova chiamata OCR/LLM.
5. Output riproducibile a partire dai `*.json` intermedi senza ricontattare le API.

## 3. Decisioni di progetto (delta rispetto al PRD)

Queste decisioni sono state prese in fase di brainstorming e **prevalgono** sul PRD dove
divergono:

- **Traduzione via OpenRouter unico provider.** Niente SDK Anthropic/Google separati: un solo
  client OpenRouter (API OpenAI-compatibile, chiamata HTTP via `httpx`). Niente interfaccia
  `Translator` astratta — una sola classe concreta `OpenRouterTranslator`; i test mockano il
  livello HTTP.
- **Lista modelli ordinata con fallback.** `OPENROUTER_MODELS` è una lista in ordine di
  preferenza. Per ogni pagina: si parte dal primo modello; ogni modello ha fino a
  `MODEL_ATTEMPTS` (default 2) tentativi con backoff esponenziale; al secondo errore si passa al
  modello successivo; esauriti tutti i modelli la pagina fallisce con errore esplicito. Si logga
  quale modello ha prodotto ogni pagina.
- **Render: PDF primario, DOCX secondario.** Entrambi sempre prodotti (default
  `OUTPUT_FORMATS=pdf,docx`). PDF via pandoc con `--pdf-engine=weasyprint` (pip-installabile,
  niente LaTeX di sistema, ottima resa delle tabelle HTML). DOCX via pandoc.
- **OCR 3 fisso.** `mistral-ocr-2512`. Nessun supporto OCR 4 / `include_blocks` in M1.
- **Configurazione tutta in `.env`.** Niente `config.yaml` in M1 (vedi §11 per le fasi
  successive). `glossary.yaml` resta perché è contenuto di dominio, non configurazione.
- **Tooling `uv` + `pyproject.toml`**, entry point console `manualtrans`.
- **Check M1 = punti §10.1–§10.2** (integrità placeholder + parità struttura EN↔IT). I punti
  §10.3–§10.5 (block-count, confidence gate, leakage) sono M2.

## 4. Architettura: pipeline a 4 stadi su artefatti disco

```
PDF ─[ocr]→ doc.json + media/ ─[translate]→ doc_it.json ─[assemble]→ output.md ─[render]→ output.pdf + output.docx
        cache                       cache                    placeholder check
```

Ogni stadio legge/scrive un artefatto su disco ed è eseguibile in isolamento (riproducibilità,
debug, ripresa dopo errore). Lo **schema intermedio normalizzato** `doc.json` (§7) disaccoppia gli
stadi 2–4 dal modello OCR: traduzione, assemble e render non sanno quale OCR ha prodotto il file.
`cache.py` intercetta OCR e traduzione prima di ogni chiamata API.

## 5. Moduli (package `manualtrans/`)

Ogni modulo ha una responsabilità unica e un'interfaccia testabile in isolamento.

| Modulo | Responsabilità | Dipende da |
|---|---|---|
| `models.py` | Schemi pydantic `Doc / Page / Image / Table` (schema §7). Caricamento/serializzazione di `doc.json` e `doc_it.json`. | pydantic |
| `config.py` | Carica le impostazioni da `.env` con `pydantic-settings`; espone un oggetto config tipato. | pydantic-settings |
| `cache.py` | Cache su disco (`CACHE_DIR`, default `.cache/`). Chiave = SHA-256(file sorgente + parametri rilevanti). Get/set per artefatto. | stdlib |
| `glossary.py` | Carica e valida `glossary.yaml`; rende il blocco testuale `{glossary}` da iniettare nel prompt. | pyyaml, pydantic |
| `prompt.py` | Template del system prompt §6, parametrizzato con `{glossary}`. | — |
| `ocr.py` | Wrappa l'SDK `mistralai`: upload PDF, `client.ocr.process`, estrazione immagini base64 in `media/` come file, costruzione `doc.json`. Consulta la cache. | mistralai, models, cache |
| `translate.py` | `OpenRouterTranslator`: traduce il `markdown` di ogni pagina (`temperature=0`). Loop di fallback sulla lista modelli con retry/backoff per modello. Consulta la cache per pagina. Logga il modello usato. | httpx, prompt, glossary, cache, models, config |
| `assemble.py` | Risolve i placeholder (immagini → path in `media/`; tabelle → HTML inline), unisce le pagine in `output.md` applicando `HEADER_FOOTER_POLICY`. **Fallisce** su placeholder orfani o count immagini/tabelle in≠out. | models |
| `render.py` | `output.md` → PDF (weasyprint) e DOCX via pandoc (`--resource-path=media`). Logga warning se la resa tabelle in DOCX degrada (no downgrade silenzioso). | pandoc (binario di sistema) |
| `check.py` | Validazioni §10.1–§10.2; exit code ≠0 con indicazione pagina su fallimento. | models |
| `main.py` | CLI Typer: `run`, `ocr`, `translate`, `assemble`, `render`, `check`. | tutti i precedenti |

## 6. System prompt di traduzione (§6 del PRD)

Il cuore del progetto. Da non riscrivere casualmente. System prompt parametrizzato con
`{glossary}` che impone (sintesi, testo integrale in `prompt.py` secondo §6):

1. Restituire **solo** il markdown tradotto (nessun preambolo, nessun fence aggiunto).
2. Preservare **esattamente** la sintassi markdown (heading, liste, grassetti, tabelle, code fence).
3. Preservare **byte-per-byte** i placeholder `![...](...)` e `[...](...html)`.
4. **Non** tradurre: sigle/acronimi di settore, modelli/codici prodotto, frequenze/valori/unità,
   blocchi di codice ed equazioni.
5. Stringhe di display/menu del dispositivo → lasciate in inglese, traducendo il testo descrittivo
   attorno.
6. Tabelle: tradurre solo intestazioni e celle descrittive; lasciare invariate celle numeriche e
   unità.
7. Una unità in ingresso = una unità in uscita (niente aggiunte, riassunti, omissioni).
8. Usare le traduzioni preferite del glossario quando applicabili.

User message = markdown della pagina.

## 7. Schema intermedio `doc.json` (§5 del PRD)

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
      "header": "…|null",
      "footer": "…|null"
    }
  ]
}
```

`doc_it.json` ha lo stesso schema con `markdown` in IT; `images`/`tables` invariati. (I campi
`blocks` e `confidence` dello schema PRD §5 sono **omessi in M1**: dipendono da OCR 4.)

## 8. Glossario `glossary.yaml` (§7 del PRD)

Carica `do_not_translate` (acronimi + pattern regex per valori/unità e codici modello) e
`preferred` (mappa termine→IT preferito), più `header_footer_policy`. Iniettato nel prompt; **non**
fa sostituzione meccanica (decide l'LLM in contesto), tranne i placeholder protetti a valle dal
check. Un `glossary.yaml` iniziale è incluso nel repo con i valori del PRD §7.

## 9. CLI (§8 del PRD)

```
# end-to-end
manualtrans run input.pdf --out output [--ocr-model ocr3] [--to pdf,docx] [--glossary glossary.yaml]
#   -> output.pdf, output.docx (+ doc.json, doc_it.json, output.md, media/ intermedi)

# per stadio (debug / ripresa)
manualtrans ocr       input.pdf        # -> doc.json + media/
manualtrans translate doc.json         # -> doc_it.json
manualtrans assemble  doc_it.json      # -> output.md
manualtrans render    output.md --to pdf,docx   # -> output.pdf / output.docx

# QA
manualtrans check     doc_it.json      # validazioni §10.1–2, exit code ≠0 se fallisce
```

I flag CLI sovrascrivono i default presi da `.env`.

## 10. Configurazione (`.env`)

```
MISTRAL_API_KEY=...
OPENROUTER_API_KEY=...
OPENROUTER_MODELS=anthropic/claude-..., google/gemini-...   # lista ordinata; fallback in ordine
OCR_MODEL=mistral-ocr-2512
OUTPUT_FORMATS=pdf,docx          # pdf primario, docx secondario
HEADER_FOOTER_POLICY=keep_once   # keep_once | drop | keep_all
MODEL_ATTEMPTS=2                 # tentativi per modello prima del fallback al successivo
CACHE_DIR=.cache
```

`config.py` valida la presenza delle chiavi richieste all'avvio degli stadi che le usano (OCR
richiede `MISTRAL_API_KEY`; translate richiede `OPENROUTER_API_KEY` e almeno un modello).

## 11. Gestione errori e casi limite

- **Rate limit / 429 / timeout** (OpenRouter e Mistral): retry con backoff esponenziale via
  `httpx`. Per la traduzione, dopo `MODEL_ATTEMPTS` fallimenti su un modello si passa al successivo
  della lista; esauriti tutti → la pagina fallisce con errore esplicito (pagina indicata).
- **Ripresa:** la cache per-stadio (e per-pagina nella traduzione) evita di rifare lavoro già
  completato dopo un errore.
- **`assemble`/`check` falliscono rumorosamente:** placeholder orfani, count immagini/tabelle
  in≠out, disparità di heading o righe-tabella per pagina (intercetta il **silent row-drop** ai
  confini di split pagina di Mistral). Nessun downgrade silenzioso.
- **Tabella HTML che rende male in DOCX:** warning, si mantiene l'HTML (no downgrade silenzioso).
- **PDF nativo con layer testo:** OCR 3 comunque utile per markdown pulito.

## 12. Validazione & QA (§10.1–§10.2)

Il comando `check` (e l'`assemble`) impongono:

1. **Integrità placeholder:** nessun placeholder orfano; count immagini/tabelle in = out.
2. **Struttura markdown:** la pagina IT ha lo stesso numero di heading e di righe-tabella della EN.

## 13. Testing (`pytest`)

- **Nessuna chiamata API reale in CI.** OpenRouter mockato a livello HTTP (`httpx` mock/transport);
  l'accesso a Mistral OCR è dietro un wrapper sottile in `ocr.py` mockabile.
- Test unitari mirati:
  - `assemble`: risoluzione placeholder, **fail** su orfani e su count in≠out.
  - `check`: parità struttura (heading/righe-tabella), rilevazione row-drop.
  - `glossary`: caricamento/validazione, rendering del blocco prompt.
  - `cache`: hit/miss, chiave che cambia coi parametri rilevanti.
  - `translate`: **fallback multi-modello** — primo modello mockato che fallisce `MODEL_ATTEMPTS`
    volte → verifica passaggio al secondo modello e log del modello effettivo.
- Una fixture markdown piccola come "golden" per il flusso assemble→render.

## 14. Stack & prerequisiti (§12 del PRD)

- **Python 3.11+**, gestito con `uv`.
- Dipendenze: `mistralai`, `typer`, `pydantic`, `pydantic-settings`, `pyyaml`, `httpx`.
- **Prerequisiti di sistema:** `pandoc` (binario) e `weasyprint` (per il PDF).
- Test: `pytest`.

## 15. Fasi successive (fuori scope M1, annotate per non perderle)

- **Sistema di configurazione strutturato:** sostituire l'attuale `.env`-only con configurazione
  gerarchica (`config.yaml` + override env + override CLI), profili per documento, soglie di
  revisione. (Promemoria esplicito richiesto in brainstorming.)
- **M2 — OCR 4:** `include_blocks`, traduzione selettiva per tipo di blocco, confidence gate +
  `review.md`, check §10.3–§10.5, supporto sorgenti ZH/scansioni.
- **M3 — Review UI:** frontend affiancato EN/IT con flag di confidence.
- **M4 — Glossario dinamico:** estrazione termini → glossario per coerenza.

## 16. Limiti noti (v0.1, §15 del PRD)

- Testo dentro le immagini non tradotto (rasterizzato).
- Layout non pixel-fedele: l'output rifluisce (atteso e desiderato).
- Tabelle molto complesse possono degradare in DOCX: gestite con warning, non con downgrade
  silenzioso.
