from pathlib import Path

from manualtrans.glossary import Glossary

YAML = """
do_not_translate:
  acronyms: [SSB, CW, VFO]
  patterns:
    - '\\\\b\\\\d+\\\\s?(Hz|MHz)\\\\b'
preferred:
  squelch: squelch
  channel: canale
header_footer_policy: keep_once
"""


def test_load_and_fields(tmp_path: Path):
    p = tmp_path / "glossary.yaml"
    p.write_text(YAML, encoding="utf-8")
    g = Glossary.load(p)
    assert "SSB" in g.do_not_translate["acronyms"]
    assert g.preferred["channel"] == "canale"
    assert g.header_footer_policy == "keep_once"


def test_render_contains_terms(tmp_path: Path):
    p = tmp_path / "glossary.yaml"
    p.write_text(YAML, encoding="utf-8")
    rendered = Glossary.load(p).render()
    assert "SSB" in rendered
    assert "squelch" in rendered
    assert "canale" in rendered
