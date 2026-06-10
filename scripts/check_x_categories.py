"""Check whether X Global Trending categories are clickable with Playwright.

This script is for local diagnostics only. It reads X cookies from `.env`,
opens the Global Trending page, clicks each configured category, and writes a
small JSON report under `outputs/`.
"""

from __future__ import annotations

import json
import re
import sys
import argparse
from datetime import datetime
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))
load_dotenv(project_root / ".env", override=True)

from config.settings import CATEGORIES, SCROLLS_PER_CATEGORY, X_COOKIES, X_EXPLORE_URL


def parse_cookies(raw: str) -> List[Dict[str, Any]]:
    jar = SimpleCookie()
    jar.load(raw)
    cookies = []
    for name, morsel in jar.items():
        if not morsel.value:
            continue
        cookies.append(
            {
                "name": name,
                "value": morsel.value,
                "domain": ".x.com",
                "path": "/",
                "secure": True,
                "sameSite": "Lax",
            }
        )
    return cookies


def find_category_locator(page, category: str):
    exact = page.get_by_text(category, exact=True)
    if exact.count() > 0:
        return exact.first

    fuzzy = page.get_by_text(re.compile(re.escape(category), re.IGNORECASE))
    if fuzzy.count() > 0:
        return fuzzy.first

    return None


def scroll_horizontal_candidates(page) -> None:
    page.evaluate(
        """
        () => {
          for (const el of document.querySelectorAll('div')) {
            if (el.scrollWidth > el.clientWidth + 50) {
              el.scrollLeft = el.scrollWidth;
            }
          }
        }
        """
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Check X Global Trending category clicks.")
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show the browser window while checking categories.",
    )
    parser.add_argument(
        "--use-chrome",
        action="store_true",
        help="Launch the installed Chrome channel instead of bundled Chromium.",
    )
    args = parser.parse_args()

    cookies = parse_cookies(X_COOKIES)
    output_dir = Path("outputs")
    output_dir.mkdir(exist_ok=True)

    results = []
    print(f"URL: {X_EXPLORE_URL}")
    print(f"Categories: {len(CATEGORIES)}")
    print(f"Cookies parsed: {len(cookies)}")

    with sync_playwright() as playwright:
        launch_kwargs = {"headless": not args.headed}
        if args.use_chrome:
            launch_kwargs["channel"] = "chrome"

        try:
            browser = playwright.chromium.launch(**launch_kwargs)
        except Exception:
            if not args.use_chrome:
                raise
            print("Installed Chrome channel failed; falling back to bundled Chromium.")
            browser = playwright.chromium.launch(headless=not args.headed)
        context = browser.new_context(
            viewport={"width": 1400, "height": 1000},
            locale="en-US",
            timezone_id="Asia/Shanghai",
        )
        if cookies:
            context.add_cookies(cookies)

        page = context.new_page()
        page.goto(X_EXPLORE_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(8000)

        initial = {
            "loaded_url": page.url,
            "login_markers": page.locator("text=/Log in|Sign in|登录|登入/i").count(),
            "popular_today_visible": page.locator("text=Popular today").count(),
            "initial_articles": page.locator('article[data-testid="tweet"]').count(),
        }
        print(json.dumps(initial, ensure_ascii=False, indent=2))

        for category in CATEGORIES:
            result = {
                "category": category,
                "status": "unknown",
                "popular_today_visible": 0,
                "articles": 0,
                "url": page.url,
            }
            try:
                locator = find_category_locator(page, category)
                if locator is None:
                    scroll_horizontal_candidates(page)
                    page.wait_for_timeout(1000)
                    locator = find_category_locator(page, category)

                if locator is None:
                    result["status"] = "not_found"
                else:
                    locator.scroll_into_view_if_needed(timeout=5000)
                    locator.click(timeout=8000)
                    page.wait_for_timeout(5000)
                    seen = set()
                    for _ in range(SCROLLS_PER_CATEGORY + 1):
                        for article in page.locator('article[data-testid="tweet"]').all():
                            href = ""
                            try:
                                href = article.locator('a[href*="/status/"]').first.get_attribute("href", timeout=1000) or ""
                            except Exception:
                                href = ""
                            if href:
                                seen.add(href)
                        page.mouse.wheel(0, 1200)
                        page.wait_for_timeout(1200)

                    result["popular_today_visible"] = page.locator("text=Popular today").count()
                    result["articles"] = len(seen)
                    result["url"] = page.url
                    result["status"] = "ok" if result["articles"] > 0 else "clicked_no_articles"
            except PlaywrightTimeoutError as exc:
                result["status"] = f"timeout: {str(exc).splitlines()[0][:100]}"
            except Exception as exc:
                result["status"] = f"{type(exc).__name__}: {str(exc)[:100]}"

            results.append(result)
            print(
                "{category}\t{status}\tpopular={popular}\tarticles={articles}".format(
                    category=result["category"],
                    status=result["status"],
                    popular=result["popular_today_visible"],
                    articles=result["articles"],
                )
            )

        browser.close()

    report = {
        "timestamp": datetime.now().isoformat(),
        "initial": initial,
        "results": results,
    }
    output_path = output_dir / f"playwright_category_check_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved report: {output_path}")


if __name__ == "__main__":
    main()
