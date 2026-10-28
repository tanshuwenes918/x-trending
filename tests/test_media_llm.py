"""Keep media-only and unclassified homepage reports grounded in source facts."""

from copy import deepcopy
from unittest.mock import patch
import io
import json

import pytest

from config.settings import HOMEPAGE_CATEGORY, HOMEPAGE_GROUP_NAME
from processors import llm_processor
from processors.llm_processor import LLMProcessor


def media_item(**overrides):
    item = {
        "tweet_url": "https://x.com/athlete/status/1",
        "author": "athlete",
        "author_name": "An Athlete",
        "content": "",
        "media_types": ["image"],
        "media_urls": ["https://pbs.twimg.com/media/example.jpg"],
        "media_alt_texts": ["Image"],
        "summary": "首页热点（@athlete 图片动态）：该推文无正文，具体内容请查看原帖。",
        "source_category": HOMEPAGE_CATEGORY,
        "source_categories": [HOMEPAGE_CATEGORY],
        "homepage_rank": 1,
    }
    item.update(overrides)
    return item


def model_output(item, summary="体育（官宣重大事件）：球员宣布了未经来源支持的消息。"):
    return {
        "groups": [{"name": HOMEPAGE_GROUP_NAME, "items": [
            {"tweet_url": item["tweet_url"], "summary": summary},
        ]}],
        "top_actions": [{"tweet_url": item["tweet_url"], "title": "球星挑战", "action": "制作模板引流"}],
    }


@pytest.mark.parametrize("alt_texts", [[], ["Image"], ["图像"], ["VIDEO"], None])
def test_unseen_media_cannot_receive_model_invented_summary_or_actions(alt_texts):
    item = media_item(media_alt_texts=alt_texts)
    original = deepcopy(item)
    data = {
        "groups": [{"name": HOMEPAGE_GROUP_NAME, "items": [item]}],
        "top_actions": [{"tweet_url": item["tweet_url"], "title": "旧默认动作"}],
    }

    LLMProcessor()._merge_result(data, model_output(item))

    assert item == original
    assert data["top_actions"] == []


def test_descriptive_alt_can_support_summary_without_allowing_homepage_actions():
    item = media_item(media_alt_texts=["A red sports car parked in front of a garage."])
    data = {"groups": [{"name": HOMEPAGE_GROUP_NAME, "items": [item]}], "top_actions": []}
    summary = "首页热点（汽车配图受关注）：媒体描述为一辆停在车库前的红色跑车。"

    LLMProcessor()._merge_result(data, model_output(item, summary))

    assert item["summary"] == summary
    assert data["top_actions"] == []


def test_model_omissions_never_remove_items_or_source_fields():
    media = media_item()
    text = media_item(
        tweet_url="https://x.com/creator/status/2",
        content="New music production workflow.",
        source_category="music", source_categories=["music"],
    )
    data = {"groups": [{"name": "Music / Dance / Entertainment", "items": [media, text]}]}
    original = deepcopy(data)

    LLMProcessor()._merge_result(data, {"groups": [], "top_actions": []})

    assert data == original


def test_news_provenance_cannot_bypass_action_filter_by_moving_to_another_group():
    item = media_item(content="A source-reported public event.", source_category="Sports", source_categories=["Sports", "News"])
    data = {"groups": [{"name": "Gaming / Sports / Youth Culture", "items": [item]}], "top_actions": []}

    LLMProcessor()._merge_result(data, model_output(item, "社会（新闻动态）：原文报道了一则事件。"))

    assert data["top_actions"] == []


def test_text_with_media_can_still_receive_grounded_summary_and_action():
    item = media_item(content="Introducing a new music production workflow.", source_category="music", source_categories=["music"])
    data = {"groups": [{"name": "Music / Dance / Entertainment", "items": [item]}], "top_actions": []}
    summary = "音乐（创作流程发布）：创作者介绍新的音乐制作流程，可观察教程传播。"

    LLMProcessor()._merge_result(data, model_output(item, summary))

    assert item["summary"] == summary
    assert len(data["top_actions"]) == 1


def test_payload_retains_media_provenance_and_flags_review_without_sending_images():
    item = media_item()
    payload = LLMProcessor()._build_payload({"groups": [{"name": HOMEPAGE_GROUP_NAME, "items": [item]}]})
    source = payload["groups"][0]["items"][0]

    for field in ("author_name", "media_types", "media_alt_texts", "source_categories", "homepage_rank"):
        assert source[field] == item[field]
    assert source["media_requires_review"] is True
    assert "media_urls" not in source


def test_grounding_instructions_apply_to_dynamic_group_count():
    processor = LLMProcessor(api_key="offline", base_url="https://offline.invalid/v1", model="offline")
    response = io.BytesIO(json.dumps({"output_text": '{"groups":[],"top_actions":[]}'}).encode())
    response.headers = {}

    with patch.object(llm_processor.request, "urlopen", return_value=response) as opener:
        processor._responses_json({"groups": []})

    body = json.loads(opener.call_args.args[0].data)
    assert "前 6 个分类" not in body["instructions"]
    assert "每个分组、每条推文" in body["instructions"]
    assert "社区注释" in body["instructions"]
    assert "本请求没有提供图像或视频内容" in body["instructions"]
