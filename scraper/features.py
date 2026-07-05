"""Analyze listing text for outdoor space, furnishing, and size.

Vocabulary and false-positive guards were derived from real Rightmove/OnTheMarket
listings so we catch balcony/terrace variants without misfiring on the
"terraced house" trap.
"""

from __future__ import annotations

import re

# House-type phrases where "terrace" is NOT outdoor space — stripped first.
_HOUSE_TYPE_RE = re.compile(
    r"(end[-\s]?of[-\s]?terrace|mid[-\s]?terrace|terraced\s+\w+|terrace[d]?\s+(house|home|property|cottage))",
    re.I,
)
_SQFT_RE = re.compile(r"([\d,]{2,})\s*sq\s*\.?\s*(?:ft|foot|feet)", re.I)
_SQM_RE = re.compile(r"([\d,]{2,})\s*(?:sq\s*\.?\s*m|sqm|m2|m²)", re.I)

# Outdoor labels for the tracker
OUTDOOR_LABELS = {
    "private": "Private balcony/terrace",
    "communal": "Communal / shared",
    "juliet": "Juliet only",
    "none": "Not stated",
}


def _plausible(sqft: int | None) -> int | None:
    # Guard against spurious matches (service charges, development totals, etc.).
    return sqft if sqft and 100 <= sqft <= 6000 else None


def _sqft(t: str) -> int | None:
    m = _SQFT_RE.search(t)
    if m:
        return _plausible(int(m.group(1).replace(",", "")))
    m = _SQM_RE.search(t)
    if m:
        return _plausible(round(int(m.group(1).replace(",", "")) * 10.7639))
    return None


def _furnishing(t: str) -> str:
    if re.search(r"furnished\s+or\s+unfurnished|unfurnished\s+or\s+furnished|furnish\w*\s+(is\s+)?(flexible|negotiable)|flexible\s+furnish", t):
        return "flexible"
    if re.search(r"part[-\s]?furnished|partly\s+furnished", t):
        return "part-furnished"
    if re.search(r"un[-\s]?furnished", t):
        return "unfurnished"
    if re.search(r"\bfurnished\b", t):
        return "furnished"
    return "unknown"


_NEGATED_RE = re.compile(
    r"(no|without|not\s+have|lacks?)\s+(a\s+)?(private\s+)?(outdoor|outside)\s+space"
    r"|(no|without)\s+(a\s+)?balcon\w*|(no|without)\s+(a\s+)?terrace|(no|without)\s+(a\s+)?garden",
    re.I,
)


def _outdoor(t: str) -> str:
    # Strip house-type mentions so "terraced house" can't read as a terrace,
    # and negated phrases ("no outdoor space") so they don't read as present.
    t = _HOUSE_TYPE_RE.sub(" ", t)
    t = _NEGATED_RE.sub(" ", t)
    juliet = bool(re.search(r"juliet|juliette", t))
    # Remove juliet/french balcony phrases to see if a *real* balcony remains.
    t_nojuliet = re.sub(r"(french|juliet(te)?)\s+balcon\w*", " ", t)

    real_balcony = bool(re.search(r"\bbalcon", t_nojuliet))
    terrace_patio = bool(re.search(r"\bterrace|\bpatio|roof\s*top|roof\s*terrace|\bdeck(?:ing)?\b|veranda|loggia|winter\s+garden|courtyard", t_nojuliet))
    outdoor_space = bool(re.search(r"(outdoor|outside)\s+space|private\s+outdoor", t_nojuliet))
    garden = bool(re.search(r"\bgarden", t_nojuliet))
    communal = bool(re.search(r"communal|shared|residents", t_nojuliet))
    private_word = bool(re.search(r"private", t_nojuliet))

    if real_balcony or terrace_patio or outdoor_space:
        # A balcony is inherently private; a terrace/patio is private unless
        # explicitly communal (and not also flagged private).
        if communal and not real_balcony and not private_word:
            return "communal"
        return "private"
    if garden:
        if re.search(r"private\s+garden", t_nojuliet):
            return "private"
        return "communal"  # bare/communal garden with a flat — flag, don't claim private
    if juliet:
        return "juliet"
    return "none"


def analyze_text(*texts: str) -> dict:
    """Analyze one or more text blobs, returning outdoor/furnishing/sqft."""
    t = " ".join(x for x in texts if x).lower()
    if not t.strip():
        return {"outdoor": "none", "furnishing": "unknown", "sqft": None}
    return {"outdoor": _outdoor(t), "furnishing": _furnishing(t), "sqft": _sqft(t)}
