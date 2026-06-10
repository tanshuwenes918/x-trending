"""Main scraper for X Global Trending data."""

import logging
import re
from http.cookies import SimpleCookie
from typing import Dict, List, Any
from datetime import datetime
from urllib.parse import quote, urljoin

logger = logging.getLogger(__name__)


class TrendingScraper:
    """Scraper for X (Twitter) Global Trending data."""
    
    def __init__(self):
        """Initialize the scraper."""
        self.session = None
        self._page_cache = {}
        logger.info("TrendingScraper initialized")
    
    def scrape_all(self) -> Dict[str, List[Dict[str, Any]]]:
        """Scrape trending data for all countries and categories.
        
        Returns:
            Dictionary with country and category keys containing trending data
        """
        from config.settings import CATEGORIES, COUNTRIES

        logger.info("Starting to scrape trending data...")

        data: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        for country in COUNTRIES:
            data[country] = {}
            for category in CATEGORIES:
                try:
                    data[country][category] = self.scrape_country_category(country, category)
                except Exception as exc:
                    logger.warning(
                        "Failed scraping country=%s category=%s: %s",
                        country,
                        category,
                        exc,
                        exc_info=True,
                    )
                    data[country][category] = []

        return data
    
    def scrape_country_category(
        self,
        country: str,
        category: str
    ) -> List[Dict[str, Any]]:
        """Scrape trending data for a specific country and category.
        
        Args:
            country: Country name (e.g., "Global", "United States")
            category: Category name (e.g., "Technology", "News")
        
        Returns:
            List of trending tweets with metadata
        """
        logger.info(f"Scraping {country} - {category}...")

        page = self._fetch_page(self._build_url(country, category))
        if page is None:
            return []

        trend_terms = self._extract_trend_terms(page)
        tweets = self._extract_tweets(page)

        if not trend_terms and tweets:
            trend_terms = [category]

        return [
            {
                "trending_term": term,
                "tweets": tweets,
                "scraped_at": datetime.utcnow().isoformat(),
            }
            for term in trend_terms
        ]

    def _build_url(self, country: str, category: str) -> str:
        from config.settings import X_CATEGORY_URL_TEMPLATE, X_EXPLORE_URL

        if not X_CATEGORY_URL_TEMPLATE:
            logger.info(
                "X_CATEGORY_URL_TEMPLATE is not configured; using X_EXPLORE_URL for all requests."
            )
            return X_EXPLORE_URL

        return X_CATEGORY_URL_TEMPLATE.format(
            country=quote(country),
            category=quote(category),
            country_slug=self._slug(country),
            category_slug=self._slug(category),
        )

    def _fetch_page(self, url: str):
        if url in self._page_cache:
            return self._page_cache[url]

        try:
            from scrapling.fetchers import StealthyFetcher
        except ImportError:
            logger.error("Scrapling is not installed. Run: pip install 'scrapling[fetchers]'")
            return None

        cookies = self._playwright_cookies()
        logger.info(
            "Fetching %s%s",
            url,
            " with X cookies" if cookies else "",
        )
        page = StealthyFetcher.fetch(
            url,
            headless=True,
            network_idle=True,
            cookies=cookies or None,
            wait=5000,
            google_search=False,
        )
        self._page_cache[url] = page
        return page

    def _playwright_cookies(self) -> List[Dict[str, Any]]:
        from config.settings import X_COOKIES

        if not X_COOKIES:
            return []

        cookie = SimpleCookie()
        cookie.load(X_COOKIES)

        cookies = []
        for name, morsel in cookie.items():
            if not morsel.value:
                continue
            cookies.append(
                {
                    "name": name,
                    "value": morsel.value,
                    "domain": ".x.com",
                    "path": "/",
                    "secure": True,
                    "sameSite": "Lax",
                }
            )

        return cookies

    def _extract_trend_terms(self, page) -> List[str]:
        terms = []
        for node in page.css('[data-testid="trend"], a[href*="/search?q="]'):
            text = self._node_text(node)
            if not text:
                continue

            candidate = text.split("\n")[0].strip()
            if candidate and candidate not in terms:
                terms.append(candidate)

        return terms[:10]

    def _extract_tweets(self, page) -> List[Dict[str, Any]]:
        tweets = []
        for article in page.css('article[data-testid="tweet"]'):
            tweet = {
                "author": self._extract_author(article),
                "content": self._extract_content(article),
                "created_at": self._extract_created_at(article),
                "likes": self._extract_metric(article, "like"),
                "retweets": self._extract_metric(article, "repost"),
                "replies": self._extract_metric(article, "reply"),
                "views": self._extract_metric(article, "view"),
                "media_urls": self._extract_media_urls(article),
                "tweet_url": self._extract_tweet_url(article),
            }
            if tweet["tweet_url"] and tweet["content"]:
                tweets.append(tweet)

        return tweets

    def _extract_author(self, article) -> str:
        user = self._first(article, '[data-testid="User-Name"]')
        text = self._node_text(user)
        handle = re.search(r"@[\w_]+", text)
        return handle.group(0).lstrip("@") if handle else text.split("\n")[0].strip()

    def _extract_content(self, article) -> str:
        tweet_text = self._first(article, '[data-testid="tweetText"]')
        return self._node_text(tweet_text)

    def _extract_created_at(self, article) -> str:
        time_node = self._first(article, "time")
        return self._attr(time_node, "datetime") if time_node else ""

    def _extract_tweet_url(self, article) -> str:
        from config.settings import X_BASE_URL

        link = self._first(article, 'a[href*="/status/"]')
        href = self._attr(link, "href") if link else ""
        return urljoin(X_BASE_URL, href)

    def _extract_media_urls(self, article) -> List[str]:
        urls = []
        for node in article.css('img[src*="pbs.twimg.com/media"], video source[src]'):
            src = self._attr(node, "src")
            if src and src not in urls:
                urls.append(src)
        return urls

    def _extract_metric(self, article, metric: str) -> int:
        pattern = re.compile(rf"([\d,.]+[KkMm]?)\s+{metric}", re.IGNORECASE)
        for node in article.css("[aria-label]"):
            aria_label = self._attr(node, "aria-label")
            match = pattern.search(aria_label)
            if match:
                return self._compact_number_to_int(match.group(1))
        return 0

    def _first(self, node, selector: str):
        matches = node.css(selector)
        return matches.first if matches else None

    def _attr(self, node, name: str) -> str:
        if not node:
            return ""
        attrs = getattr(node, "attrib", {}) or {}
        return attrs.get(name, "")

    def _node_text(self, node) -> str:
        if not node:
            return ""
        text = getattr(node, "text", "")
        if callable(text):
            text = text()
        return " ".join(str(text).split())

    def _compact_number_to_int(self, value: str) -> int:
        text = value.replace(",", "").strip()
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

    def _slug(self, value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
