"""OpenRent search parser (DOM-based)."""

from __future__ import annotations

from ..models import Listing
from . import base

_EXTRACT_JS = """
() => {
  const out = [];
  const seen = new Set();
  document.querySelectorAll("a[href*='/property-to-rent/']").forEach(a => {
    const href = a.href.split('#')[0].split('?')[0];
    if (!/\\/\\d{4,}(?:$|\\/)/.test(href) && !/\\/property-to-rent\\/london\\//.test(href)) return;
    if (seen.has(href)) return; seen.add(href);
    const card = a.closest('.pli, .listing, li, article, .property') || a.parentElement;
    out.push({
      href,
      title: (a.innerText || card?.querySelector('h2,h3,.listing-title')?.innerText || '').trim(),
      text: (card?.innerText || '').trim(),
    });
  });
  return out;
}
"""


def search(context, url: str, cfg: dict, listing_type: str = "room", debug_dir=None) -> list[Listing]:
    page = context.new_page()
    try:
        base.goto(page, url)
        base.dump_html(page, debug_dir, f"openrent-{listing_type}")
        raw = page.evaluate(_EXTRACT_JS) or []
    finally:
        page.close()

    listings: list[Listing] = []
    for item in raw:
        href, title, text = item.get("href"), base.clean(item.get("title")), item.get("text", "")
        if not href:
            continue
        beds = base.parse_beds(text if text else title)
        listings.append(
            Listing(
                title=title or "OpenRent listing",
                platform="OpenRent",
                url=href,
                listing_type=listing_type,
                postcode=base.extract_postcode(text),
                price_pcm=base.parse_price_pcm(text),
                furnished="Yes" if "furnished" in text.lower() else "Unknown",
                bed_count=beds if listing_type == "room" else None,
                bed_label=("Studio" if (beds == 0) else "1-Bed") if listing_type == "studio" else "",
                notes=base.clean(text)[:180],
            )
        )
    return listings
