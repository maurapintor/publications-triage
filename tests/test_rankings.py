from types import SimpleNamespace

import pytest

from triage.config import ConfigError, RankingsConfig, _parse_rankings, _parse_validations
from triage.rankings import CS_CATEGORIES, acronyms, load_core_csv, load_sjr_csv, load_tables, rank_record

SJR = (
    "Rank;Sourceid;Title;Type;Issn;SJR;SJR Best Quartile;H index;Categories;Areas\n"
    '1;24823;Pattern Recognition;journal;"00313203, 18735142";"2,7";Q1;250;'
    '"Artificial Intelligence (Q1); Computer Vision and Pattern Recognition (Q1); Signal Processing (Q1)";Computer Science\n'
    '2;111;Some Medical Journal;journal;"12345678";"3,1";Q1;90;"Medicine (miscellaneous) (Q1); Software (Q3)";Medicine\n'
    '3;222;Journal of Systems and Software;journal;"01641212";"1,2";Q1;120;"Hardware and Architecture (Q2); Software (Q1)";Computer Science\n'
)
CORE = (
    '"1","International Conference on Document Analysis and Recognition","ICDAR","ICORE2023","A","Yes","4603","No","4"\n'
    '"2","IEEE Symposium on Security and Privacy","S&P","ICORE2023","A*","Yes","4604","No","4"\n'
    '"3","International Conference on Machine Learning and Cybernetics","ICMLC","ICORE2023","C","Yes","4611","No","4"\n'
    '"4","Computer Vision and Pattern Recognition","CVPR","ICORE2023","A*","Yes","4603","No","4"\n'
)


@pytest.fixture
def rcfg(tmp_path):
    (tmp_path / "sjr2024.csv").write_text(SJR, encoding="utf-8")
    (tmp_path / "core.csv").write_text(CORE, encoding="utf-8")
    return RankingsConfig(core_csv=str(tmp_path / "core.csv"), sjr_csv=str(tmp_path / "sjr2024.csv"),
                          sjr_categories=list(CS_CATEGORIES))


def test_parsers(rcfg):
    [pr, med, jss] = load_sjr_csv(rcfg.sjr_csv)
    assert pr.issns == {"00313203", "18735142"} and pr.categories["Signal Processing"] == "Q1"
    confs = load_core_csv(rcfg.core_csv)
    assert [(c.acronym, c.rank) for c in confs][:2] == [("ICDAR", "A"), ("S&P", "A*")]


def test_acronyms():
    assert acronyms("Document Analysis and Recognition – ICDAR 2026. Proceedings, Part IV") == ["ICDAR"]
    assert acronyms("Proceedings of CVPR2024 (IEEE)") == ["CVPR"]


def test_journal_ranking(rcfg):
    t = load_tables(rcfg)
    rec = {"type": "journal-article", "venue": "Pattern Recognition", "bibtex": "@article{k, issn = {0031-3203}}"}
    r = rank_record(rec, t, rcfg)
    assert (r["rank"], r["top"], r["status"]) == ("Q1", True, "matched") and "ISSN 00313203" in r["evidence"]
    r = rank_record({"type": "journal-article", "venue": "journal of systems and software"}, t, rcfg)
    assert r["rank"] == "Q1" and "Software" in r["evidence"]
    r = rank_record({"type": "journal-article", "venue": "Some Medical Journal"}, t, rcfg)
    assert r["rank"] == "Q3" and r["top"] is False  # Q1 only outside computer science
    narrow = RankingsConfig(sjr_csv=rcfg.sjr_csv, sjr_categories=["Hardware and Architecture"])
    assert rank_record({"type": "journal-article", "venue": "Journal of Systems and Software"},
                       load_tables(narrow), narrow)["rank"] == "Q2"
    assert rank_record({"type": "journal-article", "venue": "Unknown Journal"}, t, rcfg)["status"] == "not_found"


def test_conference_ranking(rcfg):
    t = load_tables(rcfg)
    rec = {"type": "conference-paper", "venue": "Lecture Notes in Computer Science",
           "bibtex": "@inproceedings{k, booktitle = {Document Analysis and Recognition – ICDAR 2026. Proceedings, Part IV}}"}
    r = rank_record(rec, t, rcfg)
    assert (r["rank"], r["top"], r["status"], r["evidence"]) == ("A", False, "matched", "acronym ICDAR")
    r = rank_record({"type": "conference-paper", "venue": "2025 International Conference on Machine Learning and Cybernetics"}, t, rcfg)
    assert r["rank"] == "C" and r["status"] == "matched"
    r = rank_record({"type": "conference-paper", "venue": "Proceedings of the 2025 IEEE/CVF CVPR"}, t, rcfg)
    assert r["rank"] == "A*" and r["top"] is True
    assert rank_record({"type": "preprint", "venue": "arXiv"}, t, rcfg)["status"] == "not_applicable"


