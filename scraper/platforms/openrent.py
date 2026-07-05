"""OpenRent search parser + detail-page enrichment.

Search results link to ``/property-to-rent/london/{slug}/{id}`` where the slug
encodes beds + street. Furnishing / balcony / size need the detail page.
"""

from __future__ import annotations

import re

from ..features import analyze_text
from ..fetch import dump_html, fetch_html
from ..models import Listing
from . import base

_BASE = "https://www.openrent.co.uk"
_LINK_RE = re.compile(r"/property-to-rent/london/[^/]+/\d+")
_BED_LABEL = {0: "Studio", 1: "1-Bed", 2: "2-Bed"}


def _card_text(anchor):
    node = anchor
    for _ in range(6):
        node = node.parent
        if node is None:
            break
        if "£" in node.get_text():
            return base.clean(node.get_text())
    return base.clean(anchor.get_text())


def search(url: str, cfg: dict, listing_type: str = "flat", debug_dir=None) -> list[Listing]:
    status, html = fetch_html(url)
    dump_html(debug_dir, "openrent-flat", html)
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
        beds = base.parse_beds(slug)
        if beds is None:
            beds = base.parse_beds(text)
        an = analyze_text(text)
        listings.append(
            Listing(
                title=base.clean(a.get_text()) or slug.replace("-", " ").title(),
                platform="OpenRent",
                url=_BASE + href,
                listing_type="flat",
                area=slug.replace("-", " ").title(),
                postcode=base.extract_postcode(text) or base.extract_postcode(slug),
                price_pcm=base.parse_price_pcm(month.group(0)) if month else base.parse_price_pcm(text),
                bed_count=beds,
                bed_label=_BED_LABEL.get(beds, f"{beds}-Bed" if beds is not None else ""),
                furnishing=an["furnishing"],
                outdoor=an["outdoor"],
                size_sqft=an["sqft"],
                notes=text[:160],
            )
        )
    return listings


def enrich(listing: Listing, debug_dir=None) -> None:
    """Fetch the detail page and refine outdoor/furnishing/sqft from the description."""
    try:
        status, html = fetch_html(listing.url)
    except Exception:
        return
    if status != 200:
        return
    s = base.soup(html)
    desc = s.find("div", class_=re.compile("description", re.I))
    text = base.clean(desc.get_text(" ")) if desc else base.clean(s.get_text(" "))
    a = analyze_text(text[:8000])
    order = {"private": 3, "communal": 2, "juliet": 1, "none": 0}
    if order[a["outdoor"]] > order[listing.outdoor]:
        listing.outdoor = a["outdoor"]
    if listing.furnishing in ("", "unknown") and a["furnishing"] != "unknown":
        listing.furnishing = a["furnishing"]
    if not listing.size_sqft and a["sqft"]:
        listing.size_sqft = a["sqft"]
