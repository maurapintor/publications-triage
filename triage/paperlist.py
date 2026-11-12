"""Build the user's paper list at the start of each run (no input file needed).

Sources (same approach as the bib2md / ORCID pipeline, re-implemented here):
- ORCID public API (pub.orcid.org): the works of `user.orcid` (fresh every run) and each work's detail,
  for the BibTeX that IRIS/publishers push to ORCID (cached on disk);
- Crossref (api.crossref.org, polite pool with mailto): authors, venue, year, type for works with a DOI
  (cached on disk);
- optionally the arXiv author feed `arxiv.org/a/<paper_list.arxiv_author_id>` (one request per run,
  robots.txt checked): arXiv ids for the ORCID works, plus arXiv-only preprints missing from ORCID.

The result is written to output/publications.json and has the fields load_papers() reads.
"""
from __future__ import annotations

import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

from .citations import parse_bibtex
from .papers import normalize_doi, normalize_title_key, parse_arxiv_id

log = logging.getLogger("triage")

ORCID_API = "https://pub.orcid.org/v3.0"
CROSSREF_API = "https://api.crossref.org/works"
ARXIV_FEED = "https://arxiv.org/a/{id}.atom2"
ATOM = {"a": "http://www.w3.org/2005/Atom"}

_CROSSREF_TYPE = {"proceedings-article": "conference-paper", "posted-content": "preprint",
                  "journal-article": "journal-article", "book-chapter": "book-chapter", "book": "book"}


# ---------- helpers ----------
def _value(d, *path):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def _ext_ids(obj) -> list[dict]:
    return (_value(obj, "external-ids", "external-id") or []) if obj else []


def _doi(ext: list[dict]) -> str | None:
    for e in ext:
        if (e.get("external-id-type") or "").lower() == "doi":
            return normalize_doi(e.get("external-id-value"))
    return None


def _arxiv_from_doi(doi: str | None) -> str | None:
    m = re.match(r"^10\.48550/arxiv\.(.+)$", doi or "", re.I)
    return parse_arxiv_id(m.group(1)) if m else None


def _arxiv_id(ext: list[dict], doi: str | None, *urls: str) -> str | None:
    if _arxiv_from_doi(doi):
        return _arxiv_from_doi(doi)
    for e in ext:
        t = (e.get("external-id-type") or "").lower()
        v = e.get("external-id-value") or ""
        if t == "arxiv" and parse_arxiv_id(v):
            return parse_arxiv_id(v)
        if t == "doi" and _arxiv_from_doi(v):
            return _arxiv_from_doi(v)
        u = _value(e, "external-id-url", "value") or ""
        if "arxiv.org" in u and parse_arxiv_id(u):
            return parse_arxiv_id(u)
    for u in urls:
        if u and "arxiv.org" in u and parse_arxiv_id(u):
            return parse_arxiv_id(u)
    return None


def _year_from_arxiv(aid: str | None) -> str:
    m = re.match(r"^(\d{2})(\d{2})\.", aid or "")
    if not m:
        return ""
    yy = int(m.group(1))
    return str((2000 if yy <= time.localtime().tm_year % 100 + 1 else 1900) + yy)


def _crossref_meta(msg: dict | None) -> dict:
    if not msg:
        return {}
    year = ""
    for f in ("published-print", "published-online", "published", "issued", "created"):
        dp = _value(msg, f, "date-parts") or [[]]
        if dp and dp[0] and dp[0][0]:
            year = str(dp[0][0])
            break
    authors = []
    for a in msg.get("author") or []:
        fam, giv = (a.get("family") or "").strip(), (a.get("given") or "").strip()
        if fam:
            authors.append(f"{fam}, {giv}" if giv else fam)
        elif a.get("name"):
            authors.append(a["name"])
    return {"title": (msg.get("title") or [""])[0], "venue": (msg.get("container-title") or [""])[0],
            "authors": authors, "year": year, "type": msg.get("type") or "", "publisher": msg.get("publisher") or ""}


def _bibtex_matches(bibtex: str, doi: str | None, title: str) -> bool:
    _t, _k, f = parse_bibtex(bibtex)
    if doi and normalize_doi(f.get("doi")) == doi:
        return True
    return bool(title) and normalize_title_key(f.get("title", "")) == normalize_title_key(title)


def _excluded(title: str, excluded: list[str]) -> bool:
    """Case-insensitive substring match, as in the ORCID/bib2md pipeline ('AISec' also drops 'AISec '25: ...')."""
    t = (title or "").lower()
    return any(e.strip().lower() in t for e in excluded if e.strip())


