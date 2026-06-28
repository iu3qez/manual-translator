from __future__ import annotations

import base64
import logging
from pathlib import Path

from mistralai.client import Mistral

from .cache import Cache, file_hash
from .models import Doc, Image, Page, Table

logger = logging.getLogger(__name__)


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
            # Real SDK uses tbl.content; fake test objects use tbl.html
            html = tbl.content if hasattr(tbl, "content") else tbl.html
            tables.append(Table(id=tbl.id, html=html))
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
        logger.info("ocr: cache hit for %s (model %s)", pdf_path.name, ocr_model)
        doc = Doc.model_validate_json(cached)
        out_json.write_text(cached, encoding="utf-8")
        return doc

    client = client or Mistral(api_key=api_key)
    logger.info("ocr: uploading %s to Mistral…", pdf_path.name)
    uploaded = client.files.upload(
        file={"file_name": pdf_path.name, "content": pdf_path.read_bytes()},
        purpose="ocr",
    )
    signed = client.files.get_signed_url(file_id=uploaded.id)
    logger.info("ocr: processing with %s…", ocr_model)
    response = client.ocr.process(
        model=ocr_model,
        document={"type": "document_url", "document_url": signed.url},
        table_format="html",
        include_image_base64=True,
        extract_header=True,
        extract_footer=True,
    )
    logger.info("ocr: received %d page(s), extracting media…", len(response.pages))
    doc = parse_ocr_response(
        response, pdf_path.name, source_hash, ocr_model, media_dir
    )
    payload = doc.model_dump_json(indent=2)
    cache.set(key, payload)
    out_json.write_text(payload, encoding="utf-8")
    return doc
