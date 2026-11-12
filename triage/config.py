"""Configuration loading and validation."""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

VALID_SOURCES = ("iris", "arxiv", "openalex", "unpaywall")
DEFAULT_PDF_PRIORITY = ("arxiv", "unpaywall", "openalex", "iris")  # IRIS last: only when nothing else has a PDF


class ConfigError(ValueError):
    pass


@dataclass
class Project:
    name: str
    aliases: list[str] = field(default_factory=list)

    @property
    def terms(self) -> list[str]:
        return [self.name, *self.aliases]


DEFAULT_IRIS_URL = "https://iris.unica.it"


@dataclass
class IrisConfig:
    url: str | None = DEFAULT_IRIS_URL  # the institution's IRIS address; None = no IRIS
    oai_url: str | None = None  # advanced: OAI-PMH endpoint (default: <url>/oai/request)
    metadata_prefix: str = "oai_dc"
    set: str | None = None
    export_path: str | None = None
    mode: str = "auto"  # auto | orcid | harvest

    @property
    def endpoint(self) -> str | None:
        """OAI-PMH endpoint: the machine interface every IRIS/DSpace repository offers at <url>/oai/request."""
        if self.oai_url:
            return self.oai_url
        return self.url.rstrip("/") + "/oai/request" if self.url else None

    @property
    def host(self) -> str | None:
        from urllib.parse import urlparse
        e = self.endpoint
        return urlparse(e).netloc if e else None


@dataclass
class UserConfig:
    name: str | None = None
    orcid: str | None = None  # e.g. 0000-0002-1825-0097
    email: str | None = None  # optional contact for the APIs (User-Agent, mailto=); required only by Unpaywall


@dataclass
class PaperListConfig:
    source: str = "orcid"  # orcid: built at each run from ORCID + Crossref (+ arXiv feed) | file
    file: str | None = None  # only for source: file (publications.json format)
    arxiv_author_id: str | None = None  # e.g. lovelace_a_1 (arxiv.org/a/<id>): arXiv ids + arXiv-only preprints
    exclude_titles: list[str] = field(default_factory=list)  # exact titles to skip (case-insensitive)


PAPER_LIST_SOURCES = ("orcid", "file")
IRIS_MODES = ("auto", "orcid", "harvest")
_ORCID_RE = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$")


def iris_mode(config) -> str:
    """Effective IRIS lookup: 'orcid' (records linked from the user's ORCID) or 'harvest' (whole repository)."""
    m = config.iris.mode
    if m == "auto":
        return "orcid" if config.user.orcid else "harvest"
    return m


# Minimum seconds between two requests to a host. Built in, so nobody has to know the server names;
# `rate_limits` in the config only overrides them. A robots.txt Crawl-delay is always respected on top.
DEFAULT_RATE_LIMITS = {
    "export.arxiv.org": (3.0, "arXiv API terms: at most one request every 3 s"),
    "arxiv.org": (15.0, "arxiv.org robots.txt Crawl-delay (author feed)"),
    "pub.orcid.org": (0.2, "ORCID public API"),
    "api.crossref.org": (0.2, "Crossref API (polite pool)"),
    "api.openalex.org": (0.2, "OpenAlex API"),
    "api.unpaywall.org": (0.2, "Unpaywall API"),
    "portal.core.edu.au": (2.0, "CORE portal (ranking download)"),
    "default": (1.0, "any other host"),
}
IRIS_DEFAULT_DELAY = (5.0, "IRIS repository (typical IRIS robots.txt Crawl-delay: 5)")


def rate_limit_table(config) -> list[dict]:
    """[{host, default, override, effective, why}] for the hosts this config can contact."""
    rows = []
    defaults = dict(DEFAULT_RATE_LIMITS)
    if config.iris.host:
        defaults = {config.iris.host: IRIS_DEFAULT_DELAY, **defaults}
    for host, (d, why) in defaults.items():
        o = config.rate_limits.get(host)
        rows.append({"host": host, "default": d, "override": o, "effective": d if o is None else o, "why": why})
    for host, o in config.rate_limits.items():
        if host not in defaults:
            rows.append({"host": host, "default": None, "override": o, "effective": o, "why": "custom"})
    return rows


def effective_rate_limits(config) -> dict[str, float]:
    return {r["host"]: r["effective"] for r in rate_limit_table(config)}