_LATEX_UNICODE = {"alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "lambda": "λ",
                  "mu": "μ", "pi": "π", "theta": "θ", "sigma": "σ", "ell": "ℓ"}


def clean_title(text: str) -> str:
    """LaTeX markup in a title -> readable Unicode ('\\textbackslash sigma-zero' / '$\\sigma$-zero' -> 'σ-zero')."""
    t = text or ""
    t = re.sub(r"\\textbackslash\s*", "\\\\", t)
    t = re.sub(r"\\text\w+\{([^}]*)\}", r"\1", t)
    for cmd, ch in _LATEX_UNICODE.items():
        t = re.sub(rf"\\{cmd}(?![A-Za-z])", ch, t)  # also '\\ell_0' (no word boundary before '_')
    t = re.sub(r"[\\{}$^]", "", t).replace("_", "")
    return re.sub(r"\s+", " ", t).strip()


def _tkey(title: str) -> str:
    """Dedup key: Unicode symbols and their LaTeX names compare equal ('ℓ0' = '\\ell_0' = 'ell0')."""
    t = clean_title(title)
    for cmd, ch in _LATEX_UNICODE.items():
        t = t.replace(ch, f" {cmd} ")
    return normalize_title_key(t).replace(" ", "")


def _contributors(detail: dict | None) -> list[str]:
    out = []
    for c in _value(detail, "contributors", "contributor") or []:
        name = (_value(c, "credit-name", "value") or "").strip()
        if name:
            out.append(name)
    return out


# ---------- fetchers ----------
def fetch_orcid_summaries(http, orcid: str) -> list[dict]:
    r = http.get(f"{ORCID_API}/{orcid}/works", use_cache=False, headers={"Accept": "application/json"})
    if r.status_code == 404:
        raise RuntimeError(f"ORCID iD {orcid} not found")
    if r.status_code != 200:
        raise RuntimeError(f"ORCID API returned HTTP {r.status_code}")
    return [g["work-summary"][0] for g in r.json().get("group", []) if g.get("work-summary")]


def fetch_orcid_detail(http, orcid: str, put_code) -> dict | None:
    r = http.get(f"{ORCID_API}/{orcid}/work/{put_code}", headers={"Accept": "application/json"})
    return r.json() if r.status_code == 200 else None


def fetch_crossref(http, doi: str, email: str) -> dict | None:
    r = http.get(f"{CROSSREF_API}/{quote(doi, safe='')}", params={"mailto": email} if email else None)
    if r.status_code != 200:
        return None
    try:
        return r.json().get("message")
    except ValueError:
        return None


def fetch_arxiv_feed(http, author_id: str) -> list[dict]:
    """[{arxiv_id, title, authors}] from arxiv.org/a/<id>.atom2 (robots.txt checked by the client)."""
    r = http.get(ARXIV_FEED.format(id=author_id), use_cache=False)
    if r.status_code != 200:
        raise RuntimeError(f"arXiv author feed returned HTTP {r.status_code}")
    out = []
    for e in ET.fromstring(r.text).findall("a:entry", ATOM):
        title = " ".join((e.findtext("a:title", "", ATOM) or "").split())
        aid = parse_arxiv_id(re.sub(r"v\d+$", "", (e.findtext("a:id", "", ATOM) or "").strip()))
        authors = [(a.findtext("a:name", "", ATOM) or "").strip() for a in e.findall("a:author", ATOM)]
        if len(authors) == 1 and "," in authors[0]:  # atom2 feeds put all authors in one element
            authors = [x.strip() for x in authors[0].split(",") if x.strip()]
        if title and aid:
            out.append({"arxiv_id": aid, "title": title, "authors": [a for a in authors if a]})
    return out


