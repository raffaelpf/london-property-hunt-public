"""Headless Chromium launcher using the pre-installed browser.

In the Claude Code cloud environment Chromium already lives under
``PLAYWRIGHT_BROWSERS_PATH`` (``/opt/pw-browsers``) and must NOT be
re-downloaded. Playwright normally finds it automatically; we fall back to an
explicit ``executable_path`` if the pinned revision does not match.
"""

from __future__ import annotations

import glob
import os
from contextlib import contextmanager

from playwright.sync_api import sync_playwright

# A recent, realistic desktop UA. Headless flag is stripped by Playwright, but a
# stock UA still reduces trivial bot rejections.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
]


def _fallback_executable() -> str | None:
    """Find the real Chrome binary under /opt/pw-browsers as a fallback."""
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
    for pattern in (
        f"{root}/chromium-*/chrome-linux/chrome",
        f"{root}/chromium_headless_shell-*/chrome-linux/headless_shell",
    ):
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[-1]
    return None


@contextmanager
def browser_context(headless: bool = True, timeout_ms: int = 30_000):
    """Yield a ready-to-use Playwright browsing context.

    Usage::

        with browser_context() as ctx:
            page = ctx.new_page()
            page.goto(url)
    """
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=headless, args=_LAUNCH_ARGS)
        except Exception:
            exe = _fallback_executable()
            if not exe:
                raise
            browser = p.chromium.launch(
                headless=headless, args=_LAUNCH_ARGS, executable_path=exe
            )

        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1440, "height": 900},
            locale="en-GB",
            timezone_id="Europe/London",
        )
        context.set_default_timeout(timeout_ms)
        try:
            yield context
        finally:
            context.close()
            browser.close()
