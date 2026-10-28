"""Regressions for blocked X pages and empty daily reports.

These tests keep browser, LLM, and Feishu traffic offline while exercising the
failure paths that previously sent a successful-looking, empty daily report.
"""

import argparse
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

import config.settings as settings
import main as entrypoint
import scrapers.trending_scraper as scraper_module
from scrapers.trending_scraper import TrendingScraper


def make_page(*, url="https://x.com/i/jf/global-trending", body="", ready=False, tweets=0, title="X"):
    """Provide only the Playwright surfaces used to inspect page readiness."""
    page = MagicMock()
    page.url = url
    page.title.return_value = title
    page.inner_text.return_value = body
    page.content.return_value = f"<html><title>{title}</title><body>{body}</body></html>"
    page.evaluate.return_value = body

    body_locator = MagicMock()
    body_locator.first = body_locator
    body_locator.count.return_value = 1
    body_locator.inner_text.return_value = body
    body_locator.text_content.return_value = body

    tweet_locator = MagicMock()
    tweet_locator.count.return_value = tweets
    tweet_locator.all.return_value = []
    empty_locator = MagicMock()
    empty_locator.count.return_value = 0
    empty_locator.all.return_value = []

    def locator_for(selector):
        if selector == 'article[data-testid="tweet"]':
            return tweet_locator
        if selector == "body":
            return body_locator
        return empty_locator

    page.locator.side_effect = locator_for

    ready_locator = MagicMock()
    ready_locator.first = ready_locator
    ready_locator.count.return_value = int(ready)
    ready_locator.is_visible.return_value = ready
    if not ready:
        ready_locator.wait_for.side_effect = PlaywrightTimeoutError("Readiness marker did not appear")
    page.get_by_text.return_value = ready_locator
    return page


def bind_browser(monkeypatch, scraper, page):
    """Bind scrape_all to an offline browser and small category configuration."""
    browser = MagicMock()
    browser.new_context.return_value.new_page.return_value = page
    playwright = MagicMock()
    playwright.__enter__.return_value.chromium.launch.return_value = browser
    monkeypatch.setattr(scraper_module, "sync_playwright", MagicMock(return_value=playwright))
    monkeypatch.setattr(settings, "COUNTRIES", ["Global"])
    monkeypatch.setattr(settings, "CATEGORIES", ["Technology", "Music"])
    monkeypatch.setattr(scraper, "_storage_state", lambda: None)
    monkeypatch.setattr(scraper, "_playwright_cookies", lambda: [])
    return browser


@pytest.mark.parametrize(
    "category,simplified,traditional",
    [
        ("Technology", "技术", "科技"),
        ("News", "新闻", "新聞"),
        ("Business & Finance", "商业和金融", "商業與金融"),
        ("Science", "科学", "科學"),
        ("Travel", "旅游", "旅遊"),
        ("Gaming", "游戏竞技", "遊戲"),
        ("Sports", "体育", "體育"),
        ("Health & Fitness", "健康与健身", "健康與健身"),
        ("cryptocurrency", "加密货币", "加密貨幣"),
        ("cars", "汽车", "汽車"),
        ("music", "音乐", "音樂"),
        ("dance", "舞曲", "舞蹈"),
        ("celebrity", "明星", "名人"),
        ("relationship", "关系", "人際關係"),
        ("Movies & TV", "电影与电视", "電影與電視"),
        ("Nature & Outdoors", "自然与户外", "自然與戶外"),
        ("food", "美食", "美食"),
        ("meme", "表情包", "迷因"),
        ("beauty", "美容", "美容"),
        ("Pets", "宠物", "寵物"),
        ("fashion", "时尚", "時尚"),
        ("anime", "动漫", "日本動畫"),
        ("religion", "宗教", "宗教"),
        ("Home & Garden", "家居与园艺", "居家和園藝"),
    ],
)
@pytest.mark.parametrize("locale", ["simplified", "traditional"])
def test_english_source_category_clicks_localized_chinese_button(category, simplified, traditional, locale):
    """A localized X account can use the existing English source categories."""
    button_label = simplified if locale == "simplified" else traditional
    page = make_page(ready=True)
    button = MagicMock()
    button.count.return_value = 1
    button.nth.return_value = button
    button.evaluate.return_value = True
    missing_button = MagicMock()
    missing_button.count.return_value = 0

    def find_visible_label(text, **kwargs):
        matches = text == button_label if isinstance(text, str) else text.search(button_label)
        return button if matches else missing_button

    page.get_by_text.side_effect = find_visible_label

    assert TrendingScraper()._click_category(page, category) is True

    button.click.assert_called_once()


