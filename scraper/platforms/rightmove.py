"""Rightmove search parser.

Rightmove embeds all results as JSON. We try ``window.jsonModel.properties``
first (classic search pages), then fall back to a deep search of
``__NEXT_DATA__`` for a list of property-shaped dicts.
"""

from __future__ import annotations

from ..models import Listing
from . import base

_BASE = "https://www.rightmove.co.uk"


def _abs(url: str) -> str:
    if not url:
        return ""
    return url if url.startswith("http") else _BASE + url


def _from_json_model(props: list[dict], listing_type: str) -> list[Listing]:
    out = []
    for p in props:
        price = p.get("price", {}) or {}
        display = (price.get("displayPrices") or [{}])[0].get("displayPrice")
        beds = p.get("bedrooms")
        addr = base.clean(p.get("displayAddress"))
        out.append(
            Listing(
                title=base.clean(p.get("propertyTypeFullDescription") or addr or "Rightmove listing"),
                platform="Rightmove",
                url=_abs(p.get("propertyUrl") or f"/properties/{p.get('id')}"),
                listing_type=listing_type,
                area=addr,
                postcode=base.extract_postcode(addr),
                price_pcm=base.parse_price_pcm(display) or base.parse_price_pcm(str(price.get("amount"))),
                furnished="Unknown",
                bed_count=beds if listing_type == "room" else None,
                bed_label=("Studio" if beds == 0 else "1-Bed") if listing_type == "studio" else "",
                notes=addr,
            )
        )
    return out


def search(context, url: str, cfg: dict, listing_type: str = "studio", debug_dir=None) -> list[Listing]:
    page = context.new_page()
    try:
        base.goto(page, url)
        base.dump_html(page, debug_dir, f"rightmove-{listing_type}")
        model = base.js_var(page, "jsonModel")
        props = (model or {}).get("properties") if isinstance(model, dict) else None
        if not props:
            nxt = base.next_data(page)
            if nxt:
                props = base.find_list_of_dicts(nxt, {"id", "bedrooms", "propertyUrl"}) \
                    or base.find_list_of_dicts(nxt, {"bedrooms", "displayAddress"})
    finally:
        page.close()

    return _from_json_model(props or [], listing_type)
