"""Configuration settings for X Trending scraper."""

import os
from typing import List

# Countries to scrape
COUNTRIES: List[str] = [
    "Global",
    "United States",
]

# Categories to scrape
CATEGORIES: List[str] = [
    "Technology",
    "News",
    "Business & Finance",
    "Science",
    "Travel",
    "Gaming",
    "Sports",
    "Health&Fitness",
    "cryptocurrency",
    "cars",
    "music",
    "dance",
    "celebrity",
    "relationship",
    "movies&tv",
    "nature&outdoors",
    "Entertainment",
    "food",
    "meme",
    "beauty",
    "Pets",
    "fashion",
    "religion",
    "Home & Garden",
]

# Feishu configuration
FEISHU_WEBHOOK_URL = os.getenv("FEISHU_WEBHOOK_URL", "")

# Scraping configuration
SCRAPE_INTERVAL = int(os.getenv("SCRAPE_INTERVAL", "3600"))  # Default: 1 hour
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))  # Default: 30 seconds
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))  # Default: 3 retries

# Debug mode
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

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
OUTPUT_FORMAT = os.getenv("OUTPUT_FORMAT", "feishu")  # feishu, json, csv
