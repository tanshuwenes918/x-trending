"""Configuration settings for X Trending scraper."""

import os
from pathlib import Path
from typing import Dict, List


def _csv_env(name: str, default: List[str]) -> List[str]:
    value = os.getenv(name)
    if not value:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    return int(value)

# Countries to scrape
DEFAULT_COUNTRIES: List[str] = [
    "Global",
]

# Categories to scrape
DEFAULT_CATEGORIES: List[str] = [
    "Technology",
    "News",
    "Business & Finance",
    "Science",
    "Travel",
    "Gaming",
    "Sports",
    "Health & Fitness",
    "cryptocurrency",
    "cars",
    "music",
    "dance",
    "celebrity",
    "relationship",
    "Movies & TV",
    "Nature & Outdoors",
    "food",
    "meme",
    "beauty",
    "Pets",
    "fashion",
    "anime",
    "religion",
    "Home & Garden",
]

CATEGORY_GROUPS: Dict[str, List[str]] = {
    "AI / Tech / Creator Tools": [
        "Technology",
        "Science",
        "Business & Finance",
        "cryptocurrency",
    ],
    "Music / Dance / Entertainment": [
        "music",
        "dance",
        "celebrity",
        "Movies & TV",
        "anime",
    ],
    "Viral Culture / Meme / Social Buzz": [
        "meme",
        "relationship",
        "fashion",
        "beauty",
        "food",
        "Pets",
    ],
    "Gaming / Sports / Youth Culture": [
        "Gaming",
        "Sports",
        "cars",
    ],
    "Lifestyle / Outdoor / Travel": [
        "Travel",
        "Nature & Outdoors",
        "Health & Fitness",
        "Home & Garden",
    ],
    "News / Society / Sensitive Topics": [
        "News",
        "religion",
    ],
}

COUNTRIES = _csv_env("X_COUNTRIES", DEFAULT_COUNTRIES)
CATEGORIES = _csv_env("X_CATEGORIES", DEFAULT_CATEGORIES)

# Feishu configuration
FEISHU_WEBHOOK_URL = os.getenv("FEISHU_WEBHOOK_URL", "")
FEISHU_SECRET = os.getenv("FEISHU_SECRET", "")

# Scraping configuration
SCRAPE_INTERVAL = _int_env("SCRAPE_INTERVAL", 3600)
REQUEST_TIMEOUT = _int_env("REQUEST_TIMEOUT", 30)
LLM_REQUEST_TIMEOUT = _int_env("LLM_REQUEST_TIMEOUT", 300)
MAX_RETRIES = _int_env("MAX_RETRIES", 3)
MAX_TRENDS_PER_CATEGORY = _int_env("MAX_TRENDS_PER_CATEGORY", 10)
MAX_TWEETS_PER_TREND = _int_env("MAX_TWEETS_PER_TREND", 5)
MAX_CANDIDATES_PER_CATEGORY = _int_env("MAX_CANDIDATES_PER_CATEGORY", 30)
SCROLLS_PER_CATEGORY = _int_env("SCROLLS_PER_CATEGORY", 7)
MAX_ITEMS_PER_GROUP = _int_env("MAX_ITEMS_PER_GROUP", 7)
TOP_ACTION_COUNT = _int_env("TOP_ACTION_COUNT", 5)
LOOKBACK_HOURS = _int_env("LOOKBACK_HOURS", 24)
X_BASE_URL = os.getenv("X_BASE_URL", "https://x.com")
X_EXPLORE_URL = os.getenv("X_EXPLORE_URL", f"{X_BASE_URL}/explore/tabs/trending")
X_CATEGORY_URL_TEMPLATE = os.getenv("X_CATEGORY_URL_TEMPLATE", "")
X_COOKIES = os.getenv("X_COOKIES", "")
X_STORAGE_STATE = os.getenv("X_STORAGE_STATE", "")
X_STORAGE_STATE_B64 = os.getenv("X_STORAGE_STATE_B64", "")

# OpenAI-compatible LLM configuration. Store secrets in GitHub Actions secrets.
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")
LLM_ENABLED = _bool_env("LLM_ENABLED", True)
LLM_REQUIRED = _bool_env("LLM_REQUIRED", True)
LLM_USER_AGENT = os.getenv("LLM_USER_AGENT", "OpenAI/Python 1.0")

# Debug mode
DEBUG = _bool_env("DEBUG", False)
DRY_RUN = _bool_env("DRY_RUN", False)

# Data fields to extract per tweet
TWEET_FIELDS = [
    "author",
    "content",
    "created_at",
    "likes",
    "retweets",
    "replies",
    "views",
    "media_urls",
    "tweet_url",
]

# Output format
OUTPUT_FORMAT = os.getenv("OUTPUT_FORMAT", "feishu")  # feishu, json, both, preview
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "outputs"))
TIMEZONE = os.getenv("TIMEZONE", "Asia/Shanghai")
