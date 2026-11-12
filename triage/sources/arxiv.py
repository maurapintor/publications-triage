"""arXiv checker. Uses ONLY the official API on export.arxiv.org (no crawling)."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from ..matching import classify_title_match, shares_author, title_score
from ..papers import Paper
from .base import MISSING, PRESENT, UNCERTAIN, SourceResult

API = "https://export.arxiv.org/api/query"
NS = {"a": "http://www.w3.org/2005/Atom"}


def _entries(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    out = []
    for e in root.findall("a:entry", NS):
        eid = (e.findtext("a:id", "", NS) or "").strip()
        if "/api/errors" in eid:
            continue
        m = re.search(r"arxiv\.org/abs/(.+)$", eid)
        out.append(
            {
                "id": m.group(1) if m else eid,
                "title": " ".join((e.findtext("a:title", "", NS) or "").split()),
                "authors": [(a.findtext("a:name", "", NS) or "").strip() for a in e.findall("a:author", NS)],
            }
        )
    return out


def _result(entry: dict, status: str, score: float, evidence: str) -> SourceResult:
    base_id = re.sub(r"v\d+$", "", entry["id"])
    return SourceResult(
        source="arxiv",
        status=status,
        match_id=entry["id"],
        url=f"https://arxiv.org/abs/{base_id}",
        pdf_url=f"https://export.arxiv.org/pdf/{base_id}",
        score=score,
        evidence=evidence,
        fulltext=True,
        open_access=True,
        title=entry["title"],
        authors=list(entry["authors"]),
    )


class ArxivSource:
    name = "arxiv"

    def __init__(self, http):
        self.http = http

    def _query(self, params: dict) -> list[dict]:
        r = self.http.get(API, params=params)
        if r.status_code != 200:
            raise RuntimeError(f"arXiv API returned HTTP {r.status_code}")
        return _entries(r.text)

    def check(self, paper: Paper) -> SourceResult:
        if paper.arxiv_id:
            entries = self._query({"id_list": paper.arxiv_id, "max_results": 1})
            if entries:
                score = title_score(paper.title, entries[0]["title"])
                if score < 0.5:
                    return _result(entries[0], UNCERTAIN, score, f"arxiv id given but title differs ({score:.2f})")
                return _result(entries[0], PRESENT, score, "arxiv id")
        query = 'ti:"%s"' % re.sub(r'["\\]', " ", paper.title)
        entries = self._query({"search_query": query, "max_results": 5})
        best, best_score = None, 0.0
        for e in entries:
            s = title_score(paper.title, e["title"])
            if s > best_score:
                best, best_score = e, s
        if best is None:
            return SourceResult("arxiv", MISSING, evidence="no search hit")
        overlap = shares_author(paper.authors, best["authors"])
        status = classify_title_match(best_score, overlap)
        if status == MISSING:
            return SourceResult("arxiv", MISSING, score=best_score, evidence=f"best title {best_score:.2f}")
        return _result(best, status, best_score, f"title {best_score:.2f}, author overlap={overlap}")
