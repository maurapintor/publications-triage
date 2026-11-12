from triage.matching import classify_title_match, shares_author, surname, title_score
from triage.papers import Paper
from triage.pipeline import check_paper
from triage.sources.base import SourceResult


def test_surnames():
    assert surname("Lovelace, Ada") == surname("Ada Lovelace") == "lovelace"
    assert surname("Ciná, Antonio Emanuele") == "cina"
    assert shares_author(["Turing, Alan"], ["Alan Turing", "X Y"])
    assert not shares_author(["A, B"], ["C D"])


def test_scores_and_classes():
    assert title_score("Robust {Learning}!", "robust learning") == 1.0
    assert title_score("", "x") == 0.0
    assert classify_title_match(0.95, True) == "present"
    assert classify_title_match(0.95, False) == "uncertain"
    assert classify_title_match(0.85, True) == "uncertain"
    assert classify_title_match(0.5, True) == "missing"


class Fake:
    def __init__(self, name, status=None, exc=None):
        self.name, self._s, self._e = name, status, exc

    def check(self, paper):
        if self._e:
            raise self._e
        return SourceResult(self.name, self._s)


def test_aggregation_and_error_isolation():
    res = check_paper(Paper(id="1", title="t"),
                      [Fake("iris", "missing"), Fake("arxiv", exc=RuntimeError("boom")), Fake("openalex", "present")])
    assert {k: v.status for k, v in res.items()} == {"iris": "missing", "arxiv": "error", "openalex": "present"}
    assert "boom" in res["arxiv"].evidence


def test_surname_formats():
    from triage.matching import surname
    assert surname("Lodi M. B.") == "lodi" and surname("Lovelace M.") == "lovelace" and surname("M. Lovelace") == "lovelace"
    assert surname("Lovelace, Ada") == surname("Ada Lovelace") == "lovelace"
    assert surname("De Luca, Antonio") == surname("Antonio De Luca") == "luca"
    assert surname("Gödel, Kurt") == "godel"
