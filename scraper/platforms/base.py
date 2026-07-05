"""Shared helpers for platform parsers: navigation, JSON extraction, parsing."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterator

# London postcode district, e.g. "E8", "SE11", "SW1A".
_POSTCODE_RE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?)\b")
_ACCEPT_COOKIE_SELECTORS = [
    "button:has-text('Accept all')",
    "button:has-text('Accept All')",
    "button:has-text('Accept')",
    "button:has-text('I Accept')",
    "button:has-text('Agree')",
    "#onetrust-accept-btn-handler",
    "[data-testid='cookie-accept-all']",
]


def goto(page, url: str, wait_ms: int = 2000) -> None:
    """Navigate, dismiss a cookie banner if present, and settle."""
    page.goto(url, wait_until="domcontentloaded")
    for sel in _ACCEPT_COOKIE_SELECTORS:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click(timeout=2000)
                break
        except Exception:
            continue
    try:
        page.wait_for_load_state("networkidle", timeout=wait_ms + 6000)
    except Exception:
        page.wait_for_timeout(wait_ms)


def dump_html(page, debug_dir: str | Path | None, name: str) -> None:
    """Save the current page HTML for offline selector debugging."""
    if not debug_dir:
        return
    d = Path(debug_dir)
    d.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:80]
    (d / f"{safe}.html").write_text(page.content(), encoding="utf-8")


def next_data(page) -> dict | None:
    """Return the parsed ``__NEXT_DATA__`` JSON blob, if present."""
    el = page.query_selector("#__NEXT_DATA__")
    if not el:
        return None
    try:
        return json.loads(el.inner_text())
    except Exception:
        return None


def json_ld_blocks(page) -> list[Any]:
    """Return all parsed ``application/ld+json`` blocks on the page."""
    blocks = []
    for el in page.query_selector_all("script[type='application/ld+json']"):
        try:
            blocks.append(json.loads(el.inner_text()))
        except Exception:
            continue
    return blocks


def js_var(page, var_name: str) -> Any | None:
    """Read a page JS variable (e.g. Rightmove's ``window.jsonModel``)."""
    try:
        return page.evaluate(f"() => window.{var_name} ?? null")
    except Exception:
        return None


def walk(obj: Any) -> Iterator[Any]:
    """Yield every nested dict/list node in a JSON structure."""
    yield obj
    if isinstance(obj, dict):
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)


def find_list_of_dicts(root: Any, must_have_keys: set[str]) -> list[dict]:
    """Find the largest list whose dict items all contain the given keys."""
    best: list[dict] = []
    for node in walk(root):
        if isinstance(node, list) and node and all(
            isinstance(x, dict) and must_have_keys <= set(x.keys()) for x in node
        ):
            if len(node) > len(best):
                best = node
    return best


# ---- field parsers -------------------------------------------------------

def parse_price_pcm(text: str | None) -> int | None:
    """Parse a price into £/month. Converts weekly rents to monthly."""
    if not text:
        return None
    text = str(text)
    m = re.search(r"£?\s*([\d,]+)", text)
    if not m:
        return None
    amount = int(m.group(1).replace(",", ""))
    if re.search(r"p\.?w|per week|/week|pw\b", text, re.I):
        amount = round(amount * 52 / 12)
    return amount


def parse_beds(text: str | None) -> int | None:
    """Parse a bedroom count from free text. Studio -> 0."""
    if not text:
        return None
    t = str(text).lower()
    if "studio" in t:
        return 0
    m = re.search(r"(\d+)\s*(?:bed|bedroom)", t)
    return int(m.group(1)) if m else None


def extract_postcode(text: str | None) -> str:
    if not text:
        return ""
    m = _POSTCODE_RE.search(str(text).upper())
    return m.group(1) if m else ""


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()
