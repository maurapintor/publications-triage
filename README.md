# Publications Triage

Builds your paper list from ORCID (+ Crossref, + your arXiv author feed) at every run, checks whether each
paper is on IRIS and arXiv (and optionally OpenAlex / Unpaywall), downloads open-access PDFs, reports which
configured projects are acknowledged, compares IRIS vs arXiv metadata and ranks the venues. Results are shown
in a web dashboard and written as CSV/JSON.

## Quick start (Docker)

```bash
cp config/triage.example.yaml config/triage.yaml   # then fill in the 4 required settings (see below)
docker compose up -d --build                        # dashboard on http://127.0.0.1:8000
```

Open the dashboard and press **Re-run processing**. `config/`, `cache/` and `output/` are mounted from the
host, so results, PDFs, ranking lists and config edits persist. No input file is needed: the paper list
is built from `user.orcid` at every run. The port is published on 127.0.0.1 only (no authentication).
Without compose:

```bash
docker build -t publications-triage .
docker run --rm -p 127.0.0.1:8000:8000 -v "$PWD/config:/app/config" -v "$PWD/cache:/app/cache" \
  -v "$PWD/output:/app/output" publications-triage
```

CLI commands run inside the container too, e.g.
`docker compose exec pubtriage pubtriage run --config config/triage.yaml`.

## Install (without Docker)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest            # all network calls are mocked
```

## Set up for a new user

```bash
cp config/triage.example.yaml config/triage.yaml   # git-ignored
```

Fill in the **required** settings, either in the file or in the dashboard's **Settings** tab (it saves only
what differs from the defaults). Everything else has a default that works for most people.

### Required

| Setting | What to put | Example |
|---|---|---|
| `user.orcid` | your ORCID iD: your paper list is built from your ORCID record at every run | `0000-0002-1825-0097` |
| `user.email` | your email: sent to the public APIs (arXiv, ORCID, Crossref) as contact, nothing else | `you@university.edu` |
| `iris.url` | your institution's IRIS address; leave empty if you have none (IRIS checks are skipped) | `https://iris.example.it` |
| `projects` | the funding projects to look for in the acknowledgements (`name`, optional `aliases`) | `- name: MYPROJECT` |

Minimal `config/triage.yaml`:

```yaml
user:
  name: Ada Lovelace
  orcid: 0000-0002-1825-0097
  email: ada@university.edu
iris:
  url: https://iris.example.it
projects:
  - name: MYPROJECT
  - name: OtherProject
    aliases: [Other-Project]
```

### Optional (defaults in brackets)

