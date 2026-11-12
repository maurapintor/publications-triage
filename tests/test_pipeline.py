from types import SimpleNamespace

from tests.conftest import make_pdf
from triage.papers import Paper
from triage.pipeline import run_pipeline
from triage.sources.base import SourceResult

PDF = make_pdf([["Body text that is long enough to be considered readable text."],
                ["Acknowledgements", "Supported by AURORA.", "References"]])


class FakeSource:
    name = "arxiv"

    def check(self, paper):
        return SourceResult("arxiv", "present", url="u", pdf_url="https://x/p.pdf")


class FakeHttp:
    def get(self, url, use_cache=True):
        return SimpleNamespace(status_code=200, content_type="application/pdf", content=PDF)


def _config(tmp_path, projects):
    return SimpleNamespace(cache_dir=str(tmp_path), pdf_priority=["arxiv"], projects=projects)


def test_ack_page_pdf_created(tmp_path, projects):
    [rec] = run_pipeline([Paper("p1", "T")], [FakeSource()], _config(tmp_path, projects), FakeHttp())
    assert rec.ack.matches == {"AURORA": "ack"} and rec.ack.ack_page == 2
    assert rec.ack_page_pdf and rec.ack_page_pdf.exists() and rec.ack_page_pdf.name == "p1_p2.pdf"


def test_no_download(tmp_path, projects):
    [rec] = run_pipeline([Paper("p1", "T")], [FakeSource()], _config(tmp_path, projects), FakeHttp(), download=False)
    assert rec.ack is None and rec.ack_page_pdf is None


def test_should_stop(tmp_path, projects):
    papers = [Paper(f"p{i}", "T") for i in range(5)]
    seen = []
    recs = run_pipeline(papers, [FakeSource()], _config(tmp_path, projects), FakeHttp(), download=False,
                        progress=lambda i, n, r: seen.append(i), should_stop=lambda: len(seen) >= 2)
    assert len(recs) == 2


class CountingSource:
    name = "arxiv"

    def __init__(self, http):
        self.http, self.fresh = http, []

    def check(self, paper):
        self.fresh.append((paper.id, self.http.refresh))
        return SourceResult("arxiv", "present", url="u", pdf_url="https://x/p.pdf", title="T", authors=["A B"])


class CountingHttp(FakeHttp):
    def __init__(self):
        self.refresh, self.downloads = False, 0

    def get(self, url, use_cache=True):
        self.downloads += 1
        return super().get(url, use_cache)


def test_cached_pdfs_reused_missing_retried(tmp_path, projects):
    http = CountingHttp()
    src = CountingSource(http)
    cfg = _config(tmp_path, projects)
    (tmp_path / "pdfs").mkdir()
    (tmp_path / "pdfs" / "cached.pdf").write_bytes(PDF)
    recs = run_pipeline([Paper("cached", "T"), Paper("new", "T")], [src], cfg, http)
    assert src.fresh == [("cached", False), ("new", True)]  # only the paper without a PDF is looked up fresh
    assert http.downloads == 1 and [r.pdf_cached for r in recs] == [True, False]
    assert http.refresh is False

    http2 = CountingHttp()
    src2 = CountingSource(http2)
    recs = run_pipeline([Paper("cached", "T"), Paper("new", "T")], [src2], cfg, http2, reload_pdfs=True)
    assert src2.fresh == [("cached", True), ("new", True)] and http2.downloads == 2
    assert [r.pdf_cached for r in recs] == [False, False]


def test_iris_vs_arxiv_on_record(tmp_path, projects):
    class Iris:
        name = "iris"

        def check(self, paper):
            return SourceResult("iris", "present", url="h", title="T", authors=["Other, X"])

    [rec] = run_pipeline([Paper("p", "T")], [Iris(), FakeSource()], _config(tmp_path, projects), FakeHttp(), download=False)
    assert rec.iris_vs_arxiv is None  # FakeSource has no title metadata
    [rec] = run_pipeline([Paper("p", "T")], [Iris(), CountingSource(CountingHttp())], _config(tmp_path, projects),
                         FakeHttp(), download=False)
    assert rec.iris_vs_arxiv["verdict"] == "authors differ" and "iris vs arxiv: authors differ" in rec.notes
