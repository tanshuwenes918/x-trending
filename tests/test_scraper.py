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
        """Set up test fixtures."""
        self.scraper = TrendingScraper()

    def test_scraper_initialization(self):
        """Test scraper initializes without errors."""
        assert self.scraper is not None

    def test_category_aliases_include_display_names(self):
        """Test compact category names map to visible X labels."""
        assert "Health & Fitness" in self.scraper._category_aliases("Health&Fitness")
        assert "Movies & TV" in self.scraper._category_aliases("movies&tv")
        assert "Nature & Outdoors" in self.scraper._category_aliases("nature&outdoors")

    def test_metric_values_parse_x_labels_and_visible_counts(self):
        """Test X metric labels and visible compact counts are parsed."""
        assert self.scraper._extract_metric_value("23.6K reposts. Repost", "repost") == 23600
        assert self.scraper._extract_metric_value("Likes 1,234", "like") == 1234
        assert self.scraper._extract_metric_value("18.9K", "view") == 18900
        assert self.scraper._extract_metric_value("1.2M views", "view") == 1200000
        assert self.scraper._extract_metric_value("75K likes", "like") == 75000


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
    assert "（" in tech_group["items"][0]["summary"]
    assert "）：" in tech_group["items"][0]["summary"]
    assert result["summary"]["eligible_tweet_count"] == 1
    assert len(result["top_actions"]) == 1


def test_llm_processor_uses_fallback_without_config():
    """Test missing LLM settings do not break the report when not required."""
    data = {"groups": [], "top_actions": []}
    result = LLMProcessor(api_key="", base_url="", model="", required=False).enrich(data)
    assert result["llm_used"] is False


def test_llm_processor_filters_sensitive_top_actions():
    """Test LLM top actions cannot point at sensitive-news source items."""
    processor = LLMProcessor(api_key="", base_url="", model="", required=False)
    data = {
        "groups": [
            {
                "name": "News / Society / Sensitive Topics",
                "items": [{"tweet_url": "https://x.com/news/status/1", "summary": "old"}],
            },
            {
                "name": "Viral Culture / Meme / Social Buzz",
                "items": [{"tweet_url": "https://x.com/meme/status/1", "summary": "old", "views": 1000}],
            },
        ],
        "top_actions": [],
    }
    processor._merge_result(
        data,
        {
            "groups": [],
            "top_actions": [
                {"title": "敏感新闻", "action": "做挑战", "tweet_url": "https://x.com/news/status/1"},
                {"title": "低风险梗图", "action": "做模板", "tweet_url": "https://x.com/meme/status/1"},
            ],
        },
    )

    assert data["top_actions"] == [
        {
            "title": "低风险梗图",
            "action": "做模板",
            "tweet_url": "https://x.com/meme/status/1",
            "likes": 0,
            "retweets": 0,
            "replies": 0,
            "views": 1000,
        }
    ]


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
                            "summary": "AI 音乐（工具热议）：创作者在讨论新工作流。",
                            "likes": 1200,
                            "retweets": 34,
                            "views": 56000,
                            "tweet_url": "https://x.com/u/status/1",
                        }
                    ],
                }
            ],
            "top_actions": [
                {
                    "title": "AI 音乐工作流",
                    "action": "做一个同款 AI 音乐模板，引导用户生成类似效果。",
                    "likes": 0,
                    "retweets": 34,
                    "views": 56000,
                    "tweet_url": "https://x.com/u/status/1",
                }
            ],
        }
    )

    content = message["content"]["post"]["zh_cn"]["content"]
    title = message["content"]["post"]["zh_cn"]["title"]
    flattened_text = "".join(part["text"] for block in content for part in block)

    assert message["msg_type"] == "post"
    assert title.startswith("X 趋势日报 | AI 视频 & AI 音乐")
    assert "X 趋势日报" not in flattened_text
    assert "摘要：" not in flattened_text
    assert "链接：" not in flattened_text
    assert "0赞" not in flattened_text
    assert "1.2K赞 + 34转 + 56K浏览" in flattened_text
    assert "34转 + 56K浏览" in flattened_text
