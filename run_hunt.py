#!/usr/bin/env python3
"""London property hunt — cloud entrypoint.

Scrapes the configured platforms, deduplicates against the Excel tracker,
assigns priorities, writes outreach files for HIGH listings, and prints a
summary. Does NOT send email (that step is deferred).

Usage:
    python run_hunt.py                         # all platforms, config.md
    python run_hunt.py --platforms SpareRoom,OpenRent --debug-dir /tmp/dbg
    python run_hunt.py --tracker /path/london_room_hunt.xlsx
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
from scraper.prioritise import prioritise
from scraper.tracker import update_tracker


def _spareroom_area(url: str) -> str:
    """Infer the area name from a SpareRoom search URL slug."""
    try:
        slug = url.split("/london/", 1)[1].split("?", 1)[0].strip("/")
        return slug.replace("_", " ").title()
    except Exception:
        return ""


def build_jobs(cfg: dict, platforms: set[str]) -> list[dict]:
    """Return the list of {platform, url, listing_type, area} search jobs."""
    jobs: list[dict] = []
    areas = cfg.get("PRIMARY_AREAS", []) + cfg.get("SECONDARY_AREAS", [])
    term = quote(",".join(areas)) if areas else "london"
    room_budget = get_int(cfg, "ROOM_BUDGET", 1500)
    studio_budget = get_int(cfg, "STUDIO_BUDGET", 1900)

    if "SpareRoom" in platforms:
        for u in cfg.get("SPAREROOM_ROOM_URLS", []):
            jobs.append({"platform": "SpareRoom", "url": u, "type": "room", "area": _spareroom_area(u)})
        for u in cfg.get("SPAREROOM_STUDIO_URLS", []):
            jobs.append({"platform": "SpareRoom", "url": u, "type": "studio", "area": _spareroom_area(u)})

    if "OpenRent" in platforms:
        jobs.append({"platform": "OpenRent", "type": "room", "area": "",
                     "url": f"https://www.openrent.co.uk/properties-to-rent/london?term={term}"
                            f"&prices_max={room_budget}&isLive=true&furnishedStatus=1&bedrooms_max=0"})
        jobs.append({"platform": "OpenRent", "type": "studio", "area": "",
                     "url": f"https://www.openrent.co.uk/properties-to-rent/london?term={term}"
                            f"&prices_max={studio_budget}&isLive=true&furnishedStatus=1&bedrooms_max=1"})

    if "Rightmove" in platforms:
        jobs.append({"platform": "Rightmove", "type": "studio", "area": "",
                     "url": "https://www.rightmove.co.uk/property-to-rent/find.html?searchType=RENT"
                            "&locationIdentifier=REGION%5E87490&maxBedrooms=1"
                            f"&maxPrice={studio_budget}&propertyTypes=flat"
                            "&letFurnishType=furnished&includeLetAgreed=false"})

    if "Zoopla" in platforms:
        jobs.append({"platform": "Zoopla", "type": "studio", "area": "",
                     "url": f"https://www.zoopla.co.uk/to-rent/flats/london/?beds_max=1"
                            f"&price_frequency=per_month&price_max={studio_budget}"
                            "&furnished_state=furnished&results_sort=newest_listings&pn=1"})
    return jobs


def run(cfg: dict, tracker_path: str, outreach_dir: str, platforms: set[str],
        debug_dir: str | None, limit: int | None) -> dict:
    jobs = build_jobs(cfg, platforms)
    all_listings: list[Listing] = []
    per_platform: dict[str, int] = {}
    errors: list[str] = []

    for job in jobs:
        module = REGISTRY[job["platform"]]
        try:
            found = module.search(job["url"], cfg, job["type"], debug_dir)
            if limit:
                found = found[:limit]
            for listing in found:
                if not listing.area and job["area"]:
                    listing.area = job["area"]
            per_platform[job["platform"]] = per_platform.get(job["platform"], 0) + len(found)
            all_listings.extend(found)
            print(f"  [{job['platform']}/{job['type']}] {len(found)} listings  ({job['url'][:70]}...)")
        except Exception as exc:  # isolate: one platform failing must not kill the run
            errors.append(f"{job['platform']}/{job['type']}: {exc}")
            print(f"  [{job['platform']}/{job['type']}] ERROR: {exc}", file=sys.stderr)
            if debug_dir:
                traceback.print_exc()

    # Prioritise + apply hard filters; dedupe within this run by URL.
    kept: list[Listing] = []
    seen_urls: set[str] = set()
    skipped = 0
    for listing in all_listings:
        url = listing.url.strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        priority = prioritise(listing, cfg)
        if priority is None:
            skipped += 1
            continue
        listing.priority = priority
        kept.append(listing)

    counts = update_tracker(tracker_path, kept)
    outreach_files = write_outreach_files(kept, cfg, outreach_dir)

    priorities = {"High": 0, "Medium": 0, "Low": 0}
    for listing in kept:
        priorities[listing.priority] = priorities.get(listing.priority, 0) + 1

    return {
        "per_platform": per_platform,
        "found_total": len(all_listings),
        "skipped_4bed": skipped,
        "priorities": priorities,
        "tracker": counts,
        "outreach_written": len(outreach_files),
        "errors": errors,
        "high_listings": [l for l in kept if l.priority == "High"],
    }


def print_summary(cfg: dict, res: dict, tracker_path: str) -> None:
    p = res["priorities"]
    t = res["tracker"]
    print("\n" + "=" * 60)
    print("🏠 LONDON PROPERTY HUNT — RUN SUMMARY")
    print("=" * 60)
    print(f"Platforms:      " + ", ".join(f"{k}={v}" for k, v in res["per_platform"].items()) or "none")
    print(f"Found (raw):    {res['found_total']}")
    print(f"Skipped (4+ bed rooms): {res['skipped_4bed']}")
    print(f"Priority:       🟢 HIGH {p['High']} | 🟡 MEDIUM {p['Medium']} | ⚪ LOW {p['Low']}")
    print(f"Tracker:        +{t['rooms_added']} rooms, +{t['studios_added']} studios, {t['duplicates']} dupes skipped")
    print(f"Outreach files: {res['outreach_written']} written")
    print(f"Tracker file:   {tracker_path}")
    if res["errors"]:
        print(f"\n⚠️  {len(res['errors'])} platform error(s):")
        for e in res["errors"]:
            print(f"   - {e}")
    if res["high_listings"]:
        print("\n🟢 Top HIGH listings:")
        for l in res["high_listings"][:8]:
            price = f"£{l.price_pcm}" if l.price_pcm else "£?"
            print(f"   • [{l.platform}] {l.title[:50]} | {l.area or '?'} | {price} | {l.url}")
    print("=" * 60)


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the London property hunt.")
    ap.add_argument("--config", default=None, help="Path to config.md (falls back to config.example.md)")
    ap.add_argument("--tracker", default=None, help="Path to london_room_hunt.xlsx")
    ap.add_argument("--outreach-dir", default=None, help="Directory for outreach .txt files")
    ap.add_argument("--platforms", default="SpareRoom,OpenRent,Rightmove,Zoopla",
                    help="Comma-separated subset of platforms to run")
    ap.add_argument("--debug-dir", default=None, help="Dump fetched HTML here for selector debugging")
    ap.add_argument("--limit", type=int, default=None, help="Cap listings per search (testing)")
    args = ap.parse_args()

    repo = Path(__file__).parent
    config_path = args.config or ("config.md" if (repo / "config.md").exists() else "config.example.md")
    cfg = load_config(config_path)
    print(f"Config: {config_path}")

    hunt_dir = cfg.get("YOUR_HUNT_DIR", "").strip()
    default_tracker = (
        Path(hunt_dir).expanduser() / "london_room_hunt.xlsx" if hunt_dir
        else repo / "tracker" / "london_room_hunt.xlsx"
    )
    tracker_path = str(args.tracker or default_tracker)
    outreach_dir = str(args.outreach_dir or (repo / "outreach"))
    platforms = {p.strip() for p in args.platforms.split(",") if p.strip()}

    res = run(cfg, tracker_path, outreach_dir, platforms, args.debug_dir, args.limit)
    print_summary(cfg, res, tracker_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
