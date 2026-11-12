import responses

from triage.http import HttpClient
from triage.papers import Paper
from triage.sources.oa import OpenAlexSource, UnpaywallSource


def http(tmp_path):
    return HttpClient("t@e.org", {"default": 0}, tmp_path, sleep=lambda s: None)


P = Paper(id="d", title="T", doi="10.1000/abc.1")


@responses.activate
def test_openalex_oa_pdf(tmp_path):
    responses.add(responses.GET, "https://api.openalex.org/works/doi:10.1000/abc.1", json={
        "id": "https://openalex.org/W1", "open_access": {"is_oa": True},
        "best_oa_location": {"pdf_url": "https://repo/x.pdf", "landing_page_url": "https://repo/x"}})
    r = OpenAlexSource(http(tmp_path), "t@e.org").check(P)
    assert r.status == "present" and r.pdf_url == "https://repo/x.pdf"
    assert "mailto=t%40e.org" in responses.calls[0].request.url


@responses.activate
def test_openalex_no_oa_and_unknown(tmp_path):
    responses.add(responses.GET, "https://api.openalex.org/works/doi:10.1000/abc.1",
                  json={"id": "W", "open_access": {"is_oa": False}, "best_oa_location": None})
    s = OpenAlexSource(http(tmp_path), "t@e.org")
    assert s.check(P).status == "missing"
    responses.replace(responses.GET, "https://api.openalex.org/works/doi:10.1000/abc.1", status=404)
    r = OpenAlexSource(http(tmp_path / "2"), "t@e.org").check(P)
    assert r.status == "missing" and r.evidence == "unknown doi"


@responses.activate
def test_unpaywall(tmp_path):
    responses.add(responses.GET, "https://api.unpaywall.org/v2/10.1000/abc.1", json={
        "is_oa": True, "doi": "10.1000/abc.1",
        "best_oa_location": {"url_for_pdf": "https://repo/y.pdf", "url": "https://repo/y", "host_type": "repository"}})
    r = UnpaywallSource(http(tmp_path), "t@e.org").check(P)
    assert r.status == "present" and r.pdf_url == "https://repo/y.pdf"


def test_no_doi(tmp_path):
    r = UnpaywallSource(http(tmp_path), "t@e.org").check(Paper(id="x", title="T"))
    assert r.status == "missing" and r.evidence == "no doi"
