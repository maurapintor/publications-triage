"""Dashboard web app (Flask). Local use only: there is no authentication."""
from __future__ import annotations

import logging
from pathlib import Path

from flask import Flask, Response, abort, jsonify, render_template, request, send_file

from ..citations import FORMATS, format_citations
from ..enrich import enrich_records
from ..rankings import VALID_RANKS
import re

from ..config import DEFAULT_IRIS_URL, apply_setup, setup_needed
from ..config import IRIS_MODES, VALID_SOURCES, ConfigError, iris_mode, rate_limit_table, config_to_dict, load_config, save_config, valid_columns
from ..report import load_json
from ..sources import iris_index_path
from ..sources.iris import IrisIndex
from .jobs import JobManager

log = logging.getLogger("triage")


def default_run_fn(config_path):
    def run(options, progress, should_stop):
        from ..cli import execute_run, select_sources

        config = load_config(config_path)
        select_sources(config, options.get("sources"))

        def on_progress(i, n, rec):
            statuses = ", ".join(f"{k}={v.status}" for k, v in rec.results.items())
            acks = ",".join(rec.ack.acknowledged) if rec.ack else "no pdf"
            log.info("[%d/%d] %s (%s; acks: %s)", i, n, rec.paper.title[:60], statuses, acks or "none")
            progress(i, n, rec.paper.title)

        out = execute_run(
            config,
            limit=options.get("limit") or None,
            download=not options.get("no_download", False),
            refresh=bool(options.get("refresh_cache")),
            progress=on_progress,
            should_stop=should_stop,
            reload_pdfs=bool(options.get("reload_pdfs")),
        )
        recs = out.records
        return {"papers": len(recs), "pdfs": sum(1 for r in recs if r.pdf_path),
                "pdfs_cached": sum(1 for r in recs if r.pdf_cached),
                "acknowledged": sum(1 for r in recs if r.ack and r.ack.acknowledged),
                "ack_pages": sum(1 for r in recs if r.ack_page_pdf),
                "csv": str(out.csv_path), "json": str(out.json_path)}
    return run


def _parse_run_options(body) -> dict:
    opts = {}
    if body.get("limit") not in (None, "", 0):
        try:
            opts["limit"] = int(body["limit"])
        except (TypeError, ValueError):
            raise ConfigError("limit must be an integer") from None
        if opts["limit"] < 1:
            raise ConfigError("limit must be >= 1")
    if body.get("sources"):
        if not isinstance(body["sources"], list):
            raise ConfigError("sources must be a list")
        opts["sources"] = [str(s) for s in body["sources"]]
    opts["refresh_cache"] = bool(body.get("refresh_cache"))
    opts["reload_pdfs"] = bool(body.get("reload_pdfs"))
    opts["no_download"] = bool(body.get("no_download"))
    return opts


