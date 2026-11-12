"""Venue rankings: SCImago (SJR) journal quartiles and CORE conference ranks.

Both sites disallow automated agents of AI vendors in robots.txt, and neither offers an API, so the tool
never downloads them: the user downloads the two lists once (CORE: portal.core.edu.au/conf-ranks → Export;
SJR: scimagojr.com/journalrank.php, Subject Area "Computer Science" → Download data) and points the config
at the files. Parsing is local and cheap. `rankings.venues` in the config is a manual list that always wins,
and per-paper validations (`validations.<paper id>.rank`) win over everything.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz, process

from .citations import parse_bibtex
from .papers import normalize_title_key

# SCImago subject categories of the "Computer Science" area (ASJC 17xx).
CS_CATEGORIES = (
    "Artificial Intelligence", "Computational Theory and Mathematics", "Computer Graphics and Computer-Aided Design",
    "Computer Networks and Communications", "Computer Science Applications", "Computer Science (miscellaneous)",
    "Computer Vision and Pattern Recognition", "Hardware and Architecture", "Human-Computer Interaction",
    "Information Systems", "Signal Processing", "Software",
)
QUARTILES = ("Q1", "Q2", "Q3", "Q4")
CORE_RANKS = ("A*", "A", "B", "C")
VALID_RANKS = QUARTILES + CORE_RANKS + ("Unranked",)

_ACRO_STOP = {"IEEE", "ACM", "LNCS", "LNAI", "CEUR", "USENIX", "IFIP", "SIAM", "SPIE", "EU", "USA", "UK",
              "II", "III", "IV", "VI", "VII", "VIII", "IX", "XI", "XII", "CCIS", "PMLR", "WS"}
CONFERENCE_TYPES = {"conference-paper", "proceedings-article", "book-chapter", "inproceedings", "incollection"}
JOURNAL_TYPES = {"journal-article", "article"}


def _jkey(title: str) -> str:
    """Journal/venue key: '&' and 'and' are the same ('Computers & Security' = 'Computers and Security')."""
    return normalize_title_key((title or "").replace("&", " and "))


# ---------- tables ----------
@dataclass
class Journal:
    title: str
    issns: set[str]
    categories: dict[str, str]  # category -> Qn
    best: str | None


@dataclass
class Conference:
    title: str
    acronym: str
    source: str
    rank: str


@dataclass
class Tables:
    journals: list[Journal] = field(default_factory=list)
    conferences: list[Conference] = field(default_factory=list)
    sjr_label: str = "SJR"
    errors: list[str] = field(default_factory=list)

    def __post_init__(self):
        self._by_issn = {i: j for j in self.journals for i in j.issns}
        self._by_title = {_jkey(j.title): j for j in self.journals}
        self._jkeys = list(self._by_title)
        self._by_acro: dict[str, list[Conference]] = {}
        for c in self.conferences:
            if c.acronym:
                self._by_acro.setdefault(c.acronym.upper(), []).append(c)
        self._ckeys = [normalize_title_key(c.title) for c in self.conferences]


def _read_text(path) -> str:
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def load_sjr_csv(path) -> list[Journal]:
    """SCImago 'Download data' CSV: ';'-separated, columns Title, Issn, SJR Best Quartile, Categories."""
    text = _read_text(path)
    out = []
    for row in csv.DictReader(io.StringIO(text), delimiter=";"):
        title = (row.get("Title") or "").strip()
        if not title:
            continue
        issns = {re.sub(r"[^0-9X]", "", i.upper()) for i in (row.get("Issn") or "").split(",")}
        cats = {}
        for part in (row.get("Categories") or "").split(";"):
            m = re.match(r"\s*(.+?)\s*\((Q[1-4])\)\s*$", part)
            if m:
                cats[m.group(1)] = m.group(2)
        best = (row.get("SJR Best Quartile") or "").strip() or None
        out.append(Journal(title, {i for i in issns if len(i) == 8}, cats, best if best in QUARTILES else None))
    return out


def load_core_csv(path) -> list[Conference]:
    """CORE portal export (no header): id, title, acronym, source, rank, ... — or a CSV with a header row
    containing Title, Acronym, Rank (and optionally Source)."""
    rows = list(csv.reader(io.StringIO(_read_text(path))))
    if not rows:
        return []
    head = [h.strip().lower() for h in rows[0]]
    out = []
    if "title" in head and "rank" in head:
        ti, ri = head.index("title"), head.index("rank")
        ai = head.index("acronym") if "acronym" in head else None
        si = head.index("source") if "source" in head else None
        for r in rows[1:]:
            if len(r) > max(ti, ri):
                out.append(Conference(r[ti].strip(), r[ai].strip() if ai is not None and ai < len(r) else "",
                                      r[si].strip() if si is not None and si < len(r) else "", r[ri].strip()))
    else:
        for r in rows:
            if len(r) >= 5 and r[1].strip():
                out.append(Conference(r[1].strip(), r[2].strip(), r[3].strip(), r[4].strip()))
    return out


_CACHE: dict = {}


def load_tables(rcfg) -> Tables:
    """Load (and memoize by file mtime) the tables named in the rankings config."""
    key, journals, confs, errors = [], [], [], []
    for attr in ("sjr_csv", "core_csv"):
        p = getattr(rcfg, attr)
        if p and Path(p).exists():
            key.append((p, Path(p).stat().st_mtime_ns))
        elif p:
            errors.append(f"{attr}: file not found: {p}")
    key = tuple(key)
    if key in _CACHE:
        t = _CACHE[key]
        t.errors = errors
        return t
    if rcfg.sjr_csv and Path(rcfg.sjr_csv).exists():
        try:
            journals = load_sjr_csv(rcfg.sjr_csv)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"sjr_csv unreadable: {exc}")
    if rcfg.core_csv and Path(rcfg.core_csv).exists():
        try:
            confs = load_core_csv(rcfg.core_csv)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"core_csv unreadable: {exc}")
    label = "SJR " + (re.search(r"(20\d\d)", Path(rcfg.sjr_csv).name).group(1)
                      if rcfg.sjr_csv and re.search(r"(20\d\d)", Path(rcfg.sjr_csv).name) else "")
    t = Tables(journals, confs, label.strip(), errors)
    _CACHE.clear()
    _CACHE[key] = t
    return t


# ---------- matching ----------
def acronyms(text: str) -> list[str]:
    """'Document Analysis and Recognition – ICDAR 2026' -> ['ICDAR']; 'CVPR2024' -> ['CVPR']."""
    out = []
    for tok in re.findall(r"[A-Za-z][A-Za-z0-9&\-]*", text or ""):
        t = re.sub(r"(19|20)\d\d$", "", tok).strip("-")
        if sum(c.isupper() for c in t) >= 2 and t.upper() == t.replace("&", "").upper() and t.upper() not in _ACRO_STOP \
                and not re.fullmatch(r"[IVXLC]+", t) and len(t) <= 12:
            if t.upper() not in out:
                out.append(t.upper())
    return out


_VENUE_NOISE = re.compile(
    r"\b(proceedings|proceesdings|proc|of|the|part\s+[ivxlc\d]+|\d+(st|nd|rd|th)|(19|20)\d\d|annual|"
    r"lecture notes in computer science|volume|vol)\b", re.I)


def _clean_venue(v: str) -> str:
    return normalize_title_key(_VENUE_NOISE.sub(" ", v or ""))


def _venue_candidates(rec: dict) -> tuple[list[str], str | None]:
    _t, _k, f = parse_bibtex(rec.get("bibtex"))
    cands = [x for x in (f.get("booktitle"), f.get("journal"), rec.get("venue")) if x]
    issn = f.get("issn")
    return cands, issn


def _norm_issns(value: str | None) -> set[str]:
    return {i for i in (re.sub(r"[^0-9X]", "", p.upper()) for p in re.split(r"[,;\s]+", value or "")) if len(i) == 8}


def _kind(rec: dict) -> str:
    t = (rec.get("type") or "").lower()
    if t in JOURNAL_TYPES:
        return "journal"
    if t in CONFERENCE_TYPES:
        return "conference"
    return "other"


def _manual_venue(cands: list[str], venues: dict[str, str]) -> tuple[str, str] | None:
    if not venues:
        return None
    keys = {normalize_title_key(k): (k, v) for k, v in venues.items()}
    acr = {k.upper(): (k, v) for k, v in venues.items()}
    for c in cands:
        hit = keys.get(normalize_title_key(c)) or keys.get(_clean_venue(c))
        if hit:
            return hit
        for a in acronyms(c):
            if a in acr:
                return acr[a]
    return None


def _result(kind, rank, status, source, evidence, matched=None, rcfg=None, extra=None):
    top = None
    if rank and rcfg is not None:
        top = rank in (rcfg.top_quartiles if rank in QUARTILES else rcfg.top_core)
    r = {"kind": kind, "rank": rank, "top": top, "status": status, "source": source,
         "evidence": evidence, "matched": matched}
    if extra:
        r.update(extra)
    return r


def rank_record(rec: dict, tables: Tables, rcfg, validation: dict | None = None) -> dict:
    """status: validated | manual | matched | uncertain | not_found | no_data | not_applicable."""
    kind = _kind(rec)
    if validation and validation.get("rank"):
        rk = validation["rank"]
        return _result("journal" if rk in QUARTILES else "conference" if rk in CORE_RANKS else kind, rk,
                       "validated", "validation", validation.get("note") or "set by hand", rcfg=rcfg)
    cands, issn = _venue_candidates(rec)
    manual = _manual_venue(cands, rcfg.venues)
    if manual:
        name, rk = manual
        return _result("journal" if rk in QUARTILES else "conference" if rk in CORE_RANKS else kind, rk,
                       "manual", "config rankings.venues", f"venue list entry '{name}'", rcfg=rcfg)
    if kind == "journal":
        return _rank_journal(rec, cands, issn, tables, rcfg)
    if kind == "conference":
        return _rank_conference(cands, tables, rcfg)
    return _result(kind, None, "not_applicable", None, f"type '{rec.get('type') or 'unknown'}' is not ranked")


def _cs_quartile(j: Journal, rcfg) -> tuple[str | None, list[str]]:
    cats = [(c, q) for c, q in j.categories.items() if c in rcfg.sjr_categories]
    if not cats:
        return None, []
    best = min(q for _c, q in cats)
    return best, [c for c, q in cats if q == best]


def _rank_journal(rec, cands, issn, tables: Tables, rcfg) -> dict:
    if not tables.journals:
        return _result("journal", None, "no_data", None, "no SJR file configured (rankings.sjr_csv)")
    j, status, how = None, None, ""
    for i in _norm_issns(issn):
        if i in tables._by_issn:
            j, status, how = tables._by_issn[i], "matched", f"ISSN {i}"
            break
    if j is None:
        for c in cands:
            k = _jkey(c)
            if k in tables._by_title:
                j, status, how = tables._by_title[k], "matched", "journal title"
                break
    if j is None:
        for c in cands:
            hit = process.extractOne(_jkey(c), tables._jkeys, scorer=fuzz.ratio)
            if hit and hit[1] >= 92:
                j, status, how = tables._by_title[hit[0]], "uncertain", f"similar title ({hit[1]:.0f}%)"
                break
    if j is None:
        return _result("journal", None, "not_found", tables.sjr_label, f"'{cands[0] if cands else ''}' not in the SJR list")
    q, cats = _cs_quartile(j, rcfg)
    if q is None:
        return _result("journal", None, status, tables.sjr_label,
                       f"{how}; not in the configured categories (best overall {j.best or '?'})", j.title, rcfg,
                       {"categories": j.categories})
    return _result("journal", q, status, tables.sjr_label, f"{how}; {q} in {', '.join(cats)}", j.title, rcfg,
                   {"categories": j.categories})


def _rank_conference(cands, tables: Tables, rcfg) -> dict:
    if not tables.conferences:
        return _result("conference", None, "no_data", None, "no CORE file configured (rankings.core_csv)")
    for c in cands:
        for a in acronyms(c):
            hits = tables._by_acro.get(a)
            if hits:
                best = max(hits, key=lambda h: fuzz.token_sort_ratio(_clean_venue(c), normalize_title_key(h.title)))
                return _result("conference", best.rank, "matched", f"CORE {best.source}".strip(),
                               f"acronym {a}", f"{best.title} ({best.acronym})", rcfg)
    for c in cands:
        key = _clean_venue(c)
        if not key:
            continue
        # token_sort_ratio, not token_set_ratio: a subset ("usenix security symposium" in "usenix network and
        # distributed system security symposium") must not score 100.
        hit = process.extractOne(key, tables._ckeys, scorer=fuzz.token_sort_ratio)
        if hit and hit[1] >= 88:
            conf = tables.conferences[hit[2]]
            status = "matched" if hit[1] >= 96 else "uncertain"
            return _result("conference", conf.rank, status, f"CORE {conf.source}".strip(),
                           f"similar title ({hit[1]:.0f}%)", f"{conf.title} ({conf.acronym})", rcfg)
    return _result("conference", None, "not_found", "CORE", f"'{cands[0] if cands else ''}' not in the CORE list")
