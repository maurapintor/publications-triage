import json

import responses

from tests.conftest import make_pdf
from triage.cli import main
from triage.http import HttpClient
from triage.sources.arxiv import ArxivSource

ATOM = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/2101.00001v1</id>
<title>Robust Learning of Things</title><author><name>Ada Lovelace</name></author></entry></feed>"""
PDF = make_pdf([["Body text that is long enough to be considered readable text.", "Acknowledgements",
                 "Supported by ELSA.", "References"]])


@responses.activate
def test_cli_end_to_end_mocked(tmp_path, capsys):
    (tmp_path / "pubs.json").write_text(json.dumps([
        {"title": "Robust Learning of Things", "authors": ["Lovelace, Ada"], "year": "2021", "doi": "10.1000/abc.1"},
        {"title": "Unfindable Zebra Paper", "authors": ["Nobody, N"], "year": "2020", "doi": ""}]))
    (tmp_path / "c.yaml").write_text(f"""
input_path: {tmp_path}/pubs.json
contact_email: t@e.org
cache_dir: {tmp_path}/cache
output_dir: {tmp_path}/out
sources: [arxiv]
projects: [ELSA, Sec4AI4Sec]
""")

    def cb(request):
        body = ATOM if "Robust" in request.url else "<feed xmlns='http://www.w3.org/2005/Atom'/>"
        return 200, {}, body

    responses.add_callback(responses.GET, "https://export.arxiv.org/api/query", callback=cb)
    responses.add(responses.GET, "https://export.arxiv.org/pdf/2101.00001", body=PDF, content_type="application/pdf")
    http = HttpClient("t@e.org", {"default": 0}, tmp_path / "cache", sleep=lambda s: None)
    assert main(["run", "--config", str(tmp_path / "c.yaml")], http=http, sources=[ArxivSource(http)]) == 0
    out = capsys.readouterr().out
    assert "arxiv: missing=1, present=1" in out and "papers with an acknowledged project: 1" in out
    [csv_path] = (tmp_path / "out").glob("triage_*.csv")
    text = csv_path.read_text(encoding="utf-8-sig")
    assert "ack_ELSA" in text and ",Y," in text


def test_cli_config_error(tmp_path, capsys):
    assert main(["run", "--config", str(tmp_path / "missing.yaml")]) == 2
    assert "config error" in capsys.readouterr().err


@responses.activate
def test_cli_writes_json(tmp_path):
    from triage.report import load_json
    (tmp_path / "pubs.json").write_text(json.dumps([
        {"title": "Robust Learning of Things", "authors": ["Lovelace, Ada"], "year": "2021", "doi": "10.1000/abc.1"}]))
    (tmp_path / "c.yaml").write_text(f"""
input_path: {tmp_path}/pubs.json
contact_email: t@e.org
cache_dir: {tmp_path}/cache
output_dir: {tmp_path}/out
sources: [arxiv]
projects: [ELSA]
""")
    responses.add(responses.GET, "https://export.arxiv.org/api/query", body=ATOM)
    responses.add(responses.GET, "https://export.arxiv.org/pdf/2101.00001", body=PDF, content_type="application/pdf")
    http = HttpClient("t@e.org", {"default": 0}, tmp_path / "cache", sleep=lambda s: None)
    assert main(["run", "--config", str(tmp_path / "c.yaml")], http=http, sources=[ArxivSource(http)]) == 0
    data = load_json(tmp_path / "out" / "latest.json")
    [rec] = data["records"]
    assert rec["ack"]["acknowledged"] == ["ELSA"] and rec["ack"]["page"] == 1
    assert rec["ack_page_pdf"].endswith("10.1000_abc.1_p1.pdf")


def test_serve_refuses_remote_and_starts(tmp_path, capsys):
    (tmp_path / "c.yaml").write_text("input_path: p.json\ncontact_email: t@e.org\nsources: [arxiv]\nprojects: [ELSA]\n")
    assert main(["serve", "--config", str(tmp_path / "c.yaml"), "--host", "0.0.0.0"]) == 2
    assert "refusing" in capsys.readouterr().err
    started = []
    assert main(["serve", "--config", str(tmp_path / "c.yaml"), "--port", "9001"],
                app_runner=lambda app, h, p: started.append((h, p))) == 0
    assert started == [("127.0.0.1", 9001)]
    assert main(["serve", "--config", str(tmp_path / "c.yaml"), "--host", "0.0.0.0", "--allow-remote"],
                app_runner=lambda app, h, p: started.append((h, p))) == 0
    assert started[-1] == ("0.0.0.0", 8000)
