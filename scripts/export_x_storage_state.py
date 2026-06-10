#!/usr/bin/env python3
"""Export a Playwright storage state for X login reuse in GitHub Actions."""

import base64
import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

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

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    state_path = OUTPUT_DIR / "x_storage_state.json"
    b64_path = OUTPUT_DIR / "x_storage_state.b64.txt"
    profile_dir = Path(args.profile_dir) if args.profile_dir else OUTPUT_DIR / "x_playwright_profile"

    with sync_playwright() as playwright:
        if args.persistent:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                channel="chrome",
                headless=False,
                viewport={"width": 1400, "height": 1000},
                locale="en-US",
                timezone_id="Asia/Shanghai",
            )
            browser = None
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
        print("Browser opened. Log in to X if needed, then make sure the trending page loads.")
        input("Press Enter here after X is logged in and ready...")
        context.storage_state(path=str(state_path))
        context.close()
        if browser:
            browser.close()

    encoded = base64.b64encode(state_path.read_bytes()).decode("ascii")
    b64_path.write_text(encoded, encoding="utf-8")

    print(f"Saved storage state: {state_path}")
    print(f"Saved GitHub Secret value: {b64_path}")
    print("Create or update GitHub Secret X_STORAGE_STATE_B64 with the full content of the .b64.txt file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
