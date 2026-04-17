"""
browser_tools.py — Playwright-based browser automation tools.

Registered via the tool plugin registry in base_crew.py — all agents get
these tools automatically.

SSRF-protected: all navigations validate URLs via web_fetch._is_safe_url.
Graceful fallback: returns empty tool list if Playwright is not installed.
"""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

# Module-level Playwright handle with lazy init + shared browser per process.
_browser_lock = threading.Lock()
_playwright = None
_browser = None


def _get_browser():
    """Lazily spin up a headless Chromium instance shared by all tool calls."""
    global _playwright, _browser
    if _browser is not None:
        return _browser
    with _browser_lock:
        if _browser is not None:
            return _browser
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError("playwright not installed (pip install playwright && playwright install chromium)")
        _playwright = sync_playwright().start()
        _browser = _playwright.chromium.launch(headless=True)
        return _browser


def _validate_url(url: str) -> tuple[bool, str]:
    """Reuse web_fetch SSRF protection."""
    try:
        from app.tools.web_fetch import _is_safe_url
        return _is_safe_url(url)
    except Exception:
        return True, ""  # fail-open only if validator itself crashes


def create_browser_tools() -> list:
    """Build CrewAI BaseTool instances for browser automation. Returns [] if unavailable."""
    try:
        import playwright  # noqa: F401
    except ImportError:
        logger.info("browser_tools: playwright not installed — browser tools disabled")
        return []

    try:
        from crewai.tools import BaseTool
        from pydantic import BaseModel, Field
    except ImportError:
        return []

    class _FetchInput(BaseModel):
        url: str = Field(description="URL to fetch (HTTPS only)")
        wait_selector: str = Field(default="", description="Optional CSS selector to wait for before extracting")

    class BrowserFetchTool(BaseTool):
        name: str = "browser_fetch"
        description: str = (
            "Render a URL in a headless Chromium browser and return the extracted text. "
            "Use this for JS-heavy pages where plain HTTP fetch returns empty content."
        )
        args_schema: type = _FetchInput

        def _run(self, url: str, wait_selector: str = "") -> str:
            safe, reason = _validate_url(url)
            if not safe:
                return f"URL blocked: {reason}"
            try:
                browser = _get_browser()
            except Exception as exc:
                return f"Browser init failed: {exc}"
            try:
                ctx = browser.new_context()
                page = ctx.new_page()
                page.goto(url, timeout=30000, wait_until="domcontentloaded")
                if wait_selector:
                    try:
                        page.wait_for_selector(wait_selector, timeout=10000)
                    except Exception:
                        pass
                text = page.inner_text("body")
                ctx.close()
                return text[:8000] if len(text) > 8000 else text
            except Exception as exc:
                return f"Browser fetch error: {str(exc)[:300]}"

    class _ScreenshotInput(BaseModel):
        url: str = Field(description="URL to screenshot")
        out_path: str = Field(description="Path inside /app/workspace/output/ to save PNG")

    class BrowserScreenshotTool(BaseTool):
        name: str = "browser_screenshot"
        description: str = "Render a URL and save a PNG screenshot inside /app/workspace/output/."
        args_schema: type = _ScreenshotInput

        def _run(self, url: str, out_path: str) -> str:
            safe, reason = _validate_url(url)
            if not safe:
                return f"URL blocked: {reason}"
            from pathlib import Path
            out = Path(out_path).resolve()
            try:
                out.relative_to("/app/workspace/output")
            except ValueError:
                return "out_path must live under /app/workspace/output"
            try:
                browser = _get_browser()
            except Exception as exc:
                return f"Browser init failed: {exc}"
            try:
                ctx = browser.new_context()
                page = ctx.new_page()
                page.goto(url, timeout=30000, wait_until="domcontentloaded")
                out.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(out), full_page=True)
                ctx.close()
                return f"Screenshot saved: {out}"
            except Exception as exc:
                return f"Screenshot error: {str(exc)[:300]}"

    class _ClickInput(BaseModel):
        url: str = Field(description="URL to load first")
        selector: str = Field(description="CSS selector of the element to click")

    class BrowserClickTool(BaseTool):
        name: str = "browser_click"
        description: str = "Load a URL, click a selector, and return the resulting text."
        args_schema: type = _ClickInput

        def _run(self, url: str, selector: str) -> str:
            safe, reason = _validate_url(url)
            if not safe:
                return f"URL blocked: {reason}"
            try:
                browser = _get_browser()
            except Exception as exc:
                return f"Browser init failed: {exc}"
            try:
                ctx = browser.new_context()
                page = ctx.new_page()
                page.goto(url, timeout=30000, wait_until="domcontentloaded")
                page.click(selector, timeout=10000)
                page.wait_for_load_state("domcontentloaded", timeout=10000)
                text = page.inner_text("body")
                ctx.close()
                return text[:8000] if len(text) > 8000 else text
            except Exception as exc:
                return f"Browser click error: {str(exc)[:300]}"

    return [BrowserFetchTool(), BrowserScreenshotTool(), BrowserClickTool()]
