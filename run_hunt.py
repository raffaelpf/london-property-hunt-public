#!/usr/bin/env python3
"""London flat hunt — cloud entrypoint (Revision 2).

Searches Rightmove + OnTheMarket + OpenRent for 1–2 bed flats in the configured
central areas and budget, enriches candidates from detail pages to detect
balcony/terrace + furnishing + size, deduplicates against the Excel tracker,
prioritises, and prints a summary. Does NOT send email.

Usage:
    python run_hunt.py                          # all platforms, config.md
    python run_hunt.py --platforms Rightmove --debug-dir debug --limit 20
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path
from urllib.parse import quote

from scraper.config import get_int, load_config
from scraper.models import Listing
from scraper.outreach import write_outreach_files
from scraper.platforms import REGISTRY
from scraper.prioritise import _area_tier, prioritise
from scraper.tracker import update_tracker

MAX_ENRICH = 120  # cap detail-page fetches per run

# Same flat is often listed on several portals — prefer Rightmove as the master.
_PLATFORM_RANK = {"Rightmove": 0, "OnTheMarket": 1, "OpenRent": 2}
# Direct download of the committed tracker (lives on the daily-tracker branch).
TRACKER_DOWNLOAD = (
    "https://github.com/raffaelpf/london-property-hunt-public/raw/"
    "claude/daily-tracker/tracker/london_flat_hunt.xlsx"
)


def _dupe_key(l) -> tuple:
    return (l.price_pcm, l.bed_count, (l.postcode or l.area[:14]).lower())


def dedup_master(listings: list) -> list:
    """Drop a listing when the same flat exists on a higher-priority platform.

    Rightmove < OnTheMarket < OpenRent. Same-platform listings that share a key
    are all kept (they're likely distinct flats, not cross-postings)."""
    best: dict = {}
    for l in listings:
        k = _dupe_key(l)
        rank = _PLATFORM_RANK.get(l.platform, 9)
        if k not in best or rank < best[k]:
            best[k] = rank
    return [l for l in listings if _PLATFORM_RANK.get(l.platform, 9) == best[_dupe_key(l)]]


def _slug(area: str) -> str:
    return quote(area.strip().lower().replace(" ", "-"))


def build_jobs(cfg: dict, platforms: set[str]) -> list[dict]:
    jobs: list[dict] = []
    areas = cfg.get("PRIMARY_AREAS", []) + cfg.get("SECONDARY_AREAS", [])
    pmin = get_int(cfg, "PRICE_MIN", 0) or 0
    pmax = get_int(cfg, "PRICE_MAX", 5000)
    bmin = get_int(cfg, "MIN_BEDROOMS", 1) or 1
    bmax = get_int(cfg, "MAX_BEDROOMS", 2) or 2
    keywords = quote(",".join(cfg.get("FEATURE_MUST", ["balcony", "terrace"])))

    if "Rightmove" in platforms:
        # London-wide + filters; results are post-filtered to target areas.
        jobs.append({"platform": "Rightmove", "type": "flat", "area": "",
                     "url": "https://www.rightmove.co.uk/property-to-rent/find.html?searchType=RENT"
                            "&locationIdentifier=REGION%5E87490"
                            f"&minBedrooms={bmin}&maxBedrooms={bmax}&minPrice={pmin}&maxPrice={pmax}"
                            "&propertyTypes=flat&furnishTypes=unfurnished%2CpartFurnished"
                            f"&keywords={keywords}&includeLetAgreed=false"})

    if "OnTheMarket" in platforms:
        for area in areas:
            jobs.append({"platform": "OnTheMarket", "type": "flat", "area": area,
                         "url": f"https://www.onthemarket.com/to-rent/property/{_slug(area)}/"
                                f"?max-price={pmax}&min-price={pmin}&min-bedrooms={bmin}&max-bedrooms={bmax}"})

    if "OpenRent" in platforms and areas:
        term = quote(",".join(areas))
        jobs.append({"platform": "OpenRent", "type": "flat", "area": "",
                     "url": f"https://www.openrent.co.uk/properties-to-rent/london?term={term}"
                            f"&prices_min={pmin}&prices_max={pmax}&bedrooms_min={bmin}&bedrooms_max={bmax}"
                            "&isLive=true"})
    return jobs


def _in_scope(listing: Listing, cfg: dict) -> bool:
    """Cheap pre-filter so we only fetch detail pages for plausible listings."""
    pmin = get_int(cfg, "PRICE_MIN", 0) or 0
    pmax = get_int(cfg, "PRICE_MAX")
    bmin = get_int(cfg, "MIN_BEDROOMS", 0) or 0
    bmax = get_int(cfg, "MAX_BEDROOMS")
    if listing.price_pcm is not None:
        if pmax and listing.price_pcm > pmax:
            return False
        if pmin and listing.price_pcm < pmin:
            return False
    if listing.bed_count is not None:
        if bmax is not None and listing.bed_count > bmax:
            return False
        if listing.bed_count < bmin:
            return False
    return _area_tier(listing, cfg) != "other"


def run(cfg, tracker_path, outreach_dir, platforms, debug_dir, limit) -> dict:
    jobs = build_jobs(cfg, platforms)
    raw: list[Listing] = []
    per_platform: dict[str, int] = {}
    errors: list[str] = []

    for job in jobs:
        module = REGISTRY[job["platform"]]
        try:
            found = module.search(job["url"], cfg, job["type"], debug_dir)
            if limit:
                found = found[:limit]
            for l in found:
                if not l.area and job["area"]:
                    l.area = job["area"]
            per_platform[job["platform"]] = per_platform.get(job["platform"], 0) + len(found)
            raw.extend(found)
            print(f"  [{job['platform']}] {len(found)} listings ({job['url'][:64]}...)")
        except Exception as exc:
            errors.append(f"{job['platform']}: {exc}")
            print(f"  [{job['platform']}] ERROR: {exc}", file=sys.stderr)
            if debug_dir:
                traceback.print_exc()

    # Dedup within run, pre-filter to in-scope, enrich survivors from detail pages.
    seen: set[str] = set()
    candidates: list[Listing] = []
    for l in raw:
        u = l.url.strip()
        if not u or u in seen:
            continue
        seen.add(u)
        if _in_scope(l, cfg):
            candidates.append(l)

    # Collapse cross-platform duplicates, keeping the master (Rightmove) copy.
    candidates = dedup_master(candidates)

    enriched = 0
    for l in candidates:
        module = REGISTRY.get(l.platform)
        if module and hasattr(module, "enrich") and enriched < MAX_ENRICH:
            try:
                module.enrich(l, debug_dir)
                enriched += 1
            except Exception:
                pass
    if len([c for c in candidates if hasattr(REGISTRY.get(c.platform), "enrich")]) > MAX_ENRICH:
        print(f"  ⚠️ enrich capped at {MAX_ENRICH}; some listings not detail-checked", file=sys.stderr)

    kept: list[Listing] = []
    for l in candidates:
        p = prioritise(l, cfg)
        if p is None:
            continue
        l.priority = p
        kept.append(l)

    counts = update_tracker(tracker_path, kept)
    outreach_files = write_outreach_files(kept, cfg, outreach_dir)
    priorities = {"High": 0, "Medium": 0, "Low": 0}
    for l in kept:
        priorities[l.priority] = priorities.get(l.priority, 0) + 1

    return {
        "per_platform": per_platform, "raw": len(raw), "candidates": len(candidates),
        "enriched": enriched, "priorities": priorities, "tracker": counts,
        "outreach_written": len(outreach_files), "errors": errors, "kept": kept,
    }


def print_summary(res: dict, tracker_path: str) -> None:
    """Emit a clean Markdown summary (relayed verbatim by the scheduled routine)."""
    from scraper.features import OUTDOOR_LABELS

    p, t = res["priorities"], res["tracker"]
    new_urls = set(t.get("new_urls") or [])

    def dupe_key(l):
        # Same flat listed on >1 platform: match on price + beds + location.
        return (l.price_pcm, l.bed_count, (l.postcode or l.area[:14]).lower())

    def collapse(items: list) -> list:
        """Merge cross-platform duplicates, keeping one entry with all links."""
        groups: dict = {}
        order: list = []
        for l in items:
            k = dupe_key(l)
            if k not in groups:
                groups[k] = [l]
                order.append(k)
            else:
                groups[k].append(l)
        return [groups[k] for k in order]

    def line(group: list) -> str:
        l = group[0]
        price = f"£{l.price_pcm:,}/mo" if l.price_pcm else "£?"
        size = f"{l.size_sqft} sqft" if l.size_sqft else "size n/a"
        furn = l.furnishing.replace("-", " ") if l.furnishing not in ("", "unknown") else "furnishing n/a"
        badge = " 🆕" if any(x.url.strip() in new_urls for x in group) else ""
        links = " · ".join(f"[{x.platform}]({x.url})" for x in group)
        return (f"- **{price}** · {l.bed_label or '?'} · {size} · {OUTDOOR_LABELS.get(l.outdoor)} · {furn}\n"
                f"  {l.area} — {links}{badge}")

    ranked = sorted(res["kept"], key=lambda l: {"High": 0, "Medium": 1, "Low": 2}[l.priority])
    highs = collapse([l for l in ranked if l.priority == "High"])
    meds = collapse([l for l in ranked if l.priority == "Medium"])

    out = [
        "# 🏠 London flat hunt",
        "",
        f"**{t['added']} new** today · {t['duplicates']} already tracked · "
        f"scanned {res['raw']} listings, {res['candidates']} in scope.",
        "",
        f"🟢 **{p['High']} HIGH**  ·  🟡 {p['Medium']} MEDIUM  ·  ⚪ {p['Low']} LOW  ·  🆕 = new today",
    ]
    if highs:
        out += ["", "## 🟢 HIGH priority"] + [line(l) for l in highs]
    if meds:
        out += ["", f"## 🟡 MEDIUM (top {min(6, len(meds))} of {len(meds)})"] + [line(l) for l in meds[:6]]
    if not highs and not meds:
        out += ["", "_No HIGH or MEDIUM matches in scope today._"]
    if res["errors"]:
        out += ["", "### ⚠️ Notes"] + [f"- {e}" for e in res["errors"]]
    out += ["", f"📥 **[Download the full tracker (Excel)]({TRACKER_DOWNLOAD})**"]
    print("\n".join(out))


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the London flat hunt.")
    ap.add_argument("--config", default=None)
    ap.add_argument("--tracker", default=None)
    ap.add_argument("--outreach-dir", default=None)
    ap.add_argument("--platforms", default="Rightmove,OnTheMarket,OpenRent")
    ap.add_argument("--debug-dir", default=None)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    repo = Path(__file__).parent
    config_path = args.config or ("config.md" if (repo / "config.md").exists() else "config.example.md")
    cfg = load_config(config_path)
    print(f"Config: {config_path}")

    tracker_path = str(args.tracker or (repo / "tracker" / "london_flat_hunt.xlsx"))
    outreach_dir = str(args.outreach_dir or (repo / "outreach"))
    platforms = {p.strip() for p in args.platforms.split(",") if p.strip()}

    res = run(cfg, tracker_path, outreach_dir, platforms, args.debug_dir, args.limit)
    print_summary(res, tracker_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
