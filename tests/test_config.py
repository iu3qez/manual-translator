from pathlib import Path

from manualtrans.config import Settings, get_settings


def test_env_parsing(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "mk")
    monkeypatch.setenv("OPENROUTER_API_KEY", "ok")
    monkeypatch.setenv("OPENROUTER_MODELS", "a/one, b/two ,c/three")
    monkeypatch.setenv("OUTPUT_FORMATS", "pdf, docx")
    monkeypatch.setenv("MODEL_ATTEMPTS", "3")
    s = Settings(_env_file=None)
    assert s.openrouter_models == ["a/one", "b/two", "c/three"]
    assert s.output_formats == ["pdf", "docx"]
    assert s.model_attempts == 3
    assert s.cache_dir == Path(".cache")


def test_defaults(monkeypatch):
    for k in ["MISTRAL_API_KEY", "OPENROUTER_API_KEY", "OPENROUTER_MODELS",
              "OUTPUT_FORMATS", "MODEL_ATTEMPTS", "OCR_MODEL", "HEADER_FOOTER_POLICY",
              "CACHE_DIR"]:
        monkeypatch.delenv(k, raising=False)
    s = Settings(_env_file=None)
    assert s.ocr_model == "mistral-ocr-2512"
    assert s.output_formats == ["pdf", "docx"]
    assert s.header_footer_policy == "keep_once"


def test_get_settings_overrides(monkeypatch):
    monkeypatch.delenv("OCR_MODEL", raising=False)
    s = get_settings(ocr_model="mistral-ocr-latest")
    assert s.ocr_model == "mistral-ocr-latest"
