import responses

from tests.conftest import make_pdf
from triage.http import HttpClient
from triage.pdfs import candidates, retrieve_pdf
from triage.sources.base import SourceResult

PDF = make_pdf([["hello"]])


def res(**kw):
    return {k: SourceResult(k, v[0], pdf_url=v[1]) for k, v in kw.items()}


def http(tmp_path):
    return HttpClient("t@e.org", {"default": 0}, tmp_path / "c", official_hosts=("a.example", "b.example"),
                      sleep=lambda s: None)


def test_priority_and_only_present():
    r = res(arxiv=("present", "u1"), iris=("present", "u2"), unpaywall=("uncertain", "u3"), openalex=("missing", None))
    assert list(candidates(r, ["iris", "arxiv", "unpaywall", "openalex"])) == [("iris", "u2"), ("arxiv", "u1")]


@responses.activate
def test_download_first_by_priority_and_cached(tmp_path, paper):
    responses.add(responses.GET, "https://a.example/i.pdf", body=PDF, content_type="application/pdf")
    responses.add(responses.GET, "https://b.example/a.pdf", body=PDF, content_type="application/pdf")
    r = res(iris=("present", "https://a.example/i.pdf"), arxiv=("present", "https://b.example/a.pdf"))
    out = retrieve_pdf(paper, r, ["iris", "arxiv"], http(tmp_path), tmp_path / "pdfs")
    assert out.source == "iris" and out.path.read_bytes() == PDF
    assert len(responses.calls) == 1
    out2 = retrieve_pdf(paper, r, ["iris", "arxiv"], http(tmp_path), tmp_path / "pdfs")
    assert out2.source == "iris" and len(responses.calls) == 1  # not re-downloaded


@responses.activate
def test_html_rejected_then_fallback(tmp_path, paper):
    responses.add(responses.GET, "https://a.example/i.pdf", body="<html>login</html>", content_type="text/html")
    responses.add(responses.GET, "https://b.example/a.pdf", body=PDF, content_type="application/pdf")
    r = res(iris=("present", "https://a.example/i.pdf"), arxiv=("present", "https://b.example/a.pdf"))
    out = retrieve_pdf(paper, r, ["iris", "arxiv"], http(tmp_path), tmp_path / "pdfs")
    assert out.source == "arxiv" and "HTML" in out.notes[0]


@responses.activate
def test_only_html_gives_no_pdf(tmp_path, paper):
    responses.add(responses.GET, "https://a.example/i.pdf", body="<html/>", content_type="application/pdf")  # bad magic
    out = retrieve_pdf(paper, res(iris=("present", "https://a.example/i.pdf")), ["iris"], http(tmp_path), tmp_path / "pdfs")
    assert out.path is None and "%PDF" in out.notes[0]
    assert not (tmp_path / "pdfs").exists() or not list((tmp_path / "pdfs").glob("*.pdf"))


def test_force_redownload_keeps_cache_on_failure(tmp_path, paper):
    from types import SimpleNamespace

    from triage.pdfs import retrieve_pdf
    from triage.sources.base import SourceResult
    results = {"arxiv": SourceResult("arxiv", "present", pdf_url="https://x/p.pdf")}
    target = tmp_path / "10.1000_abc.1.pdf"
    target.write_bytes(b"%PDF old")

    class Http:
        def __init__(self, resp):
            self.resp, self.calls = resp, 0

        def get(self, url, use_cache=True):
            self.calls += 1
            return self.resp

    bad = Http(SimpleNamespace(status_code=500, content_type="text/html", content=b""))
    out = retrieve_pdf(paper, results, ["arxiv"], bad, tmp_path, force=True)
    assert bad.calls == 1 and out.cached and out.path == target and "kept the cached PDF" in out.notes[-1]
    good = Http(SimpleNamespace(status_code=200, content_type="application/pdf", content=b"%PDF new"))
    out = retrieve_pdf(paper, results, ["arxiv"], good, tmp_path, force=True)
    assert not out.cached and target.read_bytes() == b"%PDF new"
    assert retrieve_pdf(paper, results, ["arxiv"], good, tmp_path).cached and good.calls == 1
