#!/usr/bin/env python3
"""Tests for listing classification (source attributes -> Claude, via the Claude
Code CLI) + the tracker "only classify new flats" logic.

Dependency-free — no pytest, no network, no `claude` CLI. Run directly:

    python3 tests/test_outdoor_classification.py

The classifier shells out to `claude -p`; these tests stub `classify._complete`
(the CLI call) so no CLI/network is needed, and cover JSON extraction, single +
batch classification, the no-backend path, and the new/dropped-flat skip logic.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraper import classify  # noqa: E402
from scraper.features import furnishing_from_label  # noqa: E402
from scraper.models import Listing  # noqa: E402
from scraper.tracker import is_known, known_identities, update_tracker  # noqa: E402

_checks = 0


def check(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        raise AssertionError(msg)


def _stub_complete(fn):
    """Force the backend on and replace the CLI call with `fn(system, user)`."""
    classify._available = True
    classify._logged = True
    classify._complete = fn


def test_furnishing_from_label():
    check(furnishing_from_label("Furnished") == "furnished", "Furnished")
    check(furnishing_from_label("Unfurnished") == "unfurnished", "Unfurnished")
    check(furnishing_from_label("Part furnished") == "part-furnished", "Part furnished")
    check(furnishing_from_label("Furnished or unfurnished") == "flexible", "flexible")
    check(furnishing_from_label("") == "unknown", "empty -> unknown")
    check(furnishing_from_label("Deposit: £3875") == "unknown", "unrelated -> unknown")


def test_extract_json():
    check(classify._extract_json('{"a":1}') == {"a": 1}, "bare object")
    check(classify._extract_json('```json\n{"a":1}\n```') == {"a": 1}, "fenced object")
    check(classify._extract_json('Here you go: [{"i":1}] done') == [{"i": 1}], "array amid prose")
    check(classify._extract_json("nope") is None, "no json -> None")


def test_classify_listing_single():
    _stub_complete(lambda s, u: '{"outdoor":"none","furnishing":"unfurnished","size_sqft":958}')
    out = classify.classify_listing(["1 bedroom"], "Moments from Covent Garden. UNFURNISHED.")
    check(out == {"outdoor": "none", "furnishing": "unfurnished", "size_sqft": 958}, "parsed verdict")
    # source attributes come before the description in the prompt
    seen = {}
    _stub_complete(lambda s, u: seen.setdefault("u", u) and None or '{"outdoor":"none","furnishing":"unknown","size_sqft":null}')
    classify.classify_listing(["Balcony"], "desc text")
    check(seen["u"].index("Attributes") < seen["u"].index("Description"), "attributes before description")


def test_classify_rejects_bad():
    _stub_complete(lambda s, u: '{"outdoor":"patio","furnishing":"furnished","size_sqft":null}')
    check(classify.classify_listing([], "x") is None, "invalid outdoor -> None")
    _stub_complete(lambda s, u: '{"outdoor":"none","furnishing":"sofa","size_sqft":null}')
    check(classify.classify_listing([], "y") is None, "invalid furnishing -> None")
    _stub_complete(lambda s, u: '{"outdoor":"private","furnishing":"furnished","size_sqft":40}')
    check(classify.classify_listing([], "z")["size_sqft"] is None, "implausible size -> None")


def test_classify_batch_applies_and_preserves_structured():
    payload = [
        {"i": 1, "outdoor": "private", "furnishing": "flexible", "size_sqft": 800},
        {"i": 2, "outdoor": "none", "furnishing": "unknown", "size_sqft": 500},
    ]
    _stub_complete(lambda s, u: json.dumps(payload))
    a = Listing(title="A", platform="OnTheMarket", url="u1", attributes=["Balcony"])
    b = Listing(title="B", platform="OnTheMarket", url="u2", description="no outdoor",
                size_sqft=650)  # structured size already set
    n = classify.classify_batch([a, b])
    check(n == 2, "both classified")
    check(a.outdoor == "private" and a.furnishing == "flexible", "verdict applied to A")
    check(a.size_sqft == 800, "size filled from Claude when empty")
    check(b.outdoor == "none", "verdict applied to B")
    check(b.size_sqft == 650, "structured size NOT overwritten by Claude")


def test_classify_batch_missing_verdict_keeps_defaults():
    _stub_complete(lambda s, u: '[{"i":1,"outdoor":"private","furnishing":"furnished","size_sqft":null}]')
    a = Listing(title="A", platform="OnTheMarket", url="u1", attributes=["Balcony"])
    b = Listing(title="B", platform="OnTheMarket", url="u2", attributes=["x"], furnishing="unfurnished")
    classify.classify_batch([a, b])  # only 1 verdict returned for 2 listings
    check(a.outdoor == "private", "A classified")
    check(b.outdoor == "none" and b.furnishing == "unfurnished", "B keeps its defaults when not returned")


def test_no_backend_leaves_listing_untouched():
    # No backend -> llm_active() is False -> classify_batch short-circuits before
    # ever calling _complete, so no need to restore the stub from earlier tests.
    classify._available = False
    classify._logged = True
    l = Listing(title="A", platform="OpenRent", url="u", attributes=["Balcony"], furnishing="unfurnished")
    n = classify.classify_batch([l])
    check(n == 0, "nothing classified without a backend")
    check(l.outdoor == "none", "outdoor stays default 'none' (no regex fallback)")
    check(l.furnishing == "unfurnished", "structured furnishing baseline preserved")
    check(classify.llm_active() is False, "llm_active False without the CLI")


def test_only_new_flats_are_reclassified():
    import tempfile

    path = os.path.join(tempfile.mkdtemp(), "known.xlsx")
    tracked = Listing(title="A", platform="OnTheMarket", url="https://x/1",
                      postcode="WC2H", price_pcm=3800, bed_count=2, bed_label="2-Bed",
                      outdoor="private", priority="High")
    update_tracker(path, [tracked])
    seen_urls, seen_keys = known_identities(path)

    same_url = Listing(title="A", platform="OnTheMarket", url="https://x/1",
                       postcode="WC2H", price_pcm=3800, bed_count=2, bed_label="2-Bed")
    relisted = Listing(title="A relisted", platform="OpenRent", url="https://x/1-NEW",
                       postcode="WC2H", price_pcm=3800, bed_count=2, bed_label="2-Bed")
    genuinely_new = Listing(title="C", platform="OnTheMarket", url="https://x/9",
                            postcode="EC1", price_pcm=4000, bed_count=1, bed_label="1-Bed")

    check(is_known(same_url, seen_urls, seen_keys), "same URL is known")
    check(is_known(relisted, seen_urls, seen_keys), "re-listed same flat (new URL) is known via price/beds/postcode")
    check(not is_known(genuinely_new, seen_urls, seen_keys), "a genuinely new flat is not known")


def test_dropped_flats_are_remembered():
    import tempfile

    path = os.path.join(tempfile.mkdtemp(), "seen.xlsx")
    kept = Listing(title="Keep", platform="OnTheMarket", url="https://y/1",
                   postcode="WC2H", price_pcm=3800, bed_count=2, bed_label="2-Bed",
                   outdoor="private", priority="High")
    dropped = Listing(title="Drop", platform="OnTheMarket", url="https://y/2",
                      postcode="EC1", price_pcm=3900, bed_count=2, bed_label="2-Bed",
                      outdoor="none")
    update_tracker(path, [kept], classified=[kept, dropped])

    seen_urls, seen_keys = known_identities(path)
    check(is_known(kept, seen_urls, seen_keys), "kept flat is known")
    check(is_known(dropped, seen_urls, seen_keys),
          "dropped no-outdoor flat is remembered via the Seen ledger")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)} tests, {_checks} checks — all passed")


if __name__ == "__main__":
    main()
