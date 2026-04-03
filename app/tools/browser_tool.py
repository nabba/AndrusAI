"""
browser_tool.py — Browser automation via Playwright.

Enables agents to interact with dynamic web pages:
  - Navigate to URLs
  - Extract text/HTML from JavaScript-rendered pages
  - Take screenshots
  - Fill forms and click buttons
  - Wait for dynamic content

Uses Playwright MCP or direct playwright library if available.
Falls back to web_fetch for simple static pages.

Safety: All URLs pass through the same SSRF protections as web_fetch.
Lifecycle hooks gate browser actions via PRE_TOOL_USE.
"""

import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path("/app/workspace/output/screenshots")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Max content size returned
MAX_TEXT_CHARS = 15000
MAX_SCREENSHOT_BYTES = 5 * 1024 * 1024  # 5MB


def _check_playwright_available() -> bool:
    """Check if Playwright is installed and browsers are available."""
    try:
        result = subprocess.run(
            ["python3", "-c", "from playwright.sync_api import sync_playwright; print('ok')"],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0 and "ok" in result.stdout
    except Exception:
        return False


def _validate_url(url: str) -> tuple[bool, str]:
    """Validate URL using existing SSRF protections from web_fetch."""
    try:
        from app.tools.web_fetch import _is_safe_url
        return _is_safe_url(url)
    except ImportError:
        # Fallback: basic validation
        if not url.startswith(("http://", "https://")):
            return False, "URL must start with http:// or https://"
        blocked = ["localhost", "127.0.0.1", "169.254", "10.", "192.168", "metadata.google"]
        for b in blocked:
            if b in url.lower():
                return False, f"Blocked URL pattern: {b}"
        return True, ""


def browse_page(url: str, wait_seconds: float = 2.0, extract: str = "text") -> dict:
    """Navigate to a URL and extract content from the rendered page.

    Args:
        url: The URL to visit
        wait_seconds: How long to wait for dynamic content (0-10s)
        extract: What to extract — "text", "html", or "both"

    Returns: {"success": bool, "text": str, "html": str, "title": str, "url": str}
    """
    safe, reason = _validate_url(url)
    if not safe:
        return {"success": False, "error": f"URL blocked: {reason}"}

    wait_seconds = max(0, min(10, wait_seconds))

    # Try Playwright first
    if _check_playwright_available():
        return _browse_playwright(url, wait_seconds, extract)

    # Fallback: use subprocess with a simple script
    return _browse_subprocess(url, wait_seconds, extract)


def take_screenshot(url: str, wait_seconds: float = 2.0, full_page: bool = False) -> dict:
    """Take a screenshot of a web page.

    Returns: {"success": bool, "path": str, "filename": str}
    """
    safe, reason = _validate_url(url)
    if not safe:
        return {"success": False, "error": f"URL blocked: {reason}"}

    wait_seconds = max(0, min(10, wait_seconds))
    filename = f"screenshot_{int(time.time())}.png"
    filepath = str(OUTPUT_DIR / filename)

    script = f"""
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={{"width": 1280, "height": 800}})
        await page.goto("{url}", wait_until="networkidle", timeout=30000)
        await asyncio.sleep({wait_seconds})
        await page.screenshot(path="{filepath}", full_page={str(full_page)})
        await browser.close()

asyncio.run(main())
"""

    try:
        result = subprocess.run(
            ["python3", "-c", script],
            capture_output=True, text=True, timeout=45,
        )
        if result.returncode == 0 and Path(filepath).exists():
            return {
                "success": True,
                "path": filepath,
                "filename": filename,
                "size_bytes": Path(filepath).stat().st_size,
            }
        return {"success": False, "error": result.stderr[:500]}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Screenshot timed out (45s)"}
    except Exception as e:
        return {"success": False, "error": str(e)[:300]}


def fill_form(url: str, fields: dict, submit_selector: str = "") -> dict:
    """Navigate to a URL, fill form fields, optionally submit.

    Args:
        url: Page URL
        fields: Dict of {selector: value} — e.g., {"#email": "test@test.com"}
        submit_selector: CSS selector of submit button (optional)

    Returns: {"success": bool, "page_text": str}
    """
    safe, reason = _validate_url(url)
    if not safe:
        return {"success": False, "error": f"URL blocked: {reason}"}

    fields_json = json.dumps(fields)
    submit_line = f'await page.click("{submit_selector}")' if submit_selector else ""

    script = f"""
import asyncio, json
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto("{url}", wait_until="networkidle", timeout=30000)
        fields = json.loads('{fields_json}')
        for selector, value in fields.items():
            await page.fill(selector, value)
        {submit_line}
        await asyncio.sleep(2)
        text = await page.inner_text("body")
        print(text[:{MAX_TEXT_CHARS}])
        await browser.close()

asyncio.run(main())
"""

    try:
        result = subprocess.run(
            ["python3", "-c", script],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return {"success": True, "page_text": result.stdout[:MAX_TEXT_CHARS]}
        return {"success": False, "error": result.stderr[:500]}
    except Exception as e:
        return {"success": False, "error": str(e)[:300]}


def _browse_playwright(url: str, wait_seconds: float, extract: str) -> dict:
    """Browse using Playwright library directly."""
    script = f"""
import asyncio
from playwright.async_api import async_playwright
import json

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto("{url}", wait_until="networkidle", timeout=30000)
        await asyncio.sleep({wait_seconds})
        title = await page.title()
        text = await page.inner_text("body") if "{extract}" in ("text", "both") else ""
        html = await page.content() if "{extract}" in ("html", "both") else ""
        result = {{"title": title, "text": text[:{MAX_TEXT_CHARS}], "html": html[:{MAX_TEXT_CHARS}], "url": page.url}}
        print(json.dumps(result))
        await browser.close()

asyncio.run(main())
"""

    try:
        result = subprocess.run(
            ["python3", "-c", script],
            capture_output=True, text=True, timeout=30 + int(wait_seconds),
        )
        if result.returncode == 0:
            data = json.loads(result.stdout.strip())
            data["success"] = True
            return data
        return {"success": False, "error": result.stderr[:500]}
    except json.JSONDecodeError:
        return {"success": False, "error": "Failed to parse browser output"}
    except Exception as e:
        return {"success": False, "error": str(e)[:300]}


def _browse_subprocess(url: str, wait_seconds: float, extract: str) -> dict:
    """Fallback: use web_fetch for static content extraction."""
    try:
        from app.tools.web_fetch import fetch_url
        text = fetch_url(url)
        return {
            "success": True,
            "text": text[:MAX_TEXT_CHARS],
            "html": "",
            "title": "",
            "url": url,
            "note": "Playwright unavailable — used static fetch fallback",
        }
    except Exception as e:
        return {"success": False, "error": f"Fallback fetch failed: {e}"}
