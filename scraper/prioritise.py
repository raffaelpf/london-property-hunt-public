"""Assign HIGH / MEDIUM / LOW priority for flat listings, and apply hard filters.

Rules (Revision 2 — Raffael's flat search):
- Hard filters (return None → drop): out of £3–4.5k range, or bedrooms outside 1–2.
- Outdoor space is a MUST but *include-but-flag*: never dropped, only downgraded.
  private balcony/terrace → HIGH-eligible; communal → MEDIUM; juliet/none → LOW (flagged).
- Size > MIN_SQFT boosts; unknown size never penalised.
"""

from __future__ import annotations

from .config import get_int
from .models import Listing


def _area_tier(listing: Listing, cfg: dict) -> str:
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

    tier = _area_tier(listing, cfg)
    big_enough = listing.size_sqft is None or (min_sqft is None) or listing.size_sqft >= min_sqft
    furnish_ok = listing.furnishing in ("unfurnished", "part-furnished", "flexible", "unknown", "")

    # Flag notes for the human -----------------------------------------
    if listing.outdoor in ("juliet", "none"):
        _note(listing, "verify balcony/terrace")
    elif listing.outdoor == "communal":
        _note(listing, "outdoor space is communal/shared")
    if listing.size_sqft is None:
        _note(listing, "size not stated")
    elif min_sqft and listing.size_sqft < min_sqft:
        _note(listing, f"under {min_sqft} sq ft")
    if listing.furnishing == "furnished":
        _note(listing, "listed furnished — check if flexible")

    # Priority ----------------------------------------------------------
    # Furnishing is a MUST (unfurnished/part/flexible). A listing that reads as
    # plainly "furnished" is kept but capped at LOW (detection isn't perfect —
    # it may actually be flexible), everything else tiers normally.
    if not furnish_ok:
        return "Low"
    if listing.outdoor == "private" and tier != "other" and big_enough:
        return "High"
    if listing.outdoor in ("private", "communal") and tier != "other":
        return "Medium"
    return "Low"


def _note(listing: Listing, note: str) -> None:
    if note.lower() in (listing.notes or "").lower():
        return
    listing.notes = f"{listing.notes}; {note}".lstrip("; ") if listing.notes else note
