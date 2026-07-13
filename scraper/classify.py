"""Claude classification of a listing's outdoor space + furnishing.

Order of evidence is **source attributes first, then the description**: each
platform's structured attributes (feature tags like "Balcony"/"Communal garden",
the letting furnishing label, keyword-match hints) are handed to Claude as the
primary signal, with the free-text description as backup. There is no regex
classifier — place names like "Covent Garden" used to fool a regex garden matcher.

Backend: the **Claude Code CLI** (`claude -p`), which is already authenticated
inside a Claude Code routine — so no ANTHROPIC_API_KEY is needed. Classification
is **batched** (many listings per model call) to amortise the CLI's per-call
startup, and skipped entirely when the CLI isn't available (the caller keeps
whatever the structured source fields gave it).
"""

from __future__ import annotations

import json
import re
import os
import shutil
import subprocess
import sys

OUTDOOR_CATEGORIES = ("private", "communal", "juliet", "none")
FURNISHING_CATEGORIES = ("unfurnished", "part-furnished", "flexible", "furnished", "unknown")
BATCH_SIZE = 12
CLI_TIMEOUT = 240
_DEFAULT_MODEL = "sonnet"  # CLI model alias; override with HUNT_LLM_MODEL.

_RULES = """You classify a UK rental flat's OUTDOOR SPACE and FURNISHING from \
its listing. You are given the portal's structured attributes (the strongest \
evidence) and the free-text description.

OUTDOOR — the outdoor space the property itself has:
- "private": its own balcony, terrace, roof terrace, patio, decking, veranda,
  loggia, or private garden. A balcony/terrace is private unless the text says
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

FURNISHING — "unfurnished", "part-furnished", "furnished" per the listing;
"flexible" if it can be let furnished OR unfurnished; "unknown" if not stated.

SIZE — internal floor area in square feet as an integer if stated (convert
"X sq m" as round(X*10.76)); null if not stated or outside ~100–6000 sq ft."""

_SYSTEM_SINGLE = _RULES + '\n\nReturn ONLY a JSON object: ' \
    '{"outdoor": ..., "furnishing": ..., "size_sqft": <int or null>}. No prose, no code fences.'
_SYSTEM_BATCH = _RULES + '\n\nYou will be given several listings. Return ONLY a JSON array ' \
    'with one object per listing, in the same order, each: ' \
    '{"i": <listing number>, "outdoor": ..., "furnishing": ..., "size_sqft": <int or null>}. ' \
    'No prose, no code fences.'

_available = "unset"   # "unset" | True | False
_logged = False


def _log_once(message: str) -> None:
    global _logged
    if not _logged:
        print(message, file=sys.stderr)
        _logged = True


def _model() -> str:
    return os.environ.get("HUNT_LLM_MODEL") or _DEFAULT_MODEL


def llm_active() -> bool:
    """True if the Claude Code CLI is available for classification this run."""
    global _available
    if _available == "unset":
        if os.environ.get("HUNT_DISABLE_LLM"):
            _log_once("  [classify] disabled (HUNT_DISABLE_LLM) — structured fields only")
            _available = False
        elif shutil.which("claude"):
            _log_once(f"  [classify] using the Claude Code CLI ({_model()}) — no API key needed")
            _available = True
        else:
            _log_once("  [classify] 'claude' CLI not found — outdoor/furnishing not assessed")
            _available = False
    return _available


def _complete(system: str, user: str) -> str | None:
    """Return Claude's text response for (system, user) via the CLI, or None."""
    if not llm_active():
        return None
    try:
        proc = subprocess.run(
            ["claude", "-p", "--system-prompt", system,
             "--model", _model(), "--output-format", "json"],
            input=user, capture_output=True, text=True, timeout=CLI_TIMEOUT,
        )
    except Exception as exc:
        _log_once(f"  [classify] Claude CLI call failed ({exc}) — structured fields only")
        return None
    if proc.returncode != 0:
        _log_once(f"  [classify] Claude CLI exit {proc.returncode} — structured fields only")
        return None
    try:
        env = json.loads(proc.stdout)
    except ValueError:
        return None
    if env.get("is_error"):
        return None
    return env.get("result")


