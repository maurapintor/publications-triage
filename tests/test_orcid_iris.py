import json

import pytest
import responses

from triage.http import HttpClient
from triage.papers import Paper
from triage.sources.iris import IrisSource, fetch_records_by_handle, oai_identifier
from triage.sources.orcid import fetch_work_handles

OAI = "https://iris.example/oai"
WORKS = {"group": [
    {"external-ids": {"external-id": [{"external-id-type": "doi", "external-id-value": "10.1/a"},
                                      {"external-id-type": "handle", "external-id-value": "11584/1"}]}},
    {"external-ids": {"external-id": [{"external-id-type": "handle", "external-id-value": "https://hdl.handle.net/11584/2"}]}},
    {"external-ids": {"external-id": [{"external-id-type": "handle", "external-id-value": "11584/1"}]}},
    {"external-ids": {"external-id": [{"external-id-type": "doi", "external-id-value": "10.1/c"}]}},
]}


def record(handle, title, creators):
    cs = "".join(f"<dc:creator>{c}</dc:creator>" for c in creators)
    return (f'<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/" xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/" '
            f'xmlns:dc="http://purl.org/dc/elements/1.1/"><GetRecord><record><header><identifier>oai:iris.example:{handle}'
            f"</identifier></header><metadata><oai_dc:dc><dc:title>{title}</dc:title>{cs}"
            f"<dc:identifier>https://hdl.handle.net/{handle}</dc:identifier></oai_dc:dc></metadata></record></GetRecord></OAI-PMH>")


def http(tmp_path):
    return HttpClient("t@e.org", {"default": 0}, tmp_path, official_hosts=("iris.example", "pub.orcid.org"),
                      sleep=lambda s: None)


@responses.activate
def test_orcid_handles(tmp_path):
    responses.add(responses.GET, "https://pub.orcid.org/v3.0/0000-0002-1825-0097/works", json=WORKS)
    assert fetch_work_handles(http(tmp_path), "0000-0002-1825-0097") == ["11584/1", "11584/2"]
    assert responses.calls[0].request.headers["Accept"] == "application/json"


@responses.activate
def test_orcid_not_found(tmp_path):
    responses.add(responses.GET, "https://pub.orcid.org/v3.0/0000-0000-0000-0000/works", status=404)
    with pytest.raises(RuntimeError, match="not found"):
        fetch_work_handles(http(tmp_path), "0000-0000-0000-0000")


@responses.activate
def test_get_records_by_handle(tmp_path):
    assert oai_identifier(OAI, "11584/1") == "oai:iris.example:11584/1"
    responses.add(responses.GET, OAI, match=[responses.matchers.query_param_matcher(
        {"verb": "GetRecord", "metadataPrefix": "oai_dc", "identifier": "oai:iris.example:11584/1"})],
        body=record("11584/1", "Robust Learning of Things", ["Lovelace M."]))
    responses.add(responses.GET, OAI, match=[responses.matchers.query_param_matcher(
        {"verb": "GetRecord", "metadataPrefix": "oai_dc", "identifier": "oai:iris.example:11584/2"})],
        body='<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><error code="idDoesNotExist">no</error></OAI-PMH>')
    h = http(tmp_path)
    recs = fetch_records_by_handle(h, OAI, ["11584/1", "11584/2"])
    assert [r.title for r in recs] == ["Robust Learning of Things"] and recs[0].url == "https://hdl.handle.net/11584/1"
    fetch_records_by_handle(h, OAI, ["11584/1"])
    assert len(responses.calls) == 2  # second fetch of 11584/1 served from the HTTP cache

    src = IrisSource(recs, missing_evidence="not among the IRIS records linked to ORCID X")
    hit = src.check(Paper("p", "Robust learning of things", authors=["Ada Lovelace"]))
    assert hit.status == "present" and hit.authors == ["Lovelace M."]
    miss = src.check(Paper("q", "Totally unrelated work on volcanoes", authors=["Someone Else"]))
    assert miss.status == "missing" and "linked to ORCID X" in miss.evidence


