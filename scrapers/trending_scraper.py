"""Playwright scraper for X Global Trending data."""

from __future__ import annotations

import logging
import re
import base64
import json
from datetime import datetime
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urljoin

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

logger = logging.getLogger(__name__)


class TrendingScraper:
    """Scrape X Global Trending category pages."""

    def __init__(self):
        """Initialize the scraper."""
        logger.info("TrendingScraper initialized")

    def scrape_all(self) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
        """Scrape trending data for all configured countries and categories."""
        from config.settings import CATEGORIES, COUNTRIES, X_EXPLORE_URL

        logger.info("Starting to scrape trending data...")
        data: Dict[str, Dict[str, List[Dict[str, Any]]]] = {
            country: {category: [] for category in CATEGORIES} for country in COUNTRIES
        }

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context_options = {
                "viewport": {"width": 1400, "height": 1000},
                "locale": "en-US",
                "timezone_id": "Asia/Shanghai",
            }
            storage_state = self._storage_state()
            if storage_state:
                logger.info("Using X Playwright storage state")
                context_options["storage_state"] = storage_state

            context = browser.new_context(**context_options)
            cookies = self._playwright_cookies()
            if cookies and not storage_state:
                logger.info("Injecting %d X cookies", len(cookies))
                context.add_cookies(cookies)
            elif cookies and storage_state:
                logger.info("Skipping X_COOKIES because X storage state is configured")

            page = context.new_page()
            logger.info("Opening %s", X_EXPLORE_URL)
            page.goto(X_EXPLORE_URL, wait_until="domcontentloaded", timeout=60000)
            self._wait_for_global_trending_ready(page)
            logger.info("Loaded X page: %s", page.url)

            if "onboarding" in page.url or "login" in page.url:
                logger.warning("X redirected to login/onboarding page. Check X_COOKIES.")

            for country in COUNTRIES:
                for category in CATEGORIES:
                    try:
                        data[country][category] = self.scrape_country_category(
                            country,
                            category,
                            page=page,
                        )
                    except Exception as exc:
                        logger.warning(
                            "Failed scraping country=%s category=%s: %s",
                            country,
                            category,
                            exc,
                            exc_info=True,
                        )
                        data[country][category] = []

            browser.close()

        return data

    def scrape_country_category(
        self,
        country: str,
        category: str,
        page: Optional[Page] = None,
    ) -> List[Dict[str, Any]]:
        """Scrape trending data for a specific country and category."""
        if page is None:
            raise RuntimeError("TrendingScraper requires a Playwright page for category scraping.")

        logger.info("Scraping %s - %s...", country, category)
        if not self._open_category(page, country, category):
            logger.warning("Category not found or not clickable: %s", category)
            logger.info("Visible non-tweet text sample: %s", self._visible_non_tweet_text_sample(page))
            return []

        self._wait_for_popular_today(page)
        tweets = self._collect_category_tweets(page)

        logger.info("Collected %d tweets for %s", len(tweets), category)
        return [
            {
                "trending_term": category,
                "tweets": tweets,
                "scraped_at": datetime.utcnow().isoformat(),
            }
        ]

    def _open_category(self, page: Page, country: str, category: str) -> bool:
        if self._goto_category_url(page, country, category):
            return True
        return self._click_category(page, category)

    def _goto_category_url(self, page: Page, country: str, category: str) -> bool:
        from config.settings import X_CATEGORY_URL_TEMPLATE

        if not X_CATEGORY_URL_TEMPLATE:
            return False

        url = X_CATEGORY_URL_TEMPLATE.format(
            country=country,
            category=quote(category),
            country_slug=self._slug(country),
            category_slug=self._slug(category),
        )
        logger.info("Opening category URL for %s: %s", category, url)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            self._wait_for_global_trending_ready(page)
            return "login" not in page.url and "onboarding" not in page.url
        except Exception as exc:
            logger.warning("Failed opening category URL for %s: %s", category, exc)
            return False

    def _click_category(self, page: Page, category: str) -> bool:
        aliases = self._category_aliases(category)

        for _ in range(3):
            self._return_to_category_nav(page)
            for alias in aliases:
                locator = self._find_text_locator(page, alias)
                if locator is None:
                    continue
                try:
                    locator.scroll_into_view_if_needed(timeout=5000)
                    locator.click(timeout=8000)
                    page.wait_for_timeout(3000)
                    return True
                except PlaywrightTimeoutError:
                    logger.debug("Timed out clicking category alias: %s", alias)
                except Exception as exc:
                    logger.debug("Failed clicking category alias %s: %s", alias, exc)

            self._scroll_horizontal_category_rails(page)
            page.wait_for_timeout(1000)

        return False

    def _find_text_locator(self, page: Page, text: str):
        for locator in (
            page.get_by_text(text, exact=True),
            page.get_by_text(re.compile(re.escape(text), re.IGNORECASE)),
        ):
            count = min(locator.count(), 20)
            for index in range(count):
                candidate = locator.nth(index)
                if self._is_safe_category_candidate(candidate):
                    return candidate

        return None

    def _is_safe_category_candidate(self, locator) -> bool:
        try:
            return bool(
                locator.evaluate(
                    """
                    (el) => {
                      if (!el || !el.isConnected) return false;
                      if (el.closest('article[data-testid="tweet"]')) return false;
                      const rect = el.getBoundingClientRect();
                      return rect.width > 0 && rect.height > 0;
                    }
                    """
                )
            )
        except Exception:
            return False

    def _category_aliases(self, category: str) -> List[str]:
        aliases = [category]
        normalized = category.lower().replace(" ", "")
        replacements = {
            "health&fitness": ["Health & Fitness", "Health and Fitness"],
            "movies&tv": ["Movies & TV", "Movies and TV"],
            "nature&outdoors": ["Nature & Outdoors", "Nature and Outdoors"],
            "cryptocurrency": ["Cryptocurrency", "Crypto"],
            "music": ["Music"],
            "dance": ["Dance"],
            "cars": ["Cars"],
            "meme": ["Meme", "Memes"],
            "anime": ["Anime"],
        }
        aliases.extend(replacements.get(normalized, []))
        return list(dict.fromkeys(aliases))

    def _return_to_category_nav(self, page: Page) -> None:
        try:
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(1000)
        except Exception as exc:
            logger.debug("Failed returning to category nav: %s", exc)

    def _scroll_horizontal_category_rails(self, page: Page) -> None:
        page.evaluate(
            """
            () => {
              for (const el of document.querySelectorAll('div')) {
                if (el.scrollWidth > el.clientWidth + 50) {
                  el.scrollLeft = Math.min(el.scrollWidth, el.scrollLeft + el.clientWidth * 0.9);
                }
              }
            }
            """
        )

    def _wait_for_global_trending_ready(self, page: Page) -> None:
        for attempt in range(2):
            try:
                page.get_by_text("Popular today").first.wait_for(timeout=30000)
                return
            except PlaywrightTimeoutError:
                if page.locator('article[data-testid="tweet"]').count() > 0:
                    return
                if attempt == 0:
                    logger.info("X trending page was not ready; reloading once")
                    page.reload(wait_until="domcontentloaded", timeout=60000)
                    continue

        logger.warning("X trending page did not show ready markers before scraping")

    def _wait_for_popular_today(self, page: Page) -> None:
        try:
            page.get_by_text("Popular today").first.wait_for(timeout=10000)
        except PlaywrightTimeoutError:
            logger.debug("Popular today text was not visible after category click.")

    def _collect_category_tweets(self, page: Page) -> List[Dict[str, Any]]:
        from config.settings import MAX_CANDIDATES_PER_CATEGORY, SCROLLS_PER_CATEGORY

        tweets_by_url: Dict[str, Dict[str, Any]] = {}

        for scroll_index in range(SCROLLS_PER_CATEGORY + 1):
            for article in page.locator('article[data-testid="tweet"]').all():
                tweet = self._extract_tweet(article)
                tweet_url = tweet.get("tweet_url", "")
                if tweet_url and tweet.get("content"):
                    tweets_by_url[tweet_url] = tweet

            if len(tweets_by_url) >= MAX_CANDIDATES_PER_CATEGORY:
                break

            page.mouse.wheel(0, 1200)
            page.wait_for_timeout(2500 if scroll_index == 0 else 1500)

        return list(tweets_by_url.values())[:MAX_CANDIDATES_PER_CATEGORY]

    def _extract_tweet(self, article) -> Dict[str, Any]:
        return {
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

    def _extract_author(self, article) -> str:
        text = self._safe_inner_text(article.locator('[data-testid="User-Name"]').first)
        handle = re.search(r"@[\w_]+", text)
        return handle.group(0).lstrip("@") if handle else text.split("\n")[0].strip()

    def _extract_content(self, article) -> str:
        parts = []
        for node in article.locator('[data-testid="tweetText"]').all():
            text = self._safe_inner_text(node)
            if text:
                parts.append(text)
        return " ".join(" ".join(parts).split())

    def _extract_created_at(self, article) -> str:
        return self._safe_attr(article.locator("time").first, "datetime")

    def _extract_tweet_url(self, article) -> str:
        from config.settings import X_BASE_URL

        href = self._safe_attr(article.locator('a[href*="/status/"]').first, "href")
        return urljoin(X_BASE_URL, href)

    def _extract_media_urls(self, article) -> List[str]:
        urls = []
        for locator in article.locator('img[src*="pbs.twimg.com/media"], video source[src]').all():
            src = self._safe_attr(locator, "src")
            if src and src not in urls:
                urls.append(src)
        return urls

    def _extract_metric(self, article, metric: str) -> int:
        selectors = {
            "like": [
                '[data-testid="like"]',
                '[data-testid="unlike"]',
            ],
            "repost": [
                '[data-testid="retweet"]',
                '[data-testid="unretweet"]',
            ],
            "reply": [
                '[data-testid="reply"]',
            ],
            "view": [
                'a[href$="/analytics"]',
                'a[aria-label*="view" i]',
                '[aria-label*="views" i]',
            ],
        }

        for selector in selectors[metric]:
            for locator in article.locator(selector).all():
                for text in self._metric_text_candidates(locator):
                    value = self._extract_metric_value(text, metric)
                    if value:
                        return value

        for locator in article.locator("[aria-label]").all():
            value = self._extract_metric_value(self._safe_attr(locator, "aria-label"), metric)
            if value:
                return value

        return 0

    def _metric_text_candidates(self, locator) -> List[str]:
        candidates = [
            self._safe_attr(locator, "aria-label"),
            self._safe_attr(locator, "title"),
            self._safe_inner_text(locator),
        ]
        try:
            for child in locator.locator("[aria-label]").all():
                candidates.append(self._safe_attr(child, "aria-label"))
        except Exception:
            pass
        return [text for text in candidates if text]

    def _extract_metric_value(self, text: str, metric: str) -> int:
        normalized = self._clean_metric_text(text)
        if not normalized:
            return 0

        keyword_groups = {
            "like": r"likes?|liked",
            "repost": r"reposts?|retweets?|reposted|retweeted",
            "reply": r"replies|reply",
            "view": r"views?|view",
        }
        number = r"(\d+(?:[,.]\d+)?\s*[KkMmBb]?)"
        keyword = f"(?:{keyword_groups[metric]})"

        for pattern in (
            rf"{number}\s+{keyword}\b",
            rf"\b{keyword}\s+{number}",
        ):
            match = re.search(pattern, normalized, re.IGNORECASE)
            if match:
                return self._compact_number_to_int(match.group(1))

        if re.fullmatch(number, normalized):
            return self._compact_number_to_int(normalized)

        return 0

    def _clean_metric_text(self, value: str) -> str:
        return " ".join(str(value or "").replace("\xa0", " ").split())

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

    def _storage_state(self) -> Optional[Dict[str, Any]]:
        from config.settings import X_STORAGE_STATE, X_STORAGE_STATE_B64

        if X_STORAGE_STATE_B64:
            try:
                decoded = base64.b64decode(X_STORAGE_STATE_B64).decode("utf-8")
                return json.loads(decoded)
            except Exception as exc:
                raise RuntimeError("Failed to parse X_STORAGE_STATE_B64") from exc

        if X_STORAGE_STATE:
            path = Path(X_STORAGE_STATE)
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
            return json.loads(X_STORAGE_STATE)

        return None

    def _safe_inner_text(self, locator) -> str:
        try:
            if locator.count() == 0:
                return ""
            return " ".join(locator.inner_text(timeout=1000).split())
        except Exception:
            return ""

    def _safe_attr(self, locator, name: str) -> str:
        try:
            if locator.count() == 0:
                return ""
            return locator.get_attribute(name, timeout=1000) or ""
        except Exception:
            return ""

    def _visible_non_tweet_text_sample(self, page: Page) -> List[str]:
        try:
            return page.evaluate(
                """
                () => Array.from(document.querySelectorAll('a, button, [role="button"], [role="tab"], [role="link"], div[tabindex]'))
                  .filter((el) => !el.closest('article[data-testid="tweet"]'))
                  .map((el) => (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' '))
                  .filter(Boolean)
                  .slice(0, 30)
                """
            )
        except Exception:
            return []

    def _compact_number_to_int(self, value: str) -> int:
        text = self._clean_metric_text(value).replace(" ", "")
        if "," in text and "." not in text and re.fullmatch(r"\d+,\d{1,2}[KkMmBb]?", text):
            text = text.replace(",", ".")
        else:
            text = text.replace(",", "")
        multiplier = 1
        if text.lower().endswith("k"):
            multiplier = 1_000
            text = text[:-1]
        elif text.lower().endswith("m"):
            multiplier = 1_000_000
            text = text[:-1]
        elif text.lower().endswith("b"):
            multiplier = 1_000_000_000
            text = text[:-1]
        try:
            return int(float(text) * multiplier)
        except ValueError:
            return 0

    def _slug(self, value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
