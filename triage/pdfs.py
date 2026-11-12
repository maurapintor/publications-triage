"""Open-access PDF retrieval with source priority, caching and validation."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import requests

from .http import BlockedByPolicy
from .papers import Paper, safe_filename
from .sources.base import PRESENT


@dataclass
class PdfOutcome:
    path: Path | None = None
    source: str | None = None
    cached: bool = False
    notes: list[str] = field(default_factory=list)


def candidates(results: dict, priority: list[str], resolvers: dict | None = None):
    """(source, pdf_url) in priority order, lazily. Only confident matches: an 'uncertain' hit may be another
    paper. resolvers: {source: fn(result) -> [urls]} for sources whose PDF links need an extra lookup (IRIS);
    they are only called when every earlier source failed."""
    for src in priority:
        r = results.get(src)
        if not (r and r.status == PRESENT):
            continue
        if r.pdf_url:
            yield src, r.pdf_url
        elif resolvers and src in resolvers:
            try:
                urls = resolvers[src](r)
            except Exception as exc:  # noqa: BLE001
                yield src, ("error", f"{type(exc).__name__}: {exc}")
                continue
            for u in urls:
                yield src, u


def _valid_pdf(resp) -> str | None:
    """Return a rejection reason, or None if the response looks like a PDF."""
    if resp.status_code != 200:
        return f"HTTP {resp.status_code}"
    if "html" in resp.content_type.lower():
        return "got HTML (landing page), not a PDF"
    if not resp.content.startswith(b"%PDF"):
        return "missing %PDF header"
    return None


def retrieve_pdf(paper: Paper, results: dict, priority: list[str], http, pdf_dir: str | Path,
                 force: bool = False, resolvers: dict | None = None) -> PdfOutcome:
    """force=True: download again even if cached; if every download fails, the cached copy is kept."""
    pdf_dir = Path(pdf_dir)
    target = pdf_dir / f"{safe_filename(paper.id)}.pdf"
    sidecar = target.with_suffix(".source")
    cached_src = (sidecar.read_text().strip() if sidecar.exists() else "cache") if target.exists() else None
    if cached_src and not force:
        return PdfOutcome(target, cached_src, cached=True)
    outcome = PdfOutcome()
    for src, url in candidates(results, priority, resolvers):
        if isinstance(url, tuple):
            outcome.notes.append(f"{src} pdf lookup failed: {url[1]}")
            continue
        try:
            resp = http.get(url, use_cache=False)
        except BlockedByPolicy as exc:
            outcome.notes.append(f"{src} pdf blocked by policy: {exc}")
            continue
        except requests.RequestException as exc:
            outcome.notes.append(f"{src} pdf download failed: {exc}")
            continue
        reason = _valid_pdf(resp)
        if reason:
            outcome.notes.append(f"{src} pdf rejected: {reason}")
            continue
        pdf_dir.mkdir(parents=True, exist_ok=True)
        target.write_bytes(resp.content)
        sidecar.write_text(src)
        outcome.path, outcome.source = target, src
        return outcome
    if cached_src:
        outcome.notes.append("re-download failed, kept the cached PDF")
        outcome.path, outcome.source, outcome.cached = target, cached_src, True
    return outcome
