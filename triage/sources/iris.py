"""IRIS checker. Index built from an OAI-PMH harvest or a manual export CSV (see plans/todo.md Q2)."""
from __future__ import annotations

import csv
import gzip
import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path

from rapidfuzz import fuzz, process

from ..matching import classify_title_match, shares_author
from ..papers import Paper, normalize_doi, normalize_title_key
from .base import MISSING, PRESENT, UNCERTAIN, SourceResult

OAI = "{http://www.openarchives.org/OAI/2.0/}"
DC = "{http://purl.org/dc/elements/1.1/}"
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"<>]+", re.I)
log = logging.getLogger("triage")
CHECKPOINT_EVERY = 20  # pages


@dataclass
class IrisRecord:
    title: str
    authors: list[str] = field(default_factory=list)
    dois: set[str] = field(default_factory=set)
    url: str | None = None
    pdf_url: str | None = None
    access: str | None = None  # open | embargoed | closed | None (unknown)
    fulltext: bool | None = None
    oai_id: str | None = None
    meta: dict = field(default_factory=dict)  # year, journal, ispartofbook, volume, issue, firstpage, lastpage, ...


_META_KEYS = ("journal", "ispartofbook", "volume", "issue", "firstpage", "lastpage", "serie", "numberofpages")


def _meta(vals) -> dict:
    """IRIS puts bibliographic details in dc:relation as 'key:value' (e.g. 'volume:654')."""
    meta = {}
    for rel in vals("relation"):
        key, sep, value = rel.partition(":")
        if sep and key in _META_KEYS and value.strip() and key not in meta:
            meta[key] = value.strip()
    years = [d[:4] for d in vals("date") if d[:4].isdigit()]
    if years:
        meta["year"] = years[0]
    publisher = vals("publisher")
    if publisher:
        meta["publisher"] = publisher[0]
    return meta


def _access_from_rights(rights: list[str]) -> str | None:
    joined = " ".join(rights).lower()
    for key, val in (("openaccess", "open"), ("embargoedaccess", "embargoed"),
                     ("closedaccess", "closed"), ("restrictedaccess", "closed")):
        if key in joined:
            return val
    return None


class OaiError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(f"OAI-PMH error {code}: {message}")
        self.code = code


@dataclass
class OaiPage:
    records: list[IrisRecord]
    deleted: list[str]
    token: str | None
    response_date: str | None
    complete_size: int | None


def parse_oai_page(xml_text: str) -> tuple[list[IrisRecord], str | None]:
    page = parse_oai_page_full(xml_text)
    return page.records, page.token


def parse_oai_page_full(xml_text: str) -> OaiPage:
    root = ET.fromstring(xml_text)
    err = root.find(f"{OAI}error")
    response_date = (root.findtext(f"{OAI}responseDate") or "").strip() or None
    if err is not None:
        if err.get("code") == "noRecordsMatch":
            return OaiPage([], [], None, response_date, 0)
        raise OaiError(err.get("code"), (err.text or "").strip())
    records, deleted = [], []
    for rec in root.iter(f"{OAI}record"):
        header = rec.find(f"{OAI}header")
        oai_id = (header.findtext(f"{OAI}identifier") or "").strip() if header is not None else ""
        if header is not None and header.get("status") == "deleted":
            if oai_id:
                deleted.append(oai_id)
            continue
        md = rec.find(f"{OAI}metadata")
        if md is None:
            continue
        vals = lambda tag: [(e.text or "").strip() for e in md.iter(f"{DC}{tag}") if (e.text or "").strip()]
        titles = vals("title")
        if not titles:
            continue
        idents = vals("identifier") + vals("relation")
        dois = {d for d in (normalize_doi(m.group(0)) for i in idents for m in [DOI_RE.search(i)] if m) if d}
        url = next((i for i in idents if i.startswith("http") and "doi.org" not in i and not i.lower().endswith(".pdf")), None)
        pdf = next((i for i in idents if i.startswith("http") and i.lower().endswith(".pdf")), None)
        formats = " ".join(vals("format")).lower()
        access = _access_from_rights(vals("rights"))
        records.append(
            IrisRecord(
                title=titles[0],
                authors=vals("creator"),
                dois=dois,
                url=url,
                pdf_url=pdf,
                access=access,
                fulltext=True if (pdf or "pdf" in formats) else None,
                oai_id=oai_id or None,
                meta=_meta(vals),
            )
        )
    tok_el = root.find(f"{OAI}ListRecords/{OAI}resumptionToken")
    token = (tok_el.text or "").strip() if tok_el is not None and tok_el.text else None
    size = tok_el.get("completeListSize") if tok_el is not None else None
    return OaiPage(records, deleted, token or None, response_date,
                   int(size) if size and size.isdigit() else None)


