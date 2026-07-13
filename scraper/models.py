"""Shared data model for a single property listing."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Listing:
    """One listing, platform-agnostic. Maps onto the tracker columns."""

    title: str
    platform: str            # SpareRoom / OpenRent / Rightmove / Zoopla
    url: str
    listing_type: str = "room"   # "room" -> Listings sheet, "studio" -> Studios sheet

    area: str = ""
    postcode: str = ""
    price_pcm: Optional[int] = None
    bills_included: str = "Unknown"   # Yes / No / Unknown
    available_from: str = ""
    furnished: str = "Unknown"        # Yes / No / Unknown (legacy room field)
    bed_count: Optional[int] = None   # bedrooms
    bed_label: str = ""               # "Studio" / "1-Bed" / "2-Bed"
    flatmates: str = ""
    contact: str = ""
    notes: str = ""

    # Flat-search fields (Revision 2)
    furnishing: str = "unknown"       # unfurnished / part-furnished / flexible / furnished / unknown
    outdoor: str = "none"             # private / communal / juliet / none
    size_sqft: Optional[int] = None
    # Structured attributes from the source portal (feature tags, letting labels,
    # keyword-match hints) — the primary evidence handed to scraper.classify.
    attributes: list = field(default_factory=list)

    priority: str = ""                # set by prioritise.py: High / Medium / Low

    def combined_text(self) -> str:
        """All free text, lowercased — used for keyword-based classification."""
        return " ".join(
            str(x) for x in (self.title, self.area, self.flatmates, self.notes)
        ).lower()

    def listing_id(self) -> str:
        """A short id derived from the URL, for outreach filenames."""
        digits = re.findall(r"(\d{4,})", self.url)
        if digits:
            return digits[-1]
        slug = re.sub(r"[^a-z0-9]+", "-", self.url.lower()).strip("-")
        return slug[-24:] if slug else "listing"
