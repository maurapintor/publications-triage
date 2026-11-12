import threading

import pytest

from tests.conftest import make_pdf
from triage.acknowledgements import AckRegion, AckResult
from triage.papers import Paper
from triage.pipeline import PaperRecord
from triage.report import load_json, write_json
from triage.sources.base import SourceResult
from triage.web import create_app

CONFIG = """
input_path: {d}/pubs.json
contact_email: t@e.org
cache_dir: {d}/cache
output_dir: {d}/out
sources: [arxiv]
projects: [AURORA, Data4AI4Data]
"""


@pytest.fixture
def setup(tmp_path, projects):
    (tmp_path / "c.yaml").write_text(CONFIG.format(d=tmp_path))
    pdf = tmp_path / "cache" / "pdfs" / "a.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(make_pdf([["Acknowledgements", "Funded by AURORA."]]))
    rec = PaperRecord(Paper("10.1/a", "Paper A", authors=["X, Y"], year="2024", doi="10.1/a"),
                      {"arxiv": SourceResult("arxiv", "present", url="https://arxiv.org/abs/1")},
                      pdf_path=pdf, pdf_source="arxiv",
                      ack=AckResult("ok", {"AURORA": "ack"}, [AckRegion(1, "heading", "Funded by AURORA.")], {"AURORA": 1}),
                      ack_page_pdf=pdf)
    rec2 = PaperRecord(Paper("t-2", "Paper B"), {"arxiv": SourceResult("arxiv", "missing")})
    write_json([rec, rec2], ["arxiv"], projects[:2], tmp_path / "out")
    return tmp_path


def client(tmp_path, run_fn=None):
    app = create_app(tmp_path / "c.yaml", run_fn=run_fn)
    app.testing = True
    return app, app.test_client()


def test_index_and_papers(setup):
    _, c = client(setup)
    assert b"Publications Triage" in c.get("/").data
    assert c.get("/static/app.js").status_code == 200
    data = c.get("/api/papers").get_json()
    assert [r["id"] for r in data["records"]] == ["10.1/a", "t-2"]
    assert data["columns"] == ["title", "year", "iris", "arxiv", "rank", "acks"]
    assert data["projects"] == ["AURORA", "Data4AI4Data"] and "ack_AURORA" in data["valid_columns"]


def test_papers_empty_when_no_run(tmp_path):
    (tmp_path / "c.yaml").write_text(CONFIG.format(d=tmp_path))
    _, c = client(tmp_path)
    assert c.get("/api/papers").get_json()["records"] == []


def test_pdf_serving_and_traversal(setup):
    _, c = client(setup)
    r = c.get("/pdf/10.1/a")
    assert r.status_code == 200 and r.data.startswith(b"%PDF") and r.mimetype == "application/pdf"
    assert c.get("/ackpage/10.1/a").status_code == 200
    assert c.get("/pdf/t-2").status_code == 404          # record without pdf
    assert c.get("/pdf/unknown").status_code == 404
    assert c.get("/pdf/..%2F..%2Fetc%2Fpasswd").status_code == 404
    assert c.get("/pdf/../../etc/passwd").status_code == 404


def test_run_lifecycle(setup):
    gate, reported = threading.Event(), threading.Event()
    calls = []

    def run_fn(options, progress, should_stop):
        calls.append(options)
        progress(1, 2, "Paper A")
        reported.set()
        gate.wait(5)
        return {"papers": 2, "pdfs": 1, "acknowledged": 1, "ack_pages": 1}

    app, c = client(setup, run_fn)
    r = c.post("/api/run", json={"limit": 3, "sources": ["arxiv"]})
    assert r.status_code == 202
    assert c.post("/api/run", json={}).status_code == 409
    assert c.put("/api/config", json={}).status_code == 409
    assert reported.wait(5)
    st = c.get("/api/status").get_json()
    assert st["state"] == "running" and st["current"] == "Paper A"
    gate.set()
    app.extensions["pubtriage_jobs"].wait(5)
    st = c.get("/api/status").get_json()
    assert st["state"] == "done" and st["summary"]["papers"] == 2
    assert calls == [{"limit": 3, "sources": ["arxiv"], "refresh_cache": False, "reload_pdfs": False,
                     "no_download": False}]


def test_run_bad_options(setup):
    _, c = client(setup, lambda *a: {})
    assert c.post("/api/run", json={"sources": ["iris"]}).status_code == 400  # not enabled in config
    assert c.post("/api/run", json={"limit": "x"}).status_code == 400


def test_cancel_and_failure(setup):
    def run_fn(options, progress, should_stop):
        for i in range(500):
            if should_stop():
                return {"papers": i}
            threading.Event().wait(0.01)
        return {}

    app, c = client(setup, run_fn)
    assert c.post("/api/run/cancel").status_code == 409
    c.post("/api/run", json={})
    assert c.post("/api/run/cancel").status_code == 200
    app.extensions["pubtriage_jobs"].wait(5)
    assert c.get("/api/status").get_json()["state"] == "cancelled"

    def boom(*a):
        raise RuntimeError("kaboom")

    app2, c2 = client(setup, boom)
    c2.post("/api/run", json={})
    app2.extensions["pubtriage_jobs"].wait(5)
    st = c2.get("/api/status").get_json()
    assert st["state"] == "failed" and "kaboom" in st["error"]


