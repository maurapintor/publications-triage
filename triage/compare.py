"""Compare the IRIS entry of a paper with its arXiv entry (title words + author list).

Case, accents and punctuation are ignored: only different words in the title, or different / missing /
reordered authors, count as differences. The result carries the details needed to show *where* they differ.
"""
from __future__ import annotations

import difflib
import re
import unicodedata

from .matching import same_author, surname
from .papers import normalize_title_key


_SPECIAL = {"ℓ": "ell", "∞": "infty"}


def _spell(text: str) -> str:
    """'σ' -> 'sigma', 'ℓ' -> 'ell'; '\\textbackslash sigma' / '$\\sigma$' -> 'sigma' (LaTeX vs Unicode)."""
    out = []
    for ch in text:
        if ch in _SPECIAL:
            out.append(" " + _SPECIAL[ch] + " ")
        elif "GREEK" in unicodedata.name(ch, ""):
            out.append(" " + unicodedata.name(ch).split()[-1].lower() + " ")
        else:
            out.append(ch)
    t = "".join(out)
    t = re.sub(r"\\textbackslash\s*", "", t)
    t = re.sub(r"\\([A-Za-z]+)", r" \1 ", t)  # \sigma -> sigma, \ell -> ell
    return t


def _words(title: str) -> list[tuple[str, str]]:
    """(display word, normalized key) for every word; words that are only markup/punctuation are dropped."""
    out = []
    for w in re.split(r"[\s\-–—/]+", title or ""):
        if not w:
            continue
        key = re.sub(r"[^a-z0-9]", "", normalize_title_key(_spell(w)))
        if key:
            out.append((w, key))
    return out


def title_diff(ref: str, other: str) -> list[dict]:
    """Word-level diff: [{op: equal|replace|delete|insert, arxiv: '...', iris: '...'}]."""
    wa, wb = _words(ref), _words(other)
    a, ka = [w for w, _ in wa], [k for _, k in wa]
    b, kb = [w for w, _ in wb], [k for _, k in wb]
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=ka, b=kb, autojunk=False).get_opcodes():
        out.append({"op": op, "arxiv": " ".join(a[i1:i2]), "iris": " ".join(b[j1:j2])})
    # drop empty tokens produced by pure punctuation words
    return [d for d in out if d["arxiv"] or d["iris"]]


def _match_order(ref: list[str], other: list[str]) -> list[int | None]:
    """For each ref author, the index of the same author in `other` (greedy, each used once)."""
    used: set[int] = set()
    out: list[int | None] = []
    for a in ref:
        j = next((k for k, b in enumerate(other) if k not in used and surname(a) == surname(b)), None)
        if j is None:
            j = next((k for k, b in enumerate(other) if k not in used and same_author(a, b)), None)
        if j is not None:
            used.add(j)
        out.append(j)
    return out


def _in_order(seq: list[int]) -> set[int]:
    """Indexes (into seq) of a longest increasing subsequence: the authors whose relative order is kept."""
    import bisect
    tails, tails_idx, prev = [], [], [-1] * len(seq)
    for k, v in enumerate(seq):
        pos = bisect.bisect_left(tails, v)
        if pos == len(tails):
            tails.append(v)
            tails_idx.append(k)
        else:
            tails[pos], tails_idx[pos] = v, k
        prev[k] = tails_idx[pos - 1] if pos else -1
    keep, k = set(), tails_idx[-1] if tails_idx else -1
    while k != -1:
        keep.add(k)
        k = prev[k]
    return keep


def author_rows(ref: list[str], other: list[str], order: list[int | None]) -> list[dict]:
    """Aligned rows: {arxiv, iris, arxiv_pos, iris_pos, status: same|moved|missing_in_iris|extra_in_iris}.

    'moved' = relative order changed (an author inserted or missing does not move the others)."""
    pairs = [(i, j) for i, j in enumerate(order) if j is not None]
    kept = {pairs[k][0] for k in _in_order([j for _i, j in pairs])}
    rows = []
    for i, (a, j) in enumerate(zip(ref, order)):
        if j is None:
            rows.append({"arxiv": a, "iris": None, "arxiv_pos": i + 1, "iris_pos": None, "status": "missing_in_iris"})
        else:
            rows.append({"arxiv": a, "iris": other[j], "arxiv_pos": i + 1, "iris_pos": j + 1,
                         "status": "same" if i in kept else "moved"})
    matched = {j for j in order if j is not None}
    for k, b in enumerate(other):
        if k not in matched:
            rows.append({"arxiv": None, "iris": b, "arxiv_pos": None, "iris_pos": k + 1, "status": "extra_in_iris"})
    return rows


def compare_entries(ref_title, ref_authors, other_title, other_authors) -> dict:
    """ref = arXiv, other = IRIS."""
    ref_a, oth_a = list(ref_authors or []), list(other_authors or [])
    tdiff = title_diff(ref_title or "", other_title or "")
    title_same = all(d["op"] == "equal" for d in tdiff)
    order = _match_order(ref_a, oth_a)
    rows = author_rows(ref_a, oth_a, order)
    missing = [r["arxiv"] for r in rows if r["status"] == "missing_in_iris"]
    extra = [r["iris"] for r in rows if r["status"] == "extra_in_iris"]
    authors_same = not missing and not extra
    order_same = authors_same and all(r["status"] == "same" for r in rows)
    if title_same and authors_same:
        verdict = "same" if order_same else "author order differs"
    elif title_same:
        verdict = "authors differ"
    elif authors_same:
        verdict = "title differs"
    else:
        verdict = "title and authors differ"
    changed_words = [d for d in tdiff if d["op"] != "equal"]
    return {
        "verdict": verdict, "title_same": title_same, "authors_same": authors_same, "order_same": order_same,
        "title_diff": tdiff, "title_changes": len(changed_words),
        "author_rows": rows, "missing_in_iris": missing, "extra_in_iris": extra,
        "moved": [r["arxiv"] for r in rows if r["status"] == "moved"],
        "iris_title": other_title, "arxiv_title": ref_title,
        "iris_authors": oth_a, "arxiv_authors": ref_a,
    }


def iris_vs_arxiv_from(sources: dict) -> dict | None:
    """Same as iris_vs_arxiv but on the JSON form of the results (dicts)."""
    i, a = (sources or {}).get("iris"), (sources or {}).get("arxiv")
    ok = ("present", "uncertain")
    if not (i and a and i.get("status") in ok and a.get("status") in ok and i.get("title") and a.get("title")):
        return None
    return compare_entries(a["title"], a.get("authors"), i["title"], i.get("authors"))


def iris_vs_arxiv(results: dict) -> dict | None:
    """None unless both IRIS and arXiv found an entry (present or uncertain) with metadata."""
    i, a = results.get("iris"), results.get("arxiv")
    ok = ("present", "uncertain")
    if not (i and a and i.status in ok and a.status in ok and i.title and a.title):
        return None
    return compare_entries(a.title, a.authors, i.title, i.authors)
