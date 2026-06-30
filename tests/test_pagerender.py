from pathlib import Path

from PIL import Image

from manualtrans.pagerender import build_pdftoppm_cmd, rasterize_pages, make_cover


def test_build_pdftoppm_cmd(tmp_path):
    cmd = build_pdftoppm_cmd(tmp_path / "in.pdf", 1, 720, 1018, tmp_path / "page-0")
    assert cmd[0] == "pdftoppm"
    assert "-singlefile" in cmd
    assert "-scale-to-x" in cmd and "720" in cmd
    assert "-scale-to-y" in cmd and "1018" in cmd


def test_rasterize_pages_uses_runner_and_skips_empty(tmp_path):
    calls = []

    class R:
        returncode = 0

    def runner(cmd, **k):
        calls.append(cmd)
        return R()

    out = rasterize_pages(tmp_path / "in.pdf", [(720, 1018), (None, None), (600, 800)],
                          tmp_path / "raster", runner=runner)
    assert set(out.keys()) == {0, 2}            # page 1 (None size) skipped
    assert out[0] == tmp_path / "raster" / "page-0.png"
    assert len(calls) == 2


def test_make_cover_adds_watermark(tmp_path):
    src = tmp_path / "p1.png"
    Image.new("RGB", (300, 400), (255, 255, 255)).save(src)
    out = make_cover(src, tmp_path / "cover.png")
    assert out.exists()
    cov = Image.open(out)
    assert cov.size == (300, 400)
    # watermark changed some pixels away from pure white
    assert any(px != (255, 255, 255) for px in cov.convert("RGB").getdata())
