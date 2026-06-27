from pathlib import Path

from manualtrans.cache import Cache, file_hash


def test_file_hash_stable(tmp_path: Path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello")
    assert file_hash(f) == file_hash(f)
    g = tmp_path / "b.bin"
    g.write_bytes(b"world")
    assert file_hash(f) != file_hash(g)


def test_key_depends_on_all_parts():
    c = Cache(Path("/tmp/unused"))
    assert c.key("h", "ocr3") == c.key("h", "ocr3")
    assert c.key("h", "ocr3") != c.key("h", "ocr4")


def test_get_miss_then_set_hit(tmp_path: Path):
    c = Cache(tmp_path / "cache")
    k = c.key("h", "translate", "page0")
    assert c.get(k) is None
    c.set(k, "tradotto")
    assert c.get(k) == "tradotto"
