"""Citation export: BibTeX and plain-text styles (APA, IEEE, MLA, Chicago, Harvard).

Input is a record dict as written to output/latest.json (title, authors "Last, First", year, venue, doi,
arxiv_id, type, url, bibtex). The ORCID BibTeX in publications.json (when present) supplies volume,
number, pages, publisher, booktitle; its entry key is replaced with a readable one.
"""
from __future__ import annotations

import re
import unicodedata

FORMATS = {
    "bibtex": ("BibTeX", "bib"),
    "apa": ("APA 7", "txt"),
    "ieee": ("IEEE", "txt"),
    "mla": ("MLA 9", "txt"),
    "chicago": ("Chicago (author-date)", "txt"),
    "harvard": ("Harvard", "txt"),
}

_TYPE_TO_BIB = {
    "journal-article": "article",
    "proceedings-article": "inproceedings",
    "conference-paper": "inproceedings",
    "preprint": "misc",
    "book-chapter": "incollection",
    "book": "book",
    "posted-content": "misc",
}


# ---------- BibTeX parsing ----------
def parse_bibtex(text: str | None) -> tuple[str | None, str | None, dict[str, str]]:
    """(entry type, key, fields) of the first entry; brace-aware, tolerant."""
    if not text:
        return None, None, {}
    m = re.search(r"@(\w+)\s*\{\s*([^,\s]*)\s*,", text)
    if not m:
        return None, None, {}
    etype, key = m.group(1).lower(), m.group(2) or None
    fields: dict[str, str] = {}
    i = m.end()
    n = len(text)
    while i < n:
        fm = re.compile(r"\s*(\w[\w-]*)\s*=\s*").match(text, i)
        if not fm:
            break
        name = fm.group(1).lower()
        i = fm.end()
        if i < n and text[i] == "{":
            depth, j = 0, i
            while j < n:
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            value, i = text[i + 1:j], j + 1
        elif i < n and text[i] == '"':
            j = text.find('"', i + 1)
            j = n if j < 0 else j
            value, i = text[i + 1:j], j + 1
        else:
            vm = re.compile(r"[^,}\s]+").match(text, i)
            value = vm.group(0) if vm else ""
            i = vm.end() if vm else i
        fields[name] = " ".join(value.split())
        cm = re.compile(r"\s*,").match(text, i)
        if cm:
            i = cm.end()
    return etype, key, fields


# ---------- names ----------
def split_name(name: str) -> tuple[str, str]:
    """'Last, First' or 'First Last' -> (last, first)."""
    name = " ".join((name or "").split())
    if "," in name:
        last, first = name.split(",", 1)
        return last.strip(), first.strip()
    parts = name.split(" ")
    return (parts[-1], " ".join(parts[:-1])) if len(parts) > 1 else (name, "")


def initials(first: str) -> str:
    out = []
    for word in first.split():
        pieces = [p for p in word.split("-") if p]
        out.append("-".join(p[0].upper() + "." for p in pieces))
    return " ".join(out)