BASE_COLUMNS = ("title", "year", "authors", "doi", "venue", "pdf_source", "acks", "ack_status", "rank")
DEFAULT_COLUMNS = ("title", "year", "iris", "arxiv", "rank", "acks")


@dataclass
class RankingsConfig:
    core_csv: str | None = None  # CORE portal export (portal.core.edu.au/conf-ranks -> Export)
    sjr_csv: str | None = None  # SCImago CSV (journalrank.php, area Computer Science -> Download data)
    sjr_categories: list[str] = field(default_factory=list)  # quartile counted only in these (default: CS)
    top_quartiles: list[str] = field(default_factory=lambda: ["Q1"])
    top_core: list[str] = field(default_factory=lambda: ["A*"])
    venues: dict[str, str] = field(default_factory=dict)  # manual: venue name or acronym -> rank
    core_source: str = "ICORE2026"  # CORE edition for the download button
    sjr_area: str = "1700"  # SCImago subject area for the download (1700 = Computer Science)
    sjr_year: str | None = None  # SCImago year for the download (None = latest)


VALIDATION_KEYS = ("iris_vs_arxiv", "rank", "note")


@dataclass
class DashboardConfig:
    host: str = "127.0.0.1"
    port: int = 8000
    columns: list[str] = field(default_factory=lambda: list(DEFAULT_COLUMNS))
    default_filters: dict[str, str] = field(default_factory=dict)


def valid_columns(projects) -> list[str]:
    return [*BASE_COLUMNS, *VALID_SOURCES, *(f"ack_{p.name}" for p in projects)]


@dataclass
class Config:
    projects: list[Project]
    contact_email: str | None = None
    cache_dir: str = "cache"
    output_dir: str = "output"
    sources: list[str] = field(default_factory=lambda: ["iris", "arxiv"])
    pdf_priority: list[str] = field(default_factory=lambda: list(DEFAULT_PDF_PRIORITY))
    rate_limits: dict[str, float] = field(default_factory=dict)
    iris: IrisConfig = field(default_factory=IrisConfig)
    user: UserConfig = field(default_factory=UserConfig)
    paper_list: PaperListConfig = field(default_factory=PaperListConfig)
    rankings: RankingsConfig = field(default_factory=RankingsConfig)
    validations: dict[str, dict] = field(default_factory=dict)  # paper id -> {iris_vs_arxiv, rank, note}
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)


def _parse_dashboard(raw, projects) -> DashboardConfig:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError("'dashboard' must be a mapping")
    d = DashboardConfig()
    d.host = str(raw.get("host") or d.host)
    try:
        d.port = int(raw.get("port") or d.port)
    except (TypeError, ValueError):
        raise ConfigError("dashboard.port must be an integer") from None
    if not 1 <= d.port <= 65535:
        raise ConfigError("dashboard.port must be between 1 and 65535")
    cols = raw.get("columns")
    if cols is not None:
        if not isinstance(cols, list) or not cols or not all(isinstance(c, str) for c in cols):
            raise ConfigError("dashboard.columns must be a non-empty list of column names")
        valid = valid_columns(projects)
        bad = [c for c in cols if c not in valid]
        if bad:
            raise ConfigError(f"unknown dashboard.columns {bad}; valid: {valid}")
        d.columns = list(cols)
    filters = raw.get("default_filters") or {}
    if not isinstance(filters, dict) or not all(isinstance(v, str) for v in filters.values()):
        raise ConfigError("dashboard.default_filters must map a column to a value, e.g. {iris: missing}")
    d.default_filters = {str(k): v for k, v in filters.items()}
    return d