def _extract_json(text: str):
    """Parse the first JSON value in text, tolerating ``` fences and prose."""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```\s*$", "", t)
    for i, ch in enumerate(t):
        if ch in "[{":
            try:
                obj, _ = json.JSONDecoder().raw_decode(t[i:])
                return obj
            except ValueError:
                continue
    return None


def _normalise(item) -> dict | None:
    if not isinstance(item, dict):
        return None
    outdoor = item.get("outdoor")
    furnishing = item.get("furnishing")
    if outdoor not in OUTDOOR_CATEGORIES or furnishing not in FURNISHING_CATEGORIES:
        return None
    size = item.get("size_sqft")
    try:
        size = int(size) if size is not None else None
    except (TypeError, ValueError):
        size = None
    if size is not None and not (100 <= size <= 6000):
        size = None
    return {"outdoor": outdoor, "furnishing": furnishing, "size_sqft": size}


def classify_listing(attributes: list, description: str) -> dict | None:
    """Classify a single listing → ``{"outdoor","furnishing","size_sqft"}`` or None."""
    text = _complete(_SYSTEM_SINGLE, _single_user(attributes, description))
    return _normalise(_extract_json(text)) if text else None


def _single_user(attributes: list, description: str) -> str:
    attrs = "; ".join(a for a in (attributes or []) if a and str(a).strip())[:1000] or "(none)"
    desc = (description or "").strip()[:4000] or "(no description)"
    return f"Attributes from the portal: {attrs}\n\nDescription: {desc}"


def _batch_user(chunk: list) -> str:
    blocks = []
    for idx, l in enumerate(chunk, 1):
        attrs = "; ".join(a for a in (getattr(l, "attributes", []) or []) if a and str(a).strip())[:800] or "(none)"
        desc = (getattr(l, "description", "") or "").strip()[:1500] or "(no description)"
        blocks.append(f"### Listing {idx}\nAttributes: {attrs}\nDescription: {desc}")
    return "Classify each listing below.\n\n" + "\n\n".join(blocks)


def _parse_batch(text: str, n: int) -> list:
    arr = _extract_json(text)
    out = [None] * n
    if not isinstance(arr, list):
        return out
    for j, item in enumerate(arr):
        idx = item.get("i") if isinstance(item, dict) else None
        pos = (idx - 1) if isinstance(idx, int) and 1 <= idx <= n else j
        if 0 <= pos < n:
            out[pos] = _normalise(item)
    return out


def classify_batch(listings: list) -> int:
    """Classify each listing in one or a few batched CLI calls; mutate
    ``outdoor`` / ``furnishing`` in place (and fill ``size_sqft`` when empty).

    Returns the number classified. Structured fields already on the listing are
    the fallback: ``size_sqft`` from a numeric portal field is never overwritten,
    and outdoor/furnishing keep their current values for any listing not returned.
    """
    todo = [l for l in listings if (getattr(l, "attributes", None) or getattr(l, "description", ""))]
    if not todo or not llm_active():
        return 0
    done = 0
    for i in range(0, len(todo), BATCH_SIZE):
        chunk = todo[i:i + BATCH_SIZE]
        text = _complete(_SYSTEM_BATCH, _batch_user(chunk))
        if text is None:
            break  # CLI failed — stop, leave the rest on their structured fields
        for listing, verdict in zip(chunk, _parse_batch(text, len(chunk))):
            if verdict is None:
                continue
            listing.outdoor = verdict["outdoor"]
            listing.furnishing = verdict["furnishing"]
            if not listing.size_sqft and verdict["size_sqft"]:
                listing.size_sqft = verdict["size_sqft"]
            done += 1
    return done
