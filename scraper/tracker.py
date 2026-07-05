"""Read/write the ``london_room_hunt.xlsx`` tracker with URL-based dedup.

Schema and formatting follow ``tracker/README.md`` exactly (two sheets,
coloured priority rows, frozen header, auto-filter, clickable URLs).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

from .models import Listing

ROOMS_SHEET = "Listings"
STUDIOS_SHEET = "Studios & 1-Beds"

ROOMS_COLS = [
    "Title", "Platform", "URL", "Area", "Postcode", "Price (pcm)",
    "Bills Included", "Available From", "Furnished", "Bed Count",
    "Flatmates", "Contact", "Notes", "Status", "Priority", "Found On",
]
STUDIOS_COLS = [
    "Title", "Platform", "URL", "Area", "Postcode", "Price (pcm)",
    "Bills Included", "Available From", "Bedrooms", "Furnished",
    "Notes", "Contact", "Status", "Priority", "Found On",
]

_COL_WIDTHS = {
    "Title": 45, "Platform": 12, "URL": 50, "Area": 18, "Postcode": 10,
    "Price (pcm)": 12, "Bills Included": 10, "Available From": 14,
    "Furnished": 10, "Bed Count": 10, "Bedrooms": 12, "Flatmates": 30,
    "Contact": 20, "Notes": 40, "Status": 14, "Priority": 10, "Found On": 12,
}

_HEADER_FILL = PatternFill("solid", fgColor="1F3864")
_HEADER_FONT = Font(color="FFFFFF", bold=True, name="Arial", size=11)
_PRIORITY_FILL = {
    "High": PatternFill("solid", fgColor="E2EFDA"),
    "Medium": PatternFill("solid", fgColor="FFFFC7"),
    "Low": PatternFill("solid", fgColor="FCE4D6"),
}
_NEW_STATUS = "NEW 🔴"


def _init_sheet(ws, cols: list[str]) -> None:
    for i, col in enumerate(cols, 1):
        cell = ws.cell(row=1, column=i, value=col)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(vertical="center")
        ws.column_dimensions[cell.column_letter].width = _COL_WIDTHS.get(col, 16)
    ws.freeze_panes = "A2"


def load_or_create(path: str | Path) -> openpyxl.Workbook:
    """Open the tracker, or create it with both sheets if it doesn't exist."""
    p = Path(path)
    if p.exists():
        return openpyxl.load_workbook(p)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    _init_sheet(wb.create_sheet(ROOMS_SHEET), ROOMS_COLS)
    _init_sheet(wb.create_sheet(STUDIOS_SHEET), STUDIOS_COLS)
    return wb


def _existing_urls(ws, cols: list[str]) -> set[str]:
    url_idx = cols.index("URL") + 1
    urls = set()
    for row in ws.iter_rows(min_row=2, min_col=url_idx, max_col=url_idx):
        val = row[0].value
        if val:
            # Hyperlink cells store the display text; the target is on .hyperlink
            urls.add(str(val).strip())
            if row[0].hyperlink:
                urls.add(str(row[0].hyperlink.target).strip())
    return urls


def _row_values(listing: Listing, cols: list[str], today: str) -> dict:
    common = {
        "Title": listing.title,
        "Platform": listing.platform,
        "URL": listing.url,
        "Area": listing.area,
        "Postcode": listing.postcode,
        "Price (pcm)": listing.price_pcm,
        "Bills Included": listing.bills_included,
        "Available From": listing.available_from,
        "Furnished": listing.furnished,
        "Notes": listing.notes,
        "Contact": listing.contact,
        "Status": _NEW_STATUS,
        "Priority": listing.priority,
        "Found On": today,
        "Bed Count": listing.bed_count,
        "Bedrooms": listing.bed_label or "Studio",
        "Flatmates": listing.flatmates,
    }
    return {c: common.get(c, "") for c in cols}


def _append(ws, listing: Listing, cols: list[str], today: str) -> None:
    values = _row_values(listing, cols, today)
    row_idx = ws.max_row + 1
    fill = _PRIORITY_FILL.get(listing.priority)
    for i, col in enumerate(cols, 1):
        cell = ws.cell(row=row_idx, column=i, value=values[col])
        if fill:
            cell.fill = fill
        if col == "URL" and listing.url:
            cell.value = listing.url
            cell.hyperlink = listing.url
            cell.font = Font(color="0563C1", underline="single")


def update_tracker(path: str | Path, listings: list[Listing]) -> dict:
    """Add non-duplicate listings to the tracker. Returns per-sheet counts."""
    wb = load_or_create(path)
    today = date.today().isoformat()

    sheets = {
        "room": (wb[ROOMS_SHEET], ROOMS_COLS),
        "studio": (wb[STUDIOS_SHEET], STUDIOS_COLS),
    }
    seen = {k: _existing_urls(ws, cols) for k, (ws, cols) in sheets.items()}
    added = {"room": 0, "studio": 0}
    dupes = 0

    for listing in listings:
        kind = "studio" if listing.listing_type == "studio" else "room"
        ws, cols = sheets[kind]
        url = listing.url.strip()
        if not url or url in seen[kind]:
            dupes += 1
            continue
        _append(ws, listing, cols, today)
        seen[kind].add(url)
        added[kind] += 1

    for ws, cols in sheets.values():
        ws.auto_filter.ref = ws.dimensions

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return {"rooms_added": added["room"], "studios_added": added["studio"], "duplicates": dupes}
