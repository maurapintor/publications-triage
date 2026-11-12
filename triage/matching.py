"""Title / author matching helpers shared by all sources."""
from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

from .papers import normalize_key, normalize_title_key

TITLE_MATCH = 0.92
TITLE_NEAR = 0.80


def title_score(a: str, b: str) -> float:
    ka, kb = normalize_title_key(a), normalize_title_key(b)
    if not ka or not kb:
        return 0.0
    return fuzz.ratio(ka, kb) / 100.0


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


_INITIAL = re.compile(r"^(?:[A-Z]\.?-?)+$")


def surname(name: str) -> str:
    """Last word of the family name. Handles 'Last, First', 'First Last' and 'Last F. M.' (IRIS style)."""
    name = (name or "").strip()
    if "," in name:
        last = name.split(",", 1)[0]
    else:
        parts = name.split()
        while len(parts) > 1 and _INITIAL.match(parts[-1]):
            parts.pop()  # 'Lodi M. B.' -> 'Lodi'
        if len(parts) > 1 and _INITIAL.match(parts[0]):
            parts = parts[1:]  # 'M. Lodi' -> 'Lodi'
        last = " ".join(parts[-1:]) if parts else ""
    words = normalize_key(_strip_accents(last)).split()
    return words[-1] if words else ""


def surnames(names: list[str]) -> set[str]:
    return {s for s in (surname(n) for n in names) if s}


def name_tokens(name: str) -> set[str]:
    """Normalized words of a name, without initials ('Lovelace Ada' -> {'lovelace', 'ada'})."""
    return {w for w in normalize_key(_strip_accents(re.sub(r"\b[A-Z]\.", " ", name or ""))).split() if len(w) > 1}


def same_author(x: str, y: str) -> bool:
    """Surname of one appears in the other name. Copes with 'First Last', 'Last, First', 'Last F.' and the
    ambiguous 'Last First' (IRIS)."""
    sx, sy = surname(x), surname(y)
    return bool(sx and sy) and (sx == sy or sx in name_tokens(y) or sy in name_tokens(x))


def shares_author(a: list[str], b: list[str]) -> bool:
    if surnames(a) & surnames(b):
        return True
    return any(same_author(x, y) for x in a for y in b)


def classify_title_match(score: float, author_overlap: bool) -> str:
    """present / uncertain / missing from a fuzzy title score and author overlap."""
    if score >= TITLE_MATCH and author_overlap:
        return "present"
    if score >= TITLE_NEAR:
        return "uncertain"
    return "missing"
