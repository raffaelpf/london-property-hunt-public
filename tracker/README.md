# Tracker Spreadsheet — Schema

The pipeline reads and writes a single committed `.xlsx` at
`tracker/london_flat_hunt.xlsx` (one sheet, `Flats`). It is created on first run
and updated in place; dedup is by listing URL.

## Sheet: `Flats`

| Column | Notes |
|---|---|
| Title | Listing title / property sub-type |
| Platform | Rightmove / OnTheMarket / OpenRent |
| URL | Full listing URL (clickable) — used for deduplication |
| Area | Neighbourhood / address |
| Postcode | e.g. SE1, WC2H |
| Price (pcm) | Monthly rent in £ |
| Bedrooms | Studio / 1-Bed / 2-Bed |
| Furnishing | Unfurnished / Part Furnished / Flexible / Furnished / Unknown |
| Balcony/Terrace | Private balcony/terrace · Communal / shared · Juliet only · Not stated |
| Size (sqft) | Internal size where stated (100–6000 sanity-bounded) |
| Available From | Move-in availability if listed |
| Notes | Flags (verify balcony, communal, size not stated, listed furnished…) |
| Status | `NEW 🔴`, then update manually as you progress |
| Priority | High / Medium / Low |
| Found On | Date the row was added |

## Row colours

| Priority | Fill |
|---|---|
| High | Green `E2EFDA` |
| Medium | Yellow `FFFFC7` |
| Low | Red/orange `FCE4D6` |

Header: dark blue `1F3864`, white bold. Row 1 frozen, auto-filter enabled,
URLs written as clickable hyperlinks. See `scraper/tracker.py`.
