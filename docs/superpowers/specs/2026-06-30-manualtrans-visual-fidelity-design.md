# Spec di design — manualtrans: fedeltà visiva (colore testo + copertina)

**Data:** 2026-06-30
**Owner:** IU3QEZ
**Riferimento:** M2 layout spec `2026-06-29-manualtrans-m2-layout-design.md`; memoria
`layout-needs-heading-hierarchy`. Sotto-progetto successivo a M2-layout (già su master).
**Scope:** preservare nel rendering due tratti visivi dell'originale che oggi si perdono:
(A) il **colore del testo** (paragrafi/titoli rossi o comunque colorati = enfasi/avvisi); (B) la
**prima pagina (copertina)**, mantenuta come immagine dell'originale con una filigrana "traduzione".
Fuori scope: colore inline sotto il livello di blocco; fedeltà tipografica fine del DOCX.

---

## 1. Motivazione

Sull'output M2 reale l'utente ha rilevato: (1) i **paragrafi/testi rossi** (contenuti "importanti")
e altri colori d'attenzione vengono persi — diventano neri; (3) la **copertina** viene rifluita e
tradotta, mentre va mantenuta come l'originale. Causa comune: i blocchi OCR-4 espongono solo
`type, bbox, content` — **nessun colore** (verificato sui dati e sui docs Mistral via Context7: le
`*_annotation_format` annotano immagini/metadati-documento, non il colore del testo). Quindi il
colore va ricavato da una **fonte esterna deterministica**: il **raster delle pagine sorgente**.

Vincolo invariato: nessun layout pixel-perfect; reflow. Qui si aggiunge solo **colore** (preservato)
e un **caso speciale copertina** (immagine).

## 2. Decisioni (brainstorming)

- **Fonte colore: campionamento pixel per blocco** dal raster della pagina sorgente (no vision, no
  text-layer). I **blocchi OCR sono le unità** di contenuto da colorare. Granularità = blocco;
  colore inline dentro un paragrafo nero è fuori scope (limite noto).
- **Scope colore: title + text block.** I paragrafi rossi (text block) vanno preservati, non solo i
  titoli.
- **Copertina (pagina 0): immagine dell'originale + filigrana** diagonale semi-trasparente
  "TRADUZIONE IN ITALIANO" **incisa nell'immagine** (così vale in PDF e DOCX). Niente reflow/traduzione
  per la pagina 0.
- **Rasterizzazione** via `pdftoppm` (già presente); manipolazione immagini via **Pillow** (già nel
  venv, dipendenza di weasyprint).
- Mappatura colore→markdown **per ordine di lettura, per pagina, con guardia** (come la
  riclassificazione heading M2): se i conteggi non combaciano, la pagina **non** viene colorata
  (mai output errato).

## 3. Architettura / componenti

```
PDF ─[ocr4]→ doc.json(blocks) ─[pagerender: raster pagine sorgente]→ page-N.png (px = dimensioni OCR)
   ├─[color: sample bbox→Block.color]→ doc.json(blocks+color)
   └─[cover: raster pag.1 + watermark PIL]→ media/cover.png
        … translate … ─[layout: reclassify + colorize spans + cover]→ md ─[render]→ PDF/DOCX
```

### 3.1 `pagerender.py` (nuovo)
- `rasterize_pages(pdf_path, out_dir, pages) -> list[Path]`: per ogni pagina, `pdftoppm` reso alle
  **dimensioni px riportate dall'OCR** per quella pagina (`-scale-to-x/-y` o equivalente) così i bbox
  mappano 1:1. Ritorna i path PNG (ordine = indice pagina).
- `make_cover(page1_png, text, out_path) -> Path`: con Pillow, disegna una filigrana diagonale
  semi-trasparente `text` (default "TRADUZIONE IN ITALIANO") centrata sull'immagine; salva `out_path`.

### 3.2 Rilevamento colore via maschera HSV (`color.py`)
Approccio **deterministico a maschere HSV** (Pillow `.convert("HSV")` + numpy vettoriale; niente
OpenCV). Si definisce una tabella di **colori d'attenzione** target con i rispettivi range HSV, es.:
```
ATTENTION_COLORS = {
  "#cc0000": [((0, 90, 60), (10, 255, 255)), ((170, 90, 60), (180, 255, 255))],  # rosso (2 range hue)
  # estendibile: blu, verde, arancio…
}
```
- `block_color(hsv_array, bbox) -> str | None`: ritaglia il bbox dall'array HSV della pagina;
  considera i pixel **di inchiostro** (V sotto una soglia, cioè non sfondo chiaro); per ogni colore
  target costruisce la maschera `inRange` (vettoriale numpy) e calcola la **frazione di pixel-inchiostro
  nella maschera**; se la frazione massima supera la soglia (es. ≥ 0.5) ritorna quell'hex, altrimenti
  `None`. (Il rosso usa due range di hue, uniti in OR.)
