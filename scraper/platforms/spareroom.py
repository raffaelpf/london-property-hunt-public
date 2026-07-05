"""SpareRoom search parser (DOM-based — no structured JSON exposed)."""

from __future__ import annotations

from ..models import Listing
from . import base

# Collect each result card's link + text in one JS call (robust to layout tweaks).
_EXTRACT_JS = """
() => {
  const out = [];
  const seen = new Set();
  document.querySelectorAll("a[href*='flatshare_detail']").forEach(a => {
    const href = a.href.split('#')[0].split('?')[0] + (a.href.includes('flatshare_id=')
      ? '?' + a.href.split('?')[1].split('&').find(p => p.startsWith('flatshare_id=')) : '');
    if (seen.has(href)) return; seen.add(href);
    const card = a.closest('article, li') || a.parentElement;
    out.push({
      href,
      title: (a.innerText || card?.querySelector('h2,h3')?.innerText || '').trim(),
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
        base.dump_html(page, debug_dir, f"spareroom-{listing_type}")
        raw = page.evaluate(_EXTRACT_JS) or []
    finally:
        page.close()

    listings: list[Listing] = []
    for item in raw:
        href, title, text = item.get("href"), base.clean(item.get("title")), item.get("text", "")
        if not href:
            continue
        listings.append(
            Listing(
                title=title or "SpareRoom listing",
                platform="SpareRoom",
                url=href,
                listing_type=listing_type,
                area="",  # filled from search URL by the orchestrator
                postcode=base.extract_postcode(text),
                price_pcm=base.parse_price_pcm(text),
                furnished="Yes" if "furnished" in text.lower() else "Unknown",
                bills_included="Yes" if "bills inc" in text.lower() else "Unknown",
                bed_count=base.parse_beds(text) if listing_type == "room" else None,
                bed_label="Studio" if listing_type == "studio" else "",
                notes=base.clean(text)[:180],
            )
        )
    return listings
