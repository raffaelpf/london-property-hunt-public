"""OnTheMarket search parser + detail-page enrichment.

Search results (in ``__NEXT_DATA__``) carry a ``features`` array, ``bedrooms``,
``short-price``, ``address``, ``details-url``. Furnishing / full description /
exact sqft live on the detail page, fetched by :func:`enrich`.
"""

from __future__ import annotations

from ..features import analyze_text
from ..fetch import dump_html, fetch_html
from ..models import Listing
from . import base

_BASE = "https://www.onthemarket.com"
_BED_LABEL = {0: "Studio", 1: "1-Bed", 2: "2-Bed"}


def _listings_array(nxt) -> list[dict]:
    best: list[dict] = []
    for node in base.walk(nxt):
        if isinstance(node, list) and node and isinstance(node[0], dict):
            if "details-url" in node[0] or "features" in node[0]:
                if len(node) > len(best):
                    best = node
    return best


def search(url: str, cfg: dict, listing_type: str = "flat", debug_dir=None) -> list[Listing]:
    status, html = fetch_html(url)
    dump_html(debug_dir, "onthemarket-flat", html)
    if status != 200:
        raise RuntimeError(f"HTTP {status}")

    nxt = base.next_data(html)
    listings: list[Listing] = []
    for p in _listings_array(nxt):
        details = p.get("details-url") or ""
        if not details:
            continue
        feats = " ".join(str(x) for x in (p.get("features") or []))
        addr = base.clean(p.get("address"))
        beds = p.get("bedrooms")
        a = analyze_text(feats)
        listings.append(
            Listing(
                title=base.clean(p.get("property-title") or p.get("humanised-property-type") or "OnTheMarket listing"),
                platform="OnTheMarket",
                url=_BASE + details if details.startswith("/") else details,
                listing_type="flat",
                area=addr,
                postcode=base.extract_postcode(addr),
                price_pcm=base.parse_price_pcm(str(p.get("short-price") or p.get("price"))),
                bed_count=beds,
                bed_label=_BED_LABEL.get(beds, f"{beds}-Bed" if beds is not None else ""),
                furnishing=a["furnishing"],
                outdoor=a["outdoor"],
                size_sqft=a["sqft"],
                notes=base.clean(feats)[:160],
            )
        )
    return listings


def enrich(listing: Listing, debug_dir=None) -> None:
    """Fetch the detail page and refine outdoor/furnishing/sqft from full text."""
    try:
        status, html = fetch_html(listing.url)
    except Exception:
        return
    if status != 200:
        return
    text = _detail_text(html)
    a = analyze_text(text)
    _merge(listing, a)


def _detail_text(html: str) -> str:
    """Pull the description + features from an OTM detail page."""
    nxt = base.next_data(html)
    parts: list[str] = []
    if nxt:
        for node in base.walk(nxt):
            if isinstance(node, dict):
                for key in ("description", "features", "full-description", "key-features"):
                    v = node.get(key)
                    if isinstance(v, str):
                        parts.append(v)
                    elif isinstance(v, list):
                        parts += [str(x) for x in v]
    if not parts:  # fallback: visible body text
        parts.append(base.soup(html).get_text(" "))
    return base.clean(" ".join(parts))[:8000]


def _merge(listing: Listing, a: dict) -> None:
    # Prefer a definite outdoor finding from the detail page.
    order = {"private": 3, "communal": 2, "juliet": 1, "none": 0}
    if order[a["outdoor"]] > order[listing.outdoor]:
        listing.outdoor = a["outdoor"]
    if listing.furnishing in ("", "unknown") and a["furnishing"] != "unknown":
        listing.furnishing = a["furnishing"]
    if not listing.size_sqft and a["sqft"]:
        listing.size_sqft = a["sqft"]
