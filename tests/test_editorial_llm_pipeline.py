"""Organize all candidates before summarizing the selected daily stories."""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import io
import json
from unittest.mock import MagicMock
from urllib.error import HTTPError

import pytest

import config.settings as settings
import exporters.feishu_exporter as exporter_module
import main as entrypoint
from processors import data_processor, llm_processor, region_resolver, report_editor
from processors.llm_processor import LLMProcessor
import scrapers.trending_scraper as scraper_module


def configured_processor():
    return LLMProcessor(api_key="offline", base_url="https://offline.invalid/v1", model="offline", required=True)


def source(number, **fields):
    return {
        "tweet_url": f"https://x.com/creator/status/{number}",
        "author": "creator", "content": f"Source text {number}",
        "summary": "保留摘要", "source_category": "music",
        "source_categories": ["music"], **fields,
    }


def old_report():
    return {"groups": [{"name": "Music / Dance / Entertainment", "items": [source(1)]}], "top_actions": []}


def region_rows(data, **overrides):
    return [
        {"tweet_url": item["tweet_url"], "related_regions": deepcopy(item.get("related_regions", [])),
         "region_review_notes": [], "region_review_reason": "Unknown subject has no confirmed association.",
         **overrides}
        for group in data["groups"] for item in group["items"]
    ]


def mock_region_review(monkeypatch):
    resolve = MagicMock(side_effect=lambda data, request_json: region_rows(data))
    monkeypatch.setattr(region_resolver.RegionResolver, "resolve", resolve)
    return resolve


def test_recommended_actions_keep_the_selected_sources_region_evidence():
    data = old_report()
    item = data["groups"][0]["items"][0]
    item["related_regions"] = [{"name": "日本", "basis": "content", "evidence": "Japan"}]
    configured_processor()._merge_result(data, {
        "groups": [], "top_actions": [{"tweet_url": item["tweet_url"], "title": "创作主题", "action": "提供音乐模板"}],
    })
    assert data["top_actions"][0]["related_regions"] == item["related_regions"]
    assert data["top_actions"][0]["related_regions"] is not item["related_regions"]


def test_all_candidates_are_organized_before_only_final_items_are_summarized(monkeypatch):
    processor = configured_processor()
    candidates = [source(number) for number in range(50)]
    data = {**old_report(), "editorial_candidates": candidates}
    events = []
    metadata = [{"tweet_url": item["tweet_url"], "subject_label": "已识别主体"} for item in candidates]

    def organize(self, received, request_json):
        events.append("organize")
        assert received is candidates
        assert len(received) == 50
        assert request_json({"candidates": received}, "editorial instructions") == {"items": metadata}
        return metadata

    def apply(self, report, received):
        events.append("select")
        assert received is metadata
        report["groups"] = [
            {"name": name, "items": [source(group_index * 5 + item_index, subject_label="已识别主体") for item_index in range(5)]}
            for group_index, name in enumerate(settings.CATEGORY_GROUPS)
        ]
        report["editorial_applied"] = True

    def respond(payload, prompt=None):
        if prompt is not None:
            events.append("editorial request")
            assert prompt == "editorial instructions"
            return {"items": metadata}
        events.append("summary request")
        assert data["editorial_applied"] is True
        assert data["region_review_completed"] is True
        items = [item for group in payload["groups"] for item in group["items"]]
        assert len(items) == 30
        assert all(item["subject_label"] == "已识别主体" for item in items)
        return {"groups": [], "top_actions": []}

    def resolve(self, received, request_json):
        events.append("regions")
        assert received is data
        assert len([item for group in received["groups"] for item in group["items"]]) == 30
        return region_rows(received)

    monkeypatch.setattr(report_editor.ReportEditor, "organize", organize)
    monkeypatch.setattr(data_processor.DataProcessor, "apply_editorial_metadata", apply, raising=False)
    monkeypatch.setattr(region_resolver.RegionResolver, "resolve", resolve)
    monkeypatch.setattr(processor, "_responses_json", respond)

    assert processor.enrich(data) is data
    assert events == ["organize", "editorial request", "select", "regions", "summary request"]
    assert data["llm_used"] is True


