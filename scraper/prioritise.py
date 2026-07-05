"""Assign HIGH / MEDIUM / LOW priority and apply hard filters.

Rules ported verbatim from ``README.md`` (Priority logic) and ``skill.md``
(bed-count + age filters). Kept deliberately simple and rule-based so a daily
unattended run is deterministic.
"""

from __future__ import annotations

from .config import get_int
from .models import Listing

# Keywords that mark a shared flat as young/student -> LOW (never HIGH).
_YOUNG_KEYWORDS = (
    "student", "students", "under 25", "under-25", "under 30", "18-25",
    "18 - 25", "young household", "recent graduate",
)


def _area_tier(listing: Listing, cfg: dict) -> str:
    """Return 'primary', 'secondary', or 'other' for the listing's location."""
    text = " ".join((listing.area, listing.postcode, listing.title)).lower()
    for area in cfg.get("PRIMARY_AREAS", []):
        if area.lower() in text:
            return "primary"
    for area in cfg.get("SECONDARY_AREAS", []):
        if area.lower() in text:
            return "secondary"
    return "other"


def _is_young_household(listing: Listing) -> bool:
    text = listing.combined_text()
    return any(kw in text for kw in _YOUNG_KEYWORDS)


def classify_room(listing: Listing, cfg: dict) -> str | None:
    """Priority for a room listing, or ``None`` to skip it entirely.

    Hard filter: properties with 4+ bedrooms are skipped (``None``).
    Unknown bed count is kept, with a note added by the caller.
    """
    if listing.bed_count is not None and listing.bed_count >= 4:
        return None  # MANDATORY skip: too many bedrooms

    budget = get_int(cfg, "ROOM_BUDGET")
    budget_no_bills = get_int(cfg, "ROOM_BUDGET_NO_BILLS", budget)
    tier = _area_tier(listing, cfg)
    within_budget = listing.price_pcm is None or (
        listing.price_pcm <= (budget_no_bills or listing.price_pcm)
    )
    tight_budget = listing.price_pcm is None or (
        listing.price_pcm <= (budget or listing.price_pcm)
    )
    furnished_ok = listing.furnished != "No"
    young = _is_young_household(listing)

    if tier == "primary" and tight_budget and furnished_ok and not young:
        return "High"
    if tier in ("primary", "secondary") and within_budget:
        return "Medium"
    return "Low"


def classify_studio(listing: Listing, cfg: dict) -> str:
    """Priority for a studio / 1-bed listing. No bed-count restriction."""
    budget = get_int(cfg, "STUDIO_BUDGET")
    tier = _area_tier(listing, cfg)
    within_budget = listing.price_pcm is None or (
        listing.price_pcm <= (budget or listing.price_pcm)
    )
    furnished_ok = listing.furnished != "No"

    if tier == "primary" and within_budget and furnished_ok:
        return "High"
    if tier in ("primary", "secondary") and within_budget:
        return "Medium"
    return "Low"


def prioritise(listing: Listing, cfg: dict) -> str | None:
    """Dispatch to the right classifier. ``None`` means skip (don't add)."""
    if listing.listing_type == "studio":
        return classify_studio(listing, cfg)
    priority = classify_room(listing, cfg)
    # Annotate unknowns so the human knows to verify before messaging.
    if priority is not None:
        if listing.bed_count is None:
            _append_note(listing, "Verify ≤3 bed before messaging")
        if _is_young_household(listing):
            _append_note(listing, "Young/student household — verify")
    return priority


def _append_note(listing: Listing, note: str) -> None:
    if note.lower() in listing.notes.lower():
        return
    listing.notes = f"{listing.notes}; {note}".lstrip("; ") if listing.notes else note
