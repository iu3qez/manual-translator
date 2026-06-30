from __future__ import annotations

import re

from .models import Doc

IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LIST_RE = re.compile(r"^\s*([-*+]\s|\d+\.\s)")


def _fix_glued_lists(md: str) -> str:
    """Recover lists that Mistral OCR mangled at their first item.

    Mistral OCR routinely drops the bullet marker on the FIRST item of a list,
    emitting a bare line glued directly above the surviving ``- `` items with no
    blank line in between. pandoc's default markdown (``lists_without_preceding_
    blankline`` disabled) then forbids the list from interrupting that paragraph,
    so the WHOLE list collapses into a single run-on paragraph (every bullet lost).

    Repair the two shapes:
      * a content line glued above a list item is itself a dropped list item →
        restore its ``- `` marker (handles runs, scanning bottom-up);
      * a genuine lead-in sentence ending in ``:`` is left as prose, but a blank
        line is inserted so the following list still renders.

    This compensates for current Mistral OCR behaviour; revisit if the OCR model
    changes (see CLAUDE.md "OCR-model-dependent workarounds").
    """
    lines = md.split("\n")

    def _is_promotable(line: str) -> bool:
        s = line.strip()
        if not s or _LIST_RE.match(line):
            return False
        return not s.startswith(("#", "<", "![", "|"))

    # Pass 1: restore dropped markers (bottom-up so a run of bare lines above a
    # list all get promoted). A lead-in ending in ':' is prose, not an item.
    for i in range(len(lines) - 2, -1, -1):
        if _LIST_RE.match(lines[i + 1]) and _is_promotable(lines[i]) \
                and not lines[i].rstrip().endswith(":"):
            lines[i] = "- " + lines[i].lstrip()

    # Pass 2: a remaining prose line glued directly above a list needs a blank
    # line so pandoc lets the list start.
    out: list[str] = []
    for i, line in enumerate(lines):
        out.append(line)
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if line.strip() and not _LIST_RE.match(line) and _is_promotable(line) \
                and _LIST_RE.match(nxt):
            out.append("")
    return "\n".join(out)
# Mistral OCR emits table placeholders as a markdown link whose href is the
# table filename, e.g. [tbl-0.html](tbl-0.html) — NOT a #anchor. The negative
# lookbehind keeps image placeholders (![...](...)) from matching.
TABLE_RE = re.compile(r"(?<!!)\[([^\]]+\.html)\]\(([^)]*\.html)\)")


class AssembleError(Exception):
    pass


def assemble(doc: Doc, header_footer_policy: str = "keep_once", cover: str | None = None) -> str:
    # `cover` truthy means the cover is rendered separately (see render.py), so
    # page 0's reflowed body is omitted here; the image is NOT injected in the body.
    parts: list[str] = []

    for page in doc.pages:
        if cover and page.index == 0:
            continue
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
            tbl_id = m.group(1)
            if tbl_id not in table_ids:
                raise AssembleError(
                    f"page {page.index}: orphan table placeholder {tbl_id}"
                )
            return table_ids[tbl_id]

        body = TABLE_RE.sub(_resolve, page.markdown)
        body = _fix_glued_lists(body)
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
