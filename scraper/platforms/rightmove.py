"""Rightmove search parser — reads the ``__NEXT_DATA__`` JSON blob."""

from __future__ import annotations

from ..fetch import dump_html, fetch_html
from ..models import Listing
from . import base

_BASE = "https://www.rightmove.co.uk"


def _abs(url: str) -> str:
    if not url:
        return ""
    return url if url.startswith("http") else _BASE + url


def _to_listing(p: dict, listing_type: str) -> Listing:
    price = p.get("price", {}) or {}
    display = (price.get("displayPrices") or [{}])[0].get("displayPrice")
    beds = p.get("bedrooms")
    addr = base.clean(p.get("displayAddress"))
    return Listing(
        title=base.clean(p.get("propertySubType") or addr or "Rightmove listing"),
        platform="Rightmove",
        url=_abs(p.get("propertyUrl") or f"/properties/{p.get('id')}").split("#")[0],
        listing_type=listing_type,
        area=addr,
        postcode=base.extract_postcode(addr),
        price_pcm=base.parse_price_pcm(display) or base.parse_price_pcm(str(price.get("amount"))),
        available_from=base.clean(p.get("letAvailableDate") or ""),
        furnished="Unknown",
        bed_count=beds if listing_type == "room" else None,
        bed_label=("Studio" if beds == 0 else "1-Bed") if listing_type == "studio" else "",
        notes=base.clean(p.get("summary"))[:180],
    )


def search(url: str, cfg: dict, listing_type: str = "studio", debug_dir=None) -> list[Listing]:
    status, html = fetch_html(url)
    dump_html(debug_dir, f"rightmove-{listing_type}", html)
    if status != 200:
        raise RuntimeError(f"HTTP {status}")

    nxt = base.next_data(html)
    props = base.find_list_of_dicts(nxt, {"id", "bedrooms", "propertyUrl"}) if nxt else []
    if not props:
        model = base.js_var(html, "jsonModel")
        props = (model or {}).get("properties", []) if isinstance(model, dict) else []
    return [_to_listing(p, listing_type) for p in props]