def test_summary_failure_preserves_applied_selection_and_retry_skips_organization(monkeypatch):
    processor = configured_processor()
    data = {**old_report(), "editorial_candidates": [source(1), source(2)]}
    organize = MagicMock(return_value=[{"tweet_url": source(2)["tweet_url"]}])

    def apply(self, report, metadata):
        report["groups"][0]["items"] = [source(2, subject_label="Chiikawa（吉伊卡哇）")]
        report["editorial_applied"] = True

    monkeypatch.setattr(report_editor.ReportEditor, "organize", organize)
    monkeypatch.setattr(data_processor.DataProcessor, "apply_editorial_metadata", apply, raising=False)
    resolve = mock_region_review(monkeypatch)
    japan = {"name": "日本", "basis": "identity", "evidence": "Chiikawa"}
    resolve.side_effect = lambda report, request_json: region_rows(
        report, related_regions=[japan], region_review_reason="Known Japanese IP."
    )
    respond = MagicMock(side_effect=RuntimeError("summary unavailable"))
    monkeypatch.setattr(processor, "_responses_json", respond)

    with pytest.raises(RuntimeError, match="summary unavailable"):
        processor.enrich(data)

    assert data["editorial_applied"] is True
    assert data["region_review_completed"] is True
    resolve.assert_called_once()
    assert data["groups"][0]["items"][0]["subject_label"] == "Chiikawa（吉伊卡哇）"
    assert processor.retry_checkpoint == data
    assert processor.retry_checkpoint is not data
    assert processor.retry_checkpoint["groups"][0]["items"][0]["related_regions"] == [japan]
    data["groups"][0]["items"][0]["summary"] = "partial summary mutation"
    assert processor.retry_checkpoint["groups"][0]["items"][0]["summary"] == "保留摘要"
    assert "llm_used" not in data
    respond.side_effect = None
    respond.return_value = {"groups": [], "top_actions": []}
    retry_data = processor.retry_checkpoint
    processor.enrich(retry_data)
    assert organize.call_count == 1
    assert resolve.call_count == 1
    assert retry_data["llm_used"] is True
    assert retry_data["groups"][0]["items"][0]["related_regions"] == [japan]
    assert processor.retry_checkpoint is None


def test_region_review_adds_subject_associations_without_changing_report_selection(monkeypatch):
    japan = {"name": "日本", "basis": "identity", "evidence": "ngnchiikawa"}
    united_states = {"name": "美国", "basis": "identity", "evidence": "埃隆·马斯克"}
    chiikawa = source(1, event_id="chiikawa", score=300, subject_label="Chiikawa", related_regions=[japan])
    musk = source(2, event_id="musk", score=200, subject_label="埃隆·马斯克", related_regions=[])
    data = {
        "editorial_applied": True,
        "groups": [{"name": "AI / Tech / Creator Tools", "items": [chiikawa, musk]}],
        "editorial_metadata": [
            {"tweet_url": chiikawa["tweet_url"], "event_id": "chiikawa", "related_regions": [deepcopy(japan)]},
            {"tweet_url": musk["tweet_url"], "event_id": "musk", "related_regions": []},
            {"tweet_url": source(3)["tweet_url"], "event_id": "musk", "related_regions": []},
        ],
        "top_actions": [deepcopy(musk)],
    }
    before = deepcopy(data)
    resolve = MagicMock(return_value=[
        {"tweet_url": chiikawa["tweet_url"], "related_regions": [], "region_review_notes": [],
         "region_review_reason": "No additional association."},
        {"tweet_url": musk["tweet_url"], "related_regions": [united_states], "region_review_notes": [],
         "region_review_reason": "美国汽车创业及企业活动关联。"},
    ])
    monkeypatch.setattr(region_resolver.RegionResolver, "resolve", resolve)

    configured_processor()._complete_report_regions(data)

    resolve.assert_called_once()
    assert chiikawa["related_regions"] == [japan]
    assert musk["related_regions"] == [united_states]
    assert data["top_actions"][0]["related_regions"] == [united_states]
    assert [item["related_regions"] for item in data["editorial_metadata"]] == [[japan], [united_states], [united_states]]
    for old, new in zip(before["groups"][0]["items"], data["groups"][0]["items"]):
        for field in ("tweet_url", "event_id", "score", "subject_label", "summary"):
            assert new[field] == old[field]
    assert data["groups"][0]["name"] == before["groups"][0]["name"]
    musk["related_regions"][0]["name"] = "changed after review"
    assert data["editorial_metadata"][1]["related_regions"][0]["name"] == "美国"
    assert data["top_actions"][0]["related_regions"][0]["name"] == "美国"


