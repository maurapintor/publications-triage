"""Open-access location checkers by DOI: OpenAlex and Unpaywall (official public APIs).

status=present means an open-access PDF location is known; missing means unknown DOI or no OA copy
(the evidence field says which).
"""
from __future__ import annotations

from urllib.parse import quote

from ..papers import Paper
from .base import MISSING, PRESENT, SourceResult


class _DoiSource:
    name = ""

    def __init__(self, http, contact_email: str):
        self.http = http
        self.email = contact_email

    def check(self, paper: Paper) -> SourceResult:
        if not paper.doi:
            return SourceResult(self.name, MISSING, evidence="no doi")
        r = self.http.get(self._url(paper.doi), params=self._params())
        if r.status_code == 404:
            return SourceResult(self.name, MISSING, evidence="unknown doi")
        if r.status_code != 200:
            raise RuntimeError(f"{self.name} returned HTTP {r.status_code}")
        return self._parse(r.json(), paper)


class OpenAlexSource(_DoiSource):
    name = "openalex"

    def _url(self, doi):
        return f"https://api.openalex.org/works/doi:{quote(doi, safe='/')}"

    def _params(self):
        return {"mailto": self.email}

    def _parse(self, data, paper):
        loc = data.get("best_oa_location") or {}
        pdf = loc.get("pdf_url")
        is_oa = bool((data.get("open_access") or {}).get("is_oa"))
        if not (is_oa and pdf):
            return SourceResult(self.name, MISSING, match_id=data.get("id"), url=data.get("id"),
                                evidence="no OA PDF location", open_access=is_oa)
        return SourceResult(self.name, PRESENT, match_id=data.get("id"), url=loc.get("landing_page_url") or data.get("id"),
                            pdf_url=pdf, score=1.0, evidence="doi", fulltext=True, open_access=True)


class UnpaywallSource(_DoiSource):
    name = "unpaywall"

    def _url(self, doi):
        return f"https://api.unpaywall.org/v2/{quote(doi, safe='/')}"

    def _params(self):
        return {"email": self.email}

    def _parse(self, data, paper):
        loc = data.get("best_oa_location") or {}
        pdf = loc.get("url_for_pdf")
        if not (data.get("is_oa") and pdf):
            return SourceResult(self.name, MISSING, evidence="no OA PDF location", open_access=bool(data.get("is_oa")))
        return SourceResult(self.name, PRESENT, match_id=data.get("doi"), url=loc.get("url"),
                            pdf_url=pdf, score=1.0, evidence=f"doi ({loc.get('host_type', '?')})",
                            fulltext=True, open_access=True)