def _parse_paper_list(raw, legacy_input_path, user) -> PaperListConfig:
    if raw is None and legacy_input_path:  # old configs: input_path -> paper_list {source: file}
        raw = {"source": "file", "file": legacy_input_path}
    raw = raw or {}
    if not isinstance(raw, dict):
        raise ConfigError("'paper_list' must be a mapping")
    pl = PaperListConfig(
        source=str(raw.get("source") or "orcid"),
        file=str(raw["file"]) if raw.get("file") else None,
        arxiv_author_id=str(raw["arxiv_author_id"]).strip() if raw.get("arxiv_author_id") else None,
        exclude_titles=_str_list(raw.get("exclude_titles"), "paper_list.exclude_titles"),
    )
    if pl.source not in PAPER_LIST_SOURCES:
        raise ConfigError(f"paper_list.source must be one of {list(PAPER_LIST_SOURCES)}")
    if pl.source == "orcid" and not user.orcid:
        raise ConfigError("paper_list.source 'orcid' needs user.orcid (or use source: file with paper_list.file)")
    if pl.source == "file" and not pl.file:
        raise ConfigError("paper_list.source 'file' needs paper_list.file")
    if pl.arxiv_author_id and not re.fullmatch(r"[A-Za-z0-9_.\-]+", pl.arxiv_author_id):
        raise ConfigError("paper_list.arxiv_author_id should look like lovelace_a_1 (from arxiv.org/a/<id>)")
    return pl


def _str_list(raw, name) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise ConfigError(f"{name} must be a list of strings")
    return [x.strip() for x in raw if x.strip()]


def _parse_rankings(raw) -> RankingsConfig:
    from .rankings import CORE_RANKS, CS_CATEGORIES, QUARTILES, VALID_RANKS

    raw = raw or {}
    if not isinstance(raw, dict):
        raise ConfigError("'rankings' must be a mapping")
    r = RankingsConfig(
        core_csv=str(raw["core_csv"]) if raw.get("core_csv") else None,
        sjr_csv=str(raw["sjr_csv"]) if raw.get("sjr_csv") else None,
        sjr_categories=_str_list(raw.get("sjr_categories"), "rankings.sjr_categories") or list(CS_CATEGORIES),
        top_quartiles=_str_list(raw.get("top_quartiles"), "rankings.top_quartiles") or ["Q1"],
        top_core=_str_list(raw.get("top_core"), "rankings.top_core") or ["A*"],
        core_source=str(raw.get("core_source") or "ICORE2026").strip(),
        sjr_area=str(raw.get("sjr_area") or "1700").strip(),
        sjr_year=str(raw["sjr_year"]).strip() if raw.get("sjr_year") else None,
    )
    if not re.fullmatch(r"[A-Za-z]+\d{4}", r.core_source):
        raise ConfigError(f"rankings.core_source {r.core_source!r} should look like ICORE2023 or CORE2021")
    if not r.sjr_area.isdigit():
        raise ConfigError("rankings.sjr_area must be a SCImago area code, e.g. 1700 (Computer Science)")
    if r.sjr_year and not re.fullmatch(r"(19|20)\d\d", r.sjr_year):
        raise ConfigError("rankings.sjr_year must be a year, e.g. 2024")
    if bad := [q for q in r.top_quartiles if q not in QUARTILES]:
        raise ConfigError(f"rankings.top_quartiles: unknown {bad}; valid: {list(QUARTILES)}")
    if bad := [q for q in r.top_core if q not in CORE_RANKS]:
        raise ConfigError(f"rankings.top_core: unknown {bad}; valid: {list(CORE_RANKS)}")
    venues = raw.get("venues") or {}
    if not isinstance(venues, dict):
        raise ConfigError("rankings.venues must map a venue name or acronym to a rank, e.g. {ICDAR: A}")
    for k, v in venues.items():
        if str(v) not in VALID_RANKS:
            raise ConfigError(f"rankings.venues[{k!r}] = {v!r}: valid ranks are {list(VALID_RANKS)}")
    r.venues = {str(k): str(v) for k, v in venues.items()}
    return r


def _parse_validations(raw) -> dict[str, dict]:
    from .rankings import VALID_RANKS

    raw = raw or {}
    if not isinstance(raw, dict):
        raise ConfigError("'validations' must map a paper id to {iris_vs_arxiv, rank, note}")
    out = {}
    for pid, v in raw.items():
        if not isinstance(v, dict):
            raise ConfigError(f"validations[{pid!r}] must be a mapping")
        if bad := [k for k in v if k not in VALIDATION_KEYS]:
            raise ConfigError(f"validations[{pid!r}]: unknown keys {bad}; valid: {list(VALIDATION_KEYS)}")
        if v.get("iris_vs_arxiv") not in (None, "accepted"):
            raise ConfigError(f"validations[{pid!r}].iris_vs_arxiv must be 'accepted'")
        if v.get("rank") is not None and str(v["rank"]) not in VALID_RANKS:
            raise ConfigError(f"validations[{pid!r}].rank: valid ranks are {list(VALID_RANKS)}")
        clean = {k: str(val) for k, val in v.items() if val not in (None, "")}
        if clean:
            out[str(pid)] = clean
    return out


