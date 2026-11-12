"""CSV report."""
from __future__ import annotations

import csv
import json
import shutil
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path


def columns(source_names: list[str], projects) -> list[str]:
    cols = ["paper_id", "title", "year", "doi", "venue"]
    for s in source_names:
        cols += [f"{s}_status", f"{s}_url"]
    if "iris" in source_names and "arxiv" in source_names:
        cols.append("iris_vs_arxiv")
    cols += ["pdf_source", "ack_status", "ack_projects_found"]
    cols += [f"ack_{p.name}" for p in projects]
    cols.append("notes")
    return cols


def record_row(rec, source_names, projects) -> dict:
    p = rec.paper
    row = {"paper_id": p.id, "title": p.title, "year": p.year, "doi": p.doi or "", "venue": p.venue}
    for s in source_names:
        r = rec.results.get(s)
        row[f"{s}_status"] = r.status if r else "not_checked"
        row[f"{s}_url"] = (r.url or "") if r else ""
    if "iris" in source_names and "arxiv" in source_names:
        row["iris_vs_arxiv"] = rec.iris_vs_arxiv["verdict"] if rec.iris_vs_arxiv else ""
    row["pdf_source"] = rec.pdf_source or ""
    if rec.ack is None:
        row["ack_status"] = "no_pdf"
    else:
        row["ack_status"] = rec.ack.status
    row["ack_projects_found"] = ";".join(rec.ack.acknowledged) if rec.ack else ""
    for proj in projects:
        if rec.ack is None or rec.ack.status != "ok":
            row[f"ack_{proj.name}"] = ""
        else:
            where = rec.ack.matches.get(proj.name)
            row[f"ack_{proj.name}"] = {"ack": "Y", "body": "body"}.get(where, "N")
    row["notes"] = " | ".join(rec.notes)
    return row


def write_csv(records, source_names, projects, output_dir, today: date | None = None, config=None) -> Path:
    """config: when given, rank / iris-vs-arxiv validation columns are filled (same logic as the dashboard)."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"triage_{(today or date.today()).isoformat()}.csv"
    # utf-8-sig: BOM so Excel detects UTF-8
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        cols = columns(source_names, projects)
        extra = {}
        if config is not None:
            from .enrich import enrich_records
            dicts = [record_to_dict(r, source_names) for r in records]
            enrich_records(dicts, config)
            extra = {d["id"]: d for d in dicts}
            i = cols.index("pdf_source")
            cols[i:i] = ["rank", "rank_top", "rank_status"]
            if "iris_vs_arxiv" in cols:
                cols.insert(cols.index("iris_vs_arxiv") + 1, "iris_vs_arxiv_accepted")
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for rec in records:
            row = record_row(rec, source_names, projects)
            d = extra.get(rec.paper.id)
            if d:
                rk = d["ranking"]
                row.update(rank=rk["rank"] or "", rank_top={True: "Y", False: "N"}.get(rk["top"], ""),
                           rank_status=rk["status"])
                if "iris_vs_arxiv" in cols:
                    c = d["iris_vs_arxiv"]
                    row["iris_vs_arxiv"] = c["verdict"] if c else ""
                    row["iris_vs_arxiv_accepted"] = "Y" if c and c["accepted"] else ""
            w.writerow(row)
    return path


def _path(p) -> str | None:
    return str(p) if p else None


def record_to_dict(rec, source_names) -> dict:
    p = rec.paper
    ack = rec.ack
    return {
        "id": p.id, "title": p.title, "authors": list(p.authors), "year": p.year, "doi": p.doi,
        "arxiv_id": p.arxiv_id, "venue": p.venue, "type": p.type, "url": p.url, "bibtex": p.bibtex,
        "sources": {
            s: (asdict(rec.results[s]) if s in rec.results else {"source": s, "status": "not_checked"})
            for s in source_names
        },
        "pdf_path": _path(rec.pdf_path),
        "pdf_source": rec.pdf_source,
        "pdf_cached": rec.pdf_cached,
        "iris_vs_arxiv": rec.iris_vs_arxiv,
        "ack": None if ack is None else {
            "status": ack.status,
            "matches": dict(ack.matches),
            "acknowledged": ack.acknowledged,
            "pages": dict(ack.pages),
            "page": ack.ack_page,
            "regions": [asdict(r) for r in ack.regions],
        },
        "ack_page_pdf": _path(rec.ack_page_pdf),
        "notes": list(rec.notes),
    }


def write_json(records, source_names, projects, output_dir, today: date | None = None,
               generated_at: str | None = None) -> Path:
    """Write output/triage_<date>.json and refresh output/latest.json."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"triage_{(today or date.today()).isoformat()}.json"
    data = {
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": list(source_names),
        "projects": [p.name for p in projects],
        "records": [record_to_dict(r, source_names) for r in records],
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    latest = out_dir / "latest.json"
    shutil.copyfile(path, latest.with_suffix(".tmp"))
    latest.with_suffix(".tmp").replace(latest)
    return path


def load_json(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
