from __future__ import annotations

import os
import time
from pathlib import Path

from rn_opportunity_radar.config import DEFAULT_HEADERS


class BrowserUnavailableError(RuntimeError):
    """Raised when a source asks for hydrated DOM fallback without Playwright support."""


def resolve_browser_path(explicit_path: str | None = None) -> str | None:
    candidate = explicit_path or os.environ.get("RN_OPPORTUNITY_RADAR_BROWSER_PATH")
    if not candidate:
        return None

    path = Path(candidate)
    return str(path) if path.exists() else None


class BrowserRenderer:
    def __init__(
        self,
        *,
        browser_path: str | None = None,
        profile_dir: str | Path | None = None,
    ) -> None:
        self.browser_path = resolve_browser_path(browser_path)
        self.profile_dir = Path(profile_dir or Path.cwd() / ".codex" / "playwright-profile")
        self._playwright = None
        self._context = None

    def __enter__(self) -> "BrowserRenderer":
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - exercised in runtime environments
            raise BrowserUnavailableError(
                "Playwright is required for browser-backed sources. Install it with `python -m pip install playwright` "
                "and then run `python -m playwright install chromium`."
            ) from exc

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        launch_kwargs = {
            "headless": True,
            "ignore_https_errors": True,
            "user_agent": DEFAULT_HEADERS["User-Agent"],
            "locale": "en-US",
            "viewport": {"width": 1400, "height": 1100},
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if self.browser_path:
            launch_kwargs["executable_path"] = self.browser_path

        self._context = self._playwright.chromium.launch_persistent_context(
            str(self.profile_dir),
            **launch_kwargs,
        )
        self._context.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', {
              get: () => undefined,
            });
            """
        )
        self._context.set_default_timeout(60_000)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    def fetch_html(
        self,
        url: str,
        *,
        wait_for_selector: str | None = None,
        extra_wait_ms: int = 0,
        timeout_ms: int = 60_000,
    ) -> str:
        if self._context is None:
            raise BrowserUnavailableError("Browser renderer must be used as a context manager.")

        page = self._context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            self._wait_for_ready(
                page,
                wait_for_selector=wait_for_selector,
                extra_wait_ms=extra_wait_ms,
                timeout_ms=timeout_ms,
            )
            return page.content()
        finally:
            page.close()

    def _wait_for_ready(
        self,
        page,
        *,
        wait_for_selector: str | None,
        extra_wait_ms: int,
        timeout_ms: int,
    ) -> None:
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        except ImportError as exc:  # pragma: no cover - import already guarded above
            raise BrowserUnavailableError("Playwright runtime became unavailable during browser fetch.") from exc

        deadline = time.monotonic() + (timeout_ms / 1000)
        last_error: Exception | None = None

        while time.monotonic() < deadline:
            if wait_for_selector:
                try:
                    page.wait_for_selector(wait_for_selector, timeout=2_500)
                    if extra_wait_ms:
                        page.wait_for_timeout(extra_wait_ms)
                    return
                except PlaywrightTimeoutError as exc:
                    last_error = exc

            title = ""
            body_text = ""
            try:
                title = (page.title() or "").strip().lower()
            except Exception:
                title = ""
            try:
                body_text = (page.locator("body").text_content(timeout=1_500) or "").strip().lower()
            except Exception:
                body_text = ""

            challenge_text = "just a moment" in title or "verify you are human" in body_text
            if not challenge_text:
                try:
                    page.wait_for_load_state("networkidle", timeout=1_500)
                except PlaywrightTimeoutError:
                    pass
                if extra_wait_ms:
                    page.wait_for_timeout(extra_wait_ms)
                return

            page.wait_for_timeout(1_500)

        if wait_for_selector:
            raise BrowserUnavailableError(
                f"Timed out waiting for selector {wait_for_selector!r} at {page.url!r}."
            ) from last_error
        raise BrowserUnavailableError(f"Timed out waiting for browser-rendered content at {page.url!r}.")


def fetch_rendered_html(
    url: str,
    *,
    browser_path: str | None = None,
    profile_dir: str | Path | None = None,
    wait_for_selector: str | None = None,
    extra_wait_ms: int = 0,
    timeout_ms: int = 60_000,
) -> str:
    with BrowserRenderer(browser_path=browser_path, profile_dir=profile_dir) as renderer:
        return renderer.fetch_html(
            url,
            wait_for_selector=wait_for_selector,
            extra_wait_ms=extra_wait_ms,
            timeout_ms=timeout_ms,
        )
