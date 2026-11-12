"""Fields derived at display/report time from a stored record + the current config.

Recomputed on every request so config edits (manual venue ranks, validations, ranking files) apply at
once, without re-running the pipeline.
"""
from __future__ import annotations

from .compare import iris_vs_arxiv_from
from .rankings import load_tables, rank_record


def enrich_records(records: list[dict], config) -> dict:
    """Adds `iris_vs_arxiv` (with `accepted`), `ranking` and `validation` to each record (in place).
    Returns info about the ranking tables (counts, errors)."""
    from .rankings_download import effective_rankings
    rcfg = effective_rankings(config)
    tables = load_tables(rcfg)
    for rec in records:
        val = config.validations.get(rec["id"]) or {}
        cmp = iris_vs_arxiv_from(rec.get("sources"))
        if cmp is not None:
            cmp["accepted"] = cmp["verdict"] != "same" and val.get("iris_vs_arxiv") == "accepted"
        rec["iris_vs_arxiv"] = cmp
        rec["ranking"] = rank_record(rec, tables, rcfg, val)
        rec["validation"] = val or None
    return {"journals": len(tables.journals), "conferences": len(tables.conferences),
            "sjr_label": tables.sjr_label, "errors": tables.errors,
            "sjr_csv": rcfg.sjr_csv, "core_csv": rcfg.core_csv,
            "core_source": rcfg.core_source, "sjr_area": rcfg.sjr_area, "sjr_year": rcfg.sjr_year}
