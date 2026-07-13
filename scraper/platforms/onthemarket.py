"""OnTheMarket search parser + detail-page enrichment.

Search results (in ``__NEXT_DATA__``) carry a ``features`` array, ``bedrooms``,
``short-price``, ``address``, ``details-url``. Furnishing / full description /
exact sqft live on the detail page, fetched by :func:`enrich`.
"""

from __future__ import annotations

from ..features import furnishing_from_label
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
        feat_list = [str(x) for x in (p.get("features") or [])]
        addr = base.clean(p.get("address"))
        beds = p.get("bedrooms")
        # Outdoor/furnishing/size are decided at enrich (Claude, from the fuller
        # detail page); the search feature tags are carried as source attributes.
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
                attributes=feat_list,
                notes=base.clean(" ".join(feat_list))[:160],
            )
        )
    return listings


def _property_node(nxt) -> dict | None:
    """Find the detail-page property object (camelCase keys)."""
    for node in base.walk(nxt):
        if isinstance(node, dict) and "lettingDetails" in node and "description" in node:
            return node
    for node in base.walk(nxt):
        if isinstance(node, dict) and "propertyTitle" in node and "description" in node:
            return node
    return None


def _node_size_sqft(node: dict) -> int | None:
    size = node.get("minimumAreaSqFt")
    if not size and node.get("minimumAreaSqM"):
        try:
            size = round(float(node["minimumAreaSqM"]) * 10.7639)
        except (TypeError, ValueError):
            size = None
    try:
        size = int(size) if size else None
    except (TypeError, ValueError):
        return None
    return size if size and 100 <= size <= 6000 else None


def enrich(listing: Listing, debug_dir=None) -> None:
    """Fetch the detail page and refine outdoor/furnishing/size from real fields.

    The furnishing lives in ``lettingDetails.items`` (e.g. ["Furnished", ...]),
    the description in ``description``, features in ``features:[{feature}]``, and
    size in ``minimumAreaSqFt`` — not in a single description blob.
    """
    try:
        status, html = fetch_html(listing.url)
    except Exception:
        return
    if status != 200:
        return

    nxt = base.next_data(html)
    node = _property_node(nxt) if nxt else None
    if node:
        letting = node.get("lettingDetails") or {}
        items = [str(x) for x in (letting.get("items") or [])] if isinstance(letting, dict) else []
        tags = [
            str(f.get("feature", "")) if isinstance(f, dict) else str(f)
            for f in (node.get("features") or [])
        ]
        # Source attributes first: letting labels + feature tags are the primary
        # evidence for the classifier (run in one batch by run_hunt); the
        # description is the backup, and size/furnishing come from structured fields.
        listing.attributes = items + tags
        listing.description = base.clean(" ".join([str(node.get("description") or ""), str(node.get("summary") or "")]))
        size = _node_size_sqft(node)
        if size:
            listing.size_sqft = size
        listing.furnishing = _furnishing_of(items)
    else:  # fallback: visible body text, no structured attributes
        listing.description = base.clean(base.soup(html).get_text(" "))


def _furnishing_of(items: list[str]) -> str:
    """First recognised furnishing label among the letting-detail items."""
    for it in items:
        f = furnishing_from_label(it)
        if f != "unknown":
            return f
    return "unknown"
