from manualtrans.prompt import SYSTEM_PROMPT_TEMPLATE, build_system_prompt


def test_template_has_glossary_slot():
    assert "{glossary}" in SYSTEM_PROMPT_TEMPLATE


def test_build_injects_glossary():
    out = build_system_prompt("ACRONIMI: SSB")
    assert "ACRONIMI: SSB" in out
    assert "{glossary}" not in out
    # Core rules are present
    assert "BYTE-PER-BYTE" in out
    assert "markdown" in out.lower()