@pytest.mark.parametrize(
    "url,body,title",
    [
        ("https://x.com/account/access", "", "X"),
        ("https://x.com/i/flow/login", "Log in to X", "X"),
        ("https://x.com/login", "Log in to X", "X"),
        ("https://x.com/i/flow/onboarding", "", "X"),
        (
            "https://x.com/i/jf/global-trending",
            "Verify you are human. Cloudflare Ray ID: 1234. Performance & security by Cloudflare.",
            "Just a moment...",
        ),
        ("https://x.com/i/jf/global-trending", "Cloudflare\nPrivacy\nTerms", "X"),
    ],
)
def test_blocked_landing_page_stops_before_category_loop_and_closes_browser(monkeypatch, url, body, title):
    scraper = TrendingScraper()
    page = make_page(url=url, body=body, title=title)
    browser = bind_browser(monkeypatch, scraper, page)
    scrape_category = MagicMock()
    monkeypatch.setattr(scraper, "scrape_country_category", scrape_category)

    with pytest.raises(scraper_module.ScrapeError):
        scraper.scrape_all()

    scrape_category.assert_not_called()
    browser.close.assert_called_once()


def test_unready_page_fails_after_one_reload():
    page = make_page(body="Loading...")

    with pytest.raises(scraper_module.ScrapeError):
        TrendingScraper()._wait_for_global_trending_ready(page)

    page.reload.assert_called_once()


@pytest.mark.parametrize("ready,tweets", [(True, 0), (False, 1)])
def test_valid_trending_page_passes_without_reloading(ready, tweets):
    page = make_page(body="Technology Music Popular today", ready=ready, tweets=tweets)

    TrendingScraper()._wait_for_global_trending_ready(page)

    page.reload.assert_not_called()


@pytest.mark.parametrize("marker", ["Popular today", "今日热门", "今日熱門"])
@pytest.mark.parametrize("wait_method", ["_wait_for_global_trending_ready", "_wait_for_popular_today"])
def test_localized_ready_marker_is_used_without_waiting_for_missing_english(marker, wait_method):
    page = make_page(body=marker)
    visible_marker = MagicMock()
    visible_marker.first = visible_marker
    missing_marker = MagicMock()
    missing_marker.first = missing_marker
    missing_marker.wait_for.side_effect = PlaywrightTimeoutError("This marker is not on the localized page")

    def find_marker(text, **kwargs):
        matches = text in marker if isinstance(text, str) else text.search(marker)
        return visible_marker if matches else missing_marker

    page.get_by_text.side_effect = find_marker

    getattr(TrendingScraper(), wait_method)(page)

    visible_marker.wait_for.assert_called_once()
    missing_marker.wait_for.assert_not_called()
    page.reload.assert_not_called()


def test_tweet_discussing_cloudflare_is_not_a_verification_page():
    page = make_page(body="Cloudflare explains why websites ask you to verify you are human", tweets=1)

    TrendingScraper()._wait_for_global_trending_ready(page)

    page.reload.assert_not_called()


@pytest.mark.parametrize("opened", [True, False])
def test_category_redirect_to_account_access_is_not_treated_as_empty(monkeypatch, opened):
    scraper = TrendingScraper()
    page = make_page(ready=True)

    def open_category(*args):
        page.url = "https://x.com/account/access"
        return opened

    monkeypatch.setattr(scraper, "_open_category", open_category)
    collect_tweets = MagicMock()
    monkeypatch.setattr(scraper, "_collect_category_tweets", collect_tweets)

    with pytest.raises(scraper_module.ScrapeError):
        scraper.scrape_country_category("Global", "Technology", page=page)

    collect_tweets.assert_not_called()


