"""Zoopla search parser.

Zoopla exposes results as JSON-LD (``ItemList`` / ``itemListElement``) and in
``__NEXT_DATA__``. We try JSON-LD first, then a deep search of the Next.js data.
"""

from __future__ import annotations

from ..models import Listing
from . import base


def _from_json_ld(blocks: list, listing_type: str) -> list[Listing]:
    out = []
    for block in blocks:
        candidates = block if isinstance(block, list) else [block]
        for b in candidates:
            if not isinstance(b, dict):
                continue
            items = b.get("itemListElement") or []
            for it in items:
                node = it.get("item", it) if isinstance(it, dict) else {}
                url = node.get("url") or it.get("url")
                if not url:
                    continue
                name = base.clean(node.get("name") or "Zoopla listing")
                out.append(
                    Listing(
                        title=name,
                        platform="Zoopla",
                        url=url,
                        listing_type=listing_type,
                        area=name,
                        postcode=base.extract_postcode(name),
                        price_pcm=base.parse_price_pcm(str(node.get("price") or "")),
                        bed_label=listing_type == "studio" and "1-Bed" or "",
                    )
                )
    return out


def _from_next_data(nxt: dict, listing_type: str) -> list[Listing]:
    listings = base.find_list_of_dicts(nxt, {"listingId", "price"}) \
        or base.find_list_of_dicts(nxt, {"numBeds", "price"})
    out = []
    for p in listings:
        addr = base.clean(p.get("address") or p.get("title") or "Zoopla listing")
        lid = p.get("listingId") or p.get("id")
        url = p.get("listingUris", {}).get("detail") if isinstance(p.get("listingUris"), dict) else None
        url = url or (f"https://www.zoopla.co.uk/to-rent/details/{lid}" if lid else "")
        if url and not url.startswith("http"):
            url = "https://www.zoopla.co.uk" + url
        beds = p.get("numBeds")
        out.append(
            Listing(
                title=addr,
                platform="Zoopla",
                url=url,
                listing_type=listing_type,
                area=addr,
                postcode=base.extract_postcode(addr),
                price_pcm=base.parse_price_pcm(str(p.get("price"))),
                bed_count=beds if listing_type == "room" else None,
                bed_label=("Studio" if beds == 0 else "1-Bed") if listing_type == "studio" else "",
            )
        )
    return [l for l in out if l.url]


def search(context, url: str, cfg: dict, listing_type: str = "studio", debug_dir=None) -> list[Listing]:
    page = context.new_page()
    try:
        base.goto(page, url)
        base.dump_html(page, debug_dir, f"zoopla-{listing_type}")
        listings = _from_json_ld(base.json_ld_blocks(page), listing_type)
        if not listings:
            nxt = base.next_data(page)
            if nxt:
                listings = _from_next_data(nxt, listing_type)
    finally:
        page.close()
    return listings
