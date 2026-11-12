from __future__ import annotations

from dataclasses import dataclass

PRESENT, MISSING, UNCERTAIN, ERROR = "present", "missing", "uncertain", "error"


@dataclass
class SourceResult:
    source: str
    status: str  # present | missing | uncertain | error
    match_id: str | None = None
    url: str | None = None
    pdf_url: str | None = None
    score: float | None = None
    evidence: str = ""  # e.g. "doi", "title 0.97 + author", "no doi"
    fulltext: bool | None = None  # source holds a full-text file (IRIS)
    open_access: bool | None = None
    title: str | None = None  # title / authors of the matched entry (for cross-source comparison)
    authors: list[str] | None = None
    meta: dict | None = None  # source-specific bibliographic details (IRIS: volume, pages, journal, year)
