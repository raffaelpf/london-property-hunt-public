"""Claude classification of a listing's outdoor space + furnishing.

Order of evidence is **source attributes first, then the description**: each
platform's structured attributes (feature tags like "Balcony"/"Communal garden",
the letting-details furnishing label, keyword-match flags) are handed to Claude
as the primary signal, with the free-text description as backup. There is no
regex classifier — place names like "Covent Garden" used to fool a regex garden
matcher; Claude reads the structured attributes and prose and isn't fooled.

Graceful degradation: if the ``anthropic`` SDK isn't installed, no API key is
set, or the call fails, :func:`classify_listing` returns ``None`` and the caller
keeps whatever the structured source fields already gave it.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

OUTDOOR_CATEGORIES = ("private", "communal", "juliet", "none")
FURNISHING_CATEGORIES = ("unfurnished", "part-furnished", "flexible", "furnished", "unknown")
DEFAULT_MODEL = "claude-opus-4-8"

_CA_BUNDLE = os.environ.get("REQUESTS_CA_BUNDLE") or "/root/.ccr/ca-bundle.crt"

_SYSTEM = """You classify a single rental flat's OUTDOOR SPACE and FURNISHING \
from its listing. You are given the portal's structured attributes first (treat \
these as the strongest evidence) and then the free-text description.

OUTDOOR — pick the category for the outdoor space the property itself has:
- "private": its own balcony, terrace, roof terrace, patio, decking, veranda,
  loggia, or a private garden. A balcony/terrace is private unless the text says
  it is shared or communal.
- "communal": the only outdoor space is shared/communal — a communal or
  residents' garden, a shared roof terrace/courtyard — or a garden mentioned
  without being clearly private.
- "juliet": the only "balcony" is a Juliet/French balcony (a railing at a door
  or window, no standing space) and there is no other outdoor space.
- "none": no outdoor space.
Ignore place names, streets, stations and areas — "Covent Garden", "Hatton
Garden", "Kensington Gardens", a "…Gardens" street or a "Covent Garden" tube
station are LOCATIONS, not outdoor space. Nearby public parks/gardens are not
the flat's own space. Only count outdoor space the property actually has.

FURNISHING — pick the letting furnishing:
- "unfurnished", "part-furnished", "furnished" per the listing.
- "flexible": the listing says it can be let furnished OR unfurnished
  (e.g. "furnished or unfurnished", "furnishing negotiable").
- "unknown": furnishing is not stated.

SIZE — internal floor area in square feet as an integer if stated (convert
"X sq m" as X*10.76, round to nearest); null if not stated or implausible
(outside ~100–6000 sq ft).

Return only the JSON object required by the schema."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "outdoor": {"type": "string", "enum": list(OUTDOOR_CATEGORIES)},
        "furnishing": {"type": "string", "enum": list(FURNISHING_CATEGORIES)},
        "size_sqft": {"type": ["integer", "null"]},
    },
    "required": ["outdoor", "furnishing", "size_sqft"],
    "additionalProperties": False,
}

# Cache verdicts within a run so re-analysing the same input costs one call.
_cache: dict[str, dict] = {}
_client_singleton = "unset"  # "unset" | None | anthropic.Anthropic
_logged_state = False


def _log_once(message: str) -> None:
    global _logged_state
    if not _logged_state:
        print(message, file=sys.stderr)
        _logged_state = True


def _model() -> str:
    return os.environ.get("HUNT_LLM_MODEL") or DEFAULT_MODEL


