"""Recover localized category controls after scrolling a virtualized feed."""

from unittest.mock import MagicMock

import pytest
from playwright.sync_api import Error as PlaywrightError, sync_playwright

import config.settings as settings
from scrapers.trending_scraper import ScrapeError, TrendingScraper
from tests.test_scrape_health import bind_browser, make_page


def test_missing_rail_is_restored_once_before_clicking_localized_category(monkeypatch):
    scraper = TrendingScraper()
    page = make_page(ready=True, tweets=1)
    nav_ready = False
    button = MagicMock()
    button.count.return_value = 1
    button.nth.return_value = button
    button.evaluate.return_value = True
    missing = MagicMock()
    missing.count.return_value = 0

    def restore(url, **kwargs):
        nonlocal nav_ready
        assert url == settings.X_EXPLORE_URL
        nav_ready = True

    def find(text, **kwargs):
        match = text == "技术" if isinstance(text, str) else bool(text.search("技术"))
        return button if nav_ready and match else missing

    page.goto.side_effect = restore
    page.get_by_text.side_effect = find
    monkeypatch.setattr(scraper, "_assert_page_accessible", lambda page: None)
    ready = MagicMock()
    monkeypatch.setattr(scraper, "_wait_for_global_trending_ready", ready)

    assert scraper._click_category(page, "Technology") is True

    page.goto.assert_called_once_with(settings.X_EXPLORE_URL, wait_until="domcontentloaded", timeout=60000)
    ready.assert_called_once_with(page)
    button.click.assert_called_once()


def test_missing_category_after_navigation_recovery_is_not_reported_as_clicked(monkeypatch):
    scraper = TrendingScraper()
    page = make_page(ready=True)
    monkeypatch.setattr(scraper, "_find_text_locator", lambda page, text: None)

    assert scraper._click_category(page, "Technology") is False

    page.goto.assert_called_once()


def test_navigation_recovery_does_not_hide_account_verification(monkeypatch):
    scraper = TrendingScraper()
    page = make_page(ready=True)
    monkeypatch.setattr(scraper, "_find_text_locator", lambda page, text: None)
    page.goto.side_effect = lambda *args, **kwargs: setattr(page, "url", "https://x.com/account/access")

    with pytest.raises(ScrapeError, match="account/access"):
        scraper._click_category(page, "Technology")


def test_feed_text_match_is_skipped_in_favor_of_category_control():
    scraper = TrendingScraper()
    page = MagicMock()
    tweet_text = MagicMock()
    tweet_text.evaluate.return_value = False
    button = MagicMock()
    button.evaluate.return_value = True
    matches = MagicMock()
    matches.count.return_value = 2
    matches.nth.side_effect = [tweet_text, button]
    page.get_by_text.return_value = matches

    assert scraper._find_text_locator(page, "新闻") is button
    tweet_text.click.assert_not_called()


def test_exact_semantic_button_takes_priority_over_broad_text_matches():
    page = MagicMock()
    button = MagicMock()
    button.evaluate.return_value = True
    buttons = MagicMock()
    buttons.count.return_value = 1
    buttons.nth.return_value = button
    page.get_by_role.return_value = buttons

    assert TrendingScraper()._find_text_locator(page, "技术") is button

    page.get_by_role.assert_called_once_with("button", name="技术", exact=True)
    page.get_by_text.return_value.count.assert_not_called()


def test_only_homepage_data_is_rejected_before_it_can_be_a_full_report(monkeypatch):
    scraper = TrendingScraper()
    page = make_page(ready=True)
    browser = bind_browser(monkeypatch, scraper, page)
    monkeypatch.setattr(scraper, "_collect_category_tweets", lambda page: [{
        "tweet_url": "https://x.com/u/status/1", "author": "u", "content": "首页内容",
        "created_at": "2026-10-29T05:00:00Z",
    }])
    monkeypatch.setattr(scraper, "scrape_country_category", lambda *args, **kwargs: [])

    with pytest.raises(ScrapeError, match="Only the homepage"):
        scraper.scrape_all()

    browser.close.assert_called_once()


def test_real_dom_restores_nested_timeline_and_rejects_tweet_or_plain_text_matches():
    """Exercise the DOM predicates on a nested feed without accessing X."""
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=True)
        except PlaywrightError:
            try:
                browser = pw.chromium.launch(headless=True, channel="msedge")
            except PlaywrightError:
                pytest.skip("No test browser is installed")
        try:
            page = browser.new_page()
            page.set_content('''
              <main id="feed" style="height:150px;overflow-y:auto">
                <div role="button" id="country">全球 美国 阿尔及利亚</div>
                <button id="category">技术</button>
                <h2 id="heading">新闻</h2>
                <article data-testid="tweet"><button id="tweet">体育</button></article>
                <div style="height:2000px">Feed</div>
              </main>
            ''')
            page.locator("#feed").evaluate("el => el.scrollTop = 1200")
            assert page.locator("#feed").evaluate("el => el.scrollTop") > 0

            scraper = TrendingScraper()
            scraper._return_to_category_nav(page)

            assert page.locator("#feed").evaluate("el => el.scrollTop") == 0
            assert scraper._find_text_locator(page, "技术").get_attribute("id") == "category"
            assert scraper._find_text_locator(page, "新闻") is None
            assert scraper._find_text_locator(page, "体育") is None
        finally:
            browser.close()
