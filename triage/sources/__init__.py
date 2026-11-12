from __future__ import annotations

from .arxiv import ArxivSource
from .base import SourceResult
import logging
from pathlib import Path

from ..config import iris_mode
from .iris import IrisIndex, IrisSource, fetch_records_by_handle, harvest, load_export_csv
from .orcid import fetch_work_handles

log = logging.getLogger("triage")
from .oa import OpenAlexSource, UnpaywallSource


def iris_index_path(config) -> Path:
    return Path(config.cache_dir) / "iris" / "index.json.gz"


def build_sources(config, http, should_stop=None) -> list:
    out = []
    for name in config.sources:
        if name == "arxiv":
            out.append(ArxivSource(http))
        elif name == "iris":
            ic = config.iris
            if ic.export_path:
                out.append(IrisSource(loader=lambda: load_export_csv(ic.export_path)))
            elif iris_mode(config) == "orcid":
                orcid = config.user.orcid

                def by_orcid(orcid=orcid):
                    handles = fetch_work_handles(http, orcid)
                    log.info("IRIS: ORCID %s links %d works to a handle", orcid, len(handles))
                    return fetch_records_by_handle(http, ic.endpoint, handles, ic.metadata_prefix, should_stop)

                out.append(IrisSource(loader=by_orcid, missing_evidence=f"not among the IRIS records linked to ORCID {orcid}",
                                      http=http, oai_url=ic.endpoint))
            else:
                index = IrisIndex(iris_index_path(config), ic.endpoint, ic.metadata_prefix, ic.set)
                out.append(IrisSource(loader=lambda: index.update(http, should_stop), http=http, oai_url=ic.endpoint))
        elif name == "openalex":
            out.append(OpenAlexSource(http, config.contact_email))
        elif name == "unpaywall":
            out.append(UnpaywallSource(http, config.contact_email))
    return out
