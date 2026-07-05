"""OpenRent search parser.

Results link to ``/property-to-rent/london/{slug}/{id}`` where the slug encodes
beds + street (e.g. ``1-bed-flat-dalston-lane-e8``). Price/availability come
from the surrounding card text (``£X per month``).
"""

from __future__ import annotations

import re

from ..fetch import dump_html, fetch_html
from ..models import Listing
from . import base

_BASE = "https://www.openrent.co.uk"
_LINK_RE = re.compile(r"/property-to-rent/london/[^/]+/\d+")


def _card_text(anchor):
    node = anchor
    for _ in range(6):
        node = node.parent
        if node is None:
            break
        if "£" in node.get_text():
            return base.clean(node.get_text())
    return base.clean(anchor.get_text())


def search(url: str, cfg: dict, listing_type: str = "room", debug_dir=None) -> list[Listing]:
    status, html = fetch_html(url)
    dump_html(debug_dir, f"openrent-{listing_type}", html)
    if status != 200:
        raise RuntimeError(f"HTTP {status}")

    s = base.soup(html)
    seen: set[str] = set()
    listings: list[Listing] = []
    for a in s.find_all("a", href=True):
        href = a["href"].split("?")[0]
        if not _LINK_RE.search(href) or href in seen:
            continue
        seen.add(href)
        slug = href.rstrip("/").split("/")[-2] if href.count("/") >= 2 else href
        text = _card_text(a)
        month = re.search(r"£([\d,]+)\s*(?:per month|pcm)", text, re.I)
        beds = base.parse_beds(slug) if base.parse_beds(slug) is not None else base.parse_beds(text)
        title = base.clean(a.get_text()) or slug.replace("-", " ").title()

        listings.append(
            Listing(
                title=title,
                platform="OpenRent",
                url=_BASE + href,
                listing_type=listing_type,
                area=slug.replace("-", " ").title(),
                postcode=base.extract_postcode(text) or base.extract_postcode(slug),
                price_pcm=base.parse_price_pcm(month.group(0)) if month else base.parse_price_pcm(text),
                furnished="Yes" if "furnished" in text.lower() else "Unknown",
                bed_count=beds if listing_type == "room" else None,
                bed_label=("Studio" if beds == 0 else "1-Bed") if listing_type == "studio" else "",
                notes=text[:180],
            )
        )
    return listings
