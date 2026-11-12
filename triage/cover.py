"""Front page for the version of a paper deposited on IRIS.

Same layout as the scripts in unica-automations/unica_iris (gen_pdf.py): logo and header image, "This is
the Author's accepted manuscript version of the following contribution:", the bold reference, the DOI
link, where the full text was downloaded from, and the license line.

The reference comes from the paper's IRIS record (authors, title, journal/proceedings, volume, pages,
year) and falls back to the ORCID/Crossref metadata. Logo, header and fonts are institution assets kept
locally in `cover.assets_dir` (git-ignored): logo.png, header.png, fonts/calibri{,-bold,-italic,-bi}.ttf.
Without them the page uses Helvetica and no images.
"""
from __future__ import annotations

import io
import re
from html import escape
from pathlib import Path

from .citations import parse_bibtex

COLOR = (45 / 255, 58 / 255, 104 / 255)
_FONT_FILES = {"normal": "calibri.ttf", "bold": "calibri-bold.ttf", "italic": "calibri-italic.ttf",
               "boldItalic": "calibri-bi.ttf"}


def _pages(first: str | None, last: str | None, pages: str | None) -> str:
    if first and last:
        return f"pagg. {first}-{last}"
    if pages:
        return "pagg. " + pages.replace("--", "-")
    return f"pag. {first}" if first else ""


def is_ieee(rec: dict, meta: dict, bib: dict) -> bool:
    """IEEE paper: IEEE DOI prefix, or IEEE as publisher / in the venue (IRIS record or BibTeX)."""
    if (rec.get("doi") or "").lower().startswith("10.1109/"):
        return True
    text = " ".join(str(x) for x in (meta.get("publisher"), bib.get("publisher"), meta.get("journal"),
                                     meta.get("ispartofbook"), rec.get("venue")) if x)
    return bool(re.search(r"\bIEEE\b|Institute of Electrical and Electronics Engineers", text, re.I))


def cover_fields(rec: dict) -> dict:
    """{reference (reportlab markup), doi, year, ieee} for a record from output/latest.json."""
    iris = (rec.get("sources") or {}).get("iris") or {}
    use_iris = iris.get("status") in ("present", "uncertain") and iris.get("title")
    meta = (iris.get("meta") or {}) if use_iris else {}
    _t, _k, bib = parse_bibtex(rec.get("bibtex"))
    authors = (iris.get("authors") if use_iris and iris.get("authors") else None) or rec.get("authors") or []
    title = (iris["title"] if use_iris else None) or rec.get("title") or ""
    venue = meta.get("journal") or meta.get("ispartofbook") or rec.get("venue") or bib.get("journal") \
        or bib.get("booktitle") or ""
    if venue.isupper():  # IRIS stores journal names in capitals: prefer the Crossref/ORCID spelling
        venue = rec.get("venue") or venue.title()
    volume = meta.get("volume") or bib.get("volume") or ""
    year = meta.get("year") or str(rec.get("year") or "") or bib.get("year") or ""
    pages = _pages(meta.get("firstpage"), meta.get("lastpage"), bib.get("pages"))
    ref = f"{escape('; '.join(authors))} <i>{escape(title)}</i>"
    if venue:
        ref += f", {escape(venue)}"
    if volume:
        ref += f", vol. {escape(volume)}"
    if year:
        ref += f", {escape(year)}"
    if pages:
        ref += f", {escape(pages)}"
    return {"reference": f"<b>{ref}.</b>", "doi": rec.get("doi") or "", "year": year,
            "ieee": is_ieee(rec, meta, bib)}


def _fonts(assets: Path) -> str:
    from reportlab.lib.fonts import addMapping
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    fdir = assets / "fonts"
    if all((fdir / f).exists() for f in _FONT_FILES.values()):
        names = {}
        for style, f in _FONT_FILES.items():
            name = f"CoverCalibri-{style}"
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, str(fdir / f)))
            names[style] = name
        pdfmetrics.registerFontFamily("CoverCalibri", **names)
        addMapping("CoverCalibri-normal", 0, 0, names["normal"])
        addMapping("CoverCalibri-normal", 1, 0, names["bold"])
        addMapping("CoverCalibri-normal", 0, 1, names["italic"])
        addMapping("CoverCalibri-normal", 1, 1, names["boldItalic"])
        return "CoverCalibri-normal"
    return "Helvetica"


def render_cover(fields: dict, cover_cfg, repository_url: str | None) -> bytes:
    from reportlab.lib.colors import Color
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    from reportlab.platypus import Paragraph

    assets = Path(cover_cfg.assets_dir)
    width, height = A4
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    font = _fonts(assets)
    color = Color(*COLOR)
    big = ParagraphStyle("cover", fontName=font, fontSize=16, textColor=color, leading=24)
    small = ParagraphStyle("cover-small", fontName=font, fontSize=12, textColor=color, leading=24)

    if (assets / "logo.png").exists():
        c.drawImage(ImageReader(str(assets / "logo.png")), 150, height - 100, width=300, height=80, mask="auto")
    if (assets / "header.png").exists():
        c.drawImage(ImageReader(str(assets / "header.png")), 50, height - 220, width=width - 100, height=100,
                    mask="auto")

    def para(text, style, x, y):
        p = Paragraph(text, style=style, encoding="utf-8")
        _w, h = p.wrapOn(c, width - 130, height)
        p.drawOn(c, x, y - h + style.leading)

    para(cover_cfg.version_text, big, 60, height - 300)
    para(fields["reference"], big, 60, height - 380)
    c.setFillColor(color)
    c.setFont(font, 16)
    c.drawString(60, height - 520, "The publisher's version is available at:")
    if fields["doi"]:
        url = f"https://dx.doi.org/{escape(fields['doi'])}"
        para(f'<a href="{url}"><u>{url}</u></a>', big, 60, height - 550)
    # Footer: one left-aligned block stacked upward from the bottom margin, same width and line spacing.
    foot = ParagraphStyle("cover-foot", fontName=font, fontSize=10.5, textColor=color, leading=14)
    items = []
    if fields.get("ieee") and cover_cfg.ieee_notice:
        items.append(escape(cover_cfg.ieee_notice.replace("{year}", fields["year"] or "").replace("©  ", "© ")))
    year = f"©{escape(fields['year'])}. " if fields["year"] else ""
    items.append(year + escape(cover_cfg.license_text))
    if repository_url:
        items.append(f"This full text was downloaded from {escape(cover_cfg.repository_name)} "
                     f"<a href='{escape(repository_url)}'><u>{escape(repository_url)}</u></a>")
    y, gap = 50, 10
    for text in reversed(items):
        p = Paragraph(text, style=foot, encoding="utf-8")
        _w, h = p.wrapOn(c, width - 120, height)
        p.drawOn(c, 60, y)
        y += h + gap
    c.save()
    return buf.getvalue()
