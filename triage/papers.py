"""Paper model, loader and text normalization."""
from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path


def normalize_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", html.unescape(text or "").lower()).strip()


def clean_latex(text: str) -> str:
    t = text or ""
    t = re.sub(r"\\textbackslash\s*", "", t)
    t = re.sub(r"\\text\w+\{([^}]*)\}", r"\1", t)
    for cmd in ("ell", "sigma", "alpha", "beta", "gamma", "delta", "epsilon", "lambda", "mu", "pi", "theta"):
        t = re.sub(rf"\\{cmd}\b", cmd, t)
    t = re.sub(r"[\\{}$_^]", "", t)
    return t.strip()


def normalize_title_key(text: str) -> str:
    return normalize_key(clean_latex(text))


def normalize_doi(value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    raw = re.sub(r"^doi\s*:\s*", "", raw, flags=re.I)
    raw = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", raw, flags=re.I)
    raw = re.sub(r"[\s.]+$", "", raw).strip()
    return raw.lower() or None


def parse_arxiv_id(text: str | None) -> str | None:
    if not text:
        return None
    normalized = re.sub(r"^arxiv:", "", text.strip(), flags=re.I)
    normalized = re.sub(r"[.,;\s]+$", "", normalized)
    if re.match(r"^[a-z\-.]+/[0-9]{7}(?:v\d+)?$", normalized, re.I):
        return normalized
    if re.match(r"^\d{4}\.\d{4,5}(?:v\d+)?$", normalized, re.I):
        return normalized
    m = re.search(r"arxiv\.org/(?:abs|pdf)/([^?#\s]+?)(?:\.pdf)?$", normalized, re.I)
    if m:
        return parse_arxiv_id(m.group(1))
    return None


@dataclass
class Paper:
    id: str
    title: str
    authors: list[str] = field(default_factory=list)
    year: str = ""
    doi: str | None = None
    arxiv_id: str | None = None
    venue: str = ""
    type: str = ""  # e.g. journal-article, proceedings-article, book-chapter, posted-content
    url: str | None = None
    bibtex: str | None = None

    @property
    def title_key(self) -> str:
        return normalize_title_key(self.title)


def make_paper_id(doi: str | None, title: str) -> str:
    if doi:
        return doi
    return "t-" + hashlib.sha1(normalize_title_key(title).encode()).hexdigest()[:12]


def safe_filename(paper_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", paper_id)


def load_papers(path: str | Path) -> list[Paper]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("publications") or data.get("items") or []
    return papers_from_records(data)


def papers_from_records(data: list[dict]) -> list[Paper]:
    papers: list[Paper] = []
    seen: set[str] = set()
    for item in data:
        title = (item.get("title") or "").strip()
        if not title:
            continue
        doi = normalize_doi(item.get("doi"))
        pid = make_paper_id(doi, title)
        if pid in seen:
            continue
        seen.add(pid)
        papers.append(
            Paper(
                id=pid,
                title=title,
                authors=list(item.get("authors") or []),
                year=str(item.get("year") or ""),
                doi=doi,
                arxiv_id=parse_arxiv_id(item.get("arxivId")) or parse_arxiv_id(item.get("arxivUrl")),
                venue=item.get("venue") or "",
                type=item.get("effectiveType") or item.get("rawType") or item.get("type") or "",
                url=item.get("publicationUrl") or None,
                bibtex=item.get("bibtex") or None,
            )
        )
    return papers
