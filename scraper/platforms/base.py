"""Shared helpers for platform parsers: HTML/JSON extraction and field parsing.

Operates on HTML strings (fetched via :mod:`scraper.fetch`), not a live browser.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterator

from bs4 import BeautifulSoup

# London postcode district, e.g. "E8", "SE11", "SW1A".
_POSTCODE_RE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?)\b")
_NEXT_DATA_RE = re.compile(
    r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', re.S
)
_JSONLD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S
)


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


# ---- embedded-JSON extraction -------------------------------------------

def next_data(html: str) -> dict | None:
    """Parse the ``__NEXT_DATA__`` script blob, if present."""
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1).strip())
    except Exception:
        return None


def json_ld_blocks(html: str) -> list[Any]:
    """Return all parsed ``application/ld+json`` blocks."""
    out = []
    for m in _JSONLD_RE.finditer(html):
        try:
            out.append(json.loads(m.group(1).strip()))
        except Exception:
            continue
    return out


def js_var(html: str, name: str) -> Any | None:
    """Extract a JS assignment like ``window.jsonModel = {...}`` as JSON.

    Uses ``JSONDecoder.raw_decode`` from the opening brace so braces inside
    strings are handled correctly.
    """
    m = re.search(re.escape(name) + r"\s*[:=]\s*(\{)", html)
    if not m:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(html[m.start(1):])
        return obj
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
    if text is None:
        return None
    text = str(text)
    m = re.search(r"£?\s*([\d,]+)", text)
    if not m:
        return None
    amount = int(m.group(1).replace(",", ""))
    if re.search(r"p\.?w|per week|/week|pw\b", text, re.I):
        amount = round(amount * 52 / 12)
    return amount or None


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
