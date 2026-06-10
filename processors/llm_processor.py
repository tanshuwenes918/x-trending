"""LLM enrichment for summaries and traffic-driving actions."""

import json
import logging
from typing import Any, Dict, List
from urllib import request
from urllib.error import HTTPError, URLError

from config.settings import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_ENABLED,
    LLM_MODEL,
    REQUEST_TIMEOUT,
    TOP_ACTION_COUNT,
)

logger = logging.getLogger(__name__)


class LLMProcessor:
    """Enrich processed report data with concise Chinese summaries."""

    def __init__(
        self,
        api_key: str = LLM_API_KEY,
        base_url: str = LLM_BASE_URL,
        model: str = LLM_MODEL,
        enabled: bool = LLM_ENABLED,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.enabled = enabled

    def enrich(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Enrich group summaries and top actions with an LLM if configured."""
        if not self._configured():
            logger.info("LLM is not configured; using fallback summaries and actions.")
            data["llm_used"] = False
            return data

        try:
            payload = self._build_payload(data)
            result = self._chat_json(payload)
            self._merge_result(data, result)
            data["llm_used"] = True
        except Exception as exc:
            logger.warning("LLM enrichment failed; using fallback output: %s", exc, exc_info=True)
            data["llm_used"] = False

        return data

    def _configured(self) -> bool:
        return bool(self.enabled and self.api_key and self.base_url and self.model)

    def _build_payload(self, data: Dict[str, Any]) -> Dict[str, Any]:
        groups = []
        for group in data.get("groups", []):
            items = []
            for item in group.get("items", []):
                items.append(
                    {
                        "tweet_url": item.get("tweet_url", ""),
                        "trending_term": item.get("trending_term", ""),
                        "source_category": item.get("source_category", ""),
                        "content": item.get("content", ""),
                        "author": item.get("author", ""),
                        "score": item.get("score", 0),
                        "metrics": {
                            "views": item.get("views", 0),
                            "likes": item.get("likes", 0),
                            "retweets": item.get("retweets", 0),
                            "replies": item.get("replies", 0),
                        },
                    }
                )
            groups.append({"name": group.get("name", ""), "items": items})
        return {"groups": groups}

    def _chat_json(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        prompt = (
            "你是一个AI视频生成、AI音乐生成产品的站外增长运营助手。"
            "请基于输入的X热门推文生成简洁中文日报素材。"
            "前6个分类只需要为每条推文输出一句摘要，不要写引流动作。"
            f"最后从所有候选里选出最适合站外获客的{TOP_ACTION_COUNT}条，输出标题和引流动作。"
            "引流动作必须围绕模板、挑战、素材、短视频、落地页或评论区导流，"
            "不要写观点媒体式选题。避开政治、宗教、版权和名人肖像高风险建议。"
            "只返回JSON，不要返回Markdown。格式："
            '{"groups":[{"name":"分类名","items":[{"tweet_url":"原链接","summary":"一句中文摘要"}]}],'
            '"top_actions":[{"title":"短标题","action":"一句引流动作","tweet_url":"原链接"}]}'
        )
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }

        req = request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
                response_body = response.read().decode("utf-8")
        except (HTTPError, URLError) as exc:
            raise RuntimeError(f"LLM request failed: {exc}") from exc

        response_data = json.loads(response_body)
        content = response_data["choices"][0]["message"]["content"]
        return self._parse_json_content(content)

    def _merge_result(self, data: Dict[str, Any], result: Dict[str, Any]) -> None:
        summaries_by_url = {}
        for group in result.get("groups", []):
            for item in group.get("items", []):
                tweet_url = item.get("tweet_url")
                summary = item.get("summary")
                if tweet_url and summary:
                    summaries_by_url[tweet_url] = summary

        for group in data.get("groups", []):
            for item in group.get("items", []):
                tweet_url = item.get("tweet_url")
                if tweet_url in summaries_by_url:
                    item["summary"] = summaries_by_url[tweet_url]

        top_actions = []
        for item in result.get("top_actions", [])[:TOP_ACTION_COUNT]:
            if item.get("title") and item.get("action") and item.get("tweet_url"):
                top_actions.append(
                    {
                        "title": item["title"],
                        "action": item["action"],
                        "tweet_url": item["tweet_url"],
                    }
                )

        if top_actions:
            data["top_actions"] = top_actions

    def _parse_json_content(self, content: str) -> Dict[str, Any]:
        text = content.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:].strip()
        return json.loads(text)