def create_app(config_path, run_fn=None, http_factory=None) -> Flask:
    """http_factory(config) -> HttpClient for the ranking downloads (tests pass a fake)."""
    config_path = Path(config_path)
    if http_factory is None:
        def http_factory(c):
            from ..cli import make_http
            return make_http(c)
    app = Flask(__name__)
    app.json.sort_keys = False
    jobs = JobManager(run_fn or default_run_fn(config_path))
    app.extensions["pubtriage_jobs"] = jobs

    def config():
        return load_config(config_path)

    def latest():
        path = Path(config().output_dir) / "latest.json"
        return load_json(path) if path.exists() else None

    def record(pid):
        data = latest()
        for r in (data or {}).get("records", []):
            if r["id"] == pid:
                return r
        abort(404)

    def send_record_file(value):
        if not value:
            abort(404)
        path = Path(value).resolve()
        if path.suffix.lower() != ".pdf" or not path.is_file():
            abort(404)
        return send_file(path, mimetype="application/pdf")

    @app.errorhandler(ConfigError)
    def _config_error(exc):
        reason = setup_needed(config_path)
        if reason:
            return jsonify(error=f"setup needed: {reason}", setup_needed=True), 409
        return jsonify(error=str(exc)), 400

    # ---------- first-start setup ----------
    def setup_http():
        if app.config.get("SETUP_HTTP") is not None:
            return app.config["SETUP_HTTP"]
        from ..http import DEFAULT_OFFICIAL_HOSTS, HttpClient
        return HttpClient(None, {"pub.orcid.org": 0.2, "default": 1.0}, "cache", official_hosts=DEFAULT_OFFICIAL_HOSTS)

    @app.get("/api/setup")
    def setup_status():
        reason = setup_needed(config_path)
        return jsonify(needed=bool(reason), reason=reason, path=str(config_path), default_iris_url=DEFAULT_IRIS_URL)

    @app.post("/api/setup/orcid")
    def setup_check_orcid():
        """Body: {orcid}. Returns the name and number of works on ORCID, to confirm the iD."""
        orcid = re.sub(r"^https?://orcid\.org/", "", ((request.get_json(silent=True) or {}).get("orcid") or "").strip())
        if not re.fullmatch(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]", orcid):
            return jsonify(error="an ORCID iD looks like 0000-0002-1825-0097"), 400
        h = setup_http()
        acc = {"Accept": "application/json"}
        person = h.get(f"https://pub.orcid.org/v3.0/{orcid}/person", use_cache=False, headers=acc)
        if person.status_code == 404:
            return jsonify(error="this ORCID iD does not exist"), 404
        if person.status_code != 200:
            return jsonify(error=f"ORCID returned HTTP {person.status_code}"), 502
        n = person.json().get("name") or {}
        name = " ".join(x for x in ((n.get("given-names") or {}).get("value"), (n.get("family-name") or {}).get("value")) if x)
        works = h.get(f"https://pub.orcid.org/v3.0/{orcid}/works", use_cache=False, headers=acc)
        count = len(works.json().get("group", [])) if works.status_code == 200 else None
        return jsonify(orcid=orcid, name=name or None, works=count)

    @app.post("/api/setup")
    def setup_save():
        """Body: {name, orcid, email, iris_url, projects: [{name, aliases}]}."""
        if jobs.running:
            return jsonify(error="a run is in progress"), 409
        b = request.get_json(silent=True) or {}
        projects = [{"name": p["name"].strip(), **({"aliases": p["aliases"]} if p.get("aliases") else {})}
                    for p in (b.get("projects") or []) if isinstance(p, dict) and (p.get("name") or "").strip()]
        if not projects:
            return jsonify(error="add at least one project to look for"), 400
        if not (b.get("orcid") or "").strip():
            return jsonify(error="your ORCID iD is needed"), 400
        apply_setup(config_path, (b.get("name") or "").strip(), b["orcid"].strip(), (b.get("email") or "").strip(),
                    (b.get("iris_url") or "").strip(), projects)
        return jsonify(ok=True)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/papers")
    def papers():
        from ..paperlist import _excluded
        c = config()
        data = latest() or {"generated_at": None, "sources": [], "projects": [], "records": []}
        excl = c.paper_list.exclude_titles
        hidden = [r["title"] for r in data.get("records", []) if _excluded(r["title"], excl)]
        data["records"] = [r for r in data.get("records", []) if not _excluded(r["title"], excl)]
        rankings_info = enrich_records(data.get("records", []), c)
        return jsonify(
            rankings=rankings_info,
            excluded_hidden=hidden,
            top_labels=c.rankings.top_quartiles + c.rankings.top_core,
            valid_ranks=list(VALID_RANKS),
            generated_at=data.get("generated_at"),
            sources=data.get("sources", []),
            configured_sources=c.sources,
            projects=[p.name for p in c.projects],
            columns=c.dashboard.columns,
            default_filters=c.dashboard.default_filters,
            valid_columns=valid_columns(c.projects),
            ranking_pages={"core": "https://portal.core.edu.au/conf-ranks/",
                           "sjr": f"https://www.scimagojr.com/journalrank.php?area={c.rankings.sjr_area}"
                                  + (f"&year={c.rankings.sjr_year}" if c.rankings.sjr_year else "")},
            citation_formats={k: v[0] for k, v in FORMATS.items()},
            iris=None if "iris" not in c.sources else {
                "mode": "export" if c.iris.export_path else iris_mode(c),
                "orcid": c.user.orcid,
                "index": IrisIndex.status(iris_index_path(c))
                if not c.iris.export_path and iris_mode(c) == "harvest" else None,
            },
            records=data.get("records", []),
        )

    @app.post("/api/export")
    def export():
        body = request.get_json(silent=True) or {}
        fmt = body.get("format", "bibtex")
        if fmt not in FORMATS:
            return jsonify(error=f"unknown format {fmt!r}; valid: {list(FORMATS)}"), 400
        by_id = {r["id"]: r for r in (latest() or {}).get("records", [])}
        ids = body.get("ids")
        recs = [by_id[i] for i in ids if i in by_id] if isinstance(ids, list) else list(by_id.values())
        if not recs:
            return jsonify(error="no papers to export"), 400
        text = format_citations(recs, fmt)
        ext = FORMATS[fmt][1]
        headers = {}
        if body.get("download", True):
            name = "papers.bib" if fmt == "bibtex" else f"citations_{fmt}.txt"
            headers["Content-Disposition"] = f'attachment; filename="{name}"'
        ctype = ("application/x-bibtex" if ext == "bib" else "text/plain") + "; charset=utf-8"
        return Response(text, headers=headers, content_type=ctype)

    @app.get("/pdf/<path:pid>")
    def pdf(pid):
        return send_record_file(record(pid).get("pdf_path"))

    @app.get("/ackpage/<path:pid>")
    def ackpage(pid):
        return send_record_file(record(pid).get("ack_page_pdf"))

    @app.get("/api/status")
    def status():
        return jsonify(jobs.status())

    @app.post("/api/run")
    def run():
        opts = _parse_run_options(request.get_json(silent=True) or {})
        if opts.get("sources"):
            from ..cli import select_sources
            select_sources(config(), opts["sources"])  # validate before starting
        if not jobs.start(opts):
            return jsonify(error="a run is already in progress"), 409
        return jsonify(jobs.status()), 202

    @app.post("/api/run/cancel")
    def cancel():
        if not jobs.cancel():
            return jsonify(error="no run in progress"), 409
        return jsonify(jobs.status())

    @app.post("/api/rankings/download")
    def rankings_download():
        """Body: {which: 'core'|'sjr'}. Downloads the list with the tool's HTTP client (robots.txt checked)."""
        from ..http import BlockedByPolicy
        from ..rankings_download import download

        which = (request.get_json(silent=True) or {}).get("which")
        if which not in ("core", "sjr"):
            return jsonify(error="which must be 'core' or 'sjr'"), 400
        c = config()
        try:
            result = download(c, http_factory(c), which)
        except (BlockedByPolicy, PermissionError) as exc:
            return jsonify(error=f"not allowed: {exc}"), 403
        except Exception as exc:  # noqa: BLE001 - shown to the user
            return jsonify(error=str(exc)), 502
        return jsonify(result)

    @app.post("/api/rankings/upload")
    def rankings_upload():
        """multipart: which=core|sjr, file=<csv>. Validated, then stored in cache/rankings/ (or the configured path)."""
        from ..rankings_download import save_list

        which = request.form.get("which")
        f = request.files.get("file")
        if which not in ("core", "sjr") or f is None:
            return jsonify(error="send which=core|sjr and a file"), 400
        data = f.read(30 * 1024 * 1024 + 1)
        if len(data) > 30 * 1024 * 1024:
            return jsonify(error="file too large (max 30 MB)"), 413
        try:
            return jsonify(save_list(config(), which, data))
        except RuntimeError as exc:
            return jsonify(error=str(exc).replace("download it by hand", "is this the right file?")), 400

    @app.post("/api/exclude")
    def exclude():
        """Body: {title, exclude: true|false}. Adds/removes the exact title in paper_list.exclude_titles."""
        body = request.get_json(silent=True) or {}
        title = (body.get("title") or "").strip()
        if not title:
            return jsonify(error="missing title"), 400
        data = config_to_dict(config())
        titles = list(data["paper_list"].get("exclude_titles") or [])
        if body.get("exclude", True):
            if title.lower() not in (t.lower() for t in titles):
                titles.append(title)
        else:
            titles = [t for t in titles if t.lower() != title.lower()]
        data["paper_list"]["exclude_titles"] = titles
        save_config(data, config_path)
        return jsonify(exclude_titles=titles)

    @app.post("/api/validate")
    def validate():
        """Body: {id, iris_vs_arxiv: 'accepted'|null, rank: 'Q1'|'A*'|...|null, note}. Keys left out are kept;
        null removes. Saved in config `validations` (applies immediately, no re-run)."""
        body = request.get_json(silent=True) or {}
        pid = body.get("id")
        if not isinstance(pid, str) or not pid:
            return jsonify(error="missing paper id"), 400
        data = config_to_dict(config())
        current = dict(data["validations"].get(pid) or {})
        for k in ("iris_vs_arxiv", "rank", "note"):
            if k in body:
                if body[k] in (None, ""):
                    current.pop(k, None)
                else:
                    current[k] = body[k]
        if current:
            data["validations"][pid] = current
        else:
            data["validations"].pop(pid, None)
        save_config(data, config_path)
        return jsonify(validation=current or None)

    @app.get("/api/config")
    def get_config():
        c = config()
        return jsonify(config=config_to_dict(c), valid_sources=list(VALID_SOURCES), iris_modes=list(IRIS_MODES),
                       rate_limits=rate_limit_table(c), iris_endpoint=c.iris.endpoint,
                       valid_columns=valid_columns(c.projects), path=str(config_path))

    @app.put("/api/config")
    def put_config():
        if jobs.running:
            return jsonify(error="cannot change the config while a run is in progress"), 409
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(error="expected a JSON object"), 400
        save_config(data, config_path)
        return get_config()

    return app