def harvest(http, oai_url: str, metadata_prefix: str = "oai_dc", oai_set: str | None = None) -> list[IrisRecord]:
    """One-shot harvest of everything (no persistence). See IrisIndex for the resumable version."""
    params: dict = {"verb": "ListRecords", "metadataPrefix": metadata_prefix}
    if oai_set:
        params["set"] = oai_set
    out: list[IrisRecord] = []
    while True:
        r = http.get(oai_url, params=params)
        if r.status_code != 200:
            raise RuntimeError(f"IRIS OAI-PMH returned HTTP {r.status_code}")
        page = parse_oai_page_full(r.text)
        out.extend(page.records)
        if not page.token:
            return out
        params = {"verb": "ListRecords", "resumptionToken": page.token}


def oai_identifier(oai_url: str, handle: str) -> str:
    """'12345/678' -> 'oai:iris.example.it:12345/678' (DSpace/IRIS convention: oai:<host>:<handle>)."""
    from urllib.parse import urlparse
    return f"oai:{urlparse(oai_url).netloc}:{handle}"


def fetch_records_by_handle(http, oai_url: str, handles: list[str], metadata_prefix: str = "oai_dc",
                            should_stop=None) -> list[IrisRecord]:
    """One OAI GetRecord per handle (cached on disk). Unknown/deleted handles are skipped."""
    out: list[IrisRecord] = []
    for i, h in enumerate(handles, 1):
        if should_stop and should_stop():
            raise HarvestInterrupted("IRIS lookup stopped")
        r = http.get(oai_url, params={"verb": "GetRecord", "metadataPrefix": metadata_prefix,
                                      "identifier": oai_identifier(oai_url, h)})
        if r.status_code != 200:
            raise RuntimeError(f"IRIS OAI-PMH returned HTTP {r.status_code}")
        try:
            page = parse_oai_page_full(r.text)
        except OaiError as exc:
            if exc.code == "idDoesNotExist":
                log.info("IRIS: handle %s not found (skipped)", h)
                continue
            raise
        out.extend(page.records)
        if i % 10 == 0 or i == len(handles):
            log.info("IRIS: %d/%d records linked from ORCID fetched", i, len(handles))
    return out


ATOM = "{http://www.w3.org/2005/Atom}"
ORE_AGGREGATES = "http://www.openarchives.org/ore/terms/aggregates"


def parse_ore_pdf_links(xml_text: str) -> list[str]:
    """PDF bitstream URLs listed in an OAI 'ore' record (DSpace/IRIS), ORIGINAL bundle first."""
    root = ET.fromstring(xml_text)
    err = root.find(f"{OAI}error")
    if err is not None:
        if err.get("code") in ("idDoesNotExist", "cannotDisseminateFormat", "noRecordsMatch"):
            return []
        raise OaiError(err.get("code"), (err.text or "").strip())
    links = []
    for link in root.iter(f"{ATOM}link"):
        if link.get("rel") != ORE_AGGREGATES:
            continue
        href = link.get("href") or ""
        if (link.get("type") or "").lower() == "application/pdf" or href.lower().endswith(".pdf"):
            links.append(href)
    return list(dict.fromkeys(links))


def handle_from_url(url: str | None) -> str | None:
    m = re.search(r"hdl\.handle\.net/(\d+/\d+)", url or "") or re.search(r"/handle/(\d+/\d+)", url or "")
    return m.group(1) if m else None


class HarvestInterrupted(RuntimeError):
    pass


