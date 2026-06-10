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
        blocks: List[List[Dict[str, str]]] = []

        for index, group in enumerate(data.get("groups", []), start=1):
            group_name = group.get("name", "")
            blocks.append(self._text_line(f"\n—— {self._section_number(index)}、{group_name} ——"))
            blocks.append(self._text_line(f"【{self._group_tag(group_name)}】"))
            items = group.get("items", [])
            if not items:
                blocks.append(self._text_line("暂无符合条件的推文。"))
                continue

            for item_index, item in enumerate(items, start=1):
                summary_text = self._truncate(item.get("summary", ""), 140)
                blocks.append(
                    self._text_with_metrics_and_link(
                        f"{item_index}. {summary_text}",
                        item,
                        item.get("tweet_url", ""),
                    )
                )

        action_section_index = len(data.get("groups", [])) + 1
        blocks.append(
            self._text_line(f"\n—— {self._section_number(action_section_index)}、最值得跟进的 5 个引流动作 ——")
        )
        blocks.append(self._text_line("【引流动作】"))
        top_actions = data.get("top_actions", [])
        if not top_actions:
            blocks.append(self._text_line("暂无可推荐的引流动作。"))
        for index, action in enumerate(top_actions, start=1):
            title = self._truncate(action.get("title", ""), 60)
            action_text = self._truncate(action.get("action", ""), 140)
            blocks.append(
                self._text_with_metrics_and_link(
                    f"{index}. {title}：{action_text}",
                    action,
                    action.get("tweet_url", ""),
                )
            )

        return self._chunk_blocks(blocks, timestamp)

    def format_plain_text(self, data: Dict[str, Any]) -> str:
        """Format processed data as readable plain text for local preview."""
        lines = [
            self._report_title(data.get("timestamp", "")),
            "",
        ]

        for index, group in enumerate(data.get("groups", []), start=1):
            group_name = group.get("name", "")
            lines.append(f"—— {self._section_number(index)}、{group_name} ——")
            lines.append(f"【{self._group_tag(group_name)}】")
            items = group.get("items", [])
            if not items:
                lines.append("暂无符合条件的推文。")
            for item_index, item in enumerate(items, start=1):
                summary_text = self._truncate(item.get("summary", ""), 140)
                lines.append(f"{item_index}. {summary_text}{self._plain_metrics_and_url(item)}")
            lines.append("")

        action_section_index = len(data.get("groups", [])) + 1
        lines.append(f"—— {self._section_number(action_section_index)}、最值得跟进的 5 个引流动作 ——")
        lines.append("【引流动作】")
        top_actions = data.get("top_actions", [])
        if not top_actions:
            lines.append("暂无可推荐的引流动作。")
        for index, action in enumerate(top_actions, start=1):
            title = self._truncate(action.get("title", ""), 60)
            action_text = self._truncate(action.get("action", ""), 140)
            lines.append(f"{index}. {title}：{action_text}{self._plain_metrics_and_url(action)}")

        return "\n".join(lines)

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
                                self._report_title(timestamp)
                                if total == 1
                                else f"{self._report_title(timestamp)} ({idx}/{total})"
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

    def _text_with_metrics_and_link(
        self,
        text: str,
        item: Dict[str, Any],
        href: str,
    ) -> List[Dict[str, str]]:
        metrics_text = self._metrics_text(item)
        suffix = f"（{metrics_text}；" if metrics_text else "（"
        if not href:
            return self._text_line(f"{text}{f'（{metrics_text}）' if metrics_text else ''}")
        return [
            {"tag": "text", "text": f"{text}{suffix}"},
            {"tag": "a", "text": href, "href": href},
            {"tag": "text", "text": "）"},
        ]

    def _plain_metrics_and_url(self, item: Dict[str, Any]) -> str:
        metrics_text = self._metrics_text(item)
        href = item.get("tweet_url", "")
        if metrics_text and href:
            return f"（{metrics_text}；{href}）"
        if metrics_text:
            return f"（{metrics_text}）"
        if href:
            return f"（{href}）"
        return ""

    def _metrics_text(self, item: Dict[str, Any]) -> str:
        parts = []
        likes = self._number_or_zero(item.get("likes", 0))
        retweets = self._number_or_zero(item.get("retweets", 0))
        replies = self._number_or_zero(item.get("replies", 0))
        views = self._number_or_zero(item.get("views", 0))

        if likes > 0:
            parts.append(f"{self._format_number(likes)}赞")
        if retweets > 0:
            parts.append(f"{self._format_number(retweets)}转")
        if replies > 0:
            parts.append(f"{self._format_number(replies)}评")
        if views > 0:
            parts.append(f"{self._format_number(views)}浏览")
        return " + ".join(parts)

    def _number_or_zero(self, value: Any) -> int:
        try:
            return int(float(value or 0))
        except (TypeError, ValueError):
            return 0

    def _format_number(self, value: Any) -> str:
        number = self._number_or_zero(value)
        if number >= 1_000_000:
            compact = f"{number / 1_000_000:.1f}".rstrip("0").rstrip(".")
            return f"{compact}M"
        if number >= 1_000:
            compact = f"{number / 1_000:.1f}".rstrip("0").rstrip(".")
            return f"{compact}K"
        return str(number)

    def _section_number(self, index: int) -> str:
        numbers = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]
        if 1 <= index <= len(numbers):
            return numbers[index - 1]
        return str(index)

    def _report_title(self, timestamp: str) -> str:
        return f"X 趋势日报 | AI 视频 & AI 音乐 {timestamp}".strip()

    def _group_tag(self, group_name: str) -> str:
        tags = {
            "AI / Tech / Creator Tools": "AI 工具",
            "Music / Dance / Entertainment": "音乐娱乐",
            "Viral Culture / Meme / Social Buzz": "社媒热点",
            "Gaming / Sports / Youth Culture": "游戏体育",
            "Lifestyle / Outdoor / Travel": "生活方式",
            "News / Society / Sensitive Topics": "新闻社会",
        }
        return tags.get(group_name, "热点观察")

    def _truncate(self, text: Any, limit: int) -> str:
        value = " ".join(str(text or "").split())
        if len(value) <= limit:
            return value
        return value[: limit - 3] + "..."
