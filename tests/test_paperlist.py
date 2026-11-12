import responses

from triage.config import load_config
from triage.http import HttpClient
from triage.papers import papers_from_records
from triage.paperlist import build_paper_list, fetch_arxiv_feed, write_paper_list

ORCID = "0000-0002-1825-0097"
CFG = f"""
contact_email: t@e.org
cache_dir: {{d}}/cache
output_dir: {{d}}/out
user: {{{{orcid: {ORCID}}}}}
paper_list: {{{{arxiv_author_id: lovelace_a_1, exclude_titles: [AISec]}}}}
sources: [arxiv]
projects: [AURORA]
"""
WORKS = {"group": [
    {"work-summary": [{"put-code": 1, "type": "journal-article", "title": {"title": {"value": "Robust Learning of Things"}},
                       "journal-title": {"value": "PATTERN RECOGNITION"}, "publication-date": {"year": {"value": "2026"}},
                       "external-ids": {"external-id": [{"external-id-type": "doi", "external-id-value": "10.1/ABC"}]}}]},
    {"work-summary": [{"put-code": 2, "type": "preprint", "title": {"title": {"value": "robust learning of things"}},
                       "external-ids": {"external-id": [{"external-id-type": "doi", "external-id-value": "10.48550/arXiv.2501.00001"}]}}]},
    {"work-summary": [{"put-code": 3, "type": "conference-paper", "title": {"title": {"value": "AISec"}}}]},
]}
CROSSREF = {"message": {"title": ["Robust learning of things"], "container-title": ["Pattern Recognition"],
                        "type": "journal-article", "author": [{"family": "Lovelace", "given": "Ada"}, {"family": "Turing", "given": "Alan"}],
                        "published-print": {"date-parts": [[2026, 1]]}}}
DETAIL1 = {"citation": {"citation-type": "bibtex",
                        "citation-value": "@article{11584_1, title = {Robust Learning of Things}, doi = {10.1/abc}, volume = {9}}"}}
FEED = """<feed xmlns="http://www.w3.org/2005/Atom">
<entry><id>http://arxiv.org/abs/2501.00001v2</id><title>Robust Learning of Things</title><author><name>Ada Lovelace, Alan Turing</name></author></entry>
<entry><id>http://arxiv.org/abs/2502.00002v1</id><title>A Preprint Only On arXiv</title><author><name>Ada Lovelace</name></author></entry>
</feed>"""


def setup(tmp_path):
    (tmp_path / "c.yaml").write_text(CFG.format(d=tmp_path))
    responses.add(responses.GET, f"https://pub.orcid.org/v3.0/{ORCID}/works", json=WORKS)
    responses.add(responses.GET, f"https://pub.orcid.org/v3.0/{ORCID}/work/1", json=DETAIL1)
    responses.add(responses.GET, f"https://pub.orcid.org/v3.0/{ORCID}/work/2", json={})
    responses.add(responses.GET, "https://api.crossref.org/works/10.1%2Fabc", json=CROSSREF)
    responses.add(responses.GET, "https://arxiv.org/robots.txt", body="User-agent: *\nCrawl-delay: 15\nDisallow: /api\n")
    responses.add(responses.GET, "https://arxiv.org/a/lovelace_a_1.atom2", body=FEED)
    return load_config(tmp_path / "c.yaml"), HttpClient("t@e.org", {"default": 0}, tmp_path / "http",
                                                        sleep=lambda s: None)


@responses.activate
def test_build_list(tmp_path):
    config, h = setup(tmp_path)
    recs = build_paper_list(config, h)
    assert [r["title"] for r in recs] == ["Robust Learning of Things", "A Preprint Only On arXiv"]
    pub, pre = recs
    assert pub["effectiveType"] == "journal-article" and pub["venue"] == "Pattern Recognition"
    assert pub["authors"] == ["Lovelace, Ada", "Turing, Alan"] and pub["doi"] == "10.1/abc"
    assert pub["arxivId"] == "2501.00001" and pub["bibtex"].startswith("@article{11584_1")
    assert pre["effectiveType"] == "preprint" and pre["authors"] == ["Ada Lovelace"] and pre["year"] == "2025"
    crossref = next(c for c in responses.calls if "crossref" in c.request.url)
    assert "mailto=t%40e.org" in crossref.request.url
    path = write_paper_list(recs, config.output_dir)
    papers = papers_from_records(recs)
    assert path.exists() and papers[0].type == "journal-article" and papers[0].arxiv_id == "2501.00001"


@responses.activate
def test_feed_failure_is_not_fatal(tmp_path):
    config, h = setup(tmp_path)
    responses.replace(responses.GET, "https://arxiv.org/a/lovelace_a_1.atom2", status=503)
    recs = build_paper_list(config, h)
    assert [r["title"] for r in recs] == ["Robust Learning of Things"]


@responses.activate
def test_feed_parse(tmp_path):
    _config, h = setup(tmp_path)
    feed = fetch_arxiv_feed(h, "lovelace_a_1")
    assert feed[0] == {"arxiv_id": "2501.00001", "title": "Robust Learning of Things",
                       "authors": ["Ada Lovelace", "Alan Turing"]}


@responses.activate
def test_run_uses_built_list(tmp_path):
    from triage.cli import main
    config, h = setup(tmp_path)
    responses.add(responses.GET, "https://export.arxiv.org/api/query", body="<feed xmlns='http://www.w3.org/2005/Atom'/>")
    assert main(["run", "--config", str(tmp_path / "c.yaml"), "--no-download"], http=h,
                sources=[__import__("triage.sources.arxiv", fromlist=["ArxivSource"]).ArxivSource(h)]) == 0
    assert (tmp_path / "out" / "publications.json").exists()


def test_clean_title_and_exclusion():
    from triage.paperlist import _excluded, clean_title
    assert clean_title("\\textbackslash sigma-zero: Optimization of \\textbackslash ell\\_0-norm Examples") == \
        "σ-zero: Optimization of ℓ0-norm Examples"
    assert clean_title("$\\sigma$-zero: x") == "σ-zero: x"
    assert _excluded("AISec '25: 18th ACM Workshop", ["AISec"]) and not _excluded("Robust things", ["AISec"])


def test_dedup_key_latex_unicode():
    from triage.paperlist import _tkey, clean_title
    assert clean_title("$\\ell_0$-norm attacks") == "ℓ0-norm attacks"
    assert _tkey("σ-zero: Optimization of ℓ0-norm") == _tkey("$\\sigma$-zero: Optimization of $\\ell_0$-norm") \
        == _tkey("\\textbackslash sigma-zero: Optimization of \\textbackslash ell\\_0-norm")
