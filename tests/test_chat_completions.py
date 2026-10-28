"""Offline compatibility checks for Responses-to-Chat Completions fallback."""

import argparse
from copy import deepcopy
import io
import json
from unittest.mock import MagicMock
from urllib.error import HTTPError

import pytest

import config.settings as settings
import exporters.feishu_exporter as exporter_module
import main as entrypoint
import processors.data_processor as processor_module
from processors import llm_processor
import scrapers.trending_scraper as scraper_module


RESULT = {
    "groups": [
        {
            "name": "Music / Dance / Entertainment",
            "items": [
                {
                    "tweet_url": "https://x.com/test/status/1",
                    "summary": "音乐（新歌发布）：新歌适合观察情绪和视频模板的传播机制。",
                }
            ],
        }
    ],
    "top_actions": [],
}
RESULT_TEXT = json.dumps(RESULT, ensure_ascii=False)


class Response(io.BytesIO):
    def __init__(self, body, content_type="application/json"):
        super().__init__(body)
        self.headers = {"Content-Type": content_type}


class StreamingResponse(Response):
    def read(self, *args):
        raise AssertionError("Chat SSE must be consumed incrementally")


def http_error(status, message="Invalid URL (POST /v1/responses)"):
    body = json.dumps({"error": {"message": message}}).encode("utf-8")
    return HTTPError("https://offline.invalid/v1/responses", status, message, {}, io.BytesIO(body))


def sse(event):
    payload = event if isinstance(event, str) else json.dumps(event, ensure_ascii=False)
    return f"data: {payload}\n\n".encode("utf-8")


def chunk(text=None, finish_reason=None, index=0):
    delta = {} if text is None else {"content": text}
    return {"choices": [{"index": index, "delta": delta, "finish_reason": finish_reason}]}


def chat_json(text=RESULT_TEXT, finish_reason="stop"):
    return json.dumps(
        {"choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": finish_reason}]},
        ensure_ascii=False,
    ).encode("utf-8")


@pytest.fixture
def processor():
    return llm_processor.LLMProcessor(
        api_key="offline-test-key",
        base_url="https://offline.invalid/v1",
        model="offline-chat-model",
        enabled=True,
        required=True,
    )


@pytest.fixture
def report():
    return {
        "timestamp": "2026-10-25T09:00:00+08:00",
        "groups": [
            {
                "name": "Music / Dance / Entertainment",
                "items": [
                    {
                        "tweet_url": "https://x.com/test/status/1",
                        "content": "Introducing a new AI music workflow",
                        "author": "creator",
                        "source_category": "music",
                        "trending_term": "music",
                        "score": 1234,
                        "likes": 100,
                        "retweets": 20,
                        "replies": 5,
                        "views": 2000,
                        "summary": "原有摘要",
                    }
                ],
            }
        ],
        "top_actions": [],
    }


@pytest.mark.parametrize("base_url", ["https://offline.invalid", "https://offline.invalid/v1"])
def test_responses_404_falls_back_with_identical_prompt_and_source_payload(monkeypatch, processor, report, base_url):
    processor.base_url = base_url
    payload = processor._build_payload(report)
    opener = MagicMock(side_effect=[http_error(404), Response(chat_json())])
    monkeypatch.setattr(llm_processor.request, "urlopen", opener)

    assert processor._responses_json(payload) == RESULT

    assert opener.call_count == 2
    responses_request = opener.call_args_list[0].args[0]
    chat_request = opener.call_args_list[1].args[0]
    responses_body = json.loads(responses_request.data)
    chat_body = json.loads(chat_request.data)
    assert responses_request.full_url == "https://offline.invalid/v1/responses"
    assert chat_request.full_url == "https://offline.invalid/v1/chat/completions"
    assert chat_request.get_method() == "POST"
    assert chat_body["model"] == responses_body["model"] == "offline-chat-model"
    assert chat_body["stream"] is True
    assert chat_body["messages"] == [
        {"role": "system", "content": responses_body["instructions"]},
        {"role": "user", "content": responses_body["input"][0]["content"][0]["text"]},
    ]
    assert json.loads(chat_body["messages"][1]["content"]) == payload
    assert "只返回 JSON" in chat_body["messages"][0]["content"]
    assert "敏感" in chat_body["messages"][0]["content"]
    assert "text/event-stream" in chat_request.get_header("Accept")
    assert chat_request.get_header("Authorization") == "Bearer offline-test-key"
    assert opener.call_args_list[1].kwargs["timeout"] == llm_processor.LLM_REQUEST_TIMEOUT


@pytest.mark.parametrize("content_type", ["text/event-stream", "application/json"])
def test_chat_sse_assembles_chinese_deltas_and_marks_report_complete(monkeypatch, processor, report, content_type):
    body = b"\xef\xbb\xbf\r\n: heartbeat\r\n\r\n"
    body += b"".join(sse(chunk(RESULT_TEXT[index:index + 7])) for index in range(0, len(RESULT_TEXT), 7))
    body += sse(chunk(finish_reason="stop")) + sse("[DONE]")
    opener = MagicMock(side_effect=[http_error(404), StreamingResponse(body, content_type)])
    monkeypatch.setattr(llm_processor.request, "urlopen", opener)

    enriched = processor.enrich(report)

    assert enriched["llm_used"] is True
    assert enriched["groups"][0]["items"][0]["summary"] == RESULT["groups"][0]["items"][0]["summary"]
    assert opener.call_count == 2