def _get_client():
    """Build (once) an Anthropic client, or return None if unavailable."""
    global _client_singleton
    if _client_singleton != "unset":
        return _client_singleton

    if os.environ.get("HUNT_DISABLE_LLM"):
        _log_once("  [classify] LLM disabled (HUNT_DISABLE_LLM) — structured fields only")
        _client_singleton = None
        return None
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        _log_once("  [classify] no ANTHROPIC_API_KEY — outdoor/furnishing not assessed")
        _client_singleton = None
        return None
    try:
        import anthropic
    except ImportError:
        _log_once("  [classify] anthropic SDK not installed — structured fields only")
        _client_singleton = None
        return None

    http_client = None
    if Path(_CA_BUNDLE).exists():
        http_client = anthropic.DefaultHttpxClient(verify=_CA_BUNDLE)
    try:
        _client_singleton = anthropic.Anthropic(http_client=http_client) if http_client \
            else anthropic.Anthropic()
    except Exception as exc:
        _log_once(f"  [classify] client init failed ({exc}) — structured fields only")
        _client_singleton = None
    else:
        _log_once(f"  [classify] Claude classification enabled ({_model()})")
    return _client_singleton


def llm_active() -> bool:
    """True if Claude classification is actually available this run.

    Used to decide whether to persist a flat as 'classified' — we only want the
    Seen ledger to hold flats Claude judged, so that adding an API key later
    doesn't leave source-field-only flats permanently cached as done.
    """
    return _get_client() is not None


def _prompt(attributes: list[str], description: str) -> str:
    attrs = "\n".join(f"- {a}" for a in attributes if a and a.strip()) or "(none provided)"
    desc = (description or "").strip()[:6000] or "(no description)"
    return f"Listing attributes from the portal:\n{attrs}\n\nDescription:\n{desc}"


def classify_listing(attributes: list[str], description: str) -> dict | None:
    """Return ``{"outdoor", "furnishing", "size_sqft"}`` via Claude, or ``None``.

    ``None`` means "no LLM verdict" — the caller keeps its structured-field
    values. ``attributes`` are the portal's structured signals (the primary
    evidence); ``description`` is the free-text backup.
    """
    client = _get_client()
    if client is None:
        return None

    content = _prompt(attributes, description)
    if not content.strip():
        return None
    if content in _cache:
        return _cache[content]

    try:
        resp = client.messages.create(
            model=_model(),
            max_tokens=256,
            system=_SYSTEM,
            messages=[{"role": "user", "content": content}],
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
        )
    except Exception as exc:
        _log_once(f"  [classify] API call failed ({exc}) — structured fields only")
        globals()["_client_singleton"] = None  # stop retrying this run
        return None

    if getattr(resp, "stop_reason", None) == "refusal":
        return None
    try:
        blob = next(b.text for b in resp.content if b.type == "text")
        data = json.loads(blob)
    except (StopIteration, ValueError, AttributeError):
        return None

    verdict = _normalise(data)
    if verdict is None:
        return None
    _cache[content] = verdict
    return verdict


def apply_classification(listing, description: str = "",
                         struct_size: int | None = None,
                         struct_furnishing: str = "unknown") -> None:
    """Set ``listing.outdoor`` / ``furnishing`` / ``size_sqft`` for one listing.

    Order of evidence: the listing's structured ``attributes`` + ``description``
    go to Claude, which decides outdoor and furnishing. Structured fields are the
    fallback when Claude is unavailable: ``struct_size`` (a numeric portal field)
    always wins for size; ``struct_furnishing`` (a letting label) is used only
    when Claude didn't run.
    """
    verdict = classify_listing(list(getattr(listing, "attributes", []) or []), description)
    if verdict is not None:
        listing.outdoor = verdict["outdoor"]
        listing.furnishing = verdict["furnishing"]
    elif struct_furnishing and struct_furnishing != "unknown":
        listing.furnishing = struct_furnishing
    if struct_size:
        listing.size_sqft = struct_size
    elif verdict is not None and verdict.get("size_sqft") and not listing.size_sqft:
        listing.size_sqft = verdict["size_sqft"]


def _normalise(data: dict) -> dict | None:
    outdoor = data.get("outdoor")
    furnishing = data.get("furnishing")
    if outdoor not in OUTDOOR_CATEGORIES or furnishing not in FURNISHING_CATEGORIES:
        return None
    size = data.get("size_sqft")
    try:
        size = int(size) if size is not None else None
    except (TypeError, ValueError):
        size = None
    if size is not None and not (100 <= size <= 6000):
        size = None
    return {"outdoor": outdoor, "furnishing": furnishing, "size_sqft": size}
