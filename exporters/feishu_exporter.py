"""Feishu (Lark) exporter for trending data."""

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Dict, List, Any
from urllib import request
from urllib.error import HTTPError, URLError

from config.settings import FEISHU_SECRET, FEISHU_WEBHOOK_URL, REQUEST_TIMEOUT

logger = logging.getLogger(__name__)


class FeishuExporter:
    """Export trending data to Feishu (Lark)."""

    def __init__(
        self,
        webhook_url: str = FEISHU_WEBHOOK_URL,
        secret: str = FEISHU_SECRET,
    ):
        """Initialize Feishu exporter.
        
        Args:
            webhook_url: Feishu webhook URL for posting messages
        """
        self.webhook_url = webhook_url
        self.secret = secret
        logger.info("FeishuExporter initialized")
    
    def export(self, data: Dict[str, Any]) -> bool:
        """Export data to Feishu.
        
        Args:
            data: Processed trending data
        
        Returns:
            True if successful, False otherwise
        """
        if not self.webhook_url:
            logger.error("Feishu webhook URL not configured")
            return False
        
        try:
            logger.info("Sending data to Feishu...")

            messages = self._format_messages(data)
            for message in messages:
                self._post_message(message)
                time.sleep(0.3)

            logger.info("Data sent to Feishu successfully")
            return True
        
        except Exception as e:
            logger.error(f"Error sending data to Feishu: {e}", exc_info=True)
            return False
    
    def _format_message(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Format data as a single Feishu message.
        
        Args:
            data: Processed trending data
        
        Returns:
            Formatted message for Feishu API
        """
        return self._format_messages(data)[0]

    def _format_messages(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Format processed data as Feishu post messages."""
        timestamp = data.get("timestamp", "")
        summary = data.get("summary", {})
        blocks = [
            self._text_line(f"X Trending Daily | {timestamp}"),
            self._text_line(
                "Countries: {country_count} | Categories: {category_count} | "
                "Trends: {trend_count} | Tweets: {tweet_count}".format(
                    country_count=summary.get("country_count", 0),
                    category_count=summary.get("category_count", 0),
                    trend_count=summary.get("trend_count", 0),
                    tweet_count=summary.get("tweet_count", 0),
                )
            ),
        ]

        countries = data.get("countries", {})
        if not countries:
            blocks.append(self._text_line("No trending data was collected."))

        for country, categories in countries.items():
            blocks.append(self._text_line(f"\n[{country}]"))
            for category, trends in categories.items():
                blocks.append(self._text_line(f"\n# {category}"))
                for trend in trends:
                    term = trend.get("trending_term") or "Untitled trend"
                    blocks.append(self._text_line(f"- {term}"))
                    for tweet in trend.get("tweets", []):
                        blocks.extend(self._tweet_lines(tweet))

        return self._chunk_blocks(blocks, timestamp)
    
    def _post_message(self, message: Dict[str, Any]) -> bool:
        """Post message to Feishu webhook.
        
        Args:
            message: Formatted message
        
        Returns:
            True if successful
        """
        payload = dict(message)
        payload.update(self._signature_payload())

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            self.webhook_url,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
                response_body = response.read().decode("utf-8")
        except (HTTPError, URLError) as exc:
            raise RuntimeError(f"Feishu webhook request failed: {exc}") from exc

        result = json.loads(response_body)
        if result.get("code", 0) != 0:
            raise RuntimeError(f"Feishu webhook rejected message: {result}")

        return True

    def _tweet_lines(self, tweet: Dict[str, Any]) -> List[List[Dict[str, str]]]:
        author = self._truncate(tweet.get("author", ""), 50)
        content = self._truncate(tweet.get("content", ""), 180)
        created_at = self._truncate(tweet.get("created_at", ""), 40)
        metrics = (
            f"likes {tweet.get('likes', 0)} | reposts {tweet.get('retweets', 0)} | "
            f"replies {tweet.get('replies', 0)} | views {tweet.get('views', 0)}"
        )

        lines = [
            self._text_line(f"  @{author} | {created_at} | {metrics}"),
            self._link_line(f"  {content}", tweet.get("tweet_url", "")),
        ]

        media_urls = tweet.get("media_urls", [])
        if media_urls:
            lines.append(self._link_line("  Media", media_urls[0]))

        return lines

    def _chunk_blocks(
        self,
        blocks: List[List[Dict[str, str]]],
        timestamp: str,
        chunk_size: int = 80,
    ) -> List[Dict[str, Any]]:
        chunks = [
            blocks[index : index + chunk_size]
            for index in range(0, len(blocks), chunk_size)
        ]
        total = len(chunks) or 1

        return [
            {
                "msg_type": "post",
                "content": {
                    "post": {
                        "zh_cn": {
                            "title": (
                                f"X Trending Daily {timestamp}"
                                if total == 1
                                else f"X Trending Daily {timestamp} ({idx}/{total})"
                            ),
                            "content": chunk,
                        }
                    }
                },
            }
            for idx, chunk in enumerate(chunks or [blocks], start=1)
        ]

    def _signature_payload(self) -> Dict[str, str]:
        if not self.secret:
            return {}

        timestamp = str(int(time.time()))
        string_to_sign = f"{timestamp}\n{self.secret}".encode("utf-8")
        digest = hmac.new(string_to_sign, b"", digestmod=hashlib.sha256).digest()
        return {
            "timestamp": timestamp,
            "sign": base64.b64encode(digest).decode("utf-8"),
        }

    def _text_line(self, text: str) -> List[Dict[str, str]]:
        return [{"tag": "text", "text": text}]

    def _link_line(self, text: str, href: str) -> List[Dict[str, str]]:
        if not href:
            return self._text_line(text)
        return [{"tag": "a", "text": text, "href": href}]

    def _truncate(self, text: Any, limit: int) -> str:
        value = " ".join(str(text or "").split())
        if len(value) <= limit:
            return value
        return value[: limit - 3] + "..."
