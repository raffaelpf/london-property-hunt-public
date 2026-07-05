"""Zoopla search parser — JSON-LD ``ItemList`` first, then ``__NEXT_DATA__``.

Note: Zoopla is fronted by Cloudflare and frequently returns 403 to
datacenter/non-interactive clients. When that happens the parser raises and the
orchestrator isolates it (the run still completes with the other platforms).
"""

from __future__ import annotations

from ..fetch import dump_html, fetch_html
from ..models import Listing
from . import base

_BASE = "https://www.zoopla.co.uk"


def _from_json_ld(blocks: list, listing_type: str) -> list[Listing]:
    out = []
    for block in blocks:
        for b in (block if isinstance(block, list) else [block]):
            if not isinstance(b, dict):
                continue
            for it in b.get("itemListElement") or []:
                node = it.get("item", it) if isinstance(it, dict) else {}
                url = node.get("url") or (it.get("url") if isinstance(it, dict) else None)
                if not url:
                    continue
                name = base.clean(node.get("name") or "Zoopla listing")
                out.append(
                    Listing(
                        title=name, platform="Zoopla", url=url, listing_type=listing_type,
                        area=name, postcode=base.extract_postcode(name),
                        price_pcm=base.parse_price_pcm(str(node.get("price") or "")),
                        bed_label="1-Bed" if listing_type == "studio" else "",
                    )
                )
    return out


def _from_next_data(nxt: dict, listing_type: str) -> list[Listing]:
    props = base.find_list_of_dicts(nxt, {"listingId", "price"}) \
        or base.find_list_of_dicts(nxt, {"numBeds", "price"})
    out = []
    for p in props:
        addr = base.clean(p.get("address") or p.get("title") or "Zoopla listing")
        lid = p.get("listingId") or p.get("id")
        uris = p.get("listingUris") if isinstance(p.get("listingUris"), dict) else {}
        url = uris.get("detail") or (f"/to-rent/details/{lid}" if lid else "")
        if url and not url.startswith("http"):
            url = _BASE + url
        beds = p.get("numBeds")
        if not url:
            continue
        out.append(
            Listing(
                title=addr, platform="Zoopla", url=url, listing_type=listing_type,
                area=addr, postcode=base.extract_postcode(addr),
                price_pcm=base.parse_price_pcm(str(p.get("price"))),
                bed_count=beds if listing_type == "room" else None,
                bed_label=("Studio" if beds == 0 else "1-Bed") if listing_type == "studio" else "",
            )
        )
    return out


def search(url: str, cfg: dict, listing_type: str = "studio", debug_dir=None) -> list[Listing]:
    status, html = fetch_html(url)
    dump_html(debug_dir, f"zoopla-{listing_type}", html)
    if status != 200:
        raise RuntimeError(f"HTTP {status} (Cloudflare block likely)")

    listings = _from_json_ld(base.json_ld_blocks(html), listing_type)
    if not listings:
        nxt = base.next_data(html)
        if nxt:
            listings = _from_next_data(nxt, listing_type)
    return listings
