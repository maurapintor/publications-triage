import responses

from tests.conftest import fixture_text
from triage.http import HttpClient
from triage.papers import Paper
from triage.sources.iris import IrisSource, harvest, load_export_csv, parse_oai_page

OAI = "https://iris.example/oai"


def recs():
    r, token = parse_oai_page(fixture_text("iris_oai.xml"))
    assert token == "TOKEN2"
    return r


def test_parse_skips_deleted():
    assert len(recs()) == 2


def test_doi_match(paper):
    r = IrisSource(recs()).check(paper)
    assert r.status == "present" and r.evidence == "doi"
    assert r.url == "https://hdl.handle.net/11584/1"
    assert r.fulltext and r.open_access and r.pdf_url.endswith("paper.pdf")


def test_title_only_match():
    p = Paper(id="x", title="Another Study on Pruning", authors=["Turing, Alan"])
    r = IrisSource(recs()).check(p)
    assert r.status == "present" and r.score == 1.0


def test_item_without_fulltext():
    p = Paper(id="x", title="Another Study on Pruning", authors=["Turing, Alan"])
    r = IrisSource(recs()).check(p)
    assert r.fulltext is None and r.open_access is False and r.pdf_url is None


def test_no_match():
    p = Paper(id="x", title="Completely Unrelated Quantum Gravity", authors=["Nobody, N"])
    assert IrisSource(recs()).check(p).status == "missing"


@responses.activate
def test_harvest_follows_resumption_token(tmp_path):
    responses.add(responses.GET, OAI + "?verb=ListRecords&metadataPrefix=oai_dc", body=fixture_text("iris_oai.xml"))
    responses.add(responses.GET, OAI + "?verb=ListRecords&resumptionToken=TOKEN2", body=fixture_text("iris_oai_page2.xml"))
    http = HttpClient("t@e.org", {"default": 0}, tmp_path, official_hosts=("iris.example",), sleep=lambda s: None)
    out = harvest(http, OAI)
    assert [r.title for r in out][-1] == "Page Two Paper" and len(out) == 3


def test_export_csv(tmp_path):
    f = tmp_path / "e.csv"
    f.write_text("title,authors,doi,url,pdf_url,access\nMy Paper,A B;C D,https://doi.org/10.1/X,https://h/1,https://h/1.pdf,embargoed\n",
                 encoding="utf-8")
    [r] = load_export_csv(str(f))
    assert r.dois == {"10.1/x"} and r.access == "embargoed" and r.fulltext
    res = IrisSource([r]).check(Paper(id="i", title="My Paper", doi="10.1/x"))
    assert res.status == "present" and res.pdf_url is None  # embargoed: no OA pdf


def _page(records, token=None, date="2026-11-12T07:00:00Z", deleted=(), size=None):
    recs = "".join(
        f"<record><header><identifier>{oid}</identifier></header><metadata><oai_dc:dc>"
        f"<dc:title>{title}</dc:title><dc:creator>Lovelace, Ada</dc:creator></oai_dc:dc></metadata></record>"
        for oid, title in records)
    dels = "".join(f'<record><header status="deleted"><identifier>{d}</identifier></header></record>' for d in deleted)
    tok = f'<resumptionToken completeListSize="{size or 0}">{token}</resumptionToken>' if token else ""
    return (f'<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/" xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/" '
            f'xmlns:dc="http://purl.org/dc/elements/1.1/"><responseDate>{date}</responseDate><ListRecords>{recs}{dels}{tok}'
            f"</ListRecords></OAI-PMH>")


def _http(tmp_path):
    return HttpClient("t@e.org", {"default": 0}, tmp_path / "http", official_hosts=("iris.example",), sleep=lambda s: None)


@responses.activate
def test_index_full_then_incremental(tmp_path):
    from triage.sources.iris import IrisIndex
    path = tmp_path / "iris" / "index.json.gz"
    responses.add(responses.GET, OAI + "?verb=ListRecords&metadataPrefix=oai_dc", body=_page([("o1", "One")], "T2"))
    responses.add(responses.GET, OAI + "?verb=ListRecords&resumptionToken=T2", body=_page([("o2", "Two")]))
    idx = IrisIndex(path, OAI)
    assert sorted(r.title for r in idx.update(_http(tmp_path))) == ["One", "Two"]
    assert IrisIndex.status(path) == {"exists": True, "records": 2, "harvested_until": "2026-11-12T07:00:00Z",
                                      "incomplete": False}
    responses.add(responses.GET, OAI + "?verb=ListRecords&metadataPrefix=oai_dc&from=2026-11-12",
                  body=_page([("o3", "Three"), ("o1", "One v2")], deleted=["o2"], date="2026-11-13T00:00:00Z"))
    idx2 = IrisIndex(path, OAI)
    assert sorted(r.title for r in idx2.update(_http(tmp_path))) == ["One v2", "Three"]
    assert IrisIndex(path, OAI).harvested_until == "2026-11-13T00:00:00Z"
    assert len(IrisIndex(path, "https://other/oai").records) == 0  # settings changed -> new index


@responses.activate
def test_index_resume_after_failure_and_expired_token(tmp_path, monkeypatch):
    import triage.sources.iris as iris
    from triage.sources.iris import IrisIndex
    monkeypatch.setattr(iris, "CHECKPOINT_EVERY", 1)
    path = tmp_path / "index.json.gz"
    responses.add(responses.GET, OAI + "?verb=ListRecords&metadataPrefix=oai_dc", body=_page([("o1", "One")], "T2"))
    responses.add(responses.GET, OAI + "?verb=ListRecords&resumptionToken=T2", status=503)
    import pytest
    with pytest.raises(RuntimeError, match="HTTP 503"):
        IrisIndex(path, OAI).update(_http(tmp_path))
    assert IrisIndex.status(path)["incomplete"] and IrisIndex.status(path)["records"] == 1
    responses.replace(responses.GET, OAI + "?verb=ListRecords&resumptionToken=T2",
                      body='<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><error code="badResumptionToken">x</error></OAI-PMH>')
    responses.replace(responses.GET, OAI + "?verb=ListRecords&metadataPrefix=oai_dc", body=_page([("o1", "One"), ("o2", "Two")]))
    out = IrisIndex(path, OAI).update(_http(tmp_path))
    assert sorted(r.title for r in out) == ["One", "Two"] and not IrisIndex.status(path)["incomplete"]


@responses.activate
def test_index_stop_and_source_error_cached(tmp_path):
    from triage.sources.iris import HarvestInterrupted, IrisIndex
    import pytest
    idx = IrisIndex(tmp_path / "i.json.gz", OAI)
    with pytest.raises(HarvestInterrupted):
        idx.update(_http(tmp_path), should_stop=lambda: True)
    calls = []

    def loader():
        calls.append(1)
        raise RuntimeError("down")

    src = IrisSource(loader=loader)
    for _ in range(3):
        with pytest.raises(RuntimeError):
            src.check(Paper(id="x", title="T"))
    assert len(calls) == 1


def test_result_carries_title_and_authors(paper):
    r = IrisSource(recs()).check(paper)
    assert r.title == "Robust Learning of Things" and r.authors == ["Lovelace, Ada"]
