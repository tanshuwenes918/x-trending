#!/usr/bin/env python3
"""Export a Playwright storage state for X login reuse in GitHub Actions."""

import base64
import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))


def parse_args():
    parser = argparse.ArgumentParser(description="Export X login storage state.")
    parser.add_argument(
        "--persistent",
        action="store_true",
        help="Reuse a local Playwright Chrome profile so the X login can persist between exports.",
    )
    parser.add_argument(
        "--profile-dir",
        default="",
        help="Persistent profile directory. Defaults to outputs/x_playwright_profile.",
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()

    from config.settings import OUTPUT_DIR, X_EXPLORE_URL
    from scrapers.trending_scraper import ScrapeError, TrendingScraper

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    state_path = OUTPUT_DIR / "x_storage_state.json"
    b64_path = OUTPUT_DIR / "x_storage_state.b64.txt"
    profile_dir = Path(args.profile_dir) if args.profile_dir else OUTPUT_DIR / "x_playwright_profile"

    try:
        with sync_playwright() as playwright:
            browser = None
            context = None
            try:
                if args.persistent:
                    context = playwright.chromium.launch_persistent_context(
                        user_data_dir=str(profile_dir),
                        channel="chrome",
                        headless=False,
                        viewport={"width": 1400, "height": 1000},
                        locale="en-US",
                        timezone_id="Asia/Shanghai",
                    )
                    print(f"Using persistent profile: {profile_dir}")
                else:
                    browser = playwright.chromium.launch(headless=False)
                    context = browser.new_context(
                        viewport={"width": 1400, "height": 1000},
                        locale="en-US",
                        timezone_id="Asia/Shanghai",
                    )
                page = context.new_page()
                page.goto(X_EXPLORE_URL, wait_until="domcontentloaded", timeout=60000)
                print("Browser opened. Complete any X login or account verification in the browser.")
                print("Make sure the Global Trending page and its categories are available.")
                input("Press Enter here when ready to verify and export...")

                # Login/verification can leave the user on another page. Verify the
                # actual scraping destination before replacing any saved state.
                page.goto(X_EXPLORE_URL, wait_until="domcontentloaded", timeout=60000)
                TrendingScraper()._wait_for_global_trending_ready(page)
                target_path = urlparse(X_EXPLORE_URL).path.rstrip("/")
                if urlparse(page.url).path.rstrip("/") != target_path:
                    raise ScrapeError(
                        "X redirected away from the configured trending page; login state was not exported."
                    )
                if target_path == "/i/jf/global-trending" or target_path.startswith("/i/jf/global-trending/"):
                    try:
                        page.get_by_role(
                            "heading", name="global_trending_timeline", exact=True
                        ).wait_for(timeout=10000)
                    except PlaywrightTimeoutError as exc:
                        raise ScrapeError(
                            "X Global Trending timeline did not load; login state was not exported."
                        ) from exc
                state_bytes = json.dumps(context.storage_state(), ensure_ascii=False).encode("utf-8")
                encoded = base64.b64encode(state_bytes).decode("ascii")
            finally:
                try:
                    if context is not None:
                        context.close()
                finally:
                    if browser is not None:
                        browser.close()
    except (Exception, KeyboardInterrupt) as exc:
        print(f"Export failed: {exc}", file=sys.stderr)
        print(
            "No login state files were written. Complete login/verification, "
            "confirm Global Trending works, and run the export again.",
            file=sys.stderr,
        )
        return 1

    state_path.write_bytes(state_bytes)
    b64_path.write_text(encoded, encoding="utf-8")

    print(f"Saved storage state: {state_path}")
    print(f"Saved GitHub Secret value: {b64_path}")
    print("Create or update GitHub Secret X_STORAGE_STATE_B64 with the full content of the .b64.txt file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
