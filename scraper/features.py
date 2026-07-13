"""Shared vocabulary for listing classification.

Outdoor space and furnishing are decided by :mod:`scraper.classify` (Claude),
given each platform's structured attributes plus the description. This module no
longer does any free-text regex matching — a regex garden-matcher used to read
place names like "Covent Garden" as a communal garden. All that remains here is
the tracker's outdoor labels and a reader for a portal's structured furnishing
label (a dedicated letting field, not the description), used both to feed Claude
and as the fallback when Claude is unavailable.
"""

from __future__ import annotations

# Outdoor labels for the tracker (keys match classify.OUTDOOR_CATEGORIES).
OUTDOOR_LABELS = {
    "private": "Private balcony/terrace",
    "communal": "Communal / shared",
    "juliet": "Juliet only",
    "none": "Not stated",
}


def furnishing_from_label(label: str) -> str:
    """Normalise a portal's structured furnishing label to our internal value.

    Reads a dedicated letting field (e.g. OnTheMarket ``lettingDetails`` item,
    Rightmove "Furnish type"), never the free-text description — so it's a
    reliable source attribute, not a keyword guess. Returns "unknown" when the
    label is absent or unrecognised.
    """
    t = (label or "").strip().lower()
    if not t:
        return "unknown"
    if "furnished or unfurnished" in t or "unfurnished or furnished" in t:
        return "flexible"
    if t.startswith("part") or "part furnished" in t or "part-furnished" in t:
        return "part-furnished"
    if "unfurnished" in t:
        return "unfurnished"
    if "furnished" in t:
        return "furnished"
    return "unknown"
