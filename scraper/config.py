"""Parse the ``config.md`` file into a plain dict.

The config file mixes Markdown prose with fenced ``KEY=value`` blocks (see
``config.example.md``). We only care about the ``KEY=value`` lines and the
indented URL lists that follow ``*_URLS`` keys; everything else is ignored.
"""

from __future__ import annotations

import re
from pathlib import Path

# Keys whose value is a list of URLs spread across the following lines.
_URL_LIST_KEYS = {"SPAREROOM_ROOM_URLS", "SPAREROOM_STUDIO_URLS"}
# Keys whose value is a comma-separated list.
_CSV_KEYS = {"PRIMARY_AREAS", "SECONDARY_AREAS", "ZOOPLA_AREAS", "FEATURE_MUST", "FURNISH_FILTER"}

_KV_RE = re.compile(r"^([A-Z][A-Z0-9_]*)\s*=\s*(.*)$")


def load_config(path: str | Path) -> dict:
    """Load ``config.md`` (or ``config.example.md``) into a dict.

    String values are returned as-is; ``*_AREAS`` become lists of strings and
    ``*_URLS`` become lists of URLs.
    """
    text = Path(path).read_text(encoding="utf-8")
    data: dict = {}
    current_list_key: str | None = None

    for raw in text.splitlines():
        line = raw.strip()
        match = _KV_RE.match(line)
        if match:
            key, val = match.group(1), match.group(2).strip()
            if key in _URL_LIST_KEYS:
                current_list_key = key
                data[key] = [val] if val.startswith("http") else []
            else:
                current_list_key = None
                data[key] = val
        elif current_list_key and line.startswith("http"):
            data[current_list_key].append(line)
        # Any other line (markdown, fences, blanks, comments) is ignored.

    for key in _CSV_KEYS:
        if isinstance(data.get(key), str):
            data[key] = [a.strip() for a in data[key].split(",") if a.strip()]

    return data


def get_int(data: dict, key: str, default: int | None = None) -> int | None:
    """Read an integer config value, tolerating stray ``£``/``,`` characters."""
    raw = data.get(key)
    if raw is None or raw == "":
        return default
    digits = re.sub(r"[^0-9]", "", str(raw))
    return int(digits) if digits else default
