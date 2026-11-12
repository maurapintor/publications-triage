"""Command line interface: pubtriage run|serve --config config/triage.yaml"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .config import ConfigError, load_config
from .http import DEFAULT_OFFICIAL_HOSTS, HttpClient
from .papers import load_papers, papers_from_records
from .pipeline import run_pipeline
from .report import write_csv, write_json
from .sources import build_sources

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="pubtriage", description="Publication availability + acknowledgement triage")
    sub = ap.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="check papers, download open PDFs, write the CSV + JSON")
    run.add_argument("--config", required=True)
    run.add_argument("--limit", type=int, help="only the first N papers")
    run.add_argument("--sources", help="comma-separated subset of configured sources")
    run.add_argument("--no-download", action="store_true", help="skip PDF download and acknowledgement scan")
    run.add_argument("--refresh-cache", action="store_true", help="ignore all cached HTTP responses")
    run.add_argument("--reload-pdfs", action="store_true",
                     help="re-check every paper and download all PDFs again (default: only papers without a PDF)")
    rk = sub.add_parser("rankings", help="download the CORE and/or SCImago ranking lists")
    rk.add_argument("--config", required=True)
    rk.add_argument("--core", action="store_true", help="download the CORE list (edition: rankings.core_source)")
    rk.add_argument("--core-file", help="import a CORE export CSV downloaded by hand")
    rk.add_argument("--sjr-file", help="import a SCImago CSV downloaded by hand (scripted download not allowed)")
    serve = sub.add_parser("serve", help="start the dashboard web app")
    serve.add_argument("--config", required=True)
    serve.add_argument("--host", help="override dashboard.host")
    serve.add_argument("--port", type=int, help="override dashboard.port")
    serve.add_argument("--allow-remote", action="store_true",
                       help="allow binding to a non-loopback address (e.g. 0.0.0.0 inside a container)")
    return ap


def select_sources(config, sources_arg) -> None:
    """Restrict config.sources to a comma-separated (or list) subset; raises ConfigError."""
    if not sources_arg:
        return
    wanted = sources_arg if isinstance(sources_arg, list) else [s.strip() for s in sources_arg.split(",")]
    wanted = [s for s in wanted if s]
    unknown = [s for s in wanted if s not in config.sources]
    if unknown:
        raise ConfigError(f"sources not enabled in config: {unknown}")
    config.sources = wanted


def make_http(config, refresh: bool = False) -> HttpClient:
    # IRIS is not an "official" host: its robots.txt is checked for OAI-PMH and for file downloads alike.
    hosts = set(DEFAULT_OFFICIAL_HOSTS)
    from .config import effective_rate_limits
    return HttpClient(config.contact_email, effective_rate_limits(config), config.cache_dir,
                      official_hosts=hosts, refresh=refresh)


def get_papers(config, http, should_stop=None):
    """The paper list: built from ORCID (+ Crossref, arXiv feed) at every run, or read from paper_list.file."""
    if config.paper_list.source == "file":
        return load_papers(config.paper_list.file)
    from .paperlist import build_paper_list, write_paper_list
    records = build_paper_list(config, http, should_stop)
    path = write_paper_list(records, config.output_dir)
    logging.getLogger("triage").info("paper list written to %s", path)
    return papers_from_records(records)


@dataclass
class RunOutput:
    records: list
    source_names: list[str]
    csv_path: Path
    json_path: Path


def execute_run(config, limit=None, download=True, refresh=False, http=None, sources=None,
                progress=None, should_stop=None, reload_pdfs=False) -> RunOutput:
    if http is None:
        http = make_http(config, refresh)
    papers = get_papers(config, http, should_stop)
    if limit:
        papers = papers[:limit]
    if sources is None:
        sources = build_sources(config, http, should_stop=should_stop)
    records = run_pipeline(papers, sources, config, http, download=download, progress=progress,
                           should_stop=should_stop, reload_pdfs=reload_pdfs)
    names = [s.name for s in sources]
    return RunOutput(records, names,
                     write_csv(records, names, config.projects, config.output_dir, config=config),
                     write_json(records, names, config.projects, config.output_dir))


def _run(args, http, sources) -> int:
    try:
        config = load_config(args.config)
        select_sources(config, args.sources)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    def progress(i, n, rec):
        statuses = ", ".join(f"{k}={v.status}" for k, v in rec.results.items())
        logging.info("[%d/%d] %s (%s)", i, n, rec.paper.title[:60], statuses)

    out = execute_run(config, limit=args.limit, download=not args.no_download, refresh=args.refresh_cache,
                      http=http, sources=sources, progress=progress, reload_pdfs=args.reload_pdfs)
    records = out.records
    print(f"\nWrote {out.csv_path} and {out.json_path} ({len(records)} papers)")
    for name in out.source_names:
        c = Counter(r.results[name].status for r in records)
        print(f"  {name}: " + ", ".join(f"{k}={v}" for k, v in sorted(c.items())))
    if not args.no_download:
        have_pdf = sum(1 for r in records if r.pdf_path)
        cached = sum(1 for r in records if r.pdf_cached)
        print(f"  PDFs from cache: {cached}; downloaded now: {have_pdf - cached}")
        acked = sum(1 for r in records if r.ack and r.ack.acknowledged)
        ack_pages = sum(1 for r in records if r.ack_page_pdf)
        print(f"  PDFs retrieved: {have_pdf}; papers with an acknowledged project: {acked}; "
              f"ack pages extracted: {ack_pages}")
    return 0


def _rankings(args, http=None) -> int:
    from .http import BlockedByPolicy
    from .rankings_download import download, save_list

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    status = 0
    for which, path in (("core", args.core_file), ("sjr", args.sjr_file)):
        if path:
            try:
                r = save_list(config, which, Path(path).read_bytes())
                print(f"{which}: imported {r['rows']} rows -> {r['path']}")
            except (OSError, RuntimeError) as exc:
                print(f"{which}: {exc}", file=sys.stderr)
                status = 1
    if args.core or not (args.core_file or args.sjr_file):
        try:
            r = download(config, http or make_http(config), "core")
            print(f"core: {r['rows']} rows -> {r['path']}")
        except BlockedByPolicy as exc:
            print(f"core: not allowed ({exc})", file=sys.stderr)
            status = 1
        except Exception as exc:  # noqa: BLE001
            print(f"core: {exc}", file=sys.stderr)
            status = 1
    return status


def _serve(args, app_runner=None) -> int:
    from .config import DashboardConfig, setup_needed

    reason = setup_needed(args.config)
    if reason:
        dash = DashboardConfig()
        print(f"First start ({reason}): open the dashboard to set it up.")
    else:
        try:
            dash = load_config(args.config).dashboard
        except ConfigError as exc:
            print(f"config error: {exc}", file=sys.stderr)
            return 2
    host = args.host or dash.host
    port = args.port or dash.port
    if host not in LOOPBACK and not args.allow_remote:
        print(f"refusing to listen on {host}: the dashboard has no authentication. "
              "Use --allow-remote if you really mean it (e.g. inside a container).", file=sys.stderr)
        return 2
    from .web import create_app

    app = create_app(args.config)
    if app_runner:
        app_runner(app, host, port)
    else:
        print(f"Dashboard on http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}")
        app.run(host=host, port=port, threaded=True, use_reloader=False)
    return 0


def main(argv=None, http=None, sources=None, app_runner=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.command == "serve":
        return _serve(args, app_runner)
    if args.command == "rankings":
        return _rankings(args, http)
    return _run(args, http, sources)


if __name__ == "__main__":
    sys.exit(main())
