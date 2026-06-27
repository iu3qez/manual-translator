from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel


class Glossary(BaseModel):
    do_not_translate: dict = {}
    preferred: dict[str, str] = {}
    header_footer_policy: str = "keep_once"

    @classmethod
    def load(cls, path: str | Path) -> "Glossary":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls.model_validate(data)

    def render(self) -> str:
        lines: list[str] = []
        acronyms = self.do_not_translate.get("acronyms", [])
        if acronyms:
            lines.append("NON tradurre questi acronimi: " + ", ".join(acronyms))
        patterns = self.do_not_translate.get("patterns", [])
        if patterns:
            lines.append("NON tradurre i token che corrispondono a questi pattern regex:")
            lines.extend(f"  - {p}" for p in patterns)
        if self.preferred:
            lines.append("Traduzioni preferite (term -> IT):")
            lines.extend(f"  - {k} -> {v}" for k, v in self.preferred.items())
        return "\n".join(lines)
