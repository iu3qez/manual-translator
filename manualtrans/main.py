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
    force: bool = typer.Option(False, "--force", help="proceed to render even if structure-parity checks fail"),
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
        if not force:
            for p in problems:
                typer.echo(f"FAIL: {p}", err=True)
            raise typer.Exit(code=1)
        for p in problems:
            typer.echo(f"WARN: {p}", err=True)

    md = assemble_doc(doc_it, header_footer_policy=gloss.header_footer_policy)
    md_path.write_text(md, encoding="utf-8")

    formats = [f.strip() for f in to.split(",")] if to else s.output_formats
    produced = render_md(md_path, base, formats, media)
    for p in produced:
        typer.echo(f"wrote {p}")