# ---------- main ----------
def build_paper_list(config, http, should_stop=None) -> list[dict]:
    pl, orcid = config.paper_list, config.user.orcid
    summaries = fetch_orcid_summaries(http, orcid)
    log.info("paper list: ORCID %s has %d works", orcid, len(summaries))
    feed = []
    if pl.arxiv_author_id:
        try:
            feed = fetch_arxiv_feed(http, pl.arxiv_author_id)
            log.info("paper list: arXiv author feed %s has %d papers", pl.arxiv_author_id, len(feed))
        except Exception as exc:  # noqa: BLE001 - the feed is optional
            log.warning("paper list: arXiv author feed failed (%s); continuing without it", exc)
    feed_by_title = {_tkey(f["title"]): f for f in feed}
    used_feed: set[str] = set()

    best: dict[str, dict] = {}
    for i, s in enumerate(summaries, 1):
        if should_stop and should_stop():
            raise RuntimeError("stopped while building the paper list")
        raw_title = (_value(s, "title", "title", "value") or "").strip()
        if not raw_title or _excluded(raw_title, pl.exclude_titles):
            continue
        title = clean_title(raw_title)
        ext = _ext_ids(s)
        doi = _doi(ext)
        raw_type = s.get("type") or "other"
        url = _value(s, "url", "value") or ""
        aid = _arxiv_id(ext, doi, url)
        tkey = _tkey(title)
        if not aid and tkey in feed_by_title:
            aid = feed_by_title[tkey]["arxiv_id"]
        if tkey in feed_by_title:
            used_feed.add(tkey)
        cr = _crossref_meta(fetch_crossref(http, doi, config.contact_email)) if doi and not _arxiv_from_doi(doi) else {}
        detail = fetch_orcid_detail(http, orcid, s.get("put-code")) if s.get("put-code") else None
        bib = (_value(detail, "citation", "citation-value") or "").strip()
        if (_value(detail, "citation", "citation-type") or "").lower() != "bibtex" or not bib.startswith("@") \
                or not (_bibtex_matches(bib, doi, title) or _bibtex_matches(bib, doi, raw_title)):
            bib = ""
        _bt, _bk, bf = parse_bibtex(bib)
        eff = _CROSSREF_TYPE.get(cr.get("type", ""), raw_type)
        if _arxiv_from_doi(doi) or (raw_type in ("preprint", "other", "working-paper") and aid and not cr):
            eff = "preprint"
        venue = (_value(s, "journal-title", "value") or "").strip()
        if cr.get("venue") and (eff == "journal-article" or not venue):
            venue = cr["venue"]
        venue = venue or bf.get("journal") or bf.get("booktitle") or ("arXiv preprint" if eff == "preprint" else "")
        authors = cr.get("authors") or [a.strip() for a in bf.get("author", "").split(" and ") if a.strip()]
        if not authors and tkey in feed_by_title:
            authors = feed_by_title[tkey]["authors"]
        if not authors:
            authors = _contributors(detail)
        if any(m in raw_title for m in ("\\", "$")) and cr.get("title"):
            title = clean_title(cr["title"])
        year = (_value(s, "publication-date", "year", "value") or cr.get("year") or bf.get("year")
                or _year_from_arxiv(aid) or "")
        rec = {"title": title, "authors": authors, "year": str(year), "venue": venue, "rawType": raw_type,
               "effectiveType": eff, "doi": doi or "", "arxivId": aid or "",
               "publicationUrl": f"https://doi.org/{doi}" if doi and not _arxiv_from_doi(doi) else (url or ""),
               "arxivUrl": f"https://arxiv.org/abs/{aid}" if aid else "", "bibtex": bib,
               "listSource": "orcid"}
        # keep one record per title: published > preprint, then newer
        prio = (0 if eff == "preprint" else 1, int(year) if str(year).isdigit() else 0)
        if tkey not in best or prio > best[tkey]["_prio"]:
            rec["_prio"] = prio
            best[tkey] = rec
        if i % 10 == 0:
            log.info("paper list: %d/%d ORCID works processed", i, len(summaries))
    out = []
    for r in best.values():
        r.pop("_prio", None)
        out.append(r)
    for key, f in feed_by_title.items():
        if key in used_feed or key in best or _excluded(f["title"], pl.exclude_titles):
            continue
        out.append({"title": clean_title(f["title"]), "authors": f["authors"], "year": _year_from_arxiv(f["arxiv_id"]),
                    "venue": "arXiv preprint", "rawType": "preprint", "effectiveType": "preprint", "doi": "",
                    "arxivId": f["arxiv_id"], "publicationUrl": "", "arxivUrl": f"https://arxiv.org/abs/{f['arxiv_id']}",
                    "bibtex": "", "listSource": "arxiv-feed"})
    out.sort(key=lambda r: -(int(r["year"]) if str(r["year"]).isdigit() else 0))
    log.info("paper list: %d papers (%d arXiv-only from the feed)", len(out),
             sum(1 for r in out if r["listSource"] == "arxiv-feed"))
    return out


def write_paper_list(records: list[dict], output_dir) -> Path:
    path = Path(output_dir) / "publications.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return path
