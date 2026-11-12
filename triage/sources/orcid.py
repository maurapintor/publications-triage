"""ORCID public API (pub.orcid.org, no login): the handles of the user's works pushed by IRIS."""
from __future__ import annotations

ORCID_API = "https://pub.orcid.org/v3.0"


def fetch_work_handles(http, orcid: str) -> list[str]:
    """Handle ids (e.g. '12345/678') of the works in the ORCID record, in ORCID order, no duplicates.

    Always fetched fresh (one request): new works appear in ORCID over time.
    """
    r = http.get(f"{ORCID_API}/{orcid}/works", use_cache=False, headers={"Accept": "application/json"})
    if r.status_code == 404:
        raise RuntimeError(f"ORCID iD {orcid} not found")
    if r.status_code != 200:
        raise RuntimeError(f"ORCID API returned HTTP {r.status_code}")
    out: list[str] = []
    for group in r.json().get("group", []):
        ids = (group.get("external-ids") or {}).get("external-id") or []
        for e in ids:
            if e.get("external-id-type") == "handle":
                h = (e.get("external-id-value") or "").strip()
                h = h.split("hdl.handle.net/")[-1]
                if h and h not in out:
                    out.append(h)
    return out