def test_bulk_source_not_refreshed_by_retries(tmp_path, projects):
    from types import SimpleNamespace

    from triage.pipeline import run_pipeline
    from triage.sources.base import SourceResult

    seen = []

    class Bulk:
        name, bulk = "iris", True

        def check(self, paper):
            seen.append(("iris", h.refresh))
            return SourceResult("iris", "missing")

    class PerPaper:
        name = "arxiv"

        def check(self, paper):
            seen.append(("arxiv", h.refresh))
            return SourceResult("arxiv", "missing")

    h = SimpleNamespace(refresh=False)
    cfg = SimpleNamespace(cache_dir=str(tmp_path), pdf_priority=["arxiv"], projects=projects)
    run_pipeline([Paper("p", "T")], [Bulk(), PerPaper()], cfg, h)  # no cached PDF -> retry
    assert seen == [("iris", False), ("arxiv", True)]
    seen.clear()
    run_pipeline([Paper("p", "T")], [Bulk(), PerPaper()], cfg, h, reload_pdfs=True)
    assert seen == [("iris", True), ("arxiv", True)] and h.refresh is False


ORE = """<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><GetRecord><record><metadata>
<atom:entry xmlns:atom="http://www.w3.org/2005/Atom">
<atom:link rel="alternate" href="https://hdl.handle.net/11584/444025"/>
<atom:link rel="http://www.openarchives.org/ore/terms/aggregates" href="https://iris.example/bitstream/11584/444025/1/paper.pdf" title="paper.pdf" type="application/pdf" length="1151534"/>
<atom:link rel="http://www.openarchives.org/ore/terms/aggregates" href="https://iris.example/bitstream/11584/444025/2/data.zip" type="application/zip"/>
</atom:entry></metadata></record></GetRecord></OAI-PMH>"""


def test_parse_ore():
    from triage.sources.iris import handle_from_url, parse_ore_pdf_links
    assert parse_ore_pdf_links(ORE) == ["https://iris.example/bitstream/11584/444025/1/paper.pdf"]
    assert parse_ore_pdf_links('<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><error code="idDoesNotExist"/></OAI-PMH>') == []
    assert handle_from_url("http://hdl.handle.net/11584/317333") == "11584/317333"


@responses.activate
def test_iris_pdf_fallback_only_when_needed(tmp_path, paper):
    from tests.conftest import make_pdf
    from triage.pdfs import retrieve_pdf
    from triage.sources.base import SourceResult

    pdf = make_pdf([["Body text that is long enough to be readable."]])
    responses.add(responses.GET, OAI, body=ORE, match=[responses.matchers.query_param_matcher(
        {"verb": "GetRecord", "metadataPrefix": "ore", "identifier": "oai:iris.example:11584/444025"})])
    responses.add(responses.GET, "https://iris.example/bitstream/11584/444025/1/paper.pdf", body=pdf,
                  content_type="application/pdf")
    responses.add(responses.GET, "https://export.arxiv.org/pdf/1", body=pdf, content_type="application/pdf")
    h = http(tmp_path)
    src = IrisSource([], http=h, oai_url=OAI)
    iris = SourceResult("iris", "present", url="https://hdl.handle.net/11584/444025", open_access=True)
    arxiv = SourceResult("arxiv", "present", pdf_url="https://export.arxiv.org/pdf/1")
    prio = ["arxiv", "iris"]

    out = retrieve_pdf(paper, {"iris": iris, "arxiv": arxiv}, prio, h, tmp_path / "a", resolvers={"iris": src.pdf_urls})
    assert out.source == "arxiv" and not any("iris.example" in c.request.url for c in responses.calls)

    out = retrieve_pdf(paper, {"iris": iris, "arxiv": SourceResult("arxiv", "missing")}, prio, h, tmp_path / "b",
                       resolvers={"iris": src.pdf_urls})
    assert out.source == "iris" and out.path.read_bytes().startswith(b"%PDF")

    closed = SourceResult("iris", "present", url="https://hdl.handle.net/11584/444025", open_access=False)
    n = len(responses.calls)
    out = retrieve_pdf(paper, {"iris": closed}, prio, h, tmp_path / "c", resolvers={"iris": src.pdf_urls})
    assert out.path is None and len(responses.calls) == n  # embargoed/closed: not even looked up


@responses.activate
def test_iris_login_page_rejected(tmp_path, paper):
    from triage.pdfs import retrieve_pdf
    from triage.sources.base import SourceResult
    responses.add(responses.GET, OAI, body=ORE)
    responses.add(responses.GET, "https://iris.example/bitstream/11584/444025/1/paper.pdf",
                  body="<html>login required</html>", content_type="text/html")
    h = http(tmp_path)
    src = IrisSource([], http=h, oai_url=OAI)
    r = SourceResult("iris", "present", url="https://hdl.handle.net/11584/444025", open_access=None)
    out = retrieve_pdf(paper, {"iris": r}, ["iris"], h, tmp_path / "d", resolvers={"iris": src.pdf_urls})
    assert out.path is None and "got HTML" in out.notes[-1]