class IrisIndex:
    """IRIS records harvested via OAI-PMH, stored in <cache>/iris/index.json.gz.

    - First run: full harvest, checkpointed every CHECKPOINT_EVERY pages so it can resume.
    - Later runs: incremental, `from=<date of last complete harvest>` (only changed/deleted records).
    Pages are not stored in the HTTP cache (the index replaces it).
    """

    def __init__(self, path, oai_url, metadata_prefix="oai_dc", oai_set=None):
        self.path = Path(path)
        self.oai_url, self.metadata_prefix, self.oai_set = oai_url, metadata_prefix, oai_set
        self.records: dict[str, IrisRecord] = {}
        self.harvested_until: str | None = None
        self.resume: dict | None = None
        self._load()

    def _key(self) -> dict:
        return {"oai_url": self.oai_url, "metadata_prefix": self.metadata_prefix, "set": self.oai_set}

    def _load(self):
        if not self.path.exists():
            return
        try:
            with gzip.open(self.path, "rt", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            log.warning("IRIS index %s unreadable, starting over", self.path)
            return
        if data.get("source") != self._key():
            log.info("IRIS settings changed, starting a new index")
            return
        for k, r in data.get("records", {}).items():
            r["dois"] = set(r.get("dois") or [])
            self.records[k] = IrisRecord(**r)
        self.harvested_until = data.get("harvested_until")
        self.resume = data.get("resume")

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"source": self._key(), "harvested_until": self.harvested_until, "resume": self.resume,
                "records": {k: {**asdict(r), "dois": sorted(r.dois)} for k, r in self.records.items()}}
        tmp = self.path.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        tmp.replace(self.path)

    @staticmethod
    def status(path) -> dict:
        """Cheap summary for the UI (reads the file)."""
        path = Path(path)
        if not path.exists():
            return {"exists": False}
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return {"exists": False}
        return {"exists": True, "records": len(data.get("records", {})),
                "harvested_until": data.get("harvested_until"), "incomplete": bool(data.get("resume"))}

    def _apply(self, page: OaiPage):
        for oid in page.deleted:
            self.records.pop(oid, None)
        for i, rec in enumerate(page.records):
            self.records[rec.oai_id or f"_noid_{len(self.records)}_{i}"] = rec

    def update(self, http, should_stop=None) -> list[IrisRecord]:
        if self.resume:
            params = {"verb": "ListRecords", "resumptionToken": self.resume["token"]}
            started, mode = self.resume["started"], self.resume.get("mode", "full")
            log.info("IRIS: resuming %s harvest (%d records so far)", mode, len(self.records))
        else:
            params = {"verb": "ListRecords", "metadataPrefix": self.metadata_prefix}
            if self.oai_set:
                params["set"] = self.oai_set
            mode = "full"
            if self.harvested_until:
                params["from"] = self.harvested_until[:10]
                mode = "incremental"
            started = None
            log.info("IRIS: %s harvest%s", mode, f" from {params['from']}" if "from" in params else
                     " (first time: the whole repository, this takes a while)")
        pages = 0
        while True:
            if should_stop and should_stop():
                self.save()
                raise HarvestInterrupted("IRIS harvest stopped; it will resume on the next run")
            r = http.get(self.oai_url, params=params, use_cache=False)
            if r.status_code != 200:
                self.save()
                raise RuntimeError(f"IRIS OAI-PMH returned HTTP {r.status_code}")
            try:
                page = parse_oai_page_full(r.text)
            except OaiError as exc:
                if exc.code == "badResumptionToken" and self.resume:
                    log.warning("IRIS: resumption token expired, restarting the %s harvest", mode)
                    self.resume = None
                    self.save()
                    return self.update(http, should_stop)
                raise
            started = started or page.response_date
            self._apply(page)
            pages += 1
            if page.complete_size and pages % 5 == 0:
                log.info("IRIS: %d records indexed (list size %d)", len(self.records), page.complete_size)
            if not page.token:
                self.harvested_until = started
                self.resume = None
                self.save()
                log.info("IRIS: harvest complete, %d records", len(self.records))
                return list(self.records.values())
            params = {"verb": "ListRecords", "resumptionToken": page.token}
            self.resume = {"token": page.token, "started": started, "mode": mode}
            if pages % CHECKPOINT_EVERY == 0:
                self.save()


