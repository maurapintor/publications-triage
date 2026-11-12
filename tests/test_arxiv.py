import responses

from tests.conftest import fixture_text
from triage.http import HttpClient
from triage.sources.arxiv import ArxivSource

API = "https://export.arxiv.org/api/query"


def src(tmp_path):
    return ArxivSource(HttpClient("t@e.org", {"default": 0}, tmp_path, sleep=lambda s: None))


@responses.activate
def test_known_id_hit(tmp_path, paper):
    paper.arxiv_id = "2101.00001"
    responses.add(responses.GET, API, body=fixture_text("arxiv_hit.xml"))
    r = src(tmp_path).check(paper)
    assert r.status == "present" and r.evidence == "arxiv id"
    assert r.url == "https://arxiv.org/abs/2101.00001"
    assert r.pdf_url == "https://export.arxiv.org/pdf/2101.00001"
    assert "id_list=2101.00001" in responses.calls[0].request.url


@responses.activate
def test_title_hit(tmp_path, paper):
    responses.add(responses.GET, API, body=fixture_text("arxiv_hit.xml"))
    r = src(tmp_path).check(paper)
    assert r.status == "present" and r.score > 0.92
    assert "search_query=ti" in responses.calls[0].request.url


@responses.activate
def test_near_miss_is_uncertain(tmp_path, paper):
    responses.add(responses.GET, API, body=fixture_text("arxiv_near.xml"))
    assert src(tmp_path).check(paper).status == "uncertain"  # similar title, no shared author


@responses.activate
def test_no_hit(tmp_path, paper):
    responses.add(responses.GET, API, body=fixture_text("arxiv_empty.xml"))
    assert src(tmp_path).check(paper).status == "missing"


@responses.activate
def test_only_official_host_used(tmp_path, paper):
    responses.add(responses.GET, API, body=fixture_text("arxiv_hit.xml"))
    src(tmp_path).check(paper)
    assert all(c.request.url.startswith("https://export.arxiv.org/") for c in responses.calls)
