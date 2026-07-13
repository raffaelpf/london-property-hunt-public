"""Rightmove search parser (flat search).

Reads ``__NEXT_DATA__``: each property carries ``keyFeatures``, ``summary``,
``displaySize`` (sqft), and — when the URL includes ``keywords=balcony,terrace``
— a ``keywords:[{keyword,matched}]`` array telling us the full listing text
mentions those terms. No detail-page fetch needed.
"""

from __future__ import annotations

import re

from ..classify import apply_classification
from ..features import furnishing_from_label
from ..fetch import dump_html, fetch_html
from ..models import Listing
from . import base

_BASE = "https://www.rightmove.co.uk"
_BED_LABEL = {0: "Studio", 1: "1-Bed", 2: "2-Bed"}


def _abs(url: str) -> str:
    if not url:
        return ""
    return (url if url.startswith("http") else _BASE + url).split("#")[0]


def _to_listing(p: dict) -> Listing:
    price = p.get("price", {}) or {}
    display = (price.get("displayPrices") or [{}])[0].get("displayPrice")
    beds = p.get("bedrooms")
    addr = base.clean(p.get("displayAddress"))
    # Source attributes: the key-feature bullets plus keyword-match hints (the
    # portal telling us a term appears in the full listing text). Outdoor and
    # furnishing are decided at enrich (Claude), from these attributes.
    kf_list = [
        base.clean(k.get("description", "")) if isinstance(k, dict) else base.clean(str(k))
        for k in (p.get("keyFeatures") or [])
    ]
    matched = [
        str(k.get("keyword")) for k in (p.get("keywords") or [])
        if isinstance(k, dict) and k.get("matched") and k.get("keyword")
    ]
    attributes = [a for a in kf_list if a] + [f"listing text mentions '{m}'" for m in matched]

    return Listing(
        title=base.clean(p.get("propertySubType") or addr or "Rightmove listing"),
        platform="Rightmove",
        url=_abs(p.get("propertyUrl") or f"/properties/{p.get('id')}"),
        listing_type="flat",
        area=addr,
        postcode=base.extract_postcode(addr),
        price_pcm=base.parse_price_pcm(display) or base.parse_price_pcm(str(price.get("amount"))),
        available_from=base.clean(p.get("letAvailableDate") or ""),
        bed_count=beds,
        bed_label=_BED_LABEL.get(beds, f"{beds}-Bed" if beds is not None else ""),
        size_sqft=_parse_sqft(p.get("displaySize")),
        attributes=attributes,
        notes=base.clean(p.get("summary"))[:160],
    )


def _parse_sqft(display_size) -> int | None:
    if not display_size:
        return None
    import re
    m = re.search(r"([\d,]+)\s*sq", str(display_size))
    return int(m.group(1).replace(",", "")) if m else None


def enrich(listing: Listing, debug_dir=None) -> None:
    """Refine furnishing/outdoor/size from the Rightmove detail page.

    The detail page ships data as a flattened ``window.__PAGE_MODEL`` blob, so we
    read the rendered HTML: the ``keyFeatures`` list, the description section, and
    the "Furnish type: …" letting label.
    """
    try:
        status, html = fetch_html(listing.url)
    except Exception:
        return
    if status != 200:
        return
    s = base.soup(html)

    # Add the detail-page key-feature bullets to the source attributes. We do NOT
    # feed Claude the full description: Rightmove's detail page has a standing
    # glossary ("GARDEN: a property has access to…") that isn't specific to this
    # flat. The key features + the search-stage keyword-match hints are enough.
    kf_html = [base.clean(el.get_text(" ")) for el in s.select('[data-testid="keyFeatures"]')]
    listing.attributes = list(listing.attributes or []) + [k for k in kf_html if k]

    # Structured furnishing label from the letting-details section.
    flat_text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    m = re.search(r"Furnish type[:\s]*([A-Za-z][A-Za-z \-]{2,24})", flat_text)
    struct_furnishing = furnishing_from_label(m.group(1)) if m else "unknown"
    if struct_furnishing != "unknown":
        listing.attributes.append(f"Furnish type: {struct_furnishing}")

    apply_classification(listing, description="", struct_furnishing=struct_furnishing)


def search(url: str, cfg: dict, listing_type: str = "flat", debug_dir=None, max_pages: int = 4) -> list[Listing]:
    """Fetch several result pages (Rightmove paginates 24/page via &index=)."""
    all_props: list[dict] = []
    seen_ids: set = set()
    for page in range(max_pages):
        page_url = url + (("&" if "?" in url else "?") + f"index={page * 24}")
        status, html = fetch_html(page_url)
        if page == 0:
            dump_html(debug_dir, "rightmove-flat", html)
            if status != 200:
                raise RuntimeError(f"HTTP {status}")
        if status != 200:
            break
        nxt = base.next_data(html)
        props = base.find_list_of_dicts(nxt, {"id", "bedrooms", "propertyUrl"}) if nxt else []
        fresh = [p for p in props if p.get("id") not in seen_ids]
        if not fresh:
            break
        seen_ids.update(p.get("id") for p in fresh)
        all_props.extend(fresh)
        if len(props) < 24:
            break
    return [_to_listing(p) for p in all_props]
