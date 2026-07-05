"""Rightmove search parser (flat search).

Reads ``__NEXT_DATA__``: each property carries ``keyFeatures``, ``summary``,
``displaySize`` (sqft), and — when the URL includes ``keywords=balcony,terrace``
— a ``keywords:[{keyword,matched}]`` array telling us the full listing text
mentions those terms. No detail-page fetch needed.
"""

from __future__ import annotations

from ..features import analyze_text
from ..fetch import dump_html, fetch_html
from ..models import Listing
from . import base

_BASE = "https://www.rightmove.co.uk"
_BED_LABEL = {0: "Studio", 1: "1-Bed", 2: "2-Bed"}


def _abs(url: str) -> str:
    if not url:
        return ""
    return (url if url.startswith("http") else _BASE + url).split("#")[0]


def _to_listing(p: dict, cfg: dict) -> Listing:
    price = p.get("price", {}) or {}
    display = (price.get("displayPrices") or [{}])[0].get("displayPrice")
    beds = p.get("bedrooms")
    addr = base.clean(p.get("displayAddress"))
    kf = " ".join(
        (k.get("description", "") if isinstance(k, dict) else str(k))
        for k in (p.get("keyFeatures") or [])
    )
    combined = " ".join([kf, base.clean(p.get("summary")), base.clean(p.get("propertyTypeFullDescription"))])
    a = analyze_text(combined)

    # Rightmove keyword-match: authoritative "term appears in the full listing".
    must = {m.lower() for m in cfg.get("FEATURE_MUST", ["balcony", "terrace"])}
    kw_hit = any(
        isinstance(k, dict) and k.get("matched") and str(k.get("keyword", "")).lower() in must
        for k in (p.get("keywords") or [])
    )
    outdoor = a["outdoor"]
    notes = base.clean(p.get("summary"))[:160]
    if outdoor == "none" and kw_hit:
        outdoor = "private"
        notes = ("balcony/terrace per listing keywords; " + notes)[:180]

    size = _parse_sqft(p.get("displaySize")) or a["sqft"]

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
        furnishing=a["furnishing"],
        outdoor=outdoor,
        size_sqft=size,
        notes=notes,
    )


def _parse_sqft(display_size) -> int | None:
    if not display_size:
        return None
    import re
    m = re.search(r"([\d,]+)\s*sq", str(display_size))
    return int(m.group(1).replace(",", "")) if m else None


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
    return [_to_listing(p, cfg) for p in all_props]
