#!/usr/bin/env python3
"""One-off: re-classify the existing tracker with the current classifier.

The old regex classifier read place names as gardens ("Covent Garden" -> a
communal garden), so the tracker accumulated flats with no real outdoor space
marked "Communal / shared". This re-fetches each tracked flat, re-classifies its
outdoor space + furnishing with Claude (scraper.classify), and:

  - drops rows whose outdoor space is now none/juliet (they fail the hard gate),
  - updates outdoor / furnishing / size / priority on the rest,
  - records every re-examined flat in the hidden Seen ledger so the daily run
    won't re-classify them.

Dry-run by default (prints the plan); pass --apply to write the tracker.

    python3 scripts/reclassify_tracker.py --tracker tracker/london_flat_hunt.xlsx
    python3 scripts/reclassify_tracker.py --tracker tracker/london_flat_hunt.xlsx --apply
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraper import classify, tracker
from scraper.config import get_int, load_config  # noqa: E402
from scraper.features import OUTDOOR_LABELS  # noqa: E402
from scraper.models import Listing  # noqa: E402
from scraper.platforms import REGISTRY  # noqa: E402
from scraper.prioritise import prioritise  # noqa: E402

_BEDS = {"studio": 0, "1-bed": 1, "2-bed": 2, "3-bed": 3}


def _bed_count(label) -> int | None:
    s = str(label or "").strip().lower()
    if s in _BEDS:
        return _BEDS[s]
    return int(s) if s.isdigit() else None


def _int_or_none(v):
    return int(v) if isinstance(v, (int, float)) else None


def _listing_from_row(d: dict) -> Listing:
    return Listing(
        title=str(d.get("Title") or ""),
        platform=str(d.get("Platform") or ""),
        url=str(d.get("URL") or ""),
        area=str(d.get("Area") or ""),
        postcode=str(d.get("Postcode") or ""),
        price_pcm=_int_or_none(d.get("Price (pcm)")),
        bed_label=str(d.get("Bedrooms") or ""),
        bed_count=_bed_count(d.get("Bedrooms")),
        size_sqft=_int_or_none(d.get("Size (sqft)")),
        available_from=str(d.get("Available From") or ""),
        notes=str(d.get("Notes") or ""),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Re-classify the existing tracker with Claude.")
    ap.add_argument("--tracker", required=True)
    ap.add_argument("--config", default=None)
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    args = ap.parse_args()

    repo = Path(__file__).resolve().parent.parent
    config_path = args.config or ("config.md" if (repo / "config.md").exists() else "config.example.md")
    cfg = load_config(config_path)

    if not classify.llm_active():
        print("Claude classifier unavailable — aborting (nothing changed).", file=sys.stderr)
        return 1

    wb = tracker.load_or_create(args.tracker)
    ws = wb[tracker.FLATS_SHEET]
    rows = tracker._read_rows(ws)
    print(f"Tracker: {args.tracker} — {len(rows)} rows")

    listings = [_listing_from_row(d) for d in rows]
    for d, l in zip(rows, listings):
        l._row = d  # type: ignore[attr-defined]
        module = REGISTRY.get(l.platform)
        if module and hasattr(module, "enrich"):
            try:
                module.enrich(l)
            except Exception as exc:  # network / parse hiccup — keep the row as-is
                l._err = str(exc)[:60]  # type: ignore[attr-defined]

    classify.classify_batch(listings)

    kept_rows, dropped, changed = [], [], []
    for l in listings:
        d = l._row  # type: ignore[attr-defined]
        old_out = str(d.get("Balcony/Terrace") or "")
        if getattr(l, "_err", None):
            kept_rows.append(d)  # couldn't re-check — leave untouched
            continue
        new_priority = prioritise(l, cfg)
        new_out = OUTDOOR_LABELS.get(l.outdoor, "Not stated")
        if new_priority is None:
            dropped.append((d, old_out, new_out))
            continue
        if new_out != old_out:
            changed.append((d, old_out, new_out))
        d["Balcony/Terrace"] = new_out
        d["Furnishing"] = l.furnishing.replace("-", " ").title() if l.furnishing not in ("", "unknown") else "Unknown"
        if l.size_sqft:
            d["Size (sqft)"] = l.size_sqft
        d["Priority"] = new_priority
        d["Notes"] = l.notes
        kept_rows.append(d)

    print(f"\n  keep:    {len(kept_rows)}")
    print(f"  drop:    {len(dropped)}   (outdoor now none/juliet — no real outdoor space)")
    print(f"  changed: {len(changed)}   (outdoor label changed but still kept)")
    if dropped:
        print("\n  Dropping:")
        for d, old, new in dropped:
            print(f"    - {old:24} -> {new:11} | {str(d.get('Area'))[:30]} | {d.get('URL')}")
    if changed:
        print("\n  Changed (kept):")
        for d, old, new in changed:
            print(f"    - {old:24} -> {new:11} | {str(d.get('Area'))[:30]} | {d.get('URL')}")

    if not args.apply:
        print("\nDry run — pass --apply to write these changes.")
        return 0

    # Record every re-examined flat (kept AND dropped) so the daily run skips them.
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    tracker._record_seen(wb, listings, stamp)

    kept_rows.sort(
        key=lambda d: (str(d.get("Found On") or ""), -tracker._PRANK.get(d.get("Priority"), 3)),
        reverse=True,
    )
    tracker._write_rows(ws, kept_rows)
    Path(args.tracker).parent.mkdir(parents=True, exist_ok=True)
    wb.save(args.tracker)
    print(f"\nApplied. Tracker now holds {len(kept_rows)} rows "
          f"({len(dropped)} dropped, {len(listings)} recorded in the Seen ledger).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
