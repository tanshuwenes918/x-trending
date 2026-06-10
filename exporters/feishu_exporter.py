"""Feishu (Lark) exporter for trending data."""

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Dict, List
from urllib import request
from urllib.error import HTTPError, URLError

from config.settings import FEISHU_SECRET, FEISHU_WEBHOOK_URL, REQUEST_TIMEOUT

logger = logging.getLogger(__name__)


class FeishuExporter:
    """Export the daily X operations report to Feishu."""

    def __init__(
        self,
        webhook_url: str = FEISHU_WEBHOOK_URL,
        secret: str = FEISHU_SECRET,
    ):
        self.webhook_url = webhook_url
        self.secret = secret
        logger.info("FeishuExporter initialized")

    def export(self, data: Dict[str, Any]) -> bool:
        """Export data to Feishu."""
        if not self.webhook_url:
            logger.error("Feishu webhook URL not configured")
            return False

        try:
            logger.info("Sending data to Feishu...")
            for message in self._format_messages(data):
                self._post_message(message)
                time.sleep(0.3)
            logger.info("Data sent to Feishu successfully")
            return True
        except Exception as exc:
            logger.error("Error sending data to Feishu: %s", exc, exc_info=True)
            return False

    def _format_message(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Format data as a single Feishu message."""
        return self._format_messages(data)[0]

    def _format_messages(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Format processed data as Feishu post messages."""
        timestamp = data.get("timestamp", "")
        summary = data.get("summary", {})
        countries = ", ".join(data.get("countries", []))
        blocks = [
            self._text_line("X 趋势日报 | AI 视频 & AI 音乐"),
            self._text_line(f"数据范围：过去 {data.get('lookback_hours', 24)} 小时"),
            self._text_line(f"国家：{countries or 'Global'}"),
            self._text_line(
                "每类最多：{max_items} 条 | 入选推文：{eligible} | LLM：{llm}".format(
                    max_items=summary.get("max_items_per_group", 7),
                    eligible=summary.get("eligible_tweet_count", 0),
                    llm="已启用" if data.get("llm_used") else "未启用",
                )
            ),
        ]

        for index, group in enumerate(data.get("groups", []), start=1):
            blocks.append(self._text_line(f"\n{self._section_number(index)}、{group.get('name', '')}"))
            items = group.get("items", [])
            if not items:
                blocks.append(self._text_line("暂无符合条件的推文。"))
                continue

            for item_index, item in enumerate(items, start=1):
                summary_text = self._truncate(item.get("summary", ""), 140)
                blocks.append(self._text_line(f"{item_index}. 摘要：{summary_text}"))
                blocks.append(self._link_line("   链接：原推文", item.get("tweet_url", "")))

        action_section_index = len(data.get("groups", [])) + 1
        blocks.append(
            self._text_line(f"\n{self._section_number(action_section_index)}、最值得跟进的 5 个引流动作")
        )
        top_actions = data.get("top_actions", [])
        if not top_actions:
            blocks.append(self._text_line("暂无可推荐的引流动作。"))
        for index, action in enumerate(top_actions, start=1):
            blocks.append(self._text_line(f"{index}. 标题：{self._truncate(action.get('title', ''), 60)}"))
            blocks.append(self._text_line(f"   引流动作：{self._truncate(action.get('action', ''), 140)}"))
            blocks.append(self._link_line("   链接：原推文", action.get("tweet_url", "")))

        return self._chunk_blocks(blocks, timestamp)

    def _post_message(self, message: Dict[str, Any]) -> bool:
        """Post message to Feishu webhook."""
        payload = dict(message)
        payload.update(self._signature_payload())

        req = request.Request(
            self.webhook_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
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

    def _chunk_blocks(
        self,
        blocks: List[List[Dict[str, str]]],
        timestamp: str,
        chunk_size: int = 90,
    ) -> List[Dict[str, Any]]:
        chunks = [
            blocks[index : index + chunk_size]
            for index in range(0, len(blocks), chunk_size)
        ] or [[]]
        total = len(chunks)

        return [
            {
                "msg_type": "post",
                "content": {
                    "post": {
                        "zh_cn": {
                            "title": (
                                f"X 趋势日报 | AI 视频 & AI 音乐 {timestamp}"
                                if total == 1
                                else f"X 趋势日报 | AI 视频 & AI 音乐 {timestamp} ({idx}/{total})"
                            ),
                            "content": chunk,
                        }
                    }
                },
            }
            for idx, chunk in enumerate(chunks, start=1)
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

    def _section_number(self, index: int) -> str:
        numbers = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]
        if 1 <= index <= len(numbers):
            return numbers[index - 1]
        return str(index)

    def _truncate(self, text: Any, limit: int) -> str:
        value = " ".join(str(text or "").split())
        if len(value) <= limit:
            return value
        return value[: limit - 3] + "..."
