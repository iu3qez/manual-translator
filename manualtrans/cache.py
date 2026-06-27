from __future__ import annotations

import hashlib
from pathlib import Path


def file_hash(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class Cache:
    def __init__(self, cache_dir: str | Path):
        self.cache_dir = Path(cache_dir)

    def key(self, *parts: str) -> str:
        joined = "\x00".join(parts).encode("utf-8")
        return hashlib.sha256(joined).hexdigest()

    def _path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.txt"

    def get(self, key: str) -> str | None:
        p = self._path(key)
        if p.exists():
            return p.read_text(encoding="utf-8")
        return None

    def set(self, key: str, value: str) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._path(key).write_text(value, encoding="utf-8")