def test_manual_and_validation_win(rcfg):
    rcfg.venues = {"ICDAR": "A*", "Pattern Recognition": "Q2"}
    t = load_tables(rcfg)
    rec = {"type": "conference-paper", "venue": "ICDAR 2026"}
    assert rank_record(rec, t, rcfg)["status"] == "manual" and rank_record(rec, t, rcfg)["rank"] == "A*"
    r = rank_record(rec, t, rcfg, {"rank": "B", "note": "checked on CORE portal"})
    assert (r["rank"], r["status"], r["evidence"], r["top"]) == ("B", "validated", "checked on CORE portal", False)


def test_no_files():
    cfg = RankingsConfig(sjr_categories=list(CS_CATEGORIES))
    t = load_tables(cfg)
    assert rank_record({"type": "journal-article", "venue": "X"}, t, cfg)["status"] == "no_data"
    missing = RankingsConfig(sjr_csv="/nope.csv", sjr_categories=list(CS_CATEGORIES))
    assert "file not found" in load_tables(missing).errors[0]


def test_config_validation():
    assert _parse_rankings(None).top_core == ["A*"] and "Software" in _parse_rankings(None).sjr_categories
    with pytest.raises(ConfigError, match="valid ranks"):
        _parse_rankings({"venues": {"X": "Q9"}})
    with pytest.raises(ConfigError, match="top_core"):
        _parse_rankings({"top_core": ["Z"]})
    assert _parse_validations({"10.1/a": {"iris_vs_arxiv": "accepted", "note": ""}}) == {"10.1/a": {"iris_vs_arxiv": "accepted"}}
    with pytest.raises(ConfigError, match="unknown keys"):
        _parse_validations({"10.1/a": {"bogus": 1}})
    with pytest.raises(ConfigError, match="must be 'accepted'"):
        _parse_validations({"10.1/a": {"iris_vs_arxiv": "yes"}})


def test_download_settings_validation():
    r = _parse_rankings({"core_source": "CORE2021", "sjr_year": 2024})
    assert (r.core_source, r.sjr_area, r.sjr_year) == ("CORE2021", "1700", "2024")
    with pytest.raises(ConfigError, match="core_source"):
        _parse_rankings({"core_source": "latest"})
    with pytest.raises(ConfigError, match="sjr_year"):
        _parse_rankings({"sjr_year": "last"})


def test_no_subset_false_match(tmp_path):
    core = ('"1","Usenix Network and Distributed System Security Symposium","NDSS","ICORE2026","A*","Yes","4604","",""\n'
            '"2","Usenix Security Symposium","USENIX-Security","ICORE2026","A*","Yes","4604","",""\n'
            '"3","National Conference of the American Association for Artificial Intelligence","AAAI","ICORE2026","A*","Yes","4602","",""\n'
            '"4","International Joint Conference on Artificial Intelligence","IJCAI","ICORE2026","A*","Yes","4602","",""\n')
    (tmp_path / "core.csv").write_text(core)
    cfg = RankingsConfig(core_csv=str(tmp_path / "core.csv"), sjr_categories=list(CS_CATEGORIES))
    t = load_tables(cfg)
    r = rank_record({"type": "conference-paper", "venue": "28th USENIX Security Symposium"}, t, cfg)
    assert r["matched"].startswith("Usenix Security Symposium"), r
    r = rank_record({"type": "conference-paper", "venue": "Proceedings of the AAAI Conference on Artificial Intelligence"}, t, cfg)
    assert r["evidence"] == "acronym AAAI" and "American Association" in r["matched"]


def test_ampersand_journal(tmp_path):
    sjr = ("Rank;Sourceid;Title;Issn;SJR Best Quartile;Categories\n"
           '1;1;Computers and Security;"01674048";Q1;"Computer Science (miscellaneous) (Q1); Law (Q1)"\n')
    (tmp_path / "s.csv").write_text(sjr)
    cfg = RankingsConfig(sjr_csv=str(tmp_path / "s.csv"), sjr_categories=list(CS_CATEGORIES))
    r = rank_record({"type": "journal-article", "venue": "COMPUTERS & SECURITY"}, load_tables(cfg), cfg)
    assert (r["rank"], r["status"]) == ("Q1", "matched")
