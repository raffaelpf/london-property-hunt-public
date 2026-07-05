"""Per-platform search + parse modules.

Each module exposes ``search(context, url, cfg, listing_type, debug_dir=None)``
returning a list of :class:`scraper.models.Listing`. A platform that errors or
gets blocked should raise; the orchestrator isolates the failure so one bad
platform never kills the whole run.
"""

from . import spareroom, openrent, rightmove, zoopla  # noqa: F401

REGISTRY = {
    "SpareRoom": spareroom,
    "OpenRent": openrent,
    "Rightmove": rightmove,
    "Zoopla": zoopla,
}
