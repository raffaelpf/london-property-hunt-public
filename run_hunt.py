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
from scraper.tracker import is_known, known_identities, update_tracker

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

    # Only enrich/classify genuinely-new flats. An existing flat's outdoor space,
    # furnishing and size don't change, and it's already recorded (dedup'd out of
    # the tracker below), so re-fetching its detail page and re-asking the LLM is
    # wasted work and cost. Known flats stay in the tracker untouched.
    seen_urls, seen_keys = known_identities(tracker_path)
    fresh = [c for c in candidates if not is_known(c, seen_urls, seen_keys)]
    skipped_known = len(candidates) - len(fresh)
    if skipped_known:
        print(f"  {len(fresh)} new to enrich/classify; {skipped_known} already tracked (skipped)")

    enriched = 0
    for l in fresh:
        module = REGISTRY.get(l.platform)
        if module and hasattr(module, "enrich") and enriched < MAX_ENRICH:
            try:
                module.enrich(l, debug_dir)
                enriched += 1
            except Exception:
                pass
    if len([c for c in fresh if hasattr(REGISTRY.get(c.platform), "enrich")]) > MAX_ENRICH:
        print(f"  ⚠️ enrich capped at {MAX_ENRICH}; some listings not detail-checked", file=sys.stderr)

    kept: list[Listing] = []
    for l in fresh:
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
        "fresh": len(fresh), "skipped_known": skipped_known,
        "enriched": enriched, "priorities": priorities, "tracker": counts,
        "outreach_written": len(outreach_files), "errors": errors, "kept": kept,
    }


def _parse_found(s) -> "datetime | None":
    from datetime import datetime
    s = str(s or "").strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _row_line(r: dict) -> str:
    price = f"£{r['Price (pcm)']:,}/mo" if isinstance(r.get("Price (pcm)"), int) else "£?"
    size = f"{r['Size (sqft)']} sqft" if r.get("Size (sqft)") else "size n/a"
    furn = r.get("Furnishing") or "?"
    outdoor = r.get("Balcony/Terrace") or "?"
    return (f"- **{price}** · {r.get('Bedrooms') or '?'} · {size} · {outdoor} · {furn}\n"
            f"  {r.get('Area') or ''} — [{r.get('Platform')}]({r.get('URL')})")


def _by_priority(rows: list, pr: str) -> list:
    return [r for r in rows if r.get("Priority") == pr]


def print_summary(res: dict, tracker_path: str, recent_hours: int = 24) -> None:
    """Emit a Markdown summary: what's new this run + a rolling recent window.

    Only genuinely new listings are highlighted; the recent window (default 24h)
    re-surfaces flats from earlier runs so an unread notification isn't missed.
    Relayed verbatim by the scheduled routine.
    """
    from datetime import datetime, timedelta

    t = res["tracker"]
    rows = t.get("rows") or []
    new_urls = set(t.get("new_urls") or [])
    total = len(rows)

    cutoff = datetime.now() - timedelta(hours=recent_hours)
    new_rows = [r for r in rows if str(r.get("URL", "")).strip() in new_urls]
    recent_rows = [
        r for r in rows
        if str(r.get("URL", "")).strip() not in new_urls
        and (_parse_found(r.get("Found On")) or datetime.min) >= cutoff
    ]

    out = ["# 🏠 London flat hunt", ""]
    if not new_rows and not recent_rows:
        out += [f"_No new or recent flats (tracker holds {total})._",
                "", f"📥 **[Download the full tracker (Excel)]({TRACKER_DOWNLOAD})**"]
        print("\n".join(out))
        return

    n_hi = len(_by_priority(new_rows, "High"))
    n_md = len(_by_priority(new_rows, "Medium"))
    n_lo = len(_by_priority(new_rows, "Low"))
    out.append(
        f"**{len(new_rows)} new this run** (🟢 {n_hi} · 🟡 {n_md} · ⚪ {n_lo}) · "
        f"{len(recent_rows)} recent (last {recent_hours}h) · tracker holds {total}."
    )

    if new_rows:
        out += ["", "## 🆕 New this run"]
        for pr, label in (("High", "🟢 HIGH"), ("Medium", "🟡 MEDIUM"), ("Low", "⚪ LOW")):
            group = _by_priority(new_rows, pr)
            if group:
                out += [f"### {label} ({len(group)})"] + [_row_line(r) for r in group]
    else:
        out += ["", "_Nothing new this run._"]

    if recent_rows:
        rec_hi = _by_priority(recent_rows, "High")
        rec_md = _by_priority(recent_rows, "Medium")
        rec_lo = _by_priority(recent_rows, "Low")
        out += ["", f"## 🕒 Still open — found in the last {recent_hours}h"]
        for label, group in (("🟢 HIGH", rec_hi), ("🟡 MEDIUM", rec_md)):
            if group:
                out += [f"### {label} ({len(group)})"] + [_row_line(r) for r in group]
        if rec_lo:
            out += [f"_+ {len(rec_lo)} recent LOW-priority — see the tracker._"]

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
    print_summary(res, tracker_path, get_int(cfg, "RECENT_HOURS", 24) or 24)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
