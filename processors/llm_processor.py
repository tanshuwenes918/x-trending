"""LLM enrichment for summaries and traffic-driving actions."""

import json
import logging
import re
from itertools import chain
from typing import Any, Dict, Iterable, Iterator, Tuple
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
            result = self._responses_json(payload)
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

    def _responses_json(self, payload: Dict[str, Any]) -> Dict[str, Any]:
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
            "stream": True,
            "instructions": prompt,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": json.dumps(payload, ensure_ascii=False),
                        }
                    ],
                }
            ],
        }

        req = request.Request(
            self._responses_url(),
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "text/event-stream, application/json",
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": LLM_USER_AGENT,
            },
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=LLM_REQUEST_TIMEOUT) as response:
                content = self._read_responses_text(response)
        except HTTPError as exc:
            try:
                detail = self._http_error_detail(exc)
            finally:
                exc.close()
            # Older compatible providers may only expose Chat Completions.
            # A model lookup failure is not evidence that the endpoint is absent.
            if exc.code == 404 and "model_not_found" not in detail.lower():
                logger.info("Responses endpoint returned HTTP 404; trying Chat Completions")
                return self._chat_completions_json(payload, prompt)
            raise RuntimeError(f"LLM request failed: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"LLM request failed: {exc}") from exc

        return self._parse_json_content(content)

    def _chat_completions_json(self, payload: Dict[str, Any], prompt: str) -> Dict[str, Any]:
        """Use the same instructions and source data on a Chat-only provider."""
        body = {
            "model": self.model,
            "stream": True,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        }
        req = request.Request(
            self._chat_completions_url(),
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "text/event-stream, application/json",
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": LLM_USER_AGENT,
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=LLM_REQUEST_TIMEOUT) as response:
                content = self._read_chat_completions_text(response)
        except HTTPError as exc:
            try:
                detail = self._http_error_detail(exc)
            finally:
                exc.close()
            raise RuntimeError(f"Chat Completions request failed: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"Chat Completions request failed: {exc}") from exc
        return self._parse_json_content(content)

    def _read_chat_completions_text(self, response: Any) -> str:
        """Inspect the body so incorrectly labelled JSON/SSE responses also work."""
        first_line = response.readline().removeprefix(b"\xef\xbb\xbf")
        while first_line and not first_line.strip():
            first_line = response.readline()
        if not first_line:
            raise RuntimeError("Chat Completions returned an empty response.")
        if first_line.startswith((b"data:", b"event:", b":", b"id:", b"retry:")):
            return self._chat_completions_stream_text(chain([first_line], response))
        response_data = json.loads((first_line + response.read()).decode("utf-8"))
        if not isinstance(response_data, dict):
            raise RuntimeError("Chat Completions response must be a JSON object.")
        if response_data.get("error"):
            raise RuntimeError(f"Chat Completions failed: {json.dumps(response_data['error'], ensure_ascii=False)[:800]}")
        choices = response_data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("Chat Completions response did not contain choices.")
        for choice in choices:
            if not isinstance(choice, dict) or choice.get("finish_reason") != "stop":
                raise RuntimeError("Chat Completions did not finish normally (expected finish_reason=stop).")
        choice = next((item for item in choices if item.get("index", 0) == 0), None)
        message = choice.get("message") if choice else None
        if not isinstance(message, dict) or message.get("refusal"):
            raise RuntimeError("Chat Completions did not contain a usable assistant message.")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Chat Completions response did not contain output text.")
        return content

    def _chat_completions_stream_text(self, lines: Iterable[bytes]) -> str:
        """Require both a normal finish reason and the standard stream terminator."""
        text_deltas = {}
        finished = set()
        for event_name, event_data in self._iter_sse_events(lines):
            if event_data.strip() == "[DONE]":
                if 0 not in finished or any(index not in finished for index in text_deltas):
                    raise RuntimeError("Chat Completions stream ended without finish_reason=stop.")
                content = "".join(text_deltas.get(0, []))
                if not content.strip():
                    raise RuntimeError("Chat Completions stream completed without output text.")
                return content
            event = json.loads(event_data)
            if not isinstance(event, dict):
                raise RuntimeError("Chat Completions stream event must be a JSON object.")
            if event_name == "error" or event.get("type") == "error" or event.get("error"):
                detail = event.get("error") or event.get("message") or event_name
                raise RuntimeError(f"Chat Completions stream failed: {json.dumps(detail, ensure_ascii=False)[:800]}")
            choices = event.get("choices")
            if not isinstance(choices, list):
                raise RuntimeError("Chat Completions stream event did not contain choices.")
            for choice in choices:
                if not isinstance(choice, dict):
                    raise RuntimeError("Chat Completions stream choice must be a JSON object.")
                index = choice.get("index", 0)
                delta = choice.get("delta") or {}
                if not isinstance(delta, dict) or delta.get("refusal"):
                    raise RuntimeError("Chat Completions stream did not contain a usable assistant delta.")
                content = delta.get("content")
                if content is not None:
                    if not isinstance(content, str):
                        raise RuntimeError("Chat Completions stream text must be a string.")
                    text_deltas.setdefault(index, []).append(content)
                finish_reason = choice.get("finish_reason")
                if finish_reason is not None:
                    if finish_reason != "stop":
                        raise RuntimeError(f"Chat Completions did not finish normally: finish_reason={finish_reason}.")
                    finished.add(index)
        raise RuntimeError("Chat Completions stream ended before [DONE]; output may be incomplete.")

    def _read_responses_text(self, response: Any) -> str:
        """Read SSE incrementally, tolerating gateways that return ordinary JSON."""
        first_line = response.readline().removeprefix(b"\xef\xbb\xbf")
        while first_line and not first_line.strip():
            first_line = response.readline()
        if not first_line:
            raise RuntimeError("Responses API returned an empty response.")

        # Inspect the body because some compatible gateways mislabel Content-Type.
        if first_line.startswith((b"data:", b"event:", b":", b"id:", b"retry:")):
            return self._responses_stream_text(chain([first_line], response))

        response_data = json.loads((first_line + response.read()).decode("utf-8"))
        self._check_response_status(response_data)
        return self._responses_output_text(response_data)

    def _iter_sse_events(self, lines: Iterable[bytes]) -> Iterator[Tuple[str, str]]:
        """Decode SSE events, including comments, CRLF and multiline data fields."""
        event_name = ""
        data_lines = []
        for raw_line in lines:
            line = raw_line.decode("utf-8").rstrip("\r\n")
            if not line:
                if data_lines:
                    yield event_name, "\n".join(data_lines)
                event_name = ""
                data_lines = []
                continue
            if line.startswith(":"):
                continue
            field, separator, value = line.partition(":")
            if separator and value.startswith(" "):
                value = value[1:]
            if field == "event":
                event_name = value
            elif field == "data":
                data_lines.append(value)

        # A few gateways close immediately after the final data line.
        if data_lines:
            yield event_name, "\n".join(data_lines)

    def _responses_stream_text(self, lines: Iterable[bytes]) -> str:
        text_deltas = {}
        finished_text = {}
        for event_name, event_data in self._iter_sse_events(lines):
            if event_data.strip() == "[DONE]":
                break
            event = json.loads(event_data)
            if not isinstance(event, dict):
                raise RuntimeError("Responses API stream event must be a JSON object.")
            event_type = event.get("type") or event_name

            if event_type in ("error", "response.failed", "response.incomplete", "response.cancelled"):
                response_data = event.get("response") or {}
                detail = (
                    event.get("error")
                    or response_data.get("error")
                    or response_data.get("incomplete_details")
                    or event.get("message")
                    or event_type
                )
                raise RuntimeError(
                    f"Responses API stream failed: {json.dumps(detail, ensure_ascii=False)[:800]}"
                )

            if event_type == "response.output_text.delta":
                key = (event.get("output_index", 0), event.get("content_index", 0))
                delta = event.get("delta")
                if isinstance(delta, str):
                    text_deltas.setdefault(key, []).append(delta)
            elif event_type == "response.output_text.done":
                key = (event.get("output_index", 0), event.get("content_index", 0))
                text = event.get("text")
                if isinstance(text, str):
                    finished_text[key] = text
            elif event_type == "response.completed":
                response_data = event.get("response") or {}
                self._check_response_status(response_data)
                # Prefer the final response; adding it to deltas would duplicate text.
                try:
                    return self._responses_output_text(response_data)
                except RuntimeError:
                    # Some gateways omit text from the completion snapshot.
                    pass
                keys = sorted(set(text_deltas) | set(finished_text))
                content = "".join(
                    finished_text[key] if key in finished_text else "".join(text_deltas[key])
                    for key in keys
                )
                if content.strip():
                    return content
                raise RuntimeError("Responses API stream completed without output text.")

        raise RuntimeError("Responses API stream ended before response.completed; output may be incomplete.")

    def _check_response_status(self, response_data: Dict[str, Any]) -> None:
        if not isinstance(response_data, dict):
            raise RuntimeError("Responses API response must be a JSON object.")
        status = response_data.get("status")
        if response_data.get("error") or (status and status != "completed"):
            detail = response_data.get("error") or response_data.get("incomplete_details") or status
            raise RuntimeError(
                f"Responses API did not complete: {json.dumps(detail, ensure_ascii=False)[:800]}"
            )

    def _responses_url(self) -> str:
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}/responses"
        return f"{self.base_url}/v1/responses"

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

    def _responses_output_text(self, response_data: Dict[str, Any]) -> str:
        """Extract text from a Responses API payload, including compatible gateways."""
        output_text = response_data.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text

        text_parts = []
        for output in response_data.get("output", []):
            if output.get("type") not in (None, "message"):
                continue
            for content in output.get("content", []):
                if content.get("type") not in (None, "output_text"):
                    continue
                text = content.get("text")
                if isinstance(text, str):
                    text_parts.append(text)

        output_text = "".join(text_parts)
        if output_text.strip():
            return output_text

        raise RuntimeError("Responses API response did not contain output text.")

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
