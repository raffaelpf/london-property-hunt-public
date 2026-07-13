#!/usr/bin/env python3
"""Tests for listing classification (source attributes -> Claude) + the tracker
"only classify new flats" logic.

Dependency-free — no pytest, no network, no API key. Run directly:

    python3 tests/test_outdoor_classification.py

The classifier itself is Claude; these tests stub the Anthropic client so no
network or key is needed, and cover: the structured furnishing-label reader,
Claude parsing/caching/rejection, how a verdict is applied over structured
fallbacks, and the new/dropped-flat skip logic.
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


def _reset_classify():
    classify._client_singleton = "unset"
    classify._logged_state = True  # silence the one-time log
    classify._cache.clear()
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        os.environ.pop(var, None)


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, payload, stop_reason="end_turn"):
        self.stop_reason = stop_reason
        self.content = [_Block(json.dumps(payload))]


def _stub(payload=None, resp=None):
    """Install a stub Anthropic client returning `resp` (or one built from
    `payload`); returns a dict tracking the number of API calls."""
    calls = {"n": 0}
    the_resp = resp if resp is not None else _Resp(payload)

    class _Messages:
        def create(self, **kw):
            calls["n"] += 1
            _stub.last_kwargs = kw
            return the_resp

    class _Client:
        messages = _Messages()

    _reset_classify()
    classify._client_singleton = _Client()
    return calls


def test_furnishing_from_label():
    check(furnishing_from_label("Furnished") == "furnished", "Furnished")
    check(furnishing_from_label("Unfurnished") == "unfurnished", "Unfurnished")
    check(furnishing_from_label("Part furnished") == "part-furnished", "Part furnished")
    check(furnishing_from_label("Furnished or unfurnished") == "flexible", "flexible")
    check(furnishing_from_label("") == "unknown", "empty -> unknown")
    check(furnishing_from_label("Deposit: £3875") == "unknown", "unrelated -> unknown")


def test_classify_parses_and_caches():
    calls = _stub({"outdoor": "none", "furnishing": "unfurnished", "size_sqft": 958})
    out = classify.classify_listing(["1 bedroom"], "Moments from Covent Garden. UNFURNISHED.")
    check(out == {"outdoor": "none", "furnishing": "unfurnished", "size_sqft": 958}, "parsed verdict")
    # Source attributes are presented before the description in the prompt.
    content = _stub.last_kwargs["messages"][0]["content"]
    check(content.index("attributes") < content.index("Description"), "attributes come before description")
    classify.classify_listing(["1 bedroom"], "Moments from Covent Garden. UNFURNISHED.")
    check(calls["n"] == 1, "identical input is cached (one API call)")


def test_classify_size_normalised():
    _stub({"outdoor": "private", "furnishing": "furnished", "size_sqft": 40})  # implausible
    out = classify.classify_listing([], "tiny")
    check(out["size_sqft"] is None, "implausible size -> None")
    _stub({"outdoor": "private", "furnishing": "furnished", "size_sqft": None})
    check(classify.classify_listing([], "x")["size_sqft"] is None, "null size ok")


def test_classify_rejects_bad_and_refusal():
    _stub({"outdoor": "patio", "furnishing": "furnished", "size_sqft": None})  # bad outdoor
    check(classify.classify_listing([], "x") is None, "invalid outdoor -> None")
    _stub({"outdoor": "none", "furnishing": "sofa", "size_sqft": None})  # bad furnishing
    check(classify.classify_listing([], "y") is None, "invalid furnishing -> None")
    _stub(resp=_Resp({"outdoor": "none", "furnishing": "furnished", "size_sqft": None}, stop_reason="refusal"))
    check(classify.classify_listing([], "z") is None, "refusal -> None")


def test_apply_uses_verdict_over_structured():
    _stub({"outdoor": "private", "furnishing": "flexible", "size_sqft": 800})
    l = Listing(title="t", platform="OnTheMarket", url="u", attributes=["Balcony"])
    classify.apply_classification(l, "desc", struct_size=None, struct_furnishing="unfurnished")
    check(l.outdoor == "private", "outdoor from Claude")
    check(l.furnishing == "flexible", "furnishing from Claude (not the structured label)")
    check(l.size_sqft == 800, "size from Claude when no structured size")

    _stub({"outdoor": "private", "furnishing": "flexible", "size_sqft": 800})
    l2 = Listing(title="t", platform="OnTheMarket", url="u2")
    classify.apply_classification(l2, "desc", struct_size=650, struct_furnishing="unfurnished")
    check(l2.size_sqft == 650, "structured size wins over Claude's")


def test_apply_fallback_without_llm():
    _reset_classify()
    classify._client_singleton = None  # no LLM
    l = Listing(title="t", platform="OpenRent", url="u", attributes=["Balcony"])
    classify.apply_classification(l, "some description", struct_size=700, struct_furnishing="unfurnished")
    check(l.outdoor == "none", "no LLM -> outdoor stays default 'none' (no regex fallback)")
    check(l.furnishing == "unfurnished", "no LLM -> furnishing from structured label")
    check(l.size_sqft == 700, "no LLM -> size from structured field")
    check(classify.llm_active() is False, "llm_active False without a client")


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
