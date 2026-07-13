#!/usr/bin/env python3
"""Tests for outdoor-space classification (regex fallback + LLM path).

Dependency-free — no pytest, no network, no API key. Run directly:

    python3 tests/test_outdoor_classification.py

Covers the Covent-Garden regression (a place name must not read as a garden),
the graceful LLM fallback when no key is set, and the LLM parsing/cache logic
with a stubbed client.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraper import classify
from scraper.features import _outdoor, analyze_text  # noqa: E402
from scraper.models import Listing  # noqa: E402
from scraper.tracker import is_known, known_identities, update_tracker  # noqa: E402

_checks = 0


def check(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        raise AssertionError(msg)


def test_place_names_are_not_gardens():
    # The reported bug: a Covent Garden flat with no outdoor space.
    covent = ("stunning flat in the heart of seven dials, moments from covent "
              "garden piazza. unfurnished. transport links: covent garden, holborn.")
    check(_outdoor(covent) == "none", "Covent Garden flat should be 'none'")

    for place in ("hatton garden", "welwyn garden city", "kensington gardens",
                  "spring gardens", "st james's gardens"):
        text = f"modern flat near {place}, close to shops and transport."
        check(_outdoor(text) == "none", f"place name '{place}' should not read as a garden")


def test_real_outdoor_still_detected():
    check(_outdoor("flat with a private garden") == "private", "private garden")
    check(_outdoor("access to a communal garden") == "communal", "communal garden")
    check(_outdoor("flat with a private balcony") == "private", "private balcony")
    check(_outdoor("only a juliet balcony here") == "juliet", "juliet balcony")
    # Place name AND a real feature: the real feature wins.
    check(_outdoor("flat in covent garden with a private balcony") == "private",
          "real balcony in a garden-named area")
    check(analyze_text("covent garden flat, unfurnished")["outdoor"] == "none",
          "analyze_text end-to-end on Covent Garden")


def test_llm_fallback_without_key():
    # No key + no injected client -> classify returns None (caller keeps regex).
    classify._client_singleton = "unset"
    classify._logged_state = True  # silence the one-time log
    classify._cache.clear()
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        os.environ.pop(var, None)
    check(classify.classify_outdoor("some listing text") is None,
          "no key -> classify_outdoor returns None")


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, category, stop_reason="end_turn"):
        self.stop_reason = stop_reason
        self.content = [_Block(json.dumps({"category": category, "evidence": "x"}))]


def _stub_client(resp):
    calls = {"n": 0}

    class _Messages:
        def create(self, **kw):
            calls["n"] += 1
            return resp

    class _Client:
        messages = _Messages()

    return _Client(), calls


def test_llm_parses_and_caches():
    classify._cache.clear()
    classify._logged_state = True
    client, calls = _stub_client(_Resp("none"))
    classify._client_singleton = client
    check(classify.classify_outdoor("covent garden, no outdoor") == "none", "parses category")
    # Second identical call is served from cache (no extra API call).
    classify.classify_outdoor("covent garden, no outdoor")
    check(calls["n"] == 1, "identical text is cached (one API call)")


def test_llm_rejects_bad_category_and_refusal():
    classify._cache.clear()
    classify._logged_state = True
    client, _ = _stub_client(_Resp("patio"))  # not a valid category
    classify._client_singleton = client
    check(classify.classify_outdoor("weird text one") is None, "invalid category -> None")

    client, _ = _stub_client(_Resp("none", stop_reason="refusal"))
    classify._client_singleton = client
    check(classify.classify_outdoor("weird text two") is None, "refusal -> None")


def test_only_new_flats_are_reclassified():
    """A flat already in the tracker is 'known' (by URL or price/beds/postcode),
    so the pipeline skips re-enriching / re-classifying it — an existing flat
    can't suddenly grow a terrace."""
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


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)} tests, {_checks} checks — all passed")


if __name__ == "__main__":
    main()
