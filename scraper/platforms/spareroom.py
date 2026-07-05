"""SpareRoom search parser.

Each result is ``<li class="listing-result">`` carrying rich ``data-listing-*``
attributes (id, url, title, neighbourhood, postcode, rooms-in-property,
normalised rate + period) — we read those directly.
"""

from __future__ import annotations

import re

from ..fetch import dump_html, fetch_html
from ..models import Listing
from . import base

_BASE = "https://www.spareroom.co.uk"


def _abs(url: str) -> str:
    if not url:
        return ""
    return url if url.startswith("http") else _BASE + url


def search(url: str, cfg: dict, listing_type: str = "room", debug_dir=None) -> list[Listing]:
    status, html = fetch_html(url)
    dump_html(debug_dir, f"spareroom-{listing_type}", html)
    if status != 200:
        raise RuntimeError(f"HTTP {status}")

    s = base.soup(html)
    listings: list[Listing] = []
    for card in s.select("li.listing-result"):
        d = card.attrs
        href = d.get("data-listing-url") or ""
        if not href:
            link = card.find("a", href=True)
            href = link["href"] if link else ""
        if not href:
            continue

        rate = d.get("data-listing-ad-rate-normalised") or d.get("data-listing-ad-headline-rate")
        period = d.get("data-listing-ad-rate-normalised-period") or d.get("data-listing-ad-headline-rate-period") or ""
        rooms = d.get("data-listing-rooms-in-property")
        card_text = base.clean(card.get_text())
        avail = re.search(r"Available\s+([A-Za-z0-9 ]+?)(?:\s*[-–]|\s{2,}|$)", card_text)

        listings.append(
            Listing(
                title=base.clean(d.get("data-listing-title")) or "SpareRoom listing",
                platform="SpareRoom",
                url=_abs(href),
                listing_type=listing_type,
                area=base.clean(d.get("data-listing-neighbourhood")),
                postcode=base.clean(d.get("data-listing-postcode")),
                price_pcm=base.parse_price_pcm(f"{rate} {period}" if rate else None),
                bills_included="Yes" if "bills inc" in card_text.lower() else "Unknown",
                available_from=base.clean(avail.group(1)) if avail else "",
                furnished="Yes" if "furnished" in card_text.lower() else "Unknown",
                bed_count=int(rooms) if (rooms and rooms.isdigit() and listing_type == "room") else None,
                bed_label="Studio" if listing_type == "studio" else "",
                flatmates=base.clean(d.get("data-listing-advertiser-role")),
                notes=card_text[:180],
            )
        )
    return listings