def test_region_failure_preserves_selection_checkpoint_and_retries_only_region_review(monkeypatch):
    processor = configured_processor()
    data = {**old_report(), "editorial_applied": True, "editorial_candidates": [source(1)]}
    before = deepcopy(data)
    organize = MagicMock(side_effect=AssertionError("Selection must not run again"))
    monkeypatch.setattr(report_editor.ReportEditor, "organize", organize)
    resolve = MagicMock(side_effect=RuntimeError("region service unavailable"))
    monkeypatch.setattr(region_resolver.RegionResolver, "resolve", resolve)
    respond = MagicMock(return_value={"groups": [], "top_actions": []})
    monkeypatch.setattr(processor, "_responses_json", respond)

    with pytest.raises(RuntimeError, match="region service unavailable"):
        processor.enrich(data)

    assert not data.get("region_review_completed")
    assert data == before
    assert processor.retry_checkpoint == before
    assert processor.retry_checkpoint is not data
    respond.assert_not_called()
    checkpoint = processor.retry_checkpoint
    resolve.side_effect = lambda report, request_json: region_rows(report)

    processor.enrich(checkpoint)

    assert checkpoint["region_review_completed"] is True
    assert checkpoint["llm_used"] is True
    assert resolve.call_count == 2
    organize.assert_not_called()
    respond.assert_called_once()


def test_global_event_scope_replaces_country_of_product_origin_everywhere(monkeypatch):
    original = {"name": "美国", "basis": "identity", "evidence": "侠盗猎车手 VI"}
    worldwide = {"name": "全球", "basis": "identity", "evidence": "侠盗猎车手 VI"}
    gta = source(1, event_id="gta", content="侠盗猎车手 VI 预加载与发售时间", score=100,
                 subject_label="GTA VI", related_regions=[original])
    data = {
        "editorial_applied": True,
        "groups": [{"name": "Gaming / Sports / Youth Culture", "items": [gta]}],
        "editorial_metadata": [
            {"tweet_url": gta["tweet_url"], "event_id": "gta", "related_regions": [deepcopy(original)]},
            {"tweet_url": source(2)["tweet_url"], "event_id": "gta", "related_regions": [deepcopy(original)]},
        ],
        "top_actions": [deepcopy(gta)],
    }
    resolve = MagicMock(return_value=[
        {"tweet_url": gta["tweet_url"], "related_regions": [worldwide], "region_review_notes": [],
         "region_review_reason": "全球发行信息，按当前事件覆盖范围标注。"},
    ])
    monkeypatch.setattr(region_resolver.RegionResolver, "resolve", resolve)

    configured_processor()._complete_report_regions(data)

    assert gta["related_regions"] == [worldwide]
    assert all(row["related_regions"] == [worldwide] for row in data["editorial_metadata"])
    assert data["top_actions"][0]["related_regions"] == [worldwide]
    assert gta["score"] == 100
    assert gta["event_id"] == "gta"
    assert gta["summary"] == "保留摘要"