def test_config_get_put(setup):
    _, c = client(setup)
    body = c.get("/api/config").get_json()
    cfg = body["config"]
    assert cfg["projects"] == [{"name": "AURORA"}, {"name": "Data4AI4Data"}] and "arxiv" in body["valid_sources"]
    cfg["projects"].append({"name": "CoDesign", "aliases": ["Co-Design"]})
    cfg["dashboard"]["columns"] = ["title", "ack_CoDesign"]
    r = c.put("/api/config", json=cfg)
    assert r.status_code == 200 and r.get_json()["config"]["projects"][-1]["name"] == "CoDesign"
    assert c.get("/api/papers").get_json()["columns"] == ["title", "ack_CoDesign"]
    before = (setup / "c.yaml").read_text()
    cfg["sources"] = ["bogus"]
    r = c.put("/api/config", json=cfg)
    assert r.status_code == 400 and "unknown sources" in r.get_json()["error"]
    assert (setup / "c.yaml").read_text() == before
    assert c.put("/api/config", data="nope", content_type="text/plain").status_code == 400


def test_default_run_fn_end_to_end(setup, monkeypatch):
    """The real run function wires config -> execute_run -> latest.json."""
    import triage.cli as cli
    from triage.web import default_run_fn

    seen = {}

    def fake_execute(config, **kw):
        seen.update(kw, sources=config.sources)
        from types import SimpleNamespace
        return SimpleNamespace(records=[], csv_path="a.csv", json_path="a.json")

    monkeypatch.setattr(cli, "execute_run", fake_execute)
    out = default_run_fn(setup / "c.yaml")({"limit": 2, "sources": ["arxiv"]}, lambda *a: None, lambda: False)
    assert out["papers"] == 0 and seen["limit"] == 2 and seen["sources"] == ["arxiv"] and seen["download"] is True


def test_export(setup):
    _, c = client(setup)
    r = c.post("/api/export", json={"format": "bibtex"})
    assert r.status_code == 200 and r.headers["Content-Disposition"].endswith('papers.bib"')
    assert r.data.decode().count("@") == 2 and r.content_type.startswith("application/x-bibtex")
    r = c.post("/api/export", json={"format": "apa", "ids": ["10.1/a"], "download": False})
    assert "Content-Disposition" not in r.headers and r.data.decode().startswith("X, Y. (2024). Paper A.")
    assert c.post("/api/export", json={"format": "nope"}).status_code == 400
    assert c.post("/api/export", json={"format": "apa", "ids": ["unknown"]}).status_code == 400
    data = c.get("/api/papers").get_json()
    assert data["citation_formats"]["bibtex"] == "BibTeX" and data["iris"] is None  # iris not enabled


def test_validate_and_rankings(setup):
    _, c = client(setup)
    r = c.post("/api/validate", json={"id": "10.1/a", "rank": "A*", "note": "checked by hand"})
    assert r.status_code == 200 and r.get_json()["validation"] == {"rank": "A*", "note": "checked by hand"}
    rec = next(x for x in c.get("/api/papers").get_json()["records"] if x["id"] == "10.1/a")
    assert rec["ranking"]["status"] == "validated" and rec["ranking"]["top"] is True
    c.post("/api/validate", json={"id": "10.1/a", "iris_vs_arxiv": "accepted"})
    assert "iris_vs_arxiv: accepted" in (setup / "c.yaml").read_text()
    c.post("/api/validate", json={"id": "10.1/a", "rank": None, "note": None, "iris_vs_arxiv": None})
    assert "validations" not in (setup / "c.yaml").read_text()
    assert c.post("/api/validate", json={"id": "10.1/a", "rank": "Q9"}).status_code == 400
    assert c.post("/api/validate", json={}).status_code == 400


def test_exclude_from_table(setup):
    _, c = client(setup)
    r = c.post("/api/exclude", json={"title": "Paper B"})
    assert r.status_code == 200 and r.get_json()["exclude_titles"] == ["Paper B"]
    data = c.get("/api/papers").get_json()
    assert [x["id"] for x in data["records"]] == ["10.1/a"] and data["excluded_hidden"] == ["Paper B"]
    assert c.get("/api/config").get_json()["config"]["paper_list"]["exclude_titles"] == ["Paper B"]
    c.post("/api/exclude", json={"title": "Paper B", "exclude": False})
    assert len(c.get("/api/papers").get_json()["records"]) == 2
    assert c.post("/api/exclude", json={}).status_code == 400
    rl = c.get("/api/config").get_json()["rate_limits"]
    assert any(r["host"] == "export.arxiv.org" and r["default"] == 3.0 for r in rl)
