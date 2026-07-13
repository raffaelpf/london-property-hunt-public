"""Browser-based fetcher for Cloudflare-protected sites (currently Zoopla).

Zoopla sits behind Cloudflare's managed challenge: every plain-HTTP client gets
a 403 "Just a moment..." page regardless of headers, and the ``cf_clearance``
cookie is bound to the client fingerprint, so it cannot be replayed through
``urllib``. What does work is a real, *headed* Chromium (under Xvfb when the
host has no display) that executes the challenge JS and clicks the Turnstile
checkbox. One solved session then navigates freely, so we keep a singleton
browser alive for the whole run and route every Zoopla fetch through it.

Proxy note: this environment's egress proxy resets TLS ClientHellos that carry
the ECH (Encrypted ClientHello) or post-quantum key-share extensions modern
Chromium sends — which is why the browser route looked dead in earlier
revisions. Both are disabled via a Chromium enterprise policy written to
``/etc/chromium/policies/managed`` (best effort; skipped when not writable).
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import time

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)

# Managed-policy dirs for Chromium and Chrome; first writable one wins.
_POLICY_DIRS = ("/etc/chromium/policies/managed", "/etc/opt/chrome/policies/managed")
_POLICY = {"PostQuantumKeyAgreementEnabled": False, "EncryptedClientHelloEnabled": False}

_CHALLENGE_TITLE = "just a moment"
_SOLVE_SECONDS = 120          # per-navigation budget to clear the challenge
_CLICK_SPACING = 12           # min seconds between Turnstile clicks
_XVFB_DISPLAY = ":93"


def _ensure_policy() -> None:
    for d in _POLICY_DIRS:
        path = os.path.join(d, "no-ech-pq.json")
        try:
            os.makedirs(d, exist_ok=True)
            if not os.path.exists(path):
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(_POLICY, f)
        except OSError:
            continue


def _find_chromium() -> str | None:
    """Prefer the pre-installed Playwright Chromium; fall back to system ones."""
    env = os.environ.get("CHROMIUM_PATH")
    if env and os.path.exists(env):
        return env
    roots = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "/opt/pw-browsers"]
    for root in roots:
        hits = sorted(glob.glob(os.path.join(root, "chromium-*", "chrome-linux", "chrome")))
        if hits:
            return hits[-1]
    for name in ("chromium", "chromium-browser", "google-chrome"):
        path = shutil.which(name)
        if path:
            return path
    return None  # let Playwright use its own download, if any


class BrowserFetcher:
    """A lazily started headed-Chromium session that solves CF challenges."""

    def __init__(self):
        self._pw = None
        self._browser = None
        self._page = None
        self._xvfb: subprocess.Popen | None = None

    # -- lifecycle ---------------------------------------------------------

    def _ensure_display(self) -> None:
        if os.environ.get("DISPLAY"):
            return
        if shutil.which("Xvfb") is None:
            raise RuntimeError("headed browser needed but no DISPLAY and no Xvfb")
        self._xvfb = subprocess.Popen(
            ["Xvfb", _XVFB_DISPLAY, "-screen", "0", "1366x850x24", "-nolisten", "tcp"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        sock = "/tmp/.X11-unix/X" + _XVFB_DISPLAY.lstrip(":")
        for _ in range(50):
            if os.path.exists(sock):
                break
            time.sleep(0.1)
        os.environ["DISPLAY"] = _XVFB_DISPLAY

    def _start(self) -> None:
        from playwright.sync_api import sync_playwright

        _ensure_policy()
        self._ensure_display()
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=False,  # headless is what Cloudflare rejects
            executable_path=_find_chromium(),
            proxy={"server": os.environ["HTTPS_PROXY"]} if os.environ.get("HTTPS_PROXY") else None,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        ctx = self._browser.new_context(
            user_agent=USER_AGENT, locale="en-GB",
            viewport={"width": 1366, "height": 850},
        )
        self._page = ctx.new_page()

    def close(self) -> None:
        for closer in (
            lambda: self._browser and self._browser.close(),
            lambda: self._pw and self._pw.stop(),
            lambda: self._xvfb and self._xvfb.terminate(),
        ):
            try:
                closer()
            except Exception:
                pass
        self._pw = self._browser = self._page = self._xvfb = None

    # -- challenge handling --------------------------------------------------

    def _is_challenge(self) -> bool:
        try:
            return _CHALLENGE_TITLE in (self._page.title() or "").lower()
        except Exception:
            return True  # navigation in flight — assume still challenged, re-check

    def _solve(self) -> bool:
        """Wait out the CF challenge, clicking the Turnstile checkbox if shown.

        Clicks must be spaced well apart: a click while the widget is in its
        "Verifying..." state resets the verification and the challenge loops
        forever. One click, then ~12s of patience, works reliably.
        """
        page = self._page
        deadline = time.time() + _SOLVE_SECONDS
        last_click = 0.0
        reloaded = False
        while time.time() < deadline:
            page.wait_for_timeout(2500)
            if not self._is_challenge():
                return True
            if time.time() - last_click < _CLICK_SPACING:
                continue
            # Checkbox centre: from the widget iframe when visible, else the
            # fixed spot Cloudflare's interstitial layout puts it (1366x850).
            x, y = 256.0, 337.0
            for frame in page.frames:
                if "challenges.cloudflare.com" not in (frame.url or ""):
                    continue
                try:
                    box = frame.frame_element().bounding_box()
                    if box:
                        x, y = box["x"] + 21, box["y"] + box["height"] / 2
                except Exception:
                    pass  # frame detached (often means the challenge just passed)
            try:
                page.mouse.move(x - 60, y - 45)
                page.mouse.click(x, y)
                last_click = time.time()
            except Exception:
                pass
            # A stuck challenge sometimes clears on a fresh load; try once.
            if not reloaded and time.time() > deadline - _SOLVE_SECONDS / 3:
                reloaded = True
                try:
                    page.reload(wait_until="domcontentloaded")
                except Exception:
                    pass
        return not self._is_challenge()

    # -- fetching -----------------------------------------------------------

    def _goto(self, url: str, timeout: int) -> int:
        """goto with a retry when Cloudflare's own post-clearance redirect is
        still in flight ("interrupted by another navigation")."""
        for attempt in range(3):
            try:
                resp = self._page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
                return resp.status if resp else 0
            except Exception as exc:
                if "interrupted by another navigation" in str(exc) and attempt < 2:
                    self._page.wait_for_timeout(3000)
                    continue
                raise
        return 0

    def get(self, url: str, timeout: int = 60) -> tuple[int, str]:
        """Navigate to ``url``, returning ``(status, html)`` post-challenge."""
        if self._page is None:
            self._start()
        page = self._page
        status = self._goto(url, timeout)
        if status == 403 or self._is_challenge():
            solved = self._solve()
            page.wait_for_timeout(2500)  # let the clearance redirect land
            # Re-request so we return the real page (loaded with the clearance
            # cookie), never the intermediate state the challenge left behind.
            status = self._goto(url, timeout)
            if not solved or self._is_challenge():
                return 403, page.content()
        html = page.content()
        if _CHALLENGE_TITLE in html[:3000].lower():  # belt-and-braces
            return 403, html
        return status, html


_session: BrowserFetcher | None = None


def get(url: str, timeout: int = 60) -> tuple[int, str]:
    """Fetch via the shared browser session (started on first use)."""
    global _session
    if _session is None:
        _session = BrowserFetcher()
    return _session.get(url, timeout)


def close() -> None:
    """Shut down the shared session (call once at the end of a run)."""
    global _session
    if _session is not None:
        _session.close()
        _session = None