| Setting | Meaning |
|---|---|
| `user.name` [none] | display only |
| `paper_list.arxiv_author_id` [none] | the id after `arxiv.org/a/` on your arXiv author page (e.g. `lovelace_a_1`): adds arXiv ids and arXiv-only preprints |
| `paper_list.exclude_titles` [`[]`] | titles to skip; a line also drops every title containing it. Filled by **Exclude this paper** in the dashboard; delete a line to bring the paper back |
| `paper_list.source` [`orcid`] | `orcid` (build the list at every run) or `file` (+ `paper_list.file`, a publications.json you provide) |
| `sources` [`[iris, arxiv]` if `iris.url` is set, else `[arxiv]`] | also available: `openalex`, `unpaywall` (extra open-access PDF locations) |
| `rankings.top_quartiles` [`[Q1]`], `rankings.top_core` [`["A*"]`] | what counts as a "top" venue |
| `rankings.venues` [`{}`] | manual venue ranks when the lists are wrong, e.g. `{ICDAR: A}` |
| `rankings.core_source` [`ICORE2026`], `sjr_area` [`1700`], `sjr_year` [latest], `sjr_categories` [all Computer Science], `core_csv` / `sjr_csv` [the downloaded/uploaded files in `cache/rankings/`] | ranking lists (see [Rankings files](#rankings-files-core-scimago)) |
| `dashboard.columns` [`[title, year, iris, arxiv, rank, acks]`], `dashboard.default_filters` [`{}`] | the table |
| `validations` [`{}`] | per-paper checks, written by the dashboard (`iris_vs_arxiv: accepted`, `rank`, `note`) |

### Advanced (change only if you know why)

| Setting | Meaning |
|---|---|
| `iris.oai_url` [`<iris.url>/oai/request`] | IRIS is read through **OAI-PMH**, the standard interface institutional repositories offer to programs. Change only if your repository uses another address |
| `iris.mode` [`auto`] | `orcid` (only the IRIS records linked from your ORCID, ~1 min), `harvest` (the whole repository, ~2 h the first time), `auto` (= `orcid` when `user.orcid` is set) |
| `iris.metadata_prefix` [`oai_dc`], `iris.set` [none], `iris.export_path` [none] | OAI details; `export_path`: a manual IRIS export CSV (`title,authors,doi,url,pdf_url,access`) instead of OAI-PMH |
| `pdf_priority` [`[arxiv, unpaywall, openalex, iris]`] | order in which sources are tried for the PDF (open access only) |
| `rate_limits` [built in] | overrides of the minimum seconds between requests per server. Built-in: `export.arxiv.org` 3, `arxiv.org` 15, your IRIS host 5, `pub.orcid.org` 0.2, `api.crossref.org` 0.2, `api.openalex.org` 0.2, `api.unpaywall.org` 0.2, `portal.core.edu.au` 2, any other 1. A site's robots.txt `Crawl-delay` is always respected on top |
| `cache_dir` [`cache`], `output_dir` [`output`] | where PDFs/caches and results go |
| `dashboard.host` [`127.0.0.1`], `dashboard.port` [`8000`] | the web server (no authentication: keep it on 127.0.0.1) |

Older configs keep working: `contact_email` is read as `user.email`, `input_path` as `paper_list.source: file`,
and an `iris.oai_url` ending in `/oai/request` as `iris.url`.

## Run

```bash
pubtriage run --config config/triage.yaml --limit 3
pubtriage run --config config/triage.yaml --sources arxiv --no-download
pubtriage run --config config/triage.yaml --reload-pdfs     # re-check every paper, download all PDFs again
pubtriage run --config config/triage.yaml --refresh-cache   # ignore every cached HTTP response
```

By default a run reuses cached PDFs (and the cached lookups of those papers); papers **without** a PDF are
looked up again (fresh arXiv/OA queries) and their download is retried.

Writes `output/triage_<date>.csv` and `output/triage_<date>.json` (+ `output/latest.json`, read by the
dashboard).

The CSV is UTF-8 with BOM (opens in Excel); a summary is printed at the end.

## Dashboard (web app)

```bash
pubtriage serve --config config/triage.yaml      # http://127.0.0.1:8000
```

- **Papers tab**: one row per paper with title, year, IRIS, arXiv and the acknowledged projects (solid tag =
  in the acknowledgements/funding text; dashed = mentioned only elsewhere, so check by hand). Search, sort
  (click a header), filters by source status / project / PDF, and a **Columns** menu. Your column and filter
  choices are remembered in the browser.
- **Re-run processing** starts the pipeline in the background (options: limit, sources, *re-load all PDFs*,
  skip PDF download), with progress, log and Cancel. A cancelled run still saves the papers done so far.
- **IRIS vs arXiv**: when a paper is on both, the IRIS entry's title and authors are compared with the arXiv
  entry. Case, accents, punctuation and LaTeX vs Unicode (`$\sigma$` / `σ`) are ignored; only different
  title words or a different author list/order count. The table shows `= arXiv` / `≠ arXiv` next to the
  IRIS status; the details show the title word diff (removed / added words highlighted) and an aligned
  author table (missing in IRIS, only in IRIS, different position). **Mark difference as checked** (with an
  optional note) stores the check in the config (`validations`); the paper then shows `≠ arXiv ✓`.
- **Venue ranking** (column *Rank*, filter *Rank*): SCImago quartile for journals (best quartile among the
  configured categories, default all Computer Science categories) and CORE rank for conferences; "top" =
  `rankings.top_quartiles` / `rankings.top_core` (default Q1 / A*). Journals are matched by ISSN, then name;
  conferences by acronym (e.g. `ICDAR` in the proceedings title), then fuzzy title (uncertain matches marked
  `?`). If the parser fails or is wrong: add the venue to `rankings.venues` (Settings tab), or use **Set rank by
  hand** in the paper's details (stored in `validations`). Ranks and checks apply immediately, no re-run.
- **Export**: *Export shown* downloads the papers currently in the table as BibTeX (`papers.bib`) or as
  plain text in APA, IEEE, MLA, Chicago or Harvard style. Each paper's details also have a **Cite** box
  (copy / download). BibTeX reuses the BibTeX that ORCID holds for the work (volume, pages, ...) with a
  readable key and the arXiv eprint added.
- **Click a row** for details: metadata, per-source status/link/match evidence, notes, per-project result
  with the acknowledgement text snippet, and two collapsed sections (loaded only when expanded): the
  **single page containing the acknowledgements** (a one-page PDF in `cache/acks/`) and the full PDF opened
  at that page.
- **Settings tab**: the same settings as the config file, in sections with a sidebar: *Required* (Profile,
  Projects), *Optional* (Paper list, Sources, Rankings, Dashboard) and *Advanced* (IRIS details, PDFs, request
  delays with their built-in defaults, storage & server). Values are validated before saving; only values that
  differ from the defaults are written, and the previous file is kept as `config/triage.yaml.bak`. Saving is
  blocked while a run is in progress.
- **Exclude this paper** (in the details): the paper disappears from the table and is skipped in future runs;
  it is listed in *Settings → Paper list → Excluded papers*, where deleting the line brings it back.

Dashboard settings in the config file:

```yaml
dashboard:
  host: 127.0.0.1
  port: 8000
  columns: [title, year, iris, arxiv, acks]   # also: authors, doi, venue, pdf_source, ack_status, openalex,
                                              # unpaywall, ack_<project>
  default_filters: {}                         # e.g. {iris: missing}, {ack_MYPROJECT: N}, {pdf: yes}
```

The app has no authentication: it only listens on 127.0.0.1 unless you pass `--allow-remote`.

## Rankings files (CORE, SCImago)

Neither site has an API; both offer a CSV export. Get them from the Papers tab (rankings line):

- **CORE**: *Download list* — the tool fetches the export itself (User-Agent `publications-triage`, rate limits,
  robots.txt checked first; a disallowed URL is reported, never worked around). Or upload an export made by hand
  (<https://portal.core.edu.au/conf-ranks/> → edition → *Export*). CLI: `pubtriage rankings --config … --core`.
- **SCImago**: its robots.txt forbids scripted CSV downloads for every agent (`Disallow: /*out=xls`, "CSV
  downloads (Download button) are generated on the fly"), so the tool never fetches it. Open the SCImago ranking
  (link in the dashboard, <https://www.scimagojr.com/journalrank.php?area=1700>), press *Download data* in your
  browser, and upload the file. CLI: `pubtriage rankings --config … --sjr-file ~/Downloads/scimagojr\ 2025.csv`.

Every file is checked (an HTML page or a file without the expected columns is rejected, the previous one kept)
and stored in `cache/rankings/core.csv` / `sjr.csv`, used whenever `core_csv` / `sjr_csv` are not set.
`rankings.core_source` (e.g. `ICORE2026`) chooses the CORE edition; `rankings.sjr_area` / `sjr_year` only shape
the SCImago link.

```yaml
rankings:
  core_source: ICORE2026
  sjr_area: "1700"
  sjr_year: null              # latest
  core_csv: null              # null = cache/rankings/core.csv (downloaded)
  sjr_csv: null               # null = cache/rankings/sjr.csv (downloaded)
  top_quartiles: [Q1]
  top_core: ["A*"]
  sjr_categories: []          # empty = all Computer Science categories
  venues: {ICDAR: A, "Pattern Recognition": Q1}   # manual list, wins over the files
validations:                  # normally written by the dashboard
  "10.1000/example.1": {iris_vs_arxiv: accepted, note: "IRIS is correct"}
  "10.1000/example.2": {rank: A}
```

The CSV also has `rank`, `rank_top`, `rank_status` and `iris_vs_arxiv_accepted` columns.

## How sources are accessed

- **Paper list** (start of every run, `paper_list.source: orcid`): the ORCID public API lists the works of
  `user.orcid` (always fresh); for each work the ORCID record (for its BibTeX) and, with a DOI, Crossref
  (`api.crossref.org`, polite pool with `mailto=`; authors, venue, year, type) are fetched and cached on disk.
  With `paper_list.arxiv_author_id`, the arXiv author feed `arxiv.org/a/<id>.atom2` (one request per run;
  robots.txt allows it, `Crawl-delay: 15` respected) adds arXiv ids and arXiv-only preprints. Duplicates (e.g.
  preprint + published version, LaTeX vs Unicode titles) are merged, keeping the published one. The list is
  written to `output/publications.json`.

- **arXiv**: only the official API on `export.arxiv.org` (and PDFs from the same host), at most one
  request per 3 s. No crawling of `arxiv.org`.
- **IRIS**: OAI-PMH (`https://iris.example.it/oai/request`), at most one request / 5 s (robots.txt
  `Crawl-delay: 5`; robots.txt is checked for every IRIS request). Used to check presence, to compare
  title/authors with arXiv and, **only for papers without a PDF from the earlier sources**, as the last PDF
  fallback: the item's files are listed with one OAI `GetRecord` in the `ore` format, then the PDF is downloaded
  from `/bitstream/...`. Records marked embargoed/closed are skipped; a restricted file returns a login page and
  is rejected, so an item with a restricted publisher version and an open author version yields the latter.
  OAI-PMH cannot filter by author (IRIS sets are publication types) and the IRIS search
  pages are disallowed for scripts, so the lookup goes through ORCID (`iris.mode: auto` / `orcid`):
  1. the ORCID public API (`pub.orcid.org`, one request per run) lists the user's works; those pushed by IRIS
     carry their IRIS handle;
  2. each handle is fetched with one OAI `GetRecord` (cached on disk; refreshed only with *Re-load all PDFs*).
  About 50 requests (~4 min) the first time, then ~1 min per run. A paper reported `missing` is not among the
  IRIS records linked to the ORCID iD: either not on IRIS or not synchronised to ORCID.
  `iris.mode: harvest` instead harvests the whole repository (~155k records, ~2 h the first time, resumable,
  then incremental) into `cache/iris/index.json.gz`. A manual IRIS export CSV (`iris.export_path`) also works.
- **OpenAlex / Unpaywall**: public APIs, queried by DOI.
- **Any other host** (PDF downloads from repositories): `robots.txt` is checked first; a disallowed
  URL is skipped and noted, never worked around.
- Responses are cached on disk, so re-runs do not repeat requests; 429/5xx are retried with backoff.

## CSV columns

`paper_id, title, year, doi, venue`, then per source `<source>_status, <source>_url`
(`present | missing | uncertain | error`), `iris_vs_arxiv` (`same`, `author order differs`, `authors differ`,
`title differs`, `title and authors differ`, or empty when not on both), then `pdf_source`, `ack_status`
(`ok | unreadable | no_pdf`), `ack_projects_found` (`;`-separated), one `ack_<project>` column per
project, and `notes`.

`ack_<project>`: `Y` = found in an acknowledgements/funding section or funding footnote;
`body` = found only elsewhere in the text (related work, an author called Elsa, ...), so check by hand;
`N` = not found; empty = no readable PDF.

For OpenAlex/Unpaywall, `present` means an open-access PDF location exists.

## Known limitations

- Paywalled PDFs are not fetched (open access only), so those papers have `no_pdf`.
- Fuzzy title matches below 0.92 or without a shared author surname are `uncertain` and listed in `notes`.
- With `iris.mode: orcid`, `missing` on IRIS means "not among the IRIS records linked to your ORCID": a paper on
  IRIS that was never synchronised to ORCID also shows as missing (`iris.mode: harvest` checks everything).
- IRIS-vs-arXiv results are recomputed on every page load; the `notes` list in a record is written at run time
  and may still mention a difference you have since marked as checked.
- The CORE download and the CSV parsers were tested only against mocked responses / sample files; the real export
  format may differ (then upload a manual export and report the difference). Rankings depend on those files; conference matching by acronym or fuzzy title can
  be wrong (marked `?` when uncertain): fix with `rankings.venues` or *Set rank by hand*.
- `pubtriage run --limit N` replaces `output/latest.json`, so the dashboard shows only those N papers until the
  next full run.
- PDFs without a text layer (scans) are reported `unreadable`; no OCR.
- The acknowledgements page is the page where the first acknowledged project (or the first
  acknowledgements heading / funding sentence) appears; a section spanning two pages shows the first one.
