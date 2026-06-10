"""LLM enrichment for summaries and traffic-driving actions."""

import json
import logging
import re
from typing import Any, Dict
from urllib import request
from urllib.error import HTTPError, URLError

from config.settings import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_ENABLED,
    LLM_MODEL,
    LLM_REQUEST_TIMEOUT,
    LLM_REQUIRED,
    LLM_USER_AGENT,
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
            "你是一个 AI 音乐生成与 AI 视频生成产品的站外增长运营负责人，产品类似 Vanso，目标是从 X 等站外平台找到可转化的内容机会。"
            "请基于输入的 X 热门推文生成简洁中文日报素材。所有输出必须是中文，并优先服务于运营选题、素材生产、评论区导流和落地页转化。"
            "前 6 个分类只需要为每条推文输出一句摘要，不要写引流动作，但摘要要点出它为什么值得运营观察，例如情绪点、模板化潜力、声音/音乐/视频化潜力或传播机制。"
            "摘要格式必须是“话题名（新闻标题）：简要描述”。"
            "新闻标题用 4 到 18 个中文字符概括事件核心；简要描述用一句话说明这条推文发布了什么、发生了什么或为什么在传播。"
            "不要直译原推文，不要保留英文分类前缀，不要写“摘要：”“链接：”。"
            "遇到政治、宗教、暴力、仇恨、灾难、犯罪、医疗诊断等敏感内容，只做克制中性概述，不输出引流建议。"
            f"最后从所有低风险候选里选出最适合给 AI 音乐/AI 视频产品获客的 {TOP_ACTION_COUNT} 条，输出标题和引流动作。"
            "优先选择能改造成以下资产的热点：AI 音乐模板、AI MV/短视频模板、角色配音/变声挑战、情绪 BGM、歌词改写、梗图转视频、分镜脚本、口播脚本、评论区关键词领取素材包。"
            "引流动作必须是可执行的一句话，包含内容形式、用户参与方式和导流方式，例如“发起 X 挑战，提供 3 个模板，评论区关键词领取并跳转生成页”。"
            "动作要自然贴合原热点，不要只写泛泛的“做模板”“发视频”；尽量明确适合用 AI 音乐、AI 配音、AI 视频或落地页素材包承接。"
            "不要选择新闻敏感、版权高风险、名人肖像高风险或需要事实核验的内容做引流动作。"
            "只返回 JSON，不要返回 Markdown。格式："
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
                "Accept": "application/json",
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": LLM_USER_AGENT,
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
        unsafe_urls = set()

        for group in data.get("groups", []):
            group_name = group.get("name", "")
            for item in group.get("items", []):
                tweet_url = item.get("tweet_url")
                if tweet_url:
                    source_items_by_url[tweet_url] = item
                    if group_name == "News / Society / Sensitive Topics":
                        unsafe_urls.add(tweet_url)

        for group in result.get("groups", []):
            for item in group.get("items", []):
                tweet_url = item.get("tweet_url")
                summary = self._clean_llm_text(item.get("summary"))
                if tweet_url and summary:
                    summaries_by_url[tweet_url] = summary

        for group in data.get("groups", []):
            for item in group.get("items", []):
                tweet_url = item.get("tweet_url")
                if tweet_url in summaries_by_url:
                    item["summary"] = summaries_by_url[tweet_url]

        top_actions = []
        for item in result.get("top_actions", [])[:TOP_ACTION_COUNT * 2]:
            tweet_url = item.get("tweet_url")
            title = self._clean_llm_text(item.get("title"))
            action = self._clean_llm_text(item.get("action"))
            if not title or not action or not tweet_url:
                continue
            if tweet_url in unsafe_urls or tweet_url not in source_items_by_url:
                continue

            source_item = source_items_by_url[tweet_url]
            top_actions.append(
                {
                    "title": title,
                    "action": action,
                    "tweet_url": tweet_url,
                    "likes": source_item.get("likes", 0),
                    "retweets": source_item.get("retweets", 0),
                    "replies": source_item.get("replies", 0),
                    "views": source_item.get("views", 0),
                }
            )
            if len(top_actions) >= TOP_ACTION_COUNT:
                break

        if top_actions:
            data["top_actions"] = top_actions

    def _parse_json_content(self, content: str) -> Dict[str, Any]:
        text = content.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:].strip()
        return json.loads(text)

    def _clean_llm_text(self, value: Any) -> str:
        text = " ".join(str(value or "").split())
        text = re.sub(r"^(摘要|链接|标题|引流动作)\s*[:：]\s*", "", text)
        return text