def test_real_editor_and_selection_contract_summarizes_distinct_top_five_per_group(monkeypatch):
    processor = configured_processor()
    monkeypatch.setattr(data_processor, "MAX_ITEMS_PER_GROUP", 5)
    now = datetime.now(timezone.utc).isoformat()
    categories = {}
    events = []
    for group_index, (group, source_categories) in enumerate(settings.CATEGORY_GROUPS.items()):
        tweets = [source(group_index * 8 + offset, created_at=now, views=offset * 100) for offset in range(8)]
        categories[source_categories[0]] = [{"trending_term": source_categories[0], "tweets": tweets}]
        # Two sources report the first concrete event. They must consume one
        # event slot, and the higher-heat source represents it without summing.
        members = [[tweets[0], tweets[1]], *[[item] for item in tweets[2:]]]
        events.extend({
            "tweet_urls": [item["tweet_url"] for item in event_members], "group": group,
            "subject_label": f"Topic {group_index}", "related_regions": [], "sensitive": False,
        } for event_members in members)
    data = data_processor.DataProcessor().process({"Global": categories})
    calls = []

    def respond(payload, prompt=None):
        calls.append((payload, prompt))
        if "candidates" in payload:
            assert len(payload["candidates"]) == 48
            return {"events": events}
        if prompt is not None:
            # Keep the real country resolver in this fixture: every selected
            # URL must receive a review, even if no country can be established.
            assert len(payload["items"]) == 30
            return {"items": [
                {"tweet_url": item["tweet_url"], "related_regions": [], "reason": "Unknown fictional topic."}
                for item in payload["items"]
            ]}
        assert len(payload["groups"]) == 6
        assert all(len(group["items"]) == 5 for group in payload["groups"])
        assert len({item["tweet_url"] for group in payload["groups"] for item in group["items"]}) == 30
        return {"groups": [], "top_actions": []}

    monkeypatch.setattr(processor, "_responses_json", respond)

    processor.enrich(data)

    assert len(calls) == 3
    assert data["summary"]["eligible_tweet_count"] == 30
    assert data["summary"]["event_count"] == 42
    assert data["editorial_applied"] is True
    assert data["region_review_completed"] is True
    assert all(item["region_review_reason"] for group in data["groups"] for item in group["items"])
    assert all(item["region_scope"] == "global" for group in data["groups"] for item in group["items"])
    assert [group["name"] for group in data["groups"]][:2] == list(settings.CATEGORY_GROUPS)[:2]
    assert all([item["views"] for item in group["items"]] == [700, 600, 500, 400, 300] for group in data["groups"])


def test_old_saved_input_keeps_single_summary_request_without_editorial_call(monkeypatch):
    data = old_report()
    processor = configured_processor()
    organize = MagicMock(side_effect=AssertionError("Legacy inputs must keep working"))
    monkeypatch.setattr(report_editor.ReportEditor, "organize", organize)
    resolve = MagicMock(side_effect=AssertionError("Legacy inputs must not require a region review"))
    monkeypatch.setattr(region_resolver.RegionResolver, "resolve", resolve)
    request_json = MagicMock(return_value={"groups": [], "top_actions": []})
    monkeypatch.setattr(processor, "_responses_json", request_json)

    processor.enrich(data)

    organize.assert_not_called()
    resolve.assert_not_called()
    request_json.assert_called_once()
    assert data["llm_used"] is True


def test_editorial_failure_stops_pipeline_before_summary_or_feishu(monkeypatch, tmp_path):
    data = {**old_report(), "editorial_candidates": [source(1)]}
    processor = configured_processor()
    editorial_failure = MagicMock(side_effect=ValueError("incomplete editorial coverage"))
    monkeypatch.setattr(report_editor.ReportEditor, "organize", editorial_failure)
    request_json = MagicMock()
    monkeypatch.setattr(processor, "_responses_json", request_json)
    monkeypatch.setattr(entrypoint, "parse_args", lambda: argparse.Namespace(output_format="both", dry_run=False, input_json=""))
    monkeypatch.setattr(entrypoint, "load_dotenv", lambda: None)
    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(settings, "DRY_RUN", False)
    scraper = MagicMock()
    scraper.scrape_all.return_value = {"Global": {}}
    monkeypatch.setattr(scraper_module, "TrendingScraper", lambda: scraper)
    processing = MagicMock()
    processing.process.return_value = deepcopy(data)
    monkeypatch.setattr(data_processor, "DataProcessor", lambda: processing)
    monkeypatch.setattr(llm_processor, "LLMProcessor", lambda: processor)
    exporter = MagicMock()
    monkeypatch.setattr(exporter_module, "FeishuExporter", lambda: exporter)

    with pytest.raises(SystemExit):
        entrypoint.main()

    request_json.assert_not_called()
    exporter.export.assert_not_called()
    assert len(list(tmp_path.glob("retry_input_*.json"))) == 1
    assert not list(tmp_path.glob("x_trending_*.json"))


