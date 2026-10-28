"""Presentation regressions for region evidence, group priority and item limits."""

from copy import deepcopy
from pathlib import Path
import re
import runpy

import pytest

from config.settings import CATEGORY_GROUPS
from exporters.feishu_exporter import FeishuExporter


ROOT = Path(__file__).resolve().parents[1]
REGION_NOTE = "地区标签表示内容涉及地区，不代表当地热榜排名。"


def source(**overrides):
    return {
        "summary": "动画（吉伊卡哇动态）：这条动画媒体动态受到关注，具体内容请查看原帖。",
        "tweet_url": "https://x.com/chiikawa/status/1",
        "views": 2_000_000,
        **overrides,
    }


def report(item, action=None):
    return {
        "timestamp": "2026-10-28T16:00:00+08:00",
        "groups": [{"name": "Music / Dance / Entertainment", "items": [item]}],
        "top_actions": [] if action is None else [action],
    }


def post_text(exporter, data):
    return "".join(
        part.get("text", "")
        for message in exporter._format_messages(data)
        for block in message["content"]["post"]["zh_cn"]["content"]
        for part in block
    )


def test_regions_display_before_summary_in_post_and_preview_without_mutating_sources():
    exporter = FeishuExporter()
    item = source(related_regions=[
        {"name": " 日本 ", "evidence": "作品来源"},
        {"name": "法国", "evidence": "正文关联人物"},
        {"name": "日本", "evidence": "重复证据"},
    ])
    data = report(item)
    original = deepcopy(data)

    for text in (post_text(exporter, data), exporter.format_plain_text(data)):
        assert f"1. 【涉及：日本／法国】{item['summary']}" in text
        assert REGION_NOTE in text
        assert "作品来源" not in text
        assert "2M浏览" in text

    assert data == original


@pytest.mark.parametrize("regions", [[], None, "日本", [{"evidence": "没有地区名称"}], [{"name": "  "}]])
def test_unknown_region_is_explicit_without_guessing_from_author(regions):
    exporter = FeishuExporter()
    item = source(author="KMbappe", related_regions=regions)

    for text in (post_text(exporter, report(item)), exporter.format_plain_text(report(item))):
        assert f"1. 【涉及地区待确认】{item['summary']}" in text
        assert "【涉及：法国】" not in text


def test_actions_can_show_regions_without_changing_metrics_or_links():
    exporter = FeishuExporter()
    action = {
        "title": "动画配乐挑战",
        "action": "提供配乐模板，邀请用户生成并分享。",
        "tweet_url": "https://x.com/chiikawa/status/1",
        "views": 2_000_000,
        "related_regions": [{"name": "日本", "evidence": "作品来源"}],
    }
    data = report(source(related_regions=[]), action)

    for text in (post_text(exporter, data), exporter.format_plain_text(data)):
        assert "1. 【涉及：日本】动画配乐挑战：提供配乐模板，邀请用户生成并分享。" in text
        assert "2M浏览" in text
        assert action["tweet_url"] in text


def test_legacy_report_without_region_metadata_keeps_original_presentation():
    exporter = FeishuExporter()
    item = source()
    data = report(item, {"title": "旧建议", "action": "旧动作", "tweet_url": item["tweet_url"]})

    for text in (post_text(exporter, data), exporter.format_plain_text(data)):
        assert f"1. {item['summary']}" in text
        assert "1. 旧建议：旧动作" in text
        assert "【涉及" not in text
        assert REGION_NOTE not in text


def test_all_six_groups_keep_ai_and_entertainment_first_in_both_outputs():
    exporter = FeishuExporter()
    names = list(CATEGORY_GROUPS)
    assert names[:2] == ["AI / Tech / Creator Tools", "Music / Dance / Entertainment"]
    assert len(names) == 6
    data = {"groups": [{"name": name, "items": [source()]} for name in names], "top_actions": []}

    for text in (post_text(exporter, data), exporter.format_plain_text(data)):
        positions = [text.index(name) for name in names]
        assert positions == sorted(positions)
        assert "一、AI / Tech / Creator Tools" in text
        assert "二、Music / Dance / Entertainment" in text
        assert "Global Trending / 首页热点" not in text


def test_five_item_limit_matches_default_example_and_scheduled_workflow(monkeypatch):
    monkeypatch.delenv("MAX_ITEMS_PER_GROUP", raising=False)
    settings = runpy.run_path(str(ROOT / "config/settings.py"))
    assert settings["MAX_ITEMS_PER_GROUP"] == 5
    assert re.search(r"^MAX_ITEMS_PER_GROUP=5$", (ROOT / ".env.example").read_text(), re.MULTILINE)
    assert re.search(r"^\s+MAX_ITEMS_PER_GROUP: 5$", (ROOT / ".github/workflows/daily-trending.yml").read_text(), re.MULTILINE)

    monkeypatch.setenv("MAX_ITEMS_PER_GROUP", "3")
    assert runpy.run_path(str(ROOT / "config/settings.py"))["MAX_ITEMS_PER_GROUP"] == 3