def _ascii(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def cite_key(rec: dict) -> str:
    last = split_name((rec.get("authors") or ["anon"])[0])[0]
    word = next((w for w in re.findall(r"[A-Za-z0-9]+", _ascii(rec.get("title") or ""))
                 if w.lower() not in {"a", "an", "the", "on", "of", "for", "and", "in", "to"}), "paper")
    return re.sub(r"[^A-Za-z0-9]", "", _ascii(last)).lower() + str(rec.get("year") or "") + word.lower()


# ---------- metadata ----------
def _meta(rec: dict) -> dict:
    etype, _key, f = parse_bibtex(rec.get("bibtex"))
    bib_type = _TYPE_TO_BIB.get(rec.get("type") or "") or etype or ("misc" if rec.get("arxiv_id") else "article")
    venue = rec.get("venue") or f.get("journal") or f.get("booktitle") or ""
    if bib_type in ("inproceedings", "incollection") and f.get("booktitle"):
        venue = f["booktitle"]  # proceedings title, not the series ("Lecture Notes in Computer Science")
    if bib_type == "misc" and rec.get("arxiv_id") and (not venue or venue.lower() in ("arxiv", "arxiv preprint")):
        venue = ""
    return {
        "type": bib_type, "title": rec.get("title") or f.get("title") or "", "authors": rec.get("authors") or [],
        "year": str(rec.get("year") or f.get("year") or ""), "venue": venue,
        "volume": f.get("volume", ""), "number": f.get("number", ""), "pages": f.get("pages", "").replace("--", "–"),
        "publisher": f.get("publisher", ""), "doi": rec.get("doi") or f.get("doi") or "",
        "arxiv": rec.get("arxiv_id") or "", "fields": f,
    }


def _link(m: dict) -> str:
    if m["doi"]:
        return f"https://doi.org/{m['doi']}"
    if m["arxiv"]:
        return f"https://arxiv.org/abs/{m['arxiv']}"
    return ""


def _venue_or_preprint(m: dict) -> str:
    if m["venue"]:
        return m["venue"]
    return f"arXiv preprint arXiv:{m['arxiv']}" if m["arxiv"] else ""


def _end(s: str) -> str:
    s = s.strip()
    return s if s.endswith((".", "?", "!")) else s + "."


# ---------- styles ----------
def bibtex(rec: dict) -> str:
    m = _meta(rec)
    f = dict(m["fields"])
    f["author"] = " and ".join(m["authors"]) or f.get("author", "")
    f["title"] = m["title"]
    f["year"] = m["year"]
    venue_field = {"article": "journal", "inproceedings": "booktitle", "incollection": "booktitle"}.get(m["type"])
    if venue_field and m["venue"]:
        f.setdefault(venue_field, m["venue"])
        if venue_field == "journal":
            f["journal"] = m["venue"]  # proper casing instead of the ORCID upper-case form
    if m["doi"]:
        f["doi"] = m["doi"]
    if m["arxiv"]:
        f.setdefault("eprint", m["arxiv"])
        f.setdefault("archiveprefix", "arXiv")
    order = ["author", "title", "journal", "booktitle", "publisher", "year", "volume", "number", "pages",
             "doi", "eprint", "archiveprefix", "url"]
    keys = [k for k in order if f.get(k)] + sorted(k for k in f if k not in order and f[k])
    body = ",\n".join(f"  {k} = {{{f[k]}}}" for k in keys)
    return f"@{m['type']}{{{cite_key(rec)},\n{body}\n}}"


def apa(rec: dict) -> str:
    m = _meta(rec)
    names = [f"{last}, {initials(first)}".strip().rstrip(",") for last, first in map(split_name, m["authors"])]
    if len(names) > 20:
        names = names[:19] + ["…", names[-1]]
        who = ", ".join(names)
    elif len(names) > 1:
        who = ", ".join(names[:-1]) + ", & " + names[-1]
    else:
        who = "".join(names)
    parts = [f"{who} ({m['year'] or 'n.d.'}).", _end(m["title"])]
    venue = _venue_or_preprint(m)
    if venue:
        v = venue
        if m["volume"]:
            v += f", {m['volume']}" + (f"({m['number']})" if m["number"] else "")
        if m["pages"]:
            v += f", {m['pages']}"
        parts.append(_end(v))
    if _link(m):
        parts.append(_link(m))
    return " ".join(parts)


def ieee(rec: dict) -> str:
    m = _meta(rec)
    names = [f"{initials(first)} {last}".strip() for last, first in map(split_name, m["authors"])]
    if len(names) > 6:
        who = names[0] + " et al."
    elif len(names) > 2:
        who = ", ".join(names[:-1]) + ", and " + names[-1]
    else:
        who = " and ".join(names)
    bits = [f'{who}, "{m["title"]},"']
    venue = _venue_or_preprint(m)
    tail = []
    if venue:
        tail.append(("in " if m["type"] in ("inproceedings", "incollection") else "") + venue)
    if m["volume"]:
        tail.append(f"vol. {m['volume']}")
    if m["number"]:
        tail.append(f"no. {m['number']}")
    if m["pages"]:
        tail.append(f"pp. {m['pages']}")
    if m["year"]:
        tail.append(m["year"])
    if m["doi"]:
        tail.append(f"doi: {m['doi']}")
    return " ".join(bits) + " " + ", ".join(tail) + "."


def mla(rec: dict) -> str:
    m = _meta(rec)
    a = [split_name(x) for x in m["authors"]]
    if len(a) >= 3:
        who = f"{a[0][0]}, {a[0][1]}, et al."
    elif len(a) == 2:
        who = f"{a[0][0]}, {a[0][1]}, and {a[1][1]} {a[1][0]}."
    elif a:
        who = _end(f"{a[0][0]}, {a[0][1]}")
    else:
        who = ""
    parts = [who, f'"{_end(m["title"])}"']
    tail = [x for x in [_venue_or_preprint(m), f"vol. {m['volume']}" if m["volume"] else "",
                        f"no. {m['number']}" if m["number"] else "", m["year"],
                        f"pp. {m['pages']}" if m["pages"] else ""] if x]
    if tail:
        parts.append(", ".join(tail) + ".")
    if _link(m):
        parts.append(_link(m) + ".")
    return " ".join(p for p in parts if p)


def chicago(rec: dict) -> str:
    m = _meta(rec)
    a = [split_name(x) for x in m["authors"]]
    names = [f"{a[0][0]}, {a[0][1]}"] + [f"{first} {last}" for last, first in a[1:]] if a else []
    if len(names) > 10:
        who = ", ".join(names[:7]) + ", et al."
    elif len(names) > 1:
        who = ", ".join(names[:-1]) + ", and " + names[-1]
    else:
        who = "".join(names)
    parts = [_end(who), _end(m["year"] or "n.d."), f'"{_end(m["title"])}"']
    venue = _venue_or_preprint(m)
    if venue:
        v = venue + (f" {m['volume']}" if m["volume"] else "") + (f" ({m['number']})" if m["number"] else "")
        v += f": {m['pages']}" if m["pages"] else ""
        parts.append(_end(v))
    if _link(m):
        parts.append(_link(m) + ".")
    return " ".join(parts)


def harvard(rec: dict) -> str:
    m = _meta(rec)
    names = [f"{last}, {initials(first)}".strip().rstrip(",") for last, first in map(split_name, m["authors"])]
    who = (", ".join(names[:-1]) + " and " + names[-1]) if len(names) > 1 else "".join(names)
    parts = [f"{who} ({m['year'] or 'n.d.'})", f"'{m['title']}',"]
    tail = [x for x in [_venue_or_preprint(m), m["volume"] + (f"({m['number']})" if m["number"] else "") if m["volume"] else "",
                        f"pp. {m['pages']}" if m["pages"] else ""] if x]
    s = " ".join(parts) + " " + ", ".join(tail) + "."
    if m["doi"]:
        s += f" doi:{m['doi']}."
    elif _link(m):
        s += f" Available at: {_link(m)}."
    return s


_STYLES = {"bibtex": bibtex, "apa": apa, "ieee": ieee, "mla": mla, "chicago": chicago, "harvard": harvard}


def format_citations(records: list[dict], fmt: str) -> str:
    if fmt not in _STYLES:
        raise ValueError(f"unknown format {fmt!r}; valid: {list(_STYLES)}")
    sep = "\n\n" if fmt == "bibtex" else "\n"
    if fmt == "ieee":
        return "\n".join(f"[{i}] {ieee(r)}" for i, r in enumerate(records, 1)) + "\n"
    return sep.join(_STYLES[fmt](r) for r in records) + "\n"
