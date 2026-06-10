"""Data processing and transformation logic."""

import logging
from typing import Dict, List, Any
from datetime import datetime
from zoneinfo import ZoneInfo

from config.settings import MAX_TRENDS_PER_CATEGORY, MAX_TWEETS_PER_TREND, TIMEZONE

logger = logging.getLogger(__name__)


class DataProcessor:
    """Process and transform scraped trending data."""
    
    def process(self, raw_data: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
        """Process raw scraped data.
        
        Args:
            raw_data: Raw data from scraper
        
        Returns:
            Processed data ready for export
        """
        logger.info("Processing scraped data...")
        
        processed_data = {
            "timestamp": datetime.now(ZoneInfo(TIMEZONE)).isoformat(),
            "timezone": TIMEZONE,
            "countries": {},
            "summary": {
                "country_count": 0,
                "category_count": 0,
                "trend_count": 0,
                "tweet_count": 0,
            },
        }

        for country, category_map in raw_data.items():
            if not isinstance(category_map, dict):
                logger.warning("Skipping invalid country payload: %s", country)
                continue

            country_payload = {}
            for category, trends in category_map.items():
                cleaned_trends = self._clean_trends(trends)
                if not cleaned_trends:
                    continue

                country_payload[category] = cleaned_trends
                processed_data["summary"]["category_count"] += 1
                processed_data["summary"]["trend_count"] += len(cleaned_trends)
                processed_data["summary"]["tweet_count"] += sum(
                    len(trend.get("tweets", [])) for trend in cleaned_trends
                )

            if country_payload:
                processed_data["countries"][country] = country_payload

        processed_data["summary"]["country_count"] = len(processed_data["countries"])
        return processed_data
    
    def validate_tweet(self, tweet: Dict[str, Any]) -> bool:
        """Validate a tweet record.
        
        Args:
            tweet: Tweet data to validate
        
        Returns:
            True if valid, False otherwise
        """
        required_fields = ["author", "content", "created_at", "tweet_url"]
        return all(tweet.get(field) for field in required_fields)

    def _clean_trends(self, trends: Any) -> List[Dict[str, Any]]:
        if not isinstance(trends, list):
            return []

        cleaned = []
        seen_tweets = set()

        for trend in trends[:MAX_TRENDS_PER_CATEGORY]:
            if not isinstance(trend, dict):
                continue

            term = self._clean_text(trend.get("trending_term") or trend.get("term"))
            tweets = []

            for tweet in trend.get("tweets", [])[:MAX_TWEETS_PER_TREND]:
                cleaned_tweet = self._clean_tweet(tweet)
                if not cleaned_tweet:
                    continue

                dedupe_key = cleaned_tweet.get("tweet_url") or (
                    cleaned_tweet.get("author"),
                    cleaned_tweet.get("content"),
                )
                if dedupe_key in seen_tweets:
                    continue
                seen_tweets.add(dedupe_key)
                tweets.append(cleaned_tweet)

            if term or tweets:
                cleaned.append(
                    {
                        "trending_term": term,
                        "tweet_count": len(tweets),
                        "tweets": tweets,
                    }
                )

        return cleaned

    def _clean_tweet(self, tweet: Any) -> Dict[str, Any]:
        if not isinstance(tweet, dict):
            return {}

        cleaned = {
            "author": self._clean_text(tweet.get("author")),
            "content": self._clean_text(tweet.get("content")),
            "created_at": self._clean_text(tweet.get("created_at")),
            "likes": self._to_int(tweet.get("likes")),
            "retweets": self._to_int(tweet.get("retweets")),
            "replies": self._to_int(tweet.get("replies")),
            "views": self._to_int(tweet.get("views")),
            "media_urls": self._clean_urls(tweet.get("media_urls", [])),
            "tweet_url": self._clean_text(tweet.get("tweet_url")),
        }

        return cleaned if self.validate_tweet(cleaned) else {}

    def _clean_text(self, value: Any) -> str:
        if value is None:
            return ""
        return " ".join(str(value).split())

    def _clean_urls(self, value: Any) -> List[str]:
        if not isinstance(value, list):
            return []
        return [self._clean_text(url) for url in value if self._clean_text(url)]

    def _to_int(self, value: Any) -> int:
        if value is None or value == "":
            return 0
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)

        text = str(value).strip().replace(",", "")
        multiplier = 1
        if text.lower().endswith("k"):
            multiplier = 1_000
            text = text[:-1]
        elif text.lower().endswith("m"):
            multiplier = 1_000_000
            text = text[:-1]

        try:
            return int(float(text) * multiplier)
        except ValueError:
            return 0
