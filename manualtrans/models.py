from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class Image(BaseModel):
    id: str
    path: str


class Table(BaseModel):
    id: str
    html: str


class Block(BaseModel):
    type: str
    bbox: list[float]
    content: str | None = None


class Page(BaseModel):
    index: int
    markdown: str
    images: list[Image] = []
    tables: list[Table] = []
    header: str | None = None
    footer: str | None = None
    blocks: list[Block] = []
    width: float | None = None
    height: float | None = None
    dpi: float | None = None


class Doc(BaseModel):
    source_pdf: str
    source_hash: str
    ocr_model: str
    pages: list[Page]

    @classmethod
    def load(cls, path: str | Path) -> "Doc":
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    def dump(self, path: str | Path) -> None:
        Path(path).write_text(self.model_dump_json(indent=2), encoding="utf-8")
