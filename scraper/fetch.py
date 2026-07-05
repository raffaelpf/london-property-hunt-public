"""HTTP fetcher that routes through the environment's agent proxy.

We use plain ``urllib`` (which honours ``HTTPS_PROXY`` and the CA bundle) rather
than a headless browser: Playwright's Chromium cannot establish a CONNECT tunnel
through this proxy (the tunnel is reset), whereas ``urllib``/``curl`` work fine.
The property sites embed their listing data in server-rendered HTML / JSON, so
no JavaScript execution is needed.
"""

from __future__ import annotations

import os
import ssl
import time
import urllib.request
from pathlib import Path

_CA_BUNDLE = os.environ.get("REQUESTS_CA_BUNDLE") or "/root/.ccr/ca-bundle.crt"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}


def _opener():
    ctx = (
        ssl.create_default_context(cafile=_CA_BUNDLE)
        if Path(_CA_BUNDLE).exists()
        else ssl.create_default_context()
    )
    proxy = (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY")
    )
    handlers = [urllib.request.HTTPSHandler(context=ctx)]
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"https": proxy, "http": proxy}))
    return urllib.request.build_opener(*handlers)


def fetch_html(url: str, timeout: int = 30, retries: int = 2) -> tuple[int, str]:
    """Fetch a URL, returning ``(status_code, html)``.

    Raises the last exception if all attempts fail (so the orchestrator can
    isolate the platform). HTTP error responses (e.g. 403) are returned as
    ``(code, body)`` rather than raised.
    """
    opener = _opener()
    req = urllib.request.Request(url, headers=_HEADERS)
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with opener.open(req, timeout=timeout) as resp:
                body = resp.read()
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.status, body.decode(charset, "replace")
        except urllib.error.HTTPError as exc:  # 4xx/5xx — don't retry, report code
            return exc.code, exc.read().decode("utf-8", "replace")
        except Exception as exc:  # transient (reset/timeout) — back off and retry
            last_exc = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise last_exc  # type: ignore[misc]


def dump_html(debug_dir: str | Path | None, name: str, html: str) -> None:
    """Save fetched HTML for offline selector debugging."""
    if not debug_dir:
        return
    import re

    d = Path(debug_dir)
    d.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:80]
    (d / f"{safe}.html").write_text(html, encoding="utf-8")
