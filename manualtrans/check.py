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
