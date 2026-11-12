import pytest

from triage.citations import FORMATS, format_citations, initials, parse_bibtex, split_name

REC = {
    "title": "Buffer-free class-incremental learning", "authors": ["Noether, Emmy", "Gödel, Kurt", "Turing, Alan"],
    "year": "2026", "venue": "Pattern Recognition", "doi": "10.1000/j.example.2025.112441", "arxiv_id": "2501.99999",
    "type": "journal-article",
    "bibtex": "@article{\n\t11584_000001,\n\tauthor = {X},\n\ttitle = {T},\n\tjournal = {PATTERN RECOGNITION},\n"
              "\tvolume = {172},\n\tnumber = {3},\n\tpages = {1--10},\n\tnote = {a {nested} value}\n}",
}
CONF = {"title": "Counterfeit answers", "authors": ["Babbage, Charles"], "year": "2026", "venue": "LNCS",
        "type": "conference-paper", "doi": None, "arxiv_id": "2512.04554",
        "bibtex": "@inproceedings{k, booktitle = {ICDAR 2026}, pages = {265--281}}"}
PREPRINT = {"title": "A preprint", "authors": ["Doe, Jane"], "year": "2025", "venue": "arXiv preprint",
            "type": "preprint", "arxiv_id": "2501.00001"}


def test_parse_bibtex_nested_and_key():
    etype, key, f = parse_bibtex(REC["bibtex"])
    assert (etype, key) == ("article", "11584_000001")
    assert f["volume"] == "172" and f["note"] == "a {nested} value" and f["pages"] == "1--10"
    assert parse_bibtex(None) == (None, None, {})


def test_names():
    assert split_name("Ciná, Antonio Emanuele") == ("Ciná", "Antonio Emanuele")
    assert split_name("Jean-Pierre Dupont") == ("Dupont", "Jean-Pierre")
    assert initials("Jean-Pierre") == "J.-P." and initials("Antonio Emanuele") == "A. E."


def test_bibtex_export():
    out = format_citations([REC, CONF, PREPRINT], "bibtex")
    assert out.count("@") == 3
    assert "@article{noether2026buffer," in out and "journal = {Pattern Recognition}" in out
    assert "11584_000001" not in out and "number = {3}" in out and "eprint = {2501.99999}" in out
    assert "@inproceedings{babbage2026counterfeit," in out and "booktitle = {ICDAR 2026}" in out
    assert "@misc{doe2025preprint," in out


def test_text_styles():
    apa = format_citations([REC], "apa")
    assert apa.startswith("Noether, E., Gödel, K., & Turing, A. (2026). Buffer-free class-incremental learning. "
                          "Pattern Recognition, 172(3), 1–10. https://doi.org/10.1000/j.example.2025.112441")
    assert format_citations([REC, CONF], "ieee").startswith('[1] E. Noether, K. Gödel, and A. Turing, "Buffer-free')
    assert "in ICDAR 2026, pp. 265–281, 2026." in format_citations([CONF], "ieee")
    assert format_citations([REC], "mla").startswith('Noether, Emmy, et al. "Buffer-free class-incremental learning."')
    assert "Kurt Gödel, and Alan Turing. 2026." in format_citations([REC], "chicago")
    assert "(2026) 'Buffer-free class-incremental learning', Pattern Recognition, 172(3), pp. 1–10." in format_citations([REC], "harvard")
    assert "arXiv preprint arXiv:2501.00001" in format_citations([PREPRINT], "apa")
    assert "https://arxiv.org/abs/2501.00001" in format_citations([PREPRINT], "apa")
    assert set(FORMATS) == {"bibtex", "apa", "ieee", "mla", "chicago", "harvard"}


def test_unknown_format():
    with pytest.raises(ValueError, match="unknown format"):
        format_citations([REC], "nope")
