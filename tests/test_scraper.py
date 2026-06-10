"""Tests for the trending scraper."""
from scrapers.trending_scraper import TrendingScraper
from processors.data_processor import DataProcessor
from exporters.feishu_exporter import FeishuExporter


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


def test_data_processor_deduplicates_and_summarizes():
    """Test processor cleans duplicate tweets and computes summary counts."""
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
                            "created_at": "2026-06-10T00:00:00Z",
                            "likes": "1.2K",
                            "retweets": "10",
                            "replies": "2",
                            "views": "3K",
                            "media_urls": ["https://example.com/a.jpg"],
                            "tweet_url": "https://x.com/u/status/1",
                        },
                        {
                            "author": "creator",
                            "content": "Duplicate",
                            "created_at": "2026-06-10T00:00:00Z",
                            "tweet_url": "https://x.com/u/status/1",
                        },
                    ],
                }
            ]
        }
    }

    result = processor.process(raw_data)

    tweets = result["countries"]["Global"]["Technology"][0]["tweets"]
    assert len(tweets) == 1
    assert tweets[0]["likes"] == 1200
    assert result["summary"]["tweet_count"] == 1


def test_feishu_formatter_builds_post_message():
    """Test Feishu exporter creates rich post payloads."""
    exporter = FeishuExporter(webhook_url="https://example.com/webhook")
    message = exporter._format_message(
        {
            "timestamp": "2026-06-10T08:30:00+08:00",
            "summary": {
                "country_count": 1,
                "category_count": 1,
                "trend_count": 1,
                "tweet_count": 0,
            },
            "countries": {"Global": {"Technology": [{"trending_term": "AI", "tweets": []}]}},
        }
    )

    assert message["msg_type"] == "post"
    assert "zh_cn" in message["content"]["post"]
