# CLAUDE.md

Publications triage: builds a researcher's paper list from ORCID, checks each paper on IRIS and arXiv,
compares the two entries, downloads open-access PDFs, finds acknowledged funding projects (and their page),
ranks venues (SCImago / CORE) and shows everything in a local Flask dashboard. Python 3.10+, no JS build step.

## Commands

```bash
pip install -e '.[dev]'                       # in .venv
pytest -q                                     # whole suite; every network call is mocked (responses)
pubtriage serve --config config/triage.yaml   # dashboard on 127.0.0.1:8000 (first start: setup page)
pubtriage run --config config/triage.yaml [--limit N] [--sources iris,arxiv] [--reload-pdfs]
docker compose up -d --build                  # same dashboard in a container
node --check triage/web/static/app.js         # syntax check after editing the frontend
```

## Layout

- `triage/cli.py`: entry point (`run`, `serve`, `rankings`); `execute_run()` = paper list → pipeline → CSV/JSON.
- `triage/paperlist.py`: paper list from ORCID works + ORCID work BibTeX + Crossref + arXiv author feed
  (default feed id = ORCID iD); dedup, LaTeX→Unicode titles, `exclude_titles` (substring match).
- `triage/pipeline.py`: per paper: sources → IRIS-vs-arXiv comparison → PDF (cache-first) → acks → ack page.
- `triage/sources/`: `arxiv.py` (official API only), `iris.py` (OAI-PMH: GetRecord by handle from ORCID,
  optional resumable full harvest `IrisIndex`, `ore` format for PDF links), `orcid.py`, `oa.py` (OpenAlex/Unpaywall).
- `triage/acknowledgements.py` + `ackpage.py`: ack section/footnote detection per page, one-page PDF.
- `triage/compare.py`: IRIS vs arXiv (word diff of titles, aligned author rows; case/punctuation/LaTeX ignored).
- `triage/rankings.py` + `rankings_download.py`: SJR/CORE CSV parsing and matching; CORE download, SJR import.
- `triage/cover.py`: IRIS cover page (reportlab) from the IRIS record's metadata; assets in `config/cover/`
  (institution logo/header and Calibri: local only, never commit them).
- `triage/enrich.py`: fields recomputed on every dashboard request (comparison, ranking, validations), so
  config edits apply without a re-run.
- `triage/config.py`: dataclasses, validation, defaults (`DEFAULT_RATE_LIMITS`, `DEFAULT_IRIS_URL`),
  `config_to_dict(minimal=True)` / `save_config`, first-start `setup_needed` / `apply_setup`.
- `triage/web/`: Flask app (`__init__.py`: JSON API), `jobs.py` (single background run), one page
  `templates/index.html` + vanilla `static/app.js` / `app.css` (Papers, Settings, setup page).
- `triage/http.py`: the only HTTP client: per-host delays, robots.txt check + Crawl-delay for non-official
  hosts, retries, on-disk cache.

## Rules

- **Site etiquette is a hard requirement.** All requests go through `HttpClient`. Use official APIs, check
  robots.txt, respect delays. Never work around a block: SCImago forbids scripted CSV downloads, so its
  file is uploaded by the user; CORE and SCImago also disallow AI agents, so don't fetch them yourself
  while developing (test with mocks). If a new source needs scraping or has unclear terms, ask first.
- **No personal data in the repository.** `config/triage.yaml`, `config/*.bak`, `config/cover/`, `cache/`,
  `output/`, `plans/` are git-ignored and stay local. Examples and tests use neutral data (ORCID sample iD
  `0000-0002-1825-0097`, fictional names, made-up project names). Before committing, grep the staged
  files for real names, ORCID iDs, emails.
- **Tests never hit the network**: use `responses` or fakes. `tests/conftest.py::make_pdf` builds small PDFs.
- **Config**: every new setting needs a default, validation in `load_config`, a place in `config_to_dict`,
  a field in the Settings tab (Required / Optional / Advanced) and a commented line in
  `config/triage.example.yaml`. Saving writes only non-default values; older keys keep loading
  (`contact_email`, `input_path`, `iris.oai_url`).
- **Dashboard**: no external scripts or CDNs; escape all data with `esc()` in `app.js`; it has no
  authentication, so it binds to 127.0.0.1 unless `--allow-remote` (used inside the container).
- `pubtriage run --limit N` overwrites `output/latest.json` with N papers: use the full run before
  looking at the dashboard.