- `annotate_block_colors(doc, page_images) -> doc`: converte ogni raster in HSV una volta, poi per i
  blocchi `title|text` setta `Block.color`; gli altri restano `None`.

### 3.3 Schema (`models.py`)
- `Block` guadagna `color: str | None = None` (retro-compatibile; OCR-3/senza-raster → None).

### 3.4 Layout — applicazione colore (`layout.py`)
- Estende il meccanismo per-pagina già esistente: nel rimappare per ordine di lettura i blocchi
  colorati (title→heading, text→paragrafo) sul markdown **IT**, avvolge il contenuto in
  `<span style="color:#rrggbb">…</span>`. Riusa la guardia conteggi: se i blocchi-colorabili della
  pagina non combaciano con gli elementi markdown corrispondenti, la pagina **non** viene colorata
  (log del conteggio colorati/saltati). Colore sorgente = EN raster; testo = IT (per ordine).

### 3.5 Copertina (`layout.py` + assemble)
- `build_cover(doc, source_pdf, media_dir) -> Path | None`: rasterizza la pagina 1, applica
  `make_cover` (watermark), salva in `media/cover.png`.
- In `main.run`/assemble: se la copertina è attiva, la pagina 0 del doc tradotto **non** viene
  emessa come markdown rifluito; al suo posto un `![cover](cover.png)` a piena pagina in testa al
  documento (CSS: prima pagina senza margini, immagine full-bleed/contain). Pagine 1+ invariate.

### 3.6 CLI / wiring (`main.py`)
- In `run` (quando layout attivo e blocchi presenti): rasterizza pagine → `annotate_block_colors` →
  costruisce la cover → passa tutto a layout/assemble. Flag `--no-color` e `--no-cover` per
  disattivare singolarmente; `--no-layout` continua a disattivare tutto il blocco visivo.

## 4. Gestione errori / casi limite

- **Raster non producibile** (pdftoppm fallisce / PDF illeggibile): log WARN, si prosegue senza
  colore né cover (nessun blocco).
- **Nessun colore rilevato**: comportamento attuale (testo nero), nessun span aggiunto.
- **Mappatura per-pagina non allineata** (liste/tabelle): pagina non colorata, log; mai colore errato.
- **OCR-3 / blocchi assenti**: niente raster-color (no blocchi), niente cover-da-blocchi; M1/M2 flat
  invariato. La cover può comunque essere prodotta (rasterizza pag.1) se richiesta.
- **Watermark**: incisa nell'immagine → indipendente da PDF/DOCX engine.

## 5. Testing (`pytest`, nessuna API)

- `block_color` (HSV mask): PNG sintetico con bbox su testo rosso puro → l'hex rosso target; testo
  grigio/nero → `None`; sfondo chiaro → `None`. Verifica anche i due range di hue del rosso.
- **Dipendenza:** aggiungere `numpy` a `pyproject.toml` (Pillow è già presente via weasyprint).
- `annotate_block_colors`: doc con bbox su zone colorate/neutre di un raster fixture → `Block.color`
  popolato solo dove atteso.
- `make_cover`: l'immagine prodotta differisce dall'input (watermark presente) e ha le stesse
  dimensioni.
- layout colore: blocco con `color` → markdown avvolto in `<span style="color:…">`; guardia conteggi
  → pagina disallineata non colorata.
- cover: pagina 0 sostituita da `![cover](cover.png)`, pagine 1+ invariate.
- `pagerender.rasterize_pages`: costruzione comando `pdftoppm` corretta (runner iniettabile, nessun
  pdftoppm reale nei test unitari).

## 6. Criteri di accettazione

1. I paragrafi/titoli rossi (e altri colori d'attenzione) dell'originale appaiono **colorati** nel
   PDF tradotto.
2. La **pagina 1** del PDF tradotto è l'immagine della copertina originale con filigrana
   "TRADUZIONE IN ITALIANO" ben visibile; il contenuto tradotto inizia da pagina 2.
3. Con `--no-color` / `--no-cover` le rispettive funzioni si disattivano; OCR-3 resta flat.
4. Nessuna chiamata API nei test; seconda esecuzione usa la cache (il raster/cover è un artefatto
   locale rigenerabile, non un costo API).

## 7. Fasi successive (fuori scope)

- Colore **inline** sotto il livello di blocco (richiede text-layer PDF o vision).
- Watermark configurabile (testo/opacità) e cover per documenti senza copertina riconoscibile.
- Sfondi/evidenziazioni di cella tabella a colori.
