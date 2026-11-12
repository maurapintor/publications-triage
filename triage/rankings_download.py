"""Get the CORE and SCImago ranking lists: CORE is downloaded on request (dashboard button / CLI); SCImago
forbids scripted CSV downloads in robots.txt, so its file is downloaded by the user in a browser and imported.

Uses the tool's own HTTP client: User-Agent `publications-triage/... (mailto:<contact_email>)`, rate limits,
and a robots.txt check before each request (a disallowed URL raises BlockedByPolicy and is never retried
another way). The responses are validated (a Cloudflare / HTML page is rejected) and written atomically.
"""
from __future__ import annotations

import csv
import io
from dataclasses import replace
from pathlib import Path

CORE_URL = "https://portal.core.edu.au/conf-ranks/"
SJR_URL = "https://www.scimagojr.com/journalrank.php"


def default_paths(config) -> dict[str, Path]:
    base = Path(config.cache_dir) / "rankings"
    return {"core": base / "core.csv", "sjr": base / "sjr.csv"}


def effective_rankings(config):
    """RankingsConfig with core_csv / sjr_csv defaulted to the download location when not configured."""
    d = default_paths(config)
    r = config.rankings
    return replace(r, core_csv=r.core_csv or (str(d["core"]) if d["core"].exists() else None),
                   sjr_csv=r.sjr_csv or (str(d["sjr"]) if d["sjr"].exists() else None))


def target_path(config, which: str) -> Path:
    configured = config.rankings.core_csv if which == "core" else config.rankings.sjr_csv
    return Path(configured) if configured else default_paths(config)[which]


def _looks_html(text: str) -> bool:
    head = text.lstrip()[:500].lower()
    return head.startswith("<") or "<html" in head or "cloudflare" in head


def _check_core(text: str) -> int:
    if _looks_html(text):
        raise RuntimeError("CORE returned a web page instead of the CSV (blocked or changed); download it by hand")
    rows = [r for r in csv.reader(io.StringIO(text)) if len(r) >= 5]
    if len(rows) < 50:
        raise RuntimeError(f"CORE export looks wrong ({len(rows)} rows); download it by hand")
    return len(rows)


def _check_sjr(text: str) -> int:
    if _looks_html(text):
        raise RuntimeError("SCImago returned a web page instead of the CSV (blocked or changed); download it by hand")
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")), delimiter=";")
    if not reader.fieldnames or "Title" not in reader.fieldnames or "Categories" not in reader.fieldnames:
        raise RuntimeError("SCImago file has no Title/Categories columns; download it by hand")
    n = sum(1 for _ in reader)
    if n < 50:
        raise RuntimeError(f"SCImago file looks wrong ({n} journals); download it by hand")
    return n


MANUAL_PAGES = {
    "core": "https://portal.core.edu.au/conf-ranks/",
    "sjr": "https://www.scimagojr.com/journalrank.php?area=1700",
}
# SCImago robots.txt (all agents): "Disallow: /*out=xls" -- CSV downloads are only for people using the
# Download button in a browser. The tool therefore never fetches it; the user downloads and imports the file.
SCRIPTED_DOWNLOAD = {"core": True, "sjr": False}


def save_list(config, which: str, data: bytes) -> dict:
    """Validate an uploaded/downloaded list and store it where the rankings look for it."""
    if which not in ("core", "sjr"):
        raise ValueError(f"unknown ranking list {which!r}")
    text = data.decode("utf-8-sig", errors="replace")
    rows = (_check_core if which == "core" else _check_sjr)(text)
    path = target_path(config, which)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return {"which": which, "path": str(path), "rows": rows}


def download(config, http, which: str) -> dict:
    """which: 'core' | 'sjr'. Returns {which, path, rows, url}. Raises on any problem (file left unchanged)."""
    r = config.rankings
    if which == "core":
        params = {"search": "", "by": "all", "source": r.core_source, "sort": "arank", "page": "1", "do": "Export"}
        url = CORE_URL
    elif which == "sjr":
        raise PermissionError("SCImago does not allow scripted CSV downloads (robots.txt: Disallow /*out=xls). "
                              "Open the SCImago page, press 'Download data' in your browser, then upload the file.")
    else:
        raise ValueError(f"unknown ranking list {which!r}")
    resp = http.get(url, params=params, use_cache=False)
    if resp.status_code != 200:
        raise RuntimeError(f"{which.upper()} download failed: HTTP {resp.status_code}")
    result = save_list(config, which, resp.content)
    result["url"] = url
    return result
