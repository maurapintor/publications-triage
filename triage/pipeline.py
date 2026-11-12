"""Check phase (per-source presence) and the full run (PDF + acknowledgements)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .acknowledgements import AckResult, analyze_pdf
from .ackpage import extract_ack_page
from .compare import iris_vs_arxiv
from .papers import safe_filename
from .papers import Paper
from .pdfs import retrieve_pdf
from .sources.base import ERROR, UNCERTAIN, SourceResult

log = logging.getLogger("triage")


@dataclass
class PaperRecord:
    paper: Paper
    results: dict[str, SourceResult] = field(default_factory=dict)
    pdf_path: Path | None = None
    pdf_source: str | None = None
    ack: AckResult | None = None
    ack_page_pdf: Path | None = None
    pdf_cached: bool = False  # PDF reused from cache (not downloaded in this run)
    iris_vs_arxiv: dict | None = None
    notes: list[str] = field(default_factory=list)


class _fresh_http:
    """Temporarily bypass the HTTP response cache (responses are still written back to it)."""

    def __init__(self, http, on: bool):
        self.http, self.on = http, on and hasattr(http, "refresh")

    def __enter__(self):
        if self.on:
            self.prev, self.http.refresh = self.http.refresh, True

    def __exit__(self, *exc):
        if self.on:
            self.http.refresh = self.prev


def check_paper(paper: Paper, sources, http=None, fresh: bool = False,
                fresh_bulk: bool = False) -> dict[str, SourceResult]:
    """Run every source; a failing source becomes an 'error' cell instead of aborting the run.

    fresh=True: per-paper lookups ignore cached HTTP responses (used to retry papers without a PDF).
    Sources marked `bulk` (IRIS: records loaded once per run) are refreshed only when fresh_bulk=True.
    """
    results = {}
    for s in sources:
        with _fresh_http(http, fresh_bulk if getattr(s, "bulk", False) else fresh):
            results.update(_check(paper, [s]))
    return results


def _check(paper, sources):
    results = {}
    for s in sources:
        try:
            results[s.name] = s.check(paper)
        except Exception as exc:  # noqa: BLE001 - deliberately isolate per-source failures
            log.warning("%s failed for %s: %s", s.name, paper.id, exc)
            results[s.name] = SourceResult(s.name, ERROR, evidence=f"{type(exc).__name__}: {exc}")
    return results


def _notes(results: dict[str, SourceResult]) -> list[str]:
    notes = []
    for name, r in results.items():
        if r.status == UNCERTAIN:
            notes.append(f"{name}: uncertain match ({r.evidence}) {r.url or ''}".strip())
        elif r.status == ERROR:
            notes.append(f"{name}: error ({r.evidence})")
    return notes


def run_pipeline(papers, sources, config, http, download: bool = True, progress=None,
                 should_stop=None, reload_pdfs: bool = False) -> list[PaperRecord]:
    """Default: papers whose PDF is cached reuse it and the cached lookups; papers without a PDF are
    looked up again (fresh HTTP) and the download is retried. reload_pdfs=True: everything is fetched
    again. should_stop(): checked before each paper; True ends the run with the records so far."""
    pdf_dir = Path(config.cache_dir) / "pdfs"
    ack_dir = Path(config.cache_dir) / "acks"
    resolvers = {s.name: s.pdf_urls for s in sources if hasattr(s, "pdf_urls")}
    records = []
    for i, paper in enumerate(papers, 1):
        if should_stop and should_stop():
            log.info("run stopped after %d/%d papers", i - 1, len(papers))
            break
        rec = PaperRecord(paper)
        cached_pdf = (pdf_dir / f"{safe_filename(paper.id)}.pdf").exists()
        fresh = reload_pdfs or (download and not cached_pdf)
        rec.results = check_paper(paper, sources, http, fresh=fresh, fresh_bulk=reload_pdfs)
        rec.notes = _notes(rec.results)
        rec.iris_vs_arxiv = iris_vs_arxiv(rec.results)
        if rec.iris_vs_arxiv and rec.iris_vs_arxiv["verdict"] != "same":
            rec.notes.append(f"iris vs arxiv: {rec.iris_vs_arxiv['verdict']}")
        if download:
            out = retrieve_pdf(paper, rec.results, config.pdf_priority, http, pdf_dir, force=reload_pdfs,
                               resolvers=resolvers)
            rec.notes += out.notes
            rec.pdf_path, rec.pdf_source = out.path, out.source
            rec.pdf_cached = bool(out.path) and out.cached
            if out.path:
                rec.ack = analyze_pdf(out.path, config.projects)
                if rec.ack.status == "unreadable":
                    rec.notes.append("pdf has no extractable text layer")
                else:
                    rec.ack_page_pdf = extract_ack_page(out.path, rec.ack.ack_page, ack_dir, paper.id,
                                                        force=reload_pdfs and not out.cached)
        records.append(rec)
        if progress:
            progress(i, len(papers), rec)
    return records
