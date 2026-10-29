"""Cover homepage capture and textless image/video sources without network IO."""

import json
from unittest.mock import MagicMock

import pytest

import config.settings as settings
import main as entrypoint
from scrapers.trending_scraper import ScrapeError, TrendingScraper
from tests.test_scrape_health import bind_browser, make_page


def test_homepage_is_collected_before_category_navigation(monkeypatch):
    scraper = TrendingScraper()
    page = make_page(ready=True)
    bind_browser(monkeypatch, scraper, page)
    events = []
    image_tweet = {"tweet_url": "https://x.com/athlete/status/1", "author": "athlete", "created_at": "2026-10-28T05:00:00Z", "content": "", "media_types": ["image"]}

    def collect(page):
        events.append("home")
        return [image_tweet]

    def category(country, name, **kwargs):
        events.append(name)
        return [{"trending_term": name, "tweets": [image_tweet]}] if name == "Technology" else []

    monkeypatch.setattr(scraper, "_collect_category_tweets", collect)
    monkeypatch.setattr(scraper, "scrape_country_category", category)

    result = scraper.scrape_all()

    assert events == ["home", "Technology", "Music"]
    home_tweets = result["Global"][settings.HOMEPAGE_CATEGORY][0]["tweets"]
    assert home_tweets == [{**image_tweet, "homepage_rank": 1}]


def test_missing_homepage_does_not_silently_publish_category_only_report(monkeypatch):
    scraper = TrendingScraper()
    page = make_page(ready=True)
    browser = bind_browser(monkeypatch, scraper, page)
    monkeypatch.setattr(scraper, "_collect_category_tweets", lambda page: [])
    category = MagicMock()
    monkeypatch.setattr(scraper, "scrape_country_category", category)

    with pytest.raises(ScrapeError, match="homepage"):
        scraper.scrape_all()

    category.assert_not_called()
    browser.close.assert_called_once()


@pytest.mark.parametrize("created_at", ["", "not-a-date"])
def test_homepage_with_unusable_metadata_does_not_silently_publish(monkeypatch, created_at):
    scraper = TrendingScraper()
    page = make_page(ready=True)
    bind_browser(monkeypatch, scraper, page)
    monkeypatch.setattr(scraper, "_collect_category_tweets", lambda page: [{
        "tweet_url": "https://x.com/u/status/1", "author": "u",
        "created_at": created_at, "media_types": ["image"],
    }])

    with pytest.raises(ScrapeError, match="metadata"):
        scraper.scrape_all()


def test_collector_keeps_textless_images_and_videos_in_feed_order(monkeypatch):
    scraper = TrendingScraper()
    page = make_page(ready=True, tweets=3)
    samples = [
        {"tweet_url": "https://x.com/u/status/1", "content": "", "media_urls": ["https://pbs.twimg.com/media/a.jpg"]},
        {"tweet_url": "https://x.com/u/status/2", "content": "", "media_types": ["video"]},
        {"tweet_url": "https://x.com/u/status/3", "content": "", "media_urls": []},
    ]
    page.locator('article[data-testid="tweet"]').all.return_value = samples
    monkeypatch.setattr(settings, "SCROLLS_PER_CATEGORY", 0)
    monkeypatch.setattr(scraper, "_extract_tweet", lambda article: article.copy())

    assert scraper._collect_category_tweets(page) == samples[:2]


def test_video_player_is_media_even_without_downloadable_video_url():
    article = MagicMock()
    article.locator.return_value.count.return_value = 0
    video_player = MagicMock()
    video_player.count.return_value = 1
    article.locator.side_effect = lambda selector: video_player if selector == 'video, [data-testid="videoPlayer"]' else MagicMock(count=lambda: 0)

    assert TrendingScraper()._extract_media_types(article) == ["video"]


def test_generic_image_alt_is_not_treated_as_image_description():
    article = MagicMock()
    images = []
    for text in ("Image", "图像", "  A stadium at night  ", "A stadium at night"):
        image = MagicMock()
        image.count.return_value = 1
        image.get_attribute.return_value = text
        images.append(image)
    article.locator.return_value.all.return_value = images

    assert TrendingScraper()._extract_media_alt_texts(article) == ["A stadium at night"]


def test_original_candidates_are_saved_before_filtering(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
    raw_data = {"Global": {"Sports": [{"tweets": [{"tweet_url": "https://x.com/u/status/1", "content": ""}]}]}}

    saved = entrypoint.write_raw_candidates(raw_data)

    assert saved.name.startswith("raw_candidates_")
    assert json.loads(saved.read_text())["raw_data"] == raw_data
