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
from urllib.parse import quote, urljoin, urlparse

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

logger = logging.getLogger(__name__)


class ScrapeError(RuntimeError):
    """X could not supply usable trending data for this run."""


class TrendingScraper:
    """Scrape X Global Trending category pages."""

    def __init__(self):
        """Initialize the scraper."""
        logger.info("TrendingScraper initialized")

    def scrape_all(self) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
        """Scrape trending data for all configured countries and categories."""
        from config.settings import CATEGORIES, COUNTRIES

        logger.info("Starting to scrape trending data...")
        data: Dict[str, Dict[str, List[Dict[str, Any]]]] = {
            country: {category: [] for category in CATEGORIES} for country in COUNTRIES
        }

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                self._scrape_with_browser(browser, data)
            finally:
                browser.close()

        tweet_count = sum(
            len(trend.get("tweets", []))
            for categories in data.values()
            for trends in categories.values()
            for trend in trends
        )
        if not tweet_count:
            raise ScrapeError(
                "X returned no tweets in any configured category. "
                "Check the X session and category page; no daily report will be sent."
            )
        return data

    def _scrape_with_browser(self, browser, data) -> None:
        from config.settings import CATEGORIES, COUNTRIES, X_EXPLORE_URL

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

        for country in COUNTRIES:
            for category in CATEGORIES:
                try:
                    data[country][category] = self.scrape_country_category(
                        country, category, page=page,
                    )
                except ScrapeError:
                    # Authentication/challenge failures invalidate the run, even
                    # if an earlier category happened to return some tweets.
                    raise
                except Exception as exc:
                    logger.warning(
                        "Failed scraping country=%s category=%s: %s",
                        country, category, exc, exc_info=True,
                    )
                    data[country][category] = []

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
        self._assert_page_accessible(page)
        if not self._open_category(page, country, category):
            self._assert_page_accessible(page)
            logger.warning("Category not found or not clickable: %s", category)
            logger.info("Visible non-tweet text sample: %s", self._visible_non_tweet_text_sample(page))
            return []

        self._assert_page_accessible(page)
        self._wait_for_popular_today(page)
        tweets = self._collect_category_tweets(page)
        self._assert_page_accessible(page)

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
            return True
        except ScrapeError:
            raise
        except Exception as exc:
            logger.warning("Failed opening category URL for %s: %s", category, exc)
            return False

    def _click_category(self, page: Page, category: str) -> bool:
        aliases = self._category_aliases(category)

        for _ in range(3):
            self._assert_page_accessible(page)
            self._return_to_category_nav(page)
            for alias in aliases:
                locator = self._find_text_locator(page, alias)
                if locator is None:
                    continue
                try:
                    locator.scroll_into_view_if_needed(timeout=5000)
                    locator.click(timeout=8000)
                    page.wait_for_timeout(3000)
                    self._assert_page_accessible(page)
                    return True
                except ScrapeError:
                    raise
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
            "technology": ["技术", "技術", "科技"],
            "news": ["新闻", "新聞"],
            "business&finance": ["商业和金融", "商業和金融", "商业与金融", "商業與金融"],
            "science": ["科学", "科學"],
            "travel": ["旅游", "旅遊"],
            "gaming": ["游戏竞技", "遊戲競技", "游戏", "遊戲"],
            "sports": ["体育", "體育"],
            "health&fitness": ["Health & Fitness", "Health and Fitness", "健康与健身", "健康與健身"],
            "cryptocurrency": ["Cryptocurrency", "Crypto", "加密货币", "加密貨幣"],
            "cars": ["Cars", "汽车", "汽車"],
            "music": ["Music", "音乐", "音樂"],
            "dance": ["Dance", "舞曲", "舞蹈"],
            "celebrity": ["明星", "名人"],
            "relationship": ["关系", "關係", "人际关系", "人際關係"],
            "movies&tv": ["Movies & TV", "Movies and TV", "电影与电视", "電影與電視"],
            "nature&outdoors": ["Nature & Outdoors", "Nature and Outdoors", "自然与户外", "自然與戶外"],
            "food": ["美食"],
            "meme": ["Meme", "Memes", "表情包", "迷因", "梗图", "梗圖"],
            "beauty": ["美容"],
            "pets": ["宠物", "寵物"],
            "fashion": ["时尚", "時尚"],
            "anime": ["Anime", "动漫", "動漫", "日本动画", "日本動畫"],
            "religion": ["宗教"],
            "home&garden": ["家居与园艺", "家居與園藝", "居家和园艺", "居家和園藝"],
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

    def _assert_page_accessible(self, page: Page) -> None:
        """Reject login and verification pages before collecting or exporting."""
        path = urlparse(page.url).path.lower()
        if path.startswith("/account/access"):
            raise ScrapeError(
                "X redirected to /account/access (account verification/access check). "
                "Open X in your browser with the same account and complete any checks. "
                "Then export a verified session and update X_STORAGE_STATE_B64."
            )
        if any(part in path for part in ("/login", "/onboarding", "/signup")):
            raise ScrapeError(
                "X requires login. Sign in with the scraper account, verify the trending "
                "page loads, then refresh X_STORAGE_STATE_B64 or X_COOKIES."
            )

        title = page.title().lower()
        challenge = page.locator(
            '#challenge-form, #cf-challenge-running, .cf-browser-verification, '
            'iframe[src*="challenges.cloudflare.com"]'
        ).count() > 0
        if not challenge and page.locator('article[data-testid="tweet"]').count() == 0:
            text = page.locator("body").inner_text(timeout=3000).strip().lower()
            challenge = (
                "just a moment" in title
                or "attention required" in title
                or "verify you are human" in text
                or "checking your browser" in text
                or ("cloudflare" in text and len(text) < 200)
            )
        if challenge:
            raise ScrapeError(
                "X is showing a Cloudflare/browser verification page instead of trends. "
                "Complete verification in your own browser and refresh X_STORAGE_STATE_B64. "
                "If it only fails on GitHub Actions, check the runner's access to X."
            )

    def _wait_for_global_trending_ready(self, page: Page) -> None:
        for attempt in range(2):
            self._assert_page_accessible(page)
            try:
                page.get_by_text(
                    re.compile(r"Popular today|今日热门|今日熱門", re.IGNORECASE)
                ).first.wait_for(timeout=30000)
                self._assert_page_accessible(page)
                return
            except PlaywrightTimeoutError:
                self._assert_page_accessible(page)
                if page.locator('article[data-testid="tweet"]').count() > 0:
                    return
                if attempt == 0:
                    logger.info("X trending page was not ready; reloading once")
                    page.reload(wait_until="domcontentloaded", timeout=60000)
                    continue

        raise ScrapeError(
            "X trending page did not show ready markers after a reload. "
            "Check that the configured URL and X session show real categories and tweets; "
            "no daily report will be sent."
        )

    def _wait_for_popular_today(self, page: Page) -> None:
        self._assert_page_accessible(page)
        try:
            page.get_by_text(
                re.compile(r"Popular today|今日热门|今日熱門", re.IGNORECASE)
            ).first.wait_for(timeout=10000)
        except PlaywrightTimeoutError:
            logger.debug("Popular today text was not visible after category click.")
        self._assert_page_accessible(page)

    def _collect_category_tweets(self, page: Page) -> List[Dict[str, Any]]:
        from config.settings import MAX_CANDIDATES_PER_CATEGORY, SCROLLS_PER_CATEGORY

        tweets_by_url: Dict[str, Dict[str, Any]] = {}

        for scroll_index in range(SCROLLS_PER_CATEGORY + 1):
            self._assert_page_accessible(page)
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
        metrics = self._extract_metrics(article)
        return {
            "author": self._extract_author(article),
            "content": self._extract_content(article),
            "created_at": self._extract_created_at(article),
            "likes": metrics["like"],
            "retweets": metrics["repost"],
            "replies": metrics["reply"],
            "views": metrics["view"],
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
                '[aria-label*="like" i]',
            ],
            "repost": [
                '[data-testid="retweet"]',
                '[data-testid="unretweet"]',
                '[aria-label*="repost" i]',
                '[aria-label*="retweet" i]',
            ],
            "reply": [
                '[data-testid="reply"]',
                '[aria-label*="reply" i]',
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

    def _extract_metrics(self, article) -> Dict[str, int]:
        metrics = {
            "reply": self._extract_metric(article, "reply"),
            "repost": self._extract_metric(article, "repost"),
            "like": self._extract_metric(article, "like"),
            "view": self._extract_metric(article, "view"),
        }

        for metric, value in self._extract_action_group_metrics(article).items():
            if not metrics.get(metric) and value:
                metrics[metric] = value

        return metrics

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
            "like": r"likes?|liked|赞|喜歡|喜欢",
            "repost": r"reposts?|retweets?|reposted|retweeted|转发|轉發",
            "reply": r"replies|reply|评论|回覆|回复",
            "view": r"views?|view|浏览|觀看|观看",
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

    def _extract_action_group_metrics(self, article) -> Dict[str, int]:
        """Infer metrics from X's compact action row when labels omit counts."""
        values = self._action_group_numbers(article)
        if not values:
            return {}

        if len(values) >= 4:
            return {
                "reply": values[0],
                "repost": values[1],
                "like": values[2],
                "view": values[3],
            }
        if len(values) == 3:
            return {
                "repost": values[0],
                "like": values[1],
                "view": values[2],
            }
        if len(values) == 2:
            return {
                "repost": values[0],
                "view": values[1],
            }
        return {}

    def _action_group_numbers(self, article) -> List[int]:
        candidates: List[int] = []
        number_pattern = re.compile(r"^\d+(?:[,.]\d+)?\s*[KkMmBb]?$")

        try:
            groups = article.locator('[role="group"]').all()
        except Exception:
            groups = []

        for group in groups:
            text = self._safe_inner_text(group)
            numbers = [
                self._compact_number_to_int(part)
                for part in re.split(r"\s+", text)
                if number_pattern.fullmatch(part)
            ]
            numbers = [number for number in numbers if number > 0]
            if len(numbers) > len(candidates):
                candidates = numbers

        return candidates

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