class Response(io.BytesIO):
    def __init__(self, body):
        super().__init__(body)
        self.headers = {"Content-Type": "application/json"}


@pytest.mark.parametrize("fallback", [False, True])
def test_custom_editorial_prompt_is_identical_on_responses_and_chat_fallback(monkeypatch, fallback):
    processor = configured_processor()
    payload = {"candidates": [{"tweet_url": "https://x.com/u/status/1"}]}
    result = {"items": [{"tweet_url": "https://x.com/u/status/1", "editorial_group": "music"}]}
    prompt = "Classify each candidate; return JSON."
    if fallback:
        responses_error = HTTPError("https://offline.invalid/v1/responses", 404, "Missing endpoint", {}, io.BytesIO(b"{}"))
        body = {"choices": [{"index": 0, "finish_reason": "stop", "message": {"content": json.dumps(result)}}]}
        responses = [responses_error, Response(json.dumps(body).encode())]
    else:
        responses = [Response(json.dumps({"output_text": json.dumps(result)}).encode())]
    opener = MagicMock(side_effect=responses)
    monkeypatch.setattr(llm_processor.request, "urlopen", opener)

    assert processor._responses_json(payload, prompt=prompt) == result

    first_body = json.loads(opener.call_args_list[0].args[0].data)
    assert first_body["instructions"] == prompt
    assert json.loads(first_body["input"][0]["content"][0]["text"]) == payload
    if fallback:
        chat_body = json.loads(opener.call_args_list[1].args[0].data)
        assert chat_body["messages"] == [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]


def test_homepage_origin_does_not_block_safe_classified_actions_but_sensitivity_does():
    first = source(1, source_categories=["Global Trending", "music"], editorial_sensitive=False)
    second = source(2, source_categories=["Global Trending", "Sports"], editorial_sensitive=True)
    third = source(3)
    report = {"groups": [{"name": "Music / Dance / Entertainment", "items": [first, second]}], "top_actions": []}
    result = {"groups": [], "top_actions": [
        {"tweet_url": item["tweet_url"], "title": "已选热点", "action": "制作原创配乐模板"}
        for item in (first, second, third)
    ]}

    LLMProcessor()._merge_result(report, result)

    assert [action["tweet_url"] for action in report["top_actions"]] == [first["tweet_url"]]


def test_subject_and_regions_are_summary_context_without_overriding_unseen_media():
    item = source(
        1, content="", subject_label="Chiikawa（吉伊卡哇）", related_regions=["日本"],
        editorial_sensitive=False, media_types=["image"],
        summary="Chiikawa（吉伊卡哇）发布图片动态；原帖未附正文，请查看原帖。",
    )
    report = {"groups": [{"name": "Music / Dance / Entertainment", "items": [item]}], "top_actions": []}
    processor = LLMProcessor()
    payload = processor._build_payload(report)["groups"][0]["items"][0]
    assert payload["subject_label"] == item["subject_label"]
    assert payload["related_regions"] == ["日本"]
    assert payload["media_requires_review"] is True
    processor._merge_result(report, {"groups": [{"items": [{"tweet_url": item["tweet_url"], "summary": "无依据的剧情介绍"}]}]})
    assert "剧情介绍" not in item["summary"]