def test_direct_category_url_does_not_hide_blocking_redirect(monkeypatch):
    scraper = TrendingScraper()
    page = make_page(ready=True)
    monkeypatch.setattr(settings, "X_CATEGORY_URL_TEMPLATE", "https://x.com/i/jf/global-trending/{category_slug}")
    page.goto.side_effect = lambda *args, **kwargs: setattr(page, "url", "https://x.com/account/access")
    click_category = MagicMock()
    monkeypatch.setattr(scraper, "_click_category", click_category)

    with pytest.raises(scraper_module.ScrapeError):
        scraper.scrape_country_category("Global", "Technology", page=page)

    click_category.assert_not_called()


@pytest.mark.parametrize("category_data", [[], [{"trending_term": "Technology", "tweets": []}]])
def test_scrape_all_rejects_zero_tweets_and_closes_browser(monkeypatch, category_data):
    scraper = TrendingScraper()
    page = make_page(ready=True)
    browser = bind_browser(monkeypatch, scraper, page)
    monkeypatch.setattr(scraper, "scrape_country_category", MagicMock(return_value=category_data))

    with pytest.raises(scraper_module.ScrapeError):
        scraper.scrape_all()

    browser.close.assert_called_once()


def test_scrape_all_does_not_swallow_session_failure_in_category_loop(monkeypatch):
    scraper = TrendingScraper()
    page = make_page(ready=True)
    browser = bind_browser(monkeypatch, scraper, page)
    monkeypatch.setattr(scraper, "_collect_category_tweets", lambda page: [{"tweet_url": "https://x.com/u/status/1", "author": "creator", "created_at": "2026-10-28T05:00:00Z", "content": "Homepage"}])
    scrape_category = MagicMock(side_effect=scraper_module.ScrapeError("X session requires verification"))
    monkeypatch.setattr(scraper, "scrape_country_category", scrape_category)

    with pytest.raises(scraper_module.ScrapeError):
        scraper.scrape_all()

    scrape_category.assert_called_once()
    browser.close.assert_called_once()


def test_scrape_all_keeps_nonempty_results_and_closes_browser(monkeypatch):
    scraper = TrendingScraper()
    page = make_page(ready=True)
    browser = bind_browser(monkeypatch, scraper, page)
    monkeypatch.setattr(scraper, "_collect_category_tweets", lambda page: [{"tweet_url": "https://x.com/u/status/1", "author": "creator", "created_at": "2026-10-28T05:00:00Z", "content": "Homepage"}])
    category_data = [{"trending_term": "Technology", "tweets": [{"tweet_url": "https://x.com/u/status/1"}]}]
    monkeypatch.setattr(scraper, "scrape_country_category", MagicMock(return_value=category_data))

    result = scraper.scrape_all()

    assert result["Global"]["Technology"] == category_data
    browser.close.assert_called_once()


@pytest.fixture
def offline_pipeline(monkeypatch, tmp_path):
    """Observe pipeline effects without initializing any remote integrations."""
    import exporters.feishu_exporter as exporter_module
    import processors.data_processor as processor_module
    import processors.llm_processor as llm_module

    args = argparse.Namespace(output_format="both", dry_run=False, input_json="")
    monkeypatch.setattr(entrypoint, "parse_args", lambda: args)
    monkeypatch.setattr(entrypoint, "load_dotenv", MagicMock())
    monkeypatch.setattr(settings, "DRY_RUN", False)
    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)

    pipeline = MagicMock()
    pipeline.args = args
    pipeline.scraper.scrape_all.return_value = {
        "Global": {"Technology": [{"tweets": [{"tweet_url": "https://x.com/u/status/1"}]}]}
    }
    pipeline.processor.process.return_value = {
        "timestamp": "2026-10-23T14:00:00+08:00",
        "groups": [{"name": "AI / Tech / Creator Tools", "items": [{"tweet_url": "https://x.com/u/status/1"}]}],
        "top_actions": [],
    }
    pipeline.llm.enrich.side_effect = lambda report: {**report, "llm_used": True}
    pipeline.exporter.export.return_value = True
    pipeline.write.return_value = Path(tmp_path / "report.json")
    monkeypatch.setattr(scraper_module, "TrendingScraper", lambda: pipeline.scraper)
    monkeypatch.setattr(processor_module, "DataProcessor", lambda: pipeline.processor)
    monkeypatch.setattr(llm_module, "LLMProcessor", lambda: pipeline.llm)
    monkeypatch.setattr(exporter_module, "FeishuExporter", lambda: pipeline.exporter)
    monkeypatch.setattr(entrypoint, "write_json_output", pipeline.write)
    return pipeline


