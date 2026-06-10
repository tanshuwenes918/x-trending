"""Data processing and transformation logic."""

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from config.settings import (
    CATEGORY_GROUPS,
    COUNTRIES,
    LOOKBACK_HOURS,
    MAX_ITEMS_PER_GROUP,
    TIMEZONE,
)

logger = logging.getLogger(__name__)


class DataProcessor:
    """Process scraped tweets into the daily operations report shape."""

    def process(self, raw_data: Dict[str, Dict[str, List[Dict[str, Any]]]]) -> Dict[str, Any]:
        """Process raw scraped data.

        Args:
            raw_data: Raw data from scraper.

        Returns:
            Processed data ready for LLM enrichment and Feishu export.
        """
        logger.info("Processing scraped data...")

        now = datetime.now(ZoneInfo(TIMEZONE))
        cutoff = now - timedelta(hours=LOOKBACK_HOURS)
        groups = self._empty_groups()
        seen_tweets = set()
        raw_tweet_count = 0

        for country, category_map in raw_data.items():
            if not isinstance(category_map, dict):
                logger.warning("Skipping invalid country payload: %s", country)
                continue

            for category, trends in category_map.items():
                group_name = self._group_for_category(category)
                if not group_name:
                    logger.info("Skipping unmapped category: %s", category)
                    continue

                for trend in trends or []:
                    if not isinstance(trend, dict):
                        continue

                    trend_term = self._clean_text(trend.get("trending_term") or trend.get("term"))
                    for tweet in trend.get("tweets", []):
                        raw_tweet_count += 1
                        cleaned = self._clean_tweet(tweet)
                        if not cleaned:
                            continue

                        tweet_time = self._parse_datetime(cleaned["created_at"])
                        if not tweet_time or tweet_time < cutoff:
                            continue

                        dedupe_key = cleaned["tweet_url"]
                        if dedupe_key in seen_tweets:
                            continue
                        seen_tweets.add(dedupe_key)

                        cleaned.update(
                            {
                                "country": country,
                                "source_category": category,
                                "trending_term": trend_term,
                                "score": self._score(cleaned),
                                "summary": self._fallback_summary(cleaned, trend_term),
                            }
                        )
                        groups[group_name]["items"].append(cleaned)

        for group in groups.values():
            group["items"] = sorted(
                group["items"],
                key=lambda item: item.get("score", 0),
                reverse=True,
            )[:MAX_ITEMS_PER_GROUP]

        group_list = list(groups.values())
        eligible_tweet_count = sum(len(group["items"]) for group in group_list)

        return {
            "timestamp": now.isoformat(),
            "timezone": TIMEZONE,
            "lookback_hours": LOOKBACK_HOURS,
            "countries": COUNTRIES,
            "groups": group_list,
            "top_actions": self._fallback_top_actions(group_list),
            "summary": {
                "country_count": len(COUNTRIES),
                "group_count": len(group_list),
                "raw_tweet_count": raw_tweet_count,
                "eligible_tweet_count": eligible_tweet_count,
                "max_items_per_group": MAX_ITEMS_PER_GROUP,
            },
        }

    def validate_tweet(self, tweet: Dict[str, Any]) -> bool:
        """Validate a tweet record."""
        required_fields = ["author", "content", "created_at", "tweet_url"]
        return all(tweet.get(field) for field in required_fields)

    def _empty_groups(self) -> Dict[str, Dict[str, Any]]:
        return {
            group_name: {
                "name": group_name,
                "source_categories": source_categories,
                "items": [],
            }
            for group_name, source_categories in CATEGORY_GROUPS.items()
        }

    def _group_for_category(self, category: str) -> Optional[str]:
        normalized = self._normalize(category)
        for group_name, source_categories in CATEGORY_GROUPS.items():
            if normalized in {self._normalize(item) for item in source_categories}:
                return group_name
        return None

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

    def _parse_datetime(self, value: str) -> Optional[datetime]:
        if not value:
            return None

        text = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            logger.debug("Could not parse tweet datetime: %s", value)
            return None

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
        return parsed.astimezone(ZoneInfo(TIMEZONE))

    def _score(self, tweet: Dict[str, Any]) -> float:
        views = tweet.get("views", 0)
        likes = tweet.get("likes", 0)
        retweets = tweet.get("retweets", 0)
        replies = tweet.get("replies", 0)

        if views:
            return views * 0.4 + likes * 2 + retweets * 4 + replies * 3
        return likes * 2 + retweets * 4 + replies * 3

    def _fallback_summary(self, tweet: Dict[str, Any], trend_term: str) -> str:
        headline = self._headline_from_content(tweet.get("content", "")) or "热门动态"
        topic = self._category_label(trend_term)
        description = self._fallback_description(tweet.get("content", ""))
        return self._truncate(f"{topic}（{headline}）：{description}", 120)

    def _fallback_top_actions(self, groups: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        candidates = []
        for group in groups:
            candidates.extend(group.get("items", []))

        actions = []
        for item in sorted(candidates, key=lambda tweet: tweet.get("score", 0), reverse=True):
            if len(actions) >= 5:
                break
            actions.append(
                {
                    "title": self._headline_from_content(item.get("content", ""))
                    or self._category_label(item.get("trending_term", "")),
                    "action": "把这条热度内容改成短视频模板或音乐生成挑战，用同款效果引导用户到平台生成。",
                    "tweet_url": item.get("tweet_url", ""),
                    "likes": item.get("likes", 0),
                    "retweets": item.get("retweets", 0),
                    "replies": item.get("replies", 0),
                    "views": item.get("views", 0),
                }
            )
        return actions

    def _headline_from_content(self, content: str) -> str:
        text = self._clean_text(content)
        if not text:
            return ""
        if self._is_mostly_ascii(text):
            return self._english_headline(text)

        separators = ["。", ".", "!", "?", "！", "？", "：", ":"]
        first_sentence = text
        for separator in separators:
            if separator in first_sentence:
                first_sentence = first_sentence.split(separator, 1)[0]
        return self._truncate(first_sentence, 28)

    def _english_headline(self, text: str) -> str:
        normalized = self._clean_text(text)
        introducing_match = re.search(
            r"\b(?:introducing|launching|announcing|released?|new trailer for)\s+(.+?)(?:[:.!?]|$)",
            normalized,
            flags=re.IGNORECASE,
        )
        if introducing_match:
            subject = self._truncate(introducing_match.group(1).strip(" \"'"), 18)
            return f"{subject}新消息" if subject else "海外新品发布"

        brand_match = re.search(r"\b([A-Z][A-Za-z0-9&.-]*(?:\s+[A-Z][A-Za-z0-9&.-]*){0,3})\b", normalized)
        if brand_match:
            subject = self._truncate(brand_match.group(1), 18)
            return f"{subject}热度上升"

        return "海外热帖发酵"

    def _fallback_description(self, content: str) -> str:
        text = self._clean_text(content)
        if not text:
            return "该推文热度较高，可作为今日内容素材观察。"

        lowered = text.lower()
        if any(keyword in lowered for keyword in ["launch", "introducing", "released", "trailer", "announced"]):
            return "原推文发布了一个新产品、新预告或新消息，适合观察用户对新鲜内容的反应。"
        if any(keyword in lowered for keyword in ["ai", "model", "tool", "app", "workflow"]):
            return "原推文讨论 AI、工具或创作流程变化，适合评估能否转成生成式内容素材。"
        if any(keyword in lowered for keyword in ["music", "song", "dance", "video", "movie", "anime"]):
            return "原推文围绕音乐、视频或娱乐内容发酵，适合作为站外短内容参考。"
        if any(keyword in lowered for keyword in ["meme", "viral", "trend"]):
            return "原推文呈现社媒热点或梗传播，适合观察是否能做成低门槛二创模板。"
        return "原推文正在获得较高互动，可作为今日热点素材池候选。"

    def _category_label(self, value: str) -> str:
        labels = {
            "technology": "科技趋势",
            "science": "科学话题",
            "business&finance": "商业科技",
            "cryptocurrency": "加密话题",
            "music": "音乐热点",
            "dance": "舞蹈热点",
            "celebrity": "明星娱乐",
            "movies&tv": "影视话题",
            "anime": "动漫话题",
            "meme": "梗文化",
            "relationship": "社交关系",
            "fashion": "时尚话题",
            "beauty": "美妆话题",
            "food": "美食话题",
            "pets": "宠物话题",
            "gaming": "游戏热点",
            "sports": "体育热点",
            "cars": "汽车话题",
            "travel": "旅行话题",
            "nature&outdoors": "户外话题",
            "health&fitness": "健康健身",
            "home&garden": "家居生活",
            "news": "新闻社会",
            "religion": "宗教社会",
        }
        return labels.get(self._normalize(value), self._clean_text(value) or "热门话题")

    def _is_mostly_ascii(self, text: str) -> bool:
        if not text:
            return False
        ascii_count = sum(1 for char in text if ord(char) < 128)
        return ascii_count / len(text) > 0.75

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

    def _normalize(self, value: str) -> str:
        return self._clean_text(value).lower().replace(" ", "")

    def _truncate(self, text: str, limit: int) -> str:
        value = self._clean_text(text)
        if len(value) <= limit:
            return value
        return value[: limit - 3] + "..."
