"""Assign HIGH / MEDIUM / LOW priority for flat listings, and apply hard filters.

Rules (Revision 5 — balcony/terrace as a hard gate):
- Hard filters (return None → drop): out of price range, bedrooms outside range,
  or NO confirmed outdoor space (juliet-only / not stated are dropped entirely —
  never tracked, never notified).
- Priority = furnishing first, then size (all survivors have outdoor space):
  - LOW    — listed *furnished* (kept: some landlords are flexible).
  - HIGH   — private balcony/terrace + furnishing in (unfurnished, part-furnished,
             flexible) + size >= MIN_SQFT confirmed.
  - MEDIUM — everything else that passed the gate (communal/shared outdoor,
             size unknown or small, furnishing unknown).
"""

from __future__ import annotations

from .config import get_int
from .models import Listing

_FURNISH_OK = ("unfurnished", "part-furnished", "flexible")


def _area_tier(listing: Listing, cfg: dict) -> str:
    """'primary' / 'secondary' / 'other' — used by run_hunt's in-scope filter."""
    text = " ".join((listing.area, listing.postcode, listing.title)).lower()
    for area in cfg.get("PRIMARY_AREAS", []):
        if area.lower() in text:
            return "primary"
    for area in cfg.get("SECONDARY_AREAS", []):
        if area.lower() in text:
            return "secondary"
    return "other"


def prioritise(listing: Listing, cfg: dict) -> str | None:
    """Return High/Medium/Low, or None to drop the listing."""
    price_min = get_int(cfg, "PRICE_MIN", 0) or 0
    price_max = get_int(cfg, "PRICE_MAX")
    min_beds = get_int(cfg, "MIN_BEDROOMS", 0) or 0
    max_beds = get_int(cfg, "MAX_BEDROOMS")
    min_sqft = get_int(cfg, "MIN_SQFT")

    # Hard filters -------------------------------------------------------
    if listing.price_pcm is not None:
        if price_max and listing.price_pcm > price_max:
            return None
        if price_min and listing.price_pcm < price_min:
            return None
    if listing.bed_count is not None:
        if max_beds is not None and listing.bed_count > max_beds:
            return None
        if listing.bed_count < min_beds:
            return None
    # Balcony/terrace is a MUST: no confirmed outdoor space -> drop entirely.
    if listing.outdoor not in ("private", "communal"):
        return None

    # Priority: furnishing first, then size ------------------------------
    if listing.furnishing == "furnished":
        _note(listing, "listed furnished — check if flexible")
        return "Low"

    furn_ok = listing.furnishing in _FURNISH_OK
    big_enough = (
        listing.size_sqft is not None
        and (min_sqft is None or listing.size_sqft >= min_sqft)
    )

    if listing.outdoor == "communal":
        _note(listing, "outdoor space is communal/shared")
    if listing.size_sqft is None:
        _note(listing, "size not stated")
    elif min_sqft and listing.size_sqft < min_sqft:
        _note(listing, f"under {min_sqft} sq ft")
    if not furn_ok:
        _note(listing, "furnishing not stated")

    if listing.outdoor == "private" and furn_ok and big_enough:
        return "High"
    return "Medium"


def _note(listing: Listing, note: str) -> None:
    if note.lower() in (listing.notes or "").lower():
        return
    listing.notes = f"{listing.notes}; {note}".lstrip("; ") if listing.notes else note
