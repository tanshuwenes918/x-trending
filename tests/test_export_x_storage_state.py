"""Login exports must not replace a usable state with a blocked session."""

import argparse
import base64
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from scripts import export_x_storage_state
from scrapers.trending_scraper import TrendingScraper


@pytest.fixture
def export_environment(monkeypatch, tmp_path):
    from config import settings

    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(settings, "X_EXPLORE_URL", "https://x.com/i/jf/global-trending/home")
    monkeypatch.setattr(export_x_storage_state, "load_dotenv", lambda: None)
    args = argparse.Namespace(persistent=False, profile_dir="")
    monkeypatch.setattr(export_x_storage_state, "parse_args", lambda: args)
    monkeypatch.setattr("builtins.input", lambda prompt: "")

    playwright_manager = MagicMock()
    playwright = playwright_manager.__enter__.return_value
    context = MagicMock()
    context.new_page.return_value.url = settings.X_EXPLORE_URL
    browser = playwright.chromium.launch.return_value
    browser.new_context.return_value = context
    playwright.chromium.launch_persistent_context.return_value = context
    context.storage_state.return_value = {"cookies": [], "origins": []}
    monkeypatch.setattr(export_x_storage_state, "sync_playwright", lambda: playwright_manager)

    verify_ready = Mock()
    monkeypatch.setattr(TrendingScraper, "_wait_for_global_trending_ready", verify_ready)
    return SimpleNamespace(
        args=args,
        context=context,
        browser=browser,
        verify_ready=verify_ready,
        page=context.new_page.return_value,
        state_path=tmp_path / "x_storage_state.json",
        b64_path=tmp_path / "x_storage_state.b64.txt",
    )


@pytest.mark.parametrize("existing_files", [False, True])
def test_blocked_export_does_not_write_or_replace_state(export_environment, existing_files, capsys):
    env = export_environment
    if existing_files:
        env.state_path.write_bytes(b"previous state")
        env.b64_path.write_bytes(b"previous encoded state")
    env.verify_ready.side_effect = RuntimeError("X page is blocked by Cloudflare/account access")

    assert export_x_storage_state.main() == 1

    if existing_files:
        assert env.state_path.read_bytes() == b"previous state"
        assert env.b64_path.read_bytes() == b"previous encoded state"
    else:
        assert not env.state_path.exists()
        assert not env.b64_path.exists()
    env.context.storage_state.assert_not_called()
    env.context.close.assert_called_once()
    env.browser.close.assert_called_once()
    assert "No login state files were written" in capsys.readouterr().err


@pytest.mark.parametrize("persistent", [False, True])
def test_verified_export_writes_matching_json_and_base64(export_environment, persistent):
    env = export_environment
    env.args.persistent = persistent

    assert export_x_storage_state.main() == 0

    assert json.loads(env.state_path.read_bytes()) == {"cookies": [], "origins": []}
    assert base64.b64decode(env.b64_path.read_text()) == env.state_path.read_bytes()
    env.verify_ready.assert_called_once_with(env.page)
    env.page.get_by_role.assert_called_once_with("heading", name="global_trending_timeline", exact=True)
    env.context.close.assert_called_once()
    if persistent:
        env.browser.close.assert_not_called()
    else:
        env.browser.close.assert_called_once()


def test_home_redirect_does_not_replace_state_even_when_tweets_are_ready(export_environment, capsys):
    env = export_environment
    env.page.url = "https://x.com/home"
    env.state_path.write_bytes(b"previous state")
    env.b64_path.write_bytes(b"previous encoded state")

    assert export_x_storage_state.main() == 1

    env.verify_ready.assert_called_once_with(env.page)
    env.context.storage_state.assert_not_called()
    assert env.state_path.read_bytes() == b"previous state"
    assert env.b64_path.read_bytes() == b"previous encoded state"
    env.context.close.assert_called_once()
    env.browser.close.assert_called_once()
    assert "redirected away" in capsys.readouterr().err


def test_missing_global_heading_does_not_replace_state(export_environment, capsys):
    env = export_environment
    env.page.get_by_role.return_value.wait_for.side_effect = PlaywrightTimeoutError("Heading not found")
    env.state_path.write_bytes(b"previous state")
    env.b64_path.write_bytes(b"previous encoded state")

    assert export_x_storage_state.main() == 1

    env.page.get_by_role.assert_called_once_with("heading", name="global_trending_timeline", exact=True)
    env.context.storage_state.assert_not_called()
    assert env.state_path.read_bytes() == b"previous state"
    assert env.b64_path.read_bytes() == b"previous encoded state"
    env.context.close.assert_called_once()
    env.browser.close.assert_called_once()
    assert "Global Trending timeline did not load" in capsys.readouterr().err


@pytest.mark.parametrize("persistent", [False, True])
def test_navigation_timeout_closes_resources_without_export(export_environment, persistent):
    env = export_environment
    env.args.persistent = persistent
    env.page.goto.side_effect = PlaywrightTimeoutError("Navigation timed out")

    assert export_x_storage_state.main() == 1

    env.verify_ready.assert_not_called()
    env.context.storage_state.assert_not_called()
    assert not env.state_path.exists()
    assert not env.b64_path.exists()
    env.context.close.assert_called_once()
    if persistent:
        env.browser.close.assert_not_called()
    else:
        env.browser.close.assert_called_once()