def load_export_csv(path: str) -> list[IrisRecord]:
    out = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            title = (row.get("title") or "").strip()
            if not title:
                continue
            doi = normalize_doi(row.get("doi"))
            access = (row.get("access") or "").strip().lower() or None
            pdf = (row.get("pdf_url") or "").strip() or None
            out.append(
                IrisRecord(
                    title=title,
                    authors=[a.strip() for a in (row.get("authors") or "").split(";") if a.strip()],
                    dois={doi} if doi else set(),
                    url=(row.get("url") or "").strip() or None,
                    pdf_url=pdf,
                    access=access,
                    fulltext=bool(pdf) or None,
                )
            )
    return out


class IrisSource:
    name = "iris"
    bulk = True  # records are loaded once per run; per-paper retries do not refetch them

    def __init__(self, records=None, loader=None, missing_evidence="no title match", http=None, oai_url=None):
        """records: preloaded list, or loader: zero-arg callable returning records (lazy, run once).

        If the loader fails, the same error is raised for every later paper (no repeated harvests).
        """
        self._records = records
        self._loader = loader
        self.missing_evidence = missing_evidence
        self.http, self.oai_url = http, oai_url
        self._error: Exception | None = None
        self._doi_map: dict[str, IrisRecord] | None = None
        self._keys: list[str] = []

    @property
    def records(self) -> list[IrisRecord]:
        if self._records is None:
            if self._error:
                raise self._error
            try:
                self._records = self._loader() if self._loader else []
            except Exception as exc:
                self._error = exc
                raise
        return self._records

    def _index(self):
        if self._doi_map is None:
            recs = self.records
            self._doi_map = {d: r for r in recs for d in r.dois}
            self._keys = [normalize_title_key(r.title) for r in recs]
        return self._doi_map

    @staticmethod
    def _result(rec: IrisRecord, status: str, score: float, evidence: str) -> SourceResult:
        open_pdf = rec.pdf_url if rec.access in (None, "open") else None
        return SourceResult(
            source="iris", status=status, match_id=rec.url, url=rec.url, pdf_url=open_pdf,
            score=score, evidence=evidence, fulltext=rec.fulltext,
            open_access={"open": True, "embargoed": False, "closed": False}.get(rec.access),
            title=rec.title, authors=list(rec.authors), meta=dict(rec.meta) or None,
        )

    def pdf_urls(self, result: SourceResult) -> list[str]:
        """Fallback PDF links for a matched IRIS record: the files listed in its OAI 'ore' record.

        Only for records that are open access or whose access is unknown (embargoed/closed are skipped);
        a restricted file comes back as an HTML login page and is rejected by the PDF check."""
        if result.pdf_url:
            return [result.pdf_url]
        if not (self.http and self.oai_url) or result.open_access is False:
            return []
        handle = handle_from_url(result.url)
        if not handle:
            return []
        r = self.http.get(self.oai_url, params={"verb": "GetRecord", "metadataPrefix": "ore",
                                                "identifier": oai_identifier(self.oai_url, handle)})
        if r.status_code != 200:
            raise RuntimeError(f"IRIS OAI-PMH returned HTTP {r.status_code}")
        return parse_ore_pdf_links(r.text)

    def check(self, paper: Paper) -> SourceResult:
        doi_map = self._index()
        if paper.doi and paper.doi in doi_map:
            return self._result(doi_map[paper.doi], PRESENT, 1.0, "doi")
        key = normalize_title_key(paper.title)
        hit = process.extractOne(key, self._keys, scorer=fuzz.ratio) if key and self._keys else None
        if not hit or hit[1] <= 0:
            return SourceResult("iris", MISSING, evidence=self.missing_evidence)
        best, best_score = self.records[hit[2]], hit[1] / 100.0
        status = classify_title_match(best_score, shares_author(paper.authors, best.authors))
        if status == MISSING:
            return SourceResult("iris", MISSING, score=best_score,
                                evidence=f"{self.missing_evidence}; best title {best_score:.2f}")
        return self._result(best, status, best_score, f"title {best_score:.2f}")
