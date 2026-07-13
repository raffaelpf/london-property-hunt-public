"""LLM-based outdoor-space classification.

The regex classifier in :mod:`scraper.features` can't tell a *place* named
"…Garden" (Covent Garden, Hatton Garden, Kensington Gardens, Garden City) from
an actual garden — so a flat with no outdoor space in Covent Garden reads as a
"communal garden", survives the hard "must have outdoor space" gate, and lands
at MEDIUM. Place-name traps like this are open-ended, so instead of piling more
special cases into the regex we ask Claude to read the listing text and decide.

Graceful degradation: if the ``anthropic`` SDK isn't installed, no API key is
configured, or the call fails, :func:`classify_outdoor` returns ``None`` and the
caller keeps its regex result. That keeps the pipeline working in environments
where no key is available (the regex fallback in ``features.py`` handles the
common place-name cases on its own).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Must match features.OUTDOOR_LABELS / prioritise.py's expectations.
CATEGORIES = ("private", "communal", "juliet", "none")
DEFAULT_MODEL = "claude-opus-4-8"

_CA_BUNDLE = os.environ.get("REQUESTS_CA_BUNDLE") or "/root/.ccr/ca-bundle.crt"

_SYSTEM = """You classify the OUTDOOR SPACE of a single rental flat from its \
listing text. Reply with the private/communal/juliet/none category that best \
describes what outdoor space the property itself has.

Categories:
- "private": the flat has its own private outdoor space the occupant can use — a
  balcony, terrace, roof terrace, patio, decking, veranda, loggia, or a private
  garden. A balcony/terrace is private by default unless the text says it is
  shared or communal.
- "communal": the only outdoor space is shared/communal — a communal or
  residents' garden, a shared roof terrace/courtyard — or a garden that is
  mentioned without being clearly private.
- "juliet": the only "balcony" is a Juliet/French balcony (a railing at a door
  or window, with no standing space) and there is no other outdoor space.
- "none": the property has no outdoor space.

Critical rules:
- IGNORE place names, streets, stations and areas. "Covent Garden", "Hatton
  Garden", "Garden City", "Kensington Gardens", "Spring Gardens", a
  "…Gardens" street or a "Covent Garden" tube station are LOCATIONS, not
  outdoor space. The word "garden" inside a location name is never a garden.
- Nearby public parks or gardens ("moments from the gardens of X Square",
  "overlooking the park") are not the flat's own outdoor space — do not count
  them.
- Only count outdoor space the property actually has. When the text merely sits
  in a "garden"-named area and describes no real balcony/terrace/garden of its
  own, the category is "none".

Return only the JSON object required by the schema."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": list(CATEGORIES)},
        "evidence": {
            "type": "string",
            "description": "Short quote or phrase from the text that justifies the category.",
        },
    },
    "required": ["category", "evidence"],
    "additionalProperties": False,
}

# Cache results within a run so re-analysing the same text costs one call.
_cache: dict[str, str] = {}
_client_singleton = "unset"  # "unset" | None | anthropic.Anthropic
_logged_state = False


def _log_once(message: str) -> None:
    global _logged_state
    if not _logged_state:
        print(message, file=sys.stderr)
        _logged_state = True


def _get_client():
    """Build (once) an Anthropic client, or return None if unavailable."""
    global _client_singleton
    if _client_singleton != "unset":
        return _client_singleton

    if os.environ.get("HUNT_DISABLE_LLM"):
        _log_once("  [classify] LLM disabled (HUNT_DISABLE_LLM) — using regex fallback")
        _client_singleton = None
        return None
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        _log_once("  [classify] no ANTHROPIC_API_KEY — using regex fallback")
        _client_singleton = None
        return None
    try:
        import anthropic
    except ImportError:
        _log_once("  [classify] anthropic SDK not installed — using regex fallback")
        _client_singleton = None
        return None

    # The SDK's httpx honours HTTPS_PROXY (trust_env); point it at the agent
    # proxy's CA bundle when present so TLS verification succeeds.
    http_client = None
    if Path(_CA_BUNDLE).exists():
        http_client = anthropic.DefaultHttpxClient(verify=_CA_BUNDLE)
    try:
        _client_singleton = anthropic.Anthropic(http_client=http_client) if http_client \
            else anthropic.Anthropic()
    except Exception as exc:  # misconfigured key/env — fall back rather than crash
        _log_once(f"  [classify] client init failed ({exc}) — using regex fallback")
        _client_singleton = None
    else:
        _log_once(f"  [classify] LLM outdoor classification enabled ({_model()})")
    return _client_singleton


def _model() -> str:
    return os.environ.get("HUNT_LLM_MODEL") or DEFAULT_MODEL


def classify_outdoor(text: str) -> str | None:
    """Return an outdoor category via Claude, or ``None`` if unavailable.

    ``None`` means "no LLM verdict" — the caller should keep its regex result.
    """
    if not text or not text.strip():
        return None
    client = _get_client()
    if client is None:
        return None

    key = text[:6000]
    if key in _cache:
        return _cache[key]

    try:
        resp = client.messages.create(
            model=_model(),
            max_tokens=512,
            system=_SYSTEM,
            messages=[{"role": "user", "content": key}],
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
        )
    except Exception as exc:
        _log_once(f"  [classify] API call failed ({exc}) — using regex fallback")
        # Disable further attempts this run so one outage doesn't retry 120x.
        globals()["_client_singleton"] = None
        return None

    if getattr(resp, "stop_reason", None) == "refusal":
        return None
    try:
        blob = next(b.text for b in resp.content if b.type == "text")
        category = json.loads(blob).get("category")
    except (StopIteration, ValueError, AttributeError, KeyError):
        return None

    if category not in CATEGORIES:
        return None
    _cache[key] = category
    return category
