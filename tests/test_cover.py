from pypdf import PdfReader
import io

from triage.config import CoverConfig
from triage.cover import cover_fields, render_cover

REC = {
    "id": "10.1000/x.1", "title": "Robust things", "authors": ["Lovelace, Ada", "Turing, Alan"], "year": "2024",
    "venue": "Information Sciences", "doi": "10.1000/x.1",
    "bibtex": "@article{k, volume = {9}, pages = {1--10}}",
    "sources": {"iris": {"status": "present", "title": "Robust Things & More", "authors": ["Lovelace, Ada", "Turing, Alan"],
                         "meta": {"journal": "INFORMATION SCIENCES", "volume": "654", "firstpage": "119838",
                                  "lastpage": "119850", "year": "2023"}}},
}


def text_of(pdf: bytes) -> str:
    return " ".join((PdfReader(io.BytesIO(pdf)).pages[0].extract_text() or "").split())


def test_fields_prefer_iris_record():
    f = cover_fields(REC)
    assert f["reference"] == ("<b>Lovelace, Ada; Turing, Alan <i>Robust Things &amp; More</i>, Information Sciences, "
                              "vol. 654, 2023, pagg. 119838-119850.</b>")
    assert f["doi"] == "10.1000/x.1" and f["year"] == "2023"


def test_fields_fallback_without_iris():
    rec = {**REC, "sources": {"iris": {"status": "missing"}}}
    f = cover_fields(rec)
    assert "<i>Robust things</i>, Information Sciences, vol. 9, 2024, pagg. 1-10." in f["reference"]


def test_render_without_assets(tmp_path):
    pdf = render_cover(cover_fields(REC), CoverConfig(assets_dir=str(tmp_path / "none")), "https://iris.example.it")
    assert pdf.startswith(b"%PDF") and len(PdfReader(io.BytesIO(pdf)).pages) == 1
    t = text_of(pdf)
    assert "accepted manuscript version of the following contribution" in t
    assert "Robust Things & More" in t and "https://dx.doi.org/10.1000/x.1" in t
    assert "UNICA IRIS https://iris.example.it" in t and "©2023. This manuscript version" in t


def test_render_with_images(tmp_path):
    from PIL import Image
    assets = tmp_path / "cover"
    assets.mkdir()
    Image.new("RGB", (40, 10), "navy").save(assets / "logo.png")
    Image.new("RGB", (40, 10), "white").save(assets / "header.png")
    cfg = CoverConfig(assets_dir=str(assets), repository_name="My IRIS", license_text="CC-BY 4.0")
    t = text_of(render_cover(cover_fields(REC), cfg, None))
    assert "©2023. CC-BY 4.0" in t and "downloaded from" not in t


def test_cover_endpoint(tmp_path):
    from tests.test_web import CONFIG
    from triage.pipeline import PaperRecord
    from triage.papers import Paper
    from triage.report import write_json
    from triage.sources.base import SourceResult
    from triage.web import create_app
    (tmp_path / "c.yaml").write_text(CONFIG.format(d=tmp_path))
    rec = PaperRecord(Paper("10.1/a", "Paper A", authors=["Lovelace, Ada"], year="2024", doi="10.1/a"),
                      {"arxiv": SourceResult("arxiv", "present")})
    from triage.config import Project
    write_json([rec], ["arxiv"], [Project("AURORA")], tmp_path / "out")
    c = create_app(tmp_path / "c.yaml").test_client()
    r = c.get("/cover/10.1/a")
    assert r.status_code == 200 and r.data.startswith(b"%PDF")
    assert r.headers["Content-Disposition"] == 'attachment; filename="cover_10.1_a.pdf"'
    assert c.get("/cover/unknown").status_code == 404


def test_ieee_notice(tmp_path):
    from triage.cover import is_ieee
    ieee = {**REC, "doi": "10.1109/TIFS.2026.1", "sources": {"iris": {"status": "missing"}}, "venue": "Some Journal"}
    f = cover_fields(ieee)
    assert f["ieee"] is True
    t = text_of(render_cover(f, CoverConfig(assets_dir=str(tmp_path)), None))
    assert "© 2024 IEEE. Personal use of this material is permitted." in t and "reuse of any copyrighted" in t
    assert not cover_fields(REC)["ieee"]
    assert "IEEE. Personal use" not in text_of(render_cover(cover_fields(REC), CoverConfig(assets_dir=str(tmp_path)), None))
    assert is_ieee({"doi": "10.1000/x", "venue": "2024 IEEE Symposium on Security and Privacy"}, {}, {})
    assert is_ieee({"doi": "10.1000/x"}, {"publisher": "Institute of Electrical and Electronics Engineers Inc."}, {})
    assert not is_ieee({"doi": "10.1000/x", "venue": "Pattern Recognition"}, {}, {"publisher": "Elsevier"})
