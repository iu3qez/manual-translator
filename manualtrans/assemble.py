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
