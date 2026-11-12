from triage.compare import compare_entries, iris_vs_arxiv
from triage.sources.base import SourceResult


def test_same_with_name_formats():
    c = compare_entries("Robust Learning of Things", ["Ada Lovelace", "Alan Turing"],
                        "Robust learning of things", ["Lovelace, Ada", "Turing, Alan"])
    assert c["verdict"] == "same" and c["title_same"] and c["authors_same"]


def test_author_differences_and_order():
    c = compare_entries("T", ["Ada Lovelace", "Alan Turing", "Grace Hopper"], "T", ["Turing, Alan", "Lovelace, Ada"])
    assert c["verdict"] == "authors differ" and c["missing_in_iris"] == ["Grace Hopper"] and c["extra_in_iris"] == []
    c = compare_entries("T", ["Ada Lovelace", "Alan Turing"], "T", ["Turing, B.", "Lovelace, M."])
    assert c["verdict"] == "author order differs"


def test_title_differs():
    c = compare_entries("A study of robust learning", ["Ada Lovelace"], "Completely different paper", ["Lovelace, Ada"])
    assert c["verdict"] == "title and authors differ" or c["verdict"] == "title differs"
    assert not c["title_same"]


def test_iris_vs_arxiv_needs_both():
    a = SourceResult("arxiv", "present", title="T", authors=["Ada Lovelace"])
    i = SourceResult("iris", "present", title="T", authors=["Lovelace, A."])
    assert iris_vs_arxiv({"arxiv": a, "iris": i})["verdict"] == "same"
    assert iris_vs_arxiv({"arxiv": a, "iris": SourceResult("iris", "missing")}) is None
    assert iris_vs_arxiv({"arxiv": a}) is None


def test_iris_last_first_without_comma():
    arx = ["Ada Lovelace", "Grace Hopper", "Norbert Wiener", "Alan Turing"]
    iris = ["Lovelace Ada", "Hopper Grace", "Wiener Norbert", "Turing Alan"]
    c = compare_entries("Fast minimum-norm attacks", arx, "Fast minimum-norm attacks", iris)
    assert c["verdict"] == "same", c
    c = compare_entries("T", arx, "T", list(reversed(iris)))
    assert c["verdict"] == "author order differs"


def test_shares_author_last_first():
    from triage.matching import shares_author
    assert shares_author(["Ada Lovelace"], ["Lovelace Ada"]) and not shares_author(["Ada Lovelace"], ["Rossi Mario"])


def test_case_and_punctuation_ignored_words_shown():
    c = compare_entries("Buffer-Free Class-Incremental Learning: A Study", ["Ada Lovelace"],
                        "buffer free class incremental learning - a study", ["Lovelace, Ada"])
    assert c["verdict"] == "same" and c["title_changes"] == 0
    c = compare_entries("Robust learning of things", ["Ada Lovelace"], "Robust training of things", ["Lovelace, Ada"])
    assert c["verdict"] == "title differs"
    assert [d for d in c["title_diff"] if d["op"] != "equal"] == [{"op": "replace", "arxiv": "learning", "iris": "training"}]
    c = compare_entries("Robust learning", ["Ada Lovelace"], "Robust learning of things", ["Lovelace, Ada"])
    assert {"op": "insert", "arxiv": "", "iris": "of things"} in c["title_diff"]


def test_author_rows():
    c = compare_entries("T", ["Ada Lovelace", "Grace Hopper", "Alan Turing"], "T",
                        ["Lovelace M.", "Turing B.", "Hopper F.", "Curie A."])
    st = [(r["arxiv"], r["iris"], r["status"]) for r in c["author_rows"]]
    assert st == [("Ada Lovelace", "Lovelace M.", "same"), ("Grace Hopper", "Hopper F.", "moved"),
                  ("Alan Turing", "Turing B.", "same"), (None, "Curie A.", "extra_in_iris")]
    assert c["verdict"] == "authors differ" and c["moved"] == ["Grace Hopper"]


def test_inserted_author_does_not_move_others():
    c = compare_entries("T", ["Ada Lovelace", "Alan Turing", "Grace Hopper"], "T",
                        ["Lovelace A.", "Lovelace M.", "Turing A.", "Hopper G."])
    assert [r["status"] for r in c["author_rows"]] == ["same", "same", "same", "extra_in_iris"]
    assert c["verdict"] == "authors differ" and c["moved"] == []


def test_latex_vs_unicode_title():
    c = compare_entries("$σ$-zero: Gradient-based Optimization of $\\ell_0$-norm Adversarial Examples", ["Ada Lovelace"],
                        "\\textbackslash sigma-zero: Gradient-based Optimization of \\textbackslash ell\\_0-norm "
                        "Adversarial Examples", ["Lovelace, Ada"])
    assert c["verdict"] == "same", [d for d in c["title_diff"] if d["op"] != "equal"]
