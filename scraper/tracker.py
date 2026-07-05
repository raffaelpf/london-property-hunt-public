"""Read/write the flat-hunt tracker (``.xlsx``) with URL-based dedup.

Single ``Flats`` sheet with flat-specific columns (furnishing, balcony/terrace,
size). Coloured priority rows, frozen header, auto-filter, clickable URLs.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

from .features import OUTDOOR_LABELS
from .models import Listing

FLATS_SHEET = "Flats"
COLS = [
    "Title", "Platform", "URL", "Area", "Postcode", "Price (pcm)", "Bedrooms",
    "Furnishing", "Balcony/Terrace", "Size (sqft)", "Available From",
    "Notes", "Status", "Priority", "Found On",
]
_COL_WIDTHS = {
    "Title": 42, "Platform": 13, "URL": 48, "Area": 24, "Postcode": 10,
    "Price (pcm)": 12, "Bedrooms": 10, "Furnishing": 15, "Balcony/Terrace": 22,
    "Size (sqft)": 11, "Available From": 14, "Notes": 44, "Status": 12,
    "Priority": 10, "Found On": 12,
}

_HEADER_FILL = PatternFill("solid", fgColor="1F3864")
_HEADER_FONT = Font(color="FFFFFF", bold=True, name="Arial", size=11)
_PRIORITY_FILL = {
    "High": PatternFill("solid", fgColor="E2EFDA"),
    "Medium": PatternFill("solid", fgColor="FFFFC7"),
    "Low": PatternFill("solid", fgColor="FCE4D6"),
}
_NEW_STATUS = "NEW 🔴"


def _init_sheet(ws) -> None:
    for i, col in enumerate(COLS, 1):
        cell = ws.cell(row=1, column=i, value=col)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(vertical="center")
        ws.column_dimensions[cell.column_letter].width = _COL_WIDTHS.get(col, 16)
    ws.freeze_panes = "A2"


def load_or_create(path: str | Path) -> openpyxl.Workbook:
    p = Path(path)
    if p.exists():
        return openpyxl.load_workbook(p)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    _init_sheet(wb.create_sheet(FLATS_SHEET))
    return wb


def _existing_urls(ws) -> set[str]:
    url_idx = COLS.index("URL") + 1
    urls: set[str] = set()
    for row in ws.iter_rows(min_row=2, min_col=url_idx, max_col=url_idx):
        cell = row[0]
        if cell.value:
            urls.add(str(cell.value).strip())
            if cell.hyperlink:
                urls.add(str(cell.hyperlink.target).strip())
    return urls


def _values(listing: Listing, today: str) -> dict:
    return {
        "Title": listing.title,
        "Platform": listing.platform,
        "URL": listing.url,
        "Area": listing.area,
        "Postcode": listing.postcode,
        "Price (pcm)": listing.price_pcm,
        "Bedrooms": listing.bed_label or (listing.bed_count if listing.bed_count is not None else ""),
        "Furnishing": listing.furnishing.replace("-", " ").title() if listing.furnishing not in ("", "unknown") else "Unknown",
        "Balcony/Terrace": OUTDOOR_LABELS.get(listing.outdoor, "Not stated"),
        "Size (sqft)": listing.size_sqft,
        "Available From": listing.available_from,
        "Notes": listing.notes,
        "Status": _NEW_STATUS,
        "Priority": listing.priority,
        "Found On": today,
    }


def _append(ws, listing: Listing, today: str) -> None:
    values = _values(listing, today)
    row_idx = ws.max_row + 1
    fill = _PRIORITY_FILL.get(listing.priority)
    for i, col in enumerate(COLS, 1):
        cell = ws.cell(row=row_idx, column=i, value=values[col])
        if fill:
            cell.fill = fill
        if col == "URL" and listing.url:
            cell.hyperlink = listing.url
            cell.font = Font(color="0563C1", underline="single")


def update_tracker(path: str | Path, listings: list[Listing]) -> dict:
    """Add non-duplicate listings to the tracker. Returns counts."""
    wb = load_or_create(path)
    ws = wb[FLATS_SHEET]
    today = date.today().isoformat()
    seen = _existing_urls(ws)
    added = dupes = 0

    for listing in listings:
        url = listing.url.strip()
        if not url or url in seen:
            dupes += 1
            continue
        _append(ws, listing, today)
        seen.add(url)
        added += 1

    ws.auto_filter.ref = ws.dimensions
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return {"added": added, "duplicates": dupes}