def _parse_projects(raw) -> list[Project]:
    if not isinstance(raw, list) or not raw:
        raise ConfigError("'projects' must be a non-empty list")
    projects = []
    for i, item in enumerate(raw):
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip():
            raise ConfigError(f"projects[{i}] must have a non-empty string 'name'")
        aliases = item.get("aliases") or []
        if not isinstance(aliases, list) or not all(isinstance(a, str) and a.strip() for a in aliases):
            raise ConfigError(f"projects[{i}].aliases must be a list of non-empty strings")
        projects.append(Project(item["name"].strip(), [a.strip() for a in aliases]))
    names = [p.name.lower() for p in projects]
    if len(set(names)) != len(names):
        raise ConfigError("duplicate project names in 'projects'")
    return projects


def load_config(path: str | Path) -> Config:
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("config must be a YAML mapping")

    user_raw = data.get("user") or {}
    if not isinstance(user_raw, dict):
        raise ConfigError("'user' must be a mapping with name, orcid, email")
    email = str(user_raw.get("email") or data.get("contact_email") or "").strip() or None
    if email and "@" not in email:
        raise ConfigError("user.email must be an email address")
    if not data.get("projects"):
        raise ConfigError("missing required config key: projects")

    iris_raw = data.get("iris") or {}
    if not isinstance(iris_raw, dict):
        raise ConfigError("'iris' must be a mapping")
    iris_url = iris_raw["url"] if "url" in iris_raw else (None if iris_raw.get("oai_url") else DEFAULT_IRIS_URL)
    has_iris = bool(iris_url or iris_raw.get("oai_url") or iris_raw.get("export_path"))
    sources = list(data.get("sources") or (["iris", "arxiv"] if has_iris else ["arxiv"]))
    bad = [s for s in sources if s not in VALID_SOURCES]
    if bad:
        raise ConfigError(f"unknown sources {bad}; valid: {list(VALID_SOURCES)}")
    priority = list(data.get("pdf_priority") or DEFAULT_PDF_PRIORITY)
    bad = [s for s in priority if s not in VALID_SOURCES]
    if bad:
        raise ConfigError(f"unknown pdf_priority entries {bad}")

    rate_limits = data.get("rate_limits") or {}
    if not isinstance(rate_limits, dict) or not all(isinstance(v, (int, float)) and v >= 0 for v in rate_limits.values()):
        raise ConfigError("rate_limits must map host -> non-negative seconds")

    iris = IrisConfig(
        url=str(iris_url).strip() if iris_url else None,
        oai_url=iris_raw.get("oai_url"),
        metadata_prefix=iris_raw.get("metadata_prefix", "oai_dc"),
        set=iris_raw.get("set"),
        export_path=iris_raw.get("export_path"),
        mode=str(iris_raw.get("mode") or "auto"),
    )
    if iris.mode not in IRIS_MODES:
        raise ConfigError(f"iris.mode must be one of {list(IRIS_MODES)}")

    if not iris.url and iris.oai_url and iris.oai_url.rstrip("/").endswith("/oai/request"):
        # older configs stored only the OAI-PMH endpoint: keep the repository address, derive the endpoint
        iris.url, iris.oai_url = iris.oai_url.rstrip("/")[: -len("/oai/request")], None
    if iris.url and not re.match(r"^https?://", iris.url):
        raise ConfigError("iris.url must start with https://, e.g. https://iris.example.it")
    orcid = str(user_raw.get("orcid") or "").strip() or None
    if orcid:
        orcid = re.sub(r"^https?://orcid\.org/", "", orcid)
        if not _ORCID_RE.match(orcid):
            raise ConfigError(f"user.orcid {orcid!r} is not an ORCID iD (format 0000-0000-0000-0000)")
    user = UserConfig(name=str(user_raw.get("name") or "").strip() or None, orcid=orcid, email=email)
    if iris.mode == "orcid" and not orcid:
        raise ConfigError("iris.mode 'orcid' needs user.orcid")
    if "unpaywall" in sources and not email:
        raise ConfigError("the Unpaywall source needs your email (user.email): Unpaywall requires one")
    if "iris" in sources and not (iris.endpoint or iris.export_path):
        raise ConfigError("source 'iris' is enabled but iris.url (your institution's IRIS address) is not set")

    projects = _parse_projects(data["projects"])
    paper_list = _parse_paper_list(data.get("paper_list"), data.get("input_path"), user)
    return Config(
        contact_email=email,
        projects=projects,
        cache_dir=str(data.get("cache_dir", "cache")),
        output_dir=str(data.get("output_dir", "output")),
        sources=sources,
        pdf_priority=priority,
        rate_limits={k: float(v) for k, v in rate_limits.items()},
        iris=iris,
        user=user,
        paper_list=paper_list,
        rankings=_parse_rankings(data.get("rankings")),
        validations=_parse_validations(data.get("validations")),
        dashboard=_parse_dashboard(data.get("dashboard"), projects),
    )


