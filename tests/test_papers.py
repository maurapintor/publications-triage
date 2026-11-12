from triage.papers import load_papers, normalize_doi, normalize_title_key, parse_arxiv_id


def test_load_papers_file(tmp_path):
    import json
    f = tmp_path / "publications.json"
    f.write_text(json.dumps([
        {"title": "A", "authors": ["X, Y"], "year": "2024", "doi": "https://doi.org/10.1/A", "arxivId": "2401.00001",
         "effectiveType": "journal-article", "bibtex": "@article{k, volume = {1}}"},
        {"title": "A", "doi": "10.1/a"},                       # duplicate DOI -> dropped
        {"title": "No DOI paper", "arxivUrl": "https://arxiv.org/abs/2402.00002v2"},
        {"title": ""},                                          # no title -> skipped
    ]))
    papers = load_papers(f)
    assert [p.id for p in papers][0] == "10.1/a" and len(papers) == 2
    assert papers[0].type == "journal-article" and papers[0].arxiv_id == "2401.00001" and papers[0].bibtex
    assert papers[1].id.startswith("t-") and papers[1].arxiv_id == "2402.00002v2"


def test_normalize_doi():
    assert normalize_doi("https://doi.org/10.1000/ABC.1.") == "10.1000/abc.1"
    assert normalize_doi("DOI: 10.1000/abc") == "10.1000/abc"
    assert normalize_doi("  ") is None and normalize_doi(None) is None


def test_normalize_title():
    assert normalize_title_key("The {\\textbf{Robust}} $\\alpha$-Net: Learning!") == "the robust alpha net learning"
    assert normalize_title_key("A  B") == normalize_title_key("a b")


def test_parse_arxiv_id():
    assert parse_arxiv_id("arXiv:2101.00001v2") == "2101.00001v2"
    assert parse_arxiv_id("https://arxiv.org/abs/2101.00001") == "2101.00001"
    assert parse_arxiv_id("hello") is None and parse_arxiv_id("") is None