def assert_empty_run_stops(pipeline):
    with pytest.raises(SystemExit) as exc:
        entrypoint.main()

    assert exc.value.code == 1
    pipeline.llm.enrich.assert_not_called()
    pipeline.write.assert_not_called()
    pipeline.exporter.export.assert_not_called()
    pipeline.exporter.format_plain_text.assert_not_called()


@pytest.mark.parametrize("raw_data", [{}, {"Global": {"Technology": []}}, {"Global": {"Technology": [{"tweets": []}]}}])
def test_main_does_not_enrich_persist_or_send_empty_scrape(offline_pipeline, raw_data):
    offline_pipeline.scraper.scrape_all.return_value = raw_data
    offline_pipeline.processor.process.return_value = {"groups": [{"items": []}]}

    assert_empty_run_stops(offline_pipeline)


@pytest.mark.parametrize(
    "empty_report",
    [
        {},
        {"groups": []},
        {"groups": [{"name": "AI / Tech / Creator Tools", "items": []}], "summary": {"eligible_tweet_count": 100}},
    ],
)
def test_main_does_not_send_report_when_processing_filters_all_tweets(offline_pipeline, empty_report):
    offline_pipeline.processor.process.return_value = empty_report

    assert_empty_run_stops(offline_pipeline)


def test_main_does_not_send_empty_input_json(offline_pipeline, tmp_path):
    input_path = tmp_path / "empty-report.json"
    input_path.write_text(json.dumps({"groups": [{"items": []}], "top_actions": []}), encoding="utf-8")
    offline_pipeline.args.input_json = str(input_path)

    assert_empty_run_stops(offline_pipeline)

    offline_pipeline.scraper.scrape_all.assert_not_called()
    offline_pipeline.processor.process.assert_not_called()


def test_main_does_not_publish_report_if_enrichment_removes_all_items(offline_pipeline):
    offline_pipeline.llm.enrich.side_effect = None
    offline_pipeline.llm.enrich.return_value = {"groups": [{"items": []}]}

    with pytest.raises(SystemExit) as exc:
        entrypoint.main()

    assert exc.value.code == 1
    offline_pipeline.llm.enrich.assert_called_once()
    offline_pipeline.write.assert_not_called()
    offline_pipeline.exporter.export.assert_not_called()


@pytest.mark.parametrize("use_input_json", [False, True])
def test_main_enriches_persists_and_sends_nonempty_report(offline_pipeline, tmp_path, use_input_json):
    report = offline_pipeline.processor.process.return_value
    if use_input_json:
        input_path = tmp_path / "valid-report.json"
        input_path.write_text(json.dumps(report), encoding="utf-8")
        offline_pipeline.args.input_json = str(input_path)

    entrypoint.main()

    offline_pipeline.llm.enrich.assert_called_once_with(report)
    enriched_report = {**report, "llm_used": True}
    offline_pipeline.write.assert_called_once_with(enriched_report)
    offline_pipeline.exporter.export.assert_called_once_with(enriched_report)
    if use_input_json:
        offline_pipeline.scraper.scrape_all.assert_not_called()
        offline_pipeline.processor.process.assert_not_called()
    else:
        offline_pipeline.processor.process.assert_called_once_with(offline_pipeline.scraper.scrape_all.return_value)
