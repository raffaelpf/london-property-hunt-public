"""Zoopla search parser + detail-page enrichment.

All fetches go through :mod:`scraper.fetch_browser` (headed Chromium) because
Zoopla 403s plain HTTP clients behind a Cloudflare challenge — see that module
for the details.

Search results live in a ``application/ld+json`` schema block (the
``lsrp-schema`` script): a ``SearchResultsPage`` whose ``itemListElement``
carries ``Product`` items with name/price/url/address/description. The richer
fields (``furnishedState``, EPC-derived ``sizeSqft``, ``smartTags`` like
``attributes.balcony``) live in the detail page's escaped Next.js flight data,
which we pull out with targeted regexes rather than parsing the whole blob.
"""

from __future__ import annotations

import re
import sys

from .. import fetch_browser
from ..features import analyze_text
from ..fetch import dump_html
from ..models import Listing
from . import base

_BED_LABEL = {0: "Studio", 1: "1-Bed", 2: "2-Bed"}

# Fields in the flight data appear both JSON-escaped (\"key\":...) and plain.
_FURNISHED_RE = re.compile(r'furnishedState\\?"\s*:\s*\\?"([a-z_]+)')
_SQFT_RE = re.compile(r'sizeSqft\\?"\s*:\s*(\d+)')
_FEATURES_RE = re.compile(r'features\\?"\s*:\s*\[((?:[^\[\]]){0,2000}?)\]')

_FURNISHED_MAP = {
    "furnished": "furnished",
    "unfurnished": "unfurnished",
    "part_furnished": "part-furnished",
    "furnished_or_unfurnished": "flexible",
}


def _schema_items(html: str) -> list[dict]:
    """Return the Product items from the SearchResultsPage JSON-LD block."""
    for block in base.json_ld_blocks(html):
        for node in base.walk(block):
            if isinstance(node, dict) and node.get("@type") == "SearchResultsPage":
                entity = node.get("mainEntity") or {}
                return [
                    li.get("item") for li in entity.get("itemListElement") or []
                    if isinstance(li.get("item"), dict)
                ]
    return []


def search(url: str, cfg: dict, listing_type: str = "flat", debug_dir=None) -> list[Listing]:
    status, html = fetch_browser.get(url)
    dump_html(debug_dir, f"zoopla-{url[:60]}", html)
    if status != 200:
        raise RuntimeError(f"HTTP {status} (Cloudflare challenge not cleared)")
    # An unknown area slug renders a valid page with an empty location name
    # ("...to rent in  - Zoopla") and zero results. Zoopla shares the area list
    # with platforms that DO know these names, so skip quietly rather than
    # error on every run — but say so on the console for typo-hunting.
    m = re.search(r"<title>([^<]*)</title>", html)
    if m and re.search(r"to rent in\s*-", m.group(1)):
        slug = re.search(r"/to-rent/\w+/([^/?]+)", url)
        print(f"  [Zoopla] skipping '{slug.group(1) if slug else url}' — "
              "not a Zoopla location name", file=sys.stderr)
        return []

    listings: list[Listing] = []
    for item in _schema_items(html):
        detail_url = item.get("url") or ""
        if not detail_url:
            continue
        related = item.get("isRelatedTo") or {}
        addr = base.clean(related.get("address"))
        name = base.clean(item.get("name"))
        desc = base.clean(item.get("description"))
        price = (item.get("offers") or {}).get("price")
        a = analyze_text(name, desc)
        beds = base.parse_beds(name)
        listings.append(
            Listing(
                title=name or "Zoopla listing",
                platform="Zoopla",
                url=detail_url,
                listing_type="flat",
                area=addr,
                postcode=base.extract_postcode(addr),
                # price_frequency=per_month is pinned in the search URL
                price_pcm=base.parse_price_pcm(str(price)) if price else None,
                bed_count=beds,
                bed_label=_BED_LABEL.get(beds, f"{beds}-Bed" if beds is not None else ""),
                furnishing=a["furnishing"],
                outdoor=a["outdoor"],
                size_sqft=a["sqft"],
                notes=desc[:160],
            )
        )
    return listings


def _flight_features(html: str) -> str:
    """Concatenate feature-array strings from the flight data (best effort)."""
    parts = []
    for m in _FEATURES_RE.finditer(html):
        chunk = m.group(1)
        if '"' in chunk:  # skip non-listing hits (e.g. polyfill query strings)
            parts.append(chunk.replace('\\"', " ").replace('"', " "))
    return " ".join(parts)[:4000]


def enrich(listing: Listing, debug_dir=None) -> None:
    """Refine furnishing / outdoor / size from the detail page."""
    try:
        status, html = fetch_browser.get(listing.url)
    except Exception:
        return
    if status != 200:
        return

    m = _FURNISHED_RE.search(html)
    if m and listing.furnishing in ("", "unknown"):
        listing.furnishing = _FURNISHED_MAP.get(m.group(1), "unknown")

    if not listing.size_sqft:
        m = _SQFT_RE.search(html)
        if m:
            size = int(m.group(1))
            if 100 <= size <= 6000:
                listing.size_sqft = size

    # Description (JSON-LD) + feature lists; smartTags are the strongest
    # balcony/terrace signal Zoopla has.
    desc = ""
    for block in base.json_ld_blocks(html):
        for node in base.walk(block):
            if isinstance(node, dict) and node.get("@type") == "RealEstateListing":
                desc = str(node.get("description") or "")
    a = analyze_text(desc, _flight_features(html))
    order = {"private": 3, "communal": 2, "juliet": 1, "none": 0}
    if order[a["outdoor"]] > order[listing.outdoor]:
        listing.outdoor = a["outdoor"]
    if listing.outdoor == "none" and re.search(r"attributes\.(balcony|terrace|roof_terrace)", html):
        listing.outdoor = "private"
    if listing.furnishing in ("", "unknown") and a["furnishing"] != "unknown":
        listing.furnishing = a["furnishing"]
    if not listing.size_sqft and a["sqft"]:
        listing.size_sqft = a["sqft"]
