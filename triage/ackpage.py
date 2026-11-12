"""Copy the page holding the acknowledgements into its own one-page PDF."""
from __future__ import annotations

from pathlib import Path

from .papers import safe_filename


def ack_page_path(out_dir, paper_id: str, page: int) -> Path:
    return Path(out_dir) / f"{safe_filename(paper_id)}_p{page}.pdf"


def extract_ack_page(pdf_path, page: int | None, out_dir, paper_id: str, force: bool = False) -> Path | None:
    """Write page `page` (1-based) of `pdf_path` to out_dir; reuse an existing file. None if impossible."""
    if not page:
        return None
    target = ack_page_path(out_dir, paper_id, page)
    if target.exists() and not force:
        return target
    from pypdf import PdfReader, PdfWriter

    try:
        reader = PdfReader(str(pdf_path))
        if page > len(reader.pages):
            return None
        writer = PdfWriter()
        writer.add_page(reader.pages[page - 1])
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        with open(tmp, "wb") as fh:
            writer.write(fh)
        tmp.replace(target)
        return target
    except Exception:
        return None
