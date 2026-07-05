"""Generate personalised outreach ``.txt`` files for HIGH-priority listings.

Message template mirrors ``skill.md`` (< 100 words, personalised opener +
tenant profile + move-in + viewing request).
"""

from __future__ import annotations

import re
from pathlib import Path

from .models import Listing


def _opening_line(listing: Listing) -> str:
    """One specific sentence referencing this listing."""
    where = listing.area or "the area"
    if listing.price_pcm:
        return f"your {listing.listing_type} in {where} at £{listing.price_pcm} pcm caught my eye"
    return f"your {listing.listing_type} listing in {where} caught my eye"


def build_message(listing: Listing, cfg: dict) -> str:
    name = cfg.get("YOUR_NAME", "").strip() or "Me"
    age = cfg.get("YOUR_AGE", "").strip()
    profession = cfg.get("YOUR_PROFESSION", "").strip()
    summary = cfg.get("YOUR_PROFILE_SUMMARY", "").strip()
    move_in = cfg.get("MOVE_IN_DATE", "").strip()

    who = ", ".join(p for p in (name, age, profession) if p)
    parts = [
        f"Hi, {_opening_line(listing)}.",
        f"I'm {who}." if who else "",
        f"{summary}." if summary and not summary.endswith(".") else summary,
        f"Looking to move in around {move_in}." if move_in else "",
        "Happy to arrange a viewing whenever suits.",
        name,
    ]
    return "\n\n".join(p for p in parts if p)


def _safe(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "x"


def write_outreach_files(listings: list[Listing], cfg: dict, out_dir: str | Path) -> list[Path]:
    """Write one ``.txt`` per HIGH-priority listing. Returns the paths written."""
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for listing in listings:
        if listing.priority != "High":
            continue
        fname = f"outreach_{_safe(listing.platform)}_{_safe(listing.area)}_{listing.listing_id()}.txt"
        target = out_path / fname
        target.write_text(build_message(listing, cfg) + "\n", encoding="utf-8")
        written.append(target)
    return written