def config_to_dict(config: Config, minimal: bool = False) -> dict:
    """YAML-ready dict in reading order (inverse of load_config). minimal=True drops values equal to the
    defaults, so the saved file only holds what the user changed."""
    d = asdict(config)
    user = {"name": config.user.name, "orcid": config.user.orcid, "email": config.contact_email}
    out = {
        "user": user,
        "paper_list": d["paper_list"],
        "sources": d["sources"],
        "iris": d["iris"],
        "projects": [{"name": p.name, **({"aliases": p.aliases} if p.aliases else {})} for p in config.projects],
        "rankings": d["rankings"],
        "dashboard": d["dashboard"],
        "pdf_priority": d["pdf_priority"],
        "rate_limits": d["rate_limits"],
        "cache_dir": d["cache_dir"],
        "output_dir": d["output_dir"],
        "validations": d["validations"],
    }
    if not minimal:
        return out
    from .rankings import CS_CATEGORIES
    ref = {
        "paper_list": asdict(PaperListConfig()), "iris": asdict(IrisConfig()),
        "rankings": {**asdict(RankingsConfig()), "sjr_categories": list(CS_CATEGORIES)},
        "dashboard": asdict(DashboardConfig()), "pdf_priority": list(DEFAULT_PDF_PRIORITY), "rate_limits": {},
        "cache_dir": "cache", "output_dir": "output", "validations": {},
        "sources": ["iris", "arxiv"] if (config.iris.url or config.iris.oai_url or config.iris.export_path) else ["arxiv"],
    }
    builtin = {r["host"]: r["default"] for r in rate_limit_table(config)}
    out["rate_limits"] = {h: v for h, v in out["rate_limits"].items() if builtin.get(h) != v}
    for key, default in ref.items():
        val = out[key]
        if isinstance(val, dict) and isinstance(default, dict) and key not in ("rate_limits", "validations"):
            val = {k: v for k, v in val.items() if v != default.get(k) and v not in (None, [], {})}
            out[key] = val
        if val == default or val in ({}, None):
            out.pop(key)
    out["user"] = {k: v for k, v in user.items() if v}
    if config.iris.url is None and not config.iris.oai_url:
        out.setdefault("iris", {})["url"] = None  # IRIS switched off: must not fall back to the default
    return out


def save_config(data: dict, path: str | Path) -> Config:
    """Validate `data` with load_config, back up the existing file to <path>.bak, then write it.

    Raises ConfigError (and leaves the file untouched) if the data is invalid. YAML comments are not kept.
    """
    path = Path(path)
    fd, tmp_name = tempfile.mkstemp(suffix=".yaml", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
        config = load_config(tmp)
        header = ("# publications-triage settings (written by the dashboard; only values that differ from the\n"
                  "# defaults are stored -- see README 'Set up for a new user' for every setting).\n")
        tmp.write_text(header + yaml.safe_dump(config_to_dict(config, minimal=True), sort_keys=False,
                                               allow_unicode=True), encoding="utf-8")
        load_config(tmp)
        if path.exists():
            shutil.copyfile(path, path.with_name(path.name + ".bak"))
            shutil.copymode(path, tmp)  # keep the original permissions (mkstemp creates 0600)
        else:
            os.chmod(tmp, 0o644)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    return config
