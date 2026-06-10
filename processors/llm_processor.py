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
    LLM_REQUEST_TIMEOUT,
    LLM_REQUIRED,
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
        required: bool = LLM_REQUIRED,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.enabled = enabled
        self.required = required

    def enrich(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Enrich group summaries and top actions with an LLM if configured."""
        if not self._configured():
            if self.required:
                raise RuntimeError(
                    "LLM is required but not configured. Set LLM_ENABLED=true, LLM_API_KEY, LLM_BASE_URL, and LLM_MODEL."
                )
            logger.info("LLM is not configured; using fallback summaries and actions.")
            data["llm_used"] = False
            return data

        try:
            payload = self._build_payload(data)
            result = self._chat_json(payload)
            self._merge_result(data, result)
            data["llm_used"] = True
        except Exception as exc:
            if self.required:
                raise RuntimeError(f"LLM enrichment is required but failed: {exc}") from exc
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
            "请基于输入的X热门推文生成简洁中文日报素材。所有输出必须是中文。"
            "前6个分类只需要为每条推文输出一句摘要，不要写引流动作。"
            "摘要格式必须是“话题名（新闻标题）：简要描述”。"
            "新闻标题用8到18个中文字符概括事件核心；简要描述用一句话说明这条推文干了什么、发布了什么或发生了什么。"
            "不要直译原推文，不要保留英文分类前缀，不要写“摘要：”“链接：”。"
            f"最后从所有候选里选出最适合站外获客的{TOP_ACTION_COUNT}条，输出标题和引流动作。"
            "引流动作必须围绕模板、挑战、素材、短视频、落地页或评论区导流，"
            "不要写观点媒体式选题。避开政治、宗教、版权和名人肖像高风险建议。"
            "只返回JSON，不要返回Markdown。格式："
            '{"groups":[{"name":"分类名","items":[{"tweet_url":"原链接","summary":"话题名（新闻标题）：简要描述"}]}],'
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
            self._chat_completions_url(),
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=LLM_REQUEST_TIMEOUT) as response:
                response_body = response.read().decode("utf-8")
        except HTTPError as exc:
            raise RuntimeError(f"LLM request failed: {self._http_error_detail(exc)}") from exc
        except URLError as exc:
            raise RuntimeError(f"LLM request failed: {exc}") from exc

        response_data = json.loads(response_body)
        content = response_data["choices"][0]["message"]["content"]
        return self._parse_json_content(content)

    def _chat_completions_url(self) -> str:
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}/chat/completions"
        return f"{self.base_url}/v1/chat/completions"

    def _models_url(self) -> str:
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}/models"
        return f"{self.base_url}/v1/models"

    def _http_error_detail(self, exc: HTTPError) -> str:
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        detail = f"HTTP Error {exc.code}: {exc.reason}"
        if body:
            detail = f"{detail}; response={body[:800]}"
        return detail

    def _merge_result(self, data: Dict[str, Any], result: Dict[str, Any]) -> None:
        summaries_by_url = {}
        source_items_by_url = {}
        for group in data.get("groups", []):
            for item in group.get("items", []):
                tweet_url = item.get("tweet_url")
                if tweet_url:
                    source_items_by_url[tweet_url] = item

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
                source_item = source_items_by_url.get(item["tweet_url"], {})
                top_actions.append(
                    {
                        "title": item["title"],
                        "action": item["action"],
                        "tweet_url": item["tweet_url"],
                        "likes": source_item.get("likes", 0),
                        "retweets": source_item.get("retweets", 0),
                        "replies": source_item.get("replies", 0),
                        "views": source_item.get("views", 0),
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
