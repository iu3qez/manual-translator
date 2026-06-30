from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import typer

from . import color, layout, pagerender
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

_OCR_ALIASES = {"ocr3": "mistral-ocr-2512", "ocr4": "mistral-ocr-latest"}


def _resolve_ocr_model(flag: str | None, default: str) -> str:
    # alias-map BOTH the CLI flag and the .env/config default, so OCR_MODEL=ocr4
    # works in .env too; raw model ids pass through unchanged.
    chosen = flag if flag is not None else default
    return _OCR_ALIASES.get(chosen, chosen)


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    # httpx/httpcore log every request; only show them under --verbose
    noisy_level = logging.DEBUG if verbose else logging.WARNING
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(noisy_level)


@app.callback()
def _main(
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="enable debug logging (place before the command)"
    ),
):
    """Configure logging for all commands. INFO by default, DEBUG with -v."""
    _setup_logging(verbose)


@app.command()
def ocr(
    input_pdf: Path,
    out: Path = typer.Option(Path("doc.json"), "--out"),
    media: Path = typer.Option(Path("media"), "--media"),
    ocr_model: Optional[str] = typer.Option(None, "--ocr-model"),
):
    s = get_settings()
    model = _resolve_ocr_model(ocr_model, s.ocr_model)
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
    out_doc = translate_document(doc, translator, cache, concurrency=s.translate_concurrency)
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
    force: bool = typer.Option(False, "--force", help="proceed to render even if structure-parity checks fail"),
    no_layout: bool = typer.Option(False, "--no-layout", help="skip layout reconstruction"),
    no_color: bool = typer.Option(False, "--no-color", help="skip attention-color preservation"),
    no_cover: bool = typer.Option(False, "--no-cover", help="skip original-cover page"),
):
    s = get_settings()
    cache = Cache(s.cache_dir)
    base = out
    doc_json = base.with_name(base.name + ".doc.json")
    doc_it_json = base.with_name(base.name + ".doc_it.json")
    md_path = base.parent / (base.name + ".md")
    media = base.with_name("media")

    model = _resolve_ocr_model(ocr_model, s.ocr_model)
    typer.echo(f"[1/4] OCR {input_pdf} (model {model})…", err=True)
    doc = run_ocr(input_pdf, doc_json, media, model, s.mistral_api_key, cache)
    typer.echo(f"      {len(doc.pages)} page(s) → {doc_json}", err=True)

    gloss = Glossary.load(glossary)
    system_prompt = build_system_prompt(gloss.render())
    translator = OpenRouterTranslator(
        s.openrouter_api_key, s.openrouter_models, system_prompt, attempts=s.model_attempts
    )
    models_label = ", ".join(s.openrouter_models) or "(none configured!)"
    typer.echo(
        f"[2/4] Translating {len(doc.pages)} page(s) [models: {models_label}; "
        f"concurrency {s.translate_concurrency}]…",
        err=True,
    )
    doc_it = translate_document(doc, translator, cache, concurrency=s.translate_concurrency)
    doc_it.dump(doc_it_json)
    typer.echo(f"      → {doc_it_json}", err=True)

    problems = check_document(doc_it, doc)
    if problems:
        if not force:
            for p in problems:
                typer.echo(f"FAIL: {p}", err=True)
            raise typer.Exit(code=1)
        for p in problems:
            typer.echo(f"WARN: {p}", err=True)

    css_path = None
    toc = False
    cover_name = None
    use_layout = (not no_layout) and any(p.blocks for p in doc.pages)
    if use_layout:
        doc_it = layout.reclassify_headings(doc_it)
        doc_it = layout.strip_ocr_toc(doc_it)
        sizes = [(p.width, p.height) for p in doc.pages]
        rasters = pagerender.rasterize_pages(input_pdf, sizes, media) if not (no_color and no_cover) else {}
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

    formats = [f.strip() for f in to.split(",")] if to else s.output_formats
    typer.echo(f"[4/4] Rendering {', '.join(formats)} via pandoc…", err=True)
    cover_img = (media / "cover.png") if cover_name else None
    produced = render_md(md_path, base, formats, media, css=css_path, toc=toc, cover=cover_img)
    for p in produced:
        typer.echo(f"wrote {p}")
