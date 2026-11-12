"""Find which configured projects are acknowledged in a paper's text."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

ACK = "ack"    # found in an acknowledgements / funding section or funding footnote
BODY = "body"  # found only elsewhere in the text (related work, author names, ...)

_HEADING = re.compile(
    r"^[ \t]*(?:\d+\.?[ \t]*)?(?:acknowledg(?:e)?ments?|funding(?:[ \t]+and[ \t]+acknowledg(?:e)?ments?)?)[ \t]*:?[ \t]*$",
    re.I | re.M,
)
_END_HEADING = re.compile(r"^[ \t]*(?:\d+\.?[ \t]*)?(?:references|bibliography|appendix)\b", re.I | re.M)
_FOOTNOTE = re.compile(
    r"\bthis\s+(?:work|research|project|study|paper)\b[^.]{0,100}?\b(?:supported|funded|financed|partially|partly|"
    r"received\s+funding|carried\s+out)",
    re.I,
)
SECTION_MAX = 2000
FOOTNOTE_LEN = 700


SNIPPET_LEN = 600


@dataclass
class AckRegion:
    page: int | None  # 1-based page number, None when the text has no page information
    kind: str  # heading | footnote
    snippet: str


@dataclass
class AckResult:
    status: str  # ok | unreadable
    matches: dict[str, str] = field(default_factory=dict)  # project name -> ack | body
    regions: list[AckRegion] = field(default_factory=list)
    pages: dict[str, int] = field(default_factory=dict)  # project name -> 1-based page of its first ack match

    @property
    def acknowledged(self) -> list[str]:
        return [p for p, w in self.matches.items() if w == ACK]

    @property
    def ack_page(self) -> int | None:
        """Page holding the acknowledgements: first page with an ack match, else the first ack region."""
        if self.pages:
            return min(self.pages.values())
        for r in self.regions:
            if r.page:
                return r.page
        return None


def extract_pages(pdf_path) -> list[str]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(pdf_path))
        return [(page.extract_text() or "") for page in reader.pages]
    except Exception:
        return []


def extract_text(pdf_path) -> str:
    return "\n".join(extract_pages(pdf_path))


def _collapse(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _term_regex(term: str) -> re.Pattern:
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])", re.I)


def _dehyphenate(text: str) -> str:
    # Re-join words hyphenated across line breaks ("Sec4AI4-\nSec" -> "Sec4AI4Sec").
    return re.sub(r"(\w)-[ \t]*\n[ \t]*(\w)", r"\1\2", text)


def _ack_regions(text: str) -> list[tuple[int, str, str]]:
    """(start offset, kind, collapsed text) of every acknowledgement-like region."""
    regions, spans = [], []
    for m in _HEADING.finditer(text):
        end = _END_HEADING.search(text, m.end())
        stop = min(m.end() + SECTION_MAX, end.start() if end else len(text))
        regions.append((m.start(), "heading", text[m.end():stop]))
        spans.append((m.start(), stop))
    for m in _FOOTNOTE.finditer(text):
        if any(a <= m.start() < b for a, b in spans):
            continue  # funding sentence inside an acknowledgements section: already covered
        regions.append((m.start(), "footnote", text[m.start(): m.start() + FOOTNOTE_LEN]))
    return [(start, kind, _collapse(r)) for start, kind, r in regions]


def _page_of(offset: int, page_starts: list[int] | None) -> int | None:
    if not page_starts:
        return None
    page = 1
    for i, start in enumerate(page_starts, 1):
        if offset >= start:
            page = i
    return page


def analyze_text(text: str, projects, page_starts: list[int] | None = None) -> AckResult:
    """page_starts: offset of each page in `text` (already dehyphenated per page), for page numbers."""
    if len(text.strip()) < 50:
        return AckResult("unreadable")
    dehyph = text if page_starts else _dehyphenate(text)
    raw_regions = _ack_regions(dehyph)
    regions = [AckRegion(_page_of(start, page_starts), kind, body[:SNIPPET_LEN]) for start, kind, body in raw_regions]
    full = _collapse(dehyph)
    matches: dict[str, str] = {}
    pages: dict[str, int] = {}
    for p in projects:
        pats = [_term_regex(t) for t in p.terms]
        hit = next(((start, body) for start, _k, body in raw_regions if any(pat.search(body) for pat in pats)), None)
        if hit:
            matches[p.name] = ACK
            page = _page_of(hit[0], page_starts)
            if page:
                pages[p.name] = page
        elif any(pat.search(full) for pat in pats):
            matches[p.name] = BODY
    return AckResult("ok", matches, regions, pages)


def analyze_pages(page_texts: list[str], projects) -> AckResult:
    """Analyse per-page text, keeping page numbers. Hyphenation is repaired within each page."""
    parts, starts, pos = [], [], 0
    for t in page_texts:
        t = _dehyphenate(t)
        starts.append(pos)
        parts.append(t)
        pos += len(t) + 1
    return analyze_text("\n".join(parts), projects, page_starts=starts)


def analyze_pdf(pdf_path, projects) -> AckResult:
    return analyze_pages(extract_pages(pdf_path), projects)