@pytest.mark.parametrize(
    "extra_event",
    [
        pytest.param({"choices": [], "usage": {"total_tokens": 123}}, id="usage-after-stop"),
        pytest.param(chunk("Other choice must not join the JSON", finish_reason="stop", index=1), id="separate-choice"),
    ],
)
def test_chat_sse_keeps_first_choice_separate_from_usage_and_other_choices(monkeypatch, processor, extra_event):
    body = sse(chunk(RESULT_TEXT, finish_reason="stop")) + sse(extra_event) + sse("[DONE]")
    opener = MagicMock(side_effect=[http_error(404), Response(body)])
    monkeypatch.setattr(llm_processor.request, "urlopen", opener)

    assert processor._responses_json({"groups": []}) == RESULT


@pytest.mark.parametrize(
    "status,message",
    [
        (400, "Invalid request body"),
        (401, "Invalid API key"),
        (404, "model_not_found"),
        (429, "Rate limit exceeded"),
        (503, "The model is overloaded"),
    ],
)
def test_unrelated_http_errors_do_not_switch_protocol(monkeypatch, processor, status, message):
    opener = MagicMock(side_effect=http_error(status, message))
    monkeypatch.setattr(llm_processor.request, "urlopen", opener)

    with pytest.raises(RuntimeError, match=message):
        processor._responses_json({"groups": []})

    opener.assert_called_once()
    assert opener.call_args.args[0].full_url.endswith("/responses")


def test_responses_overload_stream_error_does_not_switch_protocol(monkeypatch, processor):
    body = sse({"type": "error", "error": {"message": "The model is overloaded"}})
    opener = MagicMock(return_value=Response(body))
    monkeypatch.setattr(llm_processor.request, "urlopen", opener)

    with pytest.raises(RuntimeError, match="overloaded"):
        processor._responses_json({"groups": []})

    opener.assert_called_once()


INVALID_CHAT_RESPONSES = [
    pytest.param(sse(chunk(RESULT_TEXT)), id="eof-with-parseable-content"),
    pytest.param(sse(chunk(RESULT_TEXT, finish_reason="stop")), id="eof-after-stop-without-done"),
    pytest.param(sse(chunk(RESULT_TEXT)) + sse("[DONE]"), id="done-without-stop"),
    pytest.param(sse(chunk(RESULT_TEXT, finish_reason="length")) + sse("[DONE]"), id="length-finish"),
    pytest.param(sse(chunk(RESULT_TEXT, finish_reason="content_filter")) + sse("[DONE]"), id="content-filter-finish"),
    pytest.param(
        sse(chunk(RESULT_TEXT)) + sse({"error": {"message": "gateway failed"}}),
        id="error-after-parseable-content",
    ),
    pytest.param(
        sse(chunk(RESULT_TEXT, finish_reason="stop")) + sse({"error": {"message": "gateway failed"}}),
        id="error-after-stop-before-done",
    ),
    pytest.param(chat_json(finish_reason="length"), id="ordinary-json-length-finish"),
    pytest.param(chat_json(text="truncated {"), id="ordinary-json-invalid-content"),
]


@pytest.mark.parametrize("body", INVALID_CHAT_RESPONSES)
def test_invalid_chat_completion_does_not_write_or_send_report(monkeypatch, processor, report, tmp_path, body):
    args = argparse.Namespace(output_format="both", dry_run=False, input_json="")
    monkeypatch.setattr(entrypoint, "parse_args", lambda: args)
    monkeypatch.setattr(entrypoint, "load_dotenv", lambda: None)
    monkeypatch.setattr(settings, "DRY_RUN", False)
    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
    scraper = MagicMock()
    scraper.scrape_all.return_value = {"Global": {"Technology": [{"tweets": []}]}}
    data_processor = MagicMock()
    data_processor.process.return_value = deepcopy(report)
    exporter = MagicMock()
    write_report = MagicMock()
    monkeypatch.setattr(scraper_module, "TrendingScraper", lambda: scraper)
    monkeypatch.setattr(processor_module, "DataProcessor", lambda: data_processor)
    monkeypatch.setattr(llm_processor, "LLMProcessor", lambda: processor)
    monkeypatch.setattr(exporter_module, "FeishuExporter", lambda: exporter)
    monkeypatch.setattr(entrypoint, "write_json_output", write_report)
    opener = MagicMock(side_effect=[http_error(404), Response(body)])
    monkeypatch.setattr(llm_processor.request, "urlopen", opener)

    with pytest.raises(SystemExit) as exc:
        entrypoint.main()

    assert exc.value.code == 1
    assert data_processor.process.return_value == report
    assert opener.call_count == 2
    write_report.assert_not_called()
    exporter.export.assert_not_called()
    exporter.format_plain_text.assert_not_called()


@pytest.mark.parametrize("status,message", [(401, "Invalid API key"), (404, "Chat route missing")])
def test_chat_http_failure_does_not_recurse_or_return_fallback_report(monkeypatch, processor, report, status, message):
    opener = MagicMock(side_effect=[http_error(404), http_error(status, message)])
    monkeypatch.setattr(llm_processor.request, "urlopen", opener)
    original_report = deepcopy(report)

    with pytest.raises(RuntimeError, match=message):
        processor.enrich(report)

    assert opener.call_count == 2
    assert report == original_report
