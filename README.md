# Publications Triage

For every paper in your ORCID record: is it on IRIS and on arXiv, do the two entries match, which funding
projects does it acknowledge (and on which page), and how does its venue rank (SCImago quartile, CORE rank).
Results are shown in a local web dashboard and saved as CSV/JSON.

## Quick start

```bash
cp config/triage.example.yaml config/triage.yaml   # set your ORCID iD and projects
docker compose up -d --build                        # http://127.0.0.1:8000
```

Then press **Re-run processing**. Without Docker:

```bash
python -m venv .venv && source .venv/bin/activate && pip install -e '.[dev]'
pubtriage serve --config config/triage.yaml
```

## Configuration

Only two settings are required:

```yaml
user:
  orcid: 0000-0002-1825-0097      # your ORCID iD: the paper list is built from it at every run
projects:                         # funding projects to look for in the acknowledgements
  - name: MYPROJECT
  - name: OtherProject
    aliases: [Other-Project]
```

Everything else has a default and can be changed in the dashboard's **Settings** tab (which saves only what
differs from the defaults) or in the file; `config/triage.example.yaml` lists every option with its default.
The ones you are most likely to change:

| Setting | Default |
|---|---|
| `iris.url` | `https://iris.unica.it` (your university's IRIS; `null` = no IRIS checks) |
| `user.email` | none (sent as contact to arXiv/ORCID/Crossref if set; required only by the Unpaywall source) |
| `paper_list.arxiv_author_id` | none (the id in `arxiv.org/a/<id>`: adds arXiv-only preprints) |
| `paper_list.exclude_titles` | none (filled by **Exclude this paper** in the dashboard) |

`config/triage.yaml`, `cache/` and `output/` are personal and git-ignored.

## Command line

```bash
pubtriage run --config config/triage.yaml [--limit N] [--sources iris,arxiv] [--reload-pdfs]
pubtriage serve --config config/triage.yaml
pubtriage rankings --config config/triage.yaml [--core] [--sjr-file scimagojr.csv]
pytest                                            # all network calls are mocked
```

By default a run reuses cached PDFs and retries only the papers without one; `--reload-pdfs` fetches everything again.

## How sites are accessed

- Only official APIs or pages that robots.txt allows; robots.txt is checked before each request and its
  `Crawl-delay` respected, on top of built-in per-site delays. Responses are cached.
- **Paper list**: ORCID public API + Crossref, plus the arXiv author feed if configured.
- **arXiv**: the official API (`export.arxiv.org`).
- **IRIS**: OAI-PMH, the standard interface repositories offer to programs, only for the records linked from
  your ORCID (about 1 minute); IRIS PDFs are used only when no other open-access copy exists.
- **Rankings**: the CORE list is downloaded by the tool; SCImago forbids scripted downloads, so download its
  CSV in your browser and upload it from the dashboard.

## Limitations

- Only open-access PDFs; scanned PDFs without text are reported as unreadable.
- "Missing on IRIS" means "not among the IRIS records linked to your ORCID".
- Fuzzy matches (titles, venues) are marked as uncertain; fix them from the paper's details or with
  `rankings.venues`.
