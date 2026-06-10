"""Tests for the X trending pipeline."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from exporters.feishu_exporter import FeishuExporter
from processors.data_processor import DataProcessor
from processors.llm_processor import LLMProcessor
from scrapers.trending_scraper import TrendingScraper


class TestTrendingScraper:
    """Test cases for TrendingScraper."""

    def setup_method(self):
        """Setup test fixtures."""
        self.scraper = TrendingScraper()

    def test_scraper_initialization(self):
        """Test scraper initializes without errors."""
        assert self.scraper is not None

    def test_scrape_all_returns_dict(self, monkeypatch):
        """Test scrape_all returns a dictionary."""
        import config.settings as settings

        monkeypatch.setattr(settings, "COUNTRIES", ["Global"])
        monkeypatch.setattr(settings, "CATEGORIES", ["Technology"])
        monkeypatch.setattr(
            self.scraper,
            "scrape_country_category",
            lambda country, category: [{"trending_term": "AI", "tweets": []}],
        )

        result = self.scraper.scrape_all()
        assert isinstance(result, dict)
        assert result["Global"]["Technology"][0]["trending_term"] == "AI"


def test_data_processor_groups_and_ranks_recent_tweets():
    """Test processor maps source categories into operating groups."""
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    recent = (now - timedelta(hours=1)).astimezone(ZoneInfo("UTC")).isoformat()
    old = (now - timedelta(hours=30)).astimezone(ZoneInfo("UTC")).isoformat()
    processor = DataProcessor()
    raw_data = {
        "Global": {
            "Technology": [
                {
                    "trending_term": "AI music",
                    "tweets": [
                        {
                            "author": "creator",
                            "content": "New AI music workflow",
                            "created_at": recent,
                            "likes": "1.2K",
                            "retweets": "10",
                            "replies": "2",
                            "views": "3K",
                            "media_urls": ["https://example.com/a.jpg"],
                            "tweet_url": "https://x.com/u/status/1",
                        },
                        {
                            "author": "old",
                            "content": "Old tweet",
                            "created_at": old,
                            "tweet_url": "https://x.com/u/status/2",
                        },
                    ],
                }
            ]
        }
    }

    result = processor.process(raw_data)
    tech_group = result["groups"][0]

    assert tech_group["name"] == "AI / Tech / Creator Tools"
    assert len(tech_group["items"]) == 1
    assert tech_group["items"][0]["likes"] == 1200
    assert tech_group["items"][0]["summary"]
    assert result["summary"]["eligible_tweet_count"] == 1
    assert len(result["top_actions"]) == 1


def test_llm_processor_uses_fallback_without_config():
    """Test missing LLM settings do not break the report."""
    data = {"groups": [], "top_actions": []}
    result = LLMProcessor(api_key="", base_url="", model="").enrich(data)
    assert result["llm_used"] is False


def test_feishu_formatter_builds_daily_report():
    """Test Feishu exporter creates the final daily report payload."""
    exporter = FeishuExporter(webhook_url="https://example.com/webhook")
    message = exporter._format_message(
        {
            "timestamp": "2026-06-10T08:30:00+08:00",
            "lookback_hours": 24,
            "countries": ["Global"],
            "llm_used": True,
            "summary": {
                "eligible_tweet_count": 1,
                "max_items_per_group": 7,
            },
            "groups": [
                {
                    "name": "AI / Tech / Creator Tools",
                    "items": [
                        {
                            "summary": "AI music tools are getting attention.",
                            "tweet_url": "https://x.com/u/status/1",
                        }
                    ],
                }
            ],
            "top_actions": [
                {
                    "title": "AI music workflow",
                    "action": "做成AI音乐生成模板，引导用户生成同款。",
                    "tweet_url": "https://x.com/u/status/1",
                }
            ],
        }
    )

    content = message["content"]["post"]["zh_cn"]["content"]
    assert message["msg_type"] == "post"
    assert "X 趋势日报" in content[0][0]["text"]
