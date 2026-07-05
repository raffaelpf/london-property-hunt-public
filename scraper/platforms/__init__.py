"""Per-platform search + parse modules (flat search).

Each module exposes ``search(url, cfg, listing_type, debug_dir=None)`` returning
a list of :class:`scraper.models.Listing`. OnTheMarket and OpenRent also expose
``enrich(listing, debug_dir=None)`` which fetches the detail page to refine
outdoor space / furnishing / size. A platform that errors or gets blocked
raises; the orchestrator isolates it so one bad platform never kills the run.
"""

from . import openrent, onthemarket, rightmove  # noqa: F401

REGISTRY = {
    "Rightmove": rightmove,
    "OnTheMarket": onthemarket,
    "OpenRent": openrent,
}
