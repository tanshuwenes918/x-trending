"""Final region review covers every selected event and uses only raw evidence."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from processors.region_resolver import RegionResolver, RegionResolverError


def source(number, **fields):
    return {
        "tweet_url": f"https://x.com/reporter/status/{number}",
        "author": "reporter",
        "author_name": "Reporter",
        "content": f"Source {number}",
        **fields,
    }


def report(*items, candidates=None):
    data = {"groups": [{"name": "unchanged group", "items": list(items)}]}
    if candidates is not None:
        data["editorial_candidates"] = candidates
    return data


def review(item, regions=None, reason="没有可确认的新增地区"):
    return {"tweet_url": item["tweet_url"], "related_regions": regions or [], "reason": reason}


def resolve(data, rows):
    callback = Mock(return_value={"items": rows})
    return RegionResolver().resolve(data, callback), callback


def test_known_product_and_person_identity_can_support_country_absent_from_source():
    grok = source(1, content="Grok Bot 有什么新功能", subject_label="Grok Bot", summary="Grok产品更新", related_regions=[])
    musk = source(2, content="2008年，埃隆·马斯克推销汽车", subject_label="埃隆·马斯克", summary="马斯克创业经历", related_regions=[])
    data = report(grok, musk, candidates=[grok, musk])
    before = deepcopy(data)
    result, callback = resolve(data, [
        review(musk, [{"name": "美国", "basis": "identity", "evidence": "埃隆·马斯克"}]),
        review(grok, [{"name": "美国", "basis": "identity", "evidence": "Grok Bot"}]),
    ])

    assert data == before
    assert [row["tweet_url"] for row in result] == [grok["tweet_url"], musk["tweet_url"]]
    assert all(row["related_regions"][0]["name"] == "美国" for row in result)
    payload, prompt = callback.call_args.args
    assert len(payload["items"]) == 2
    assert payload["items"][0]["subject_label"] == "Grok Bot"
    assert "summary" not in payload["items"][0]["sources"][0]
    assert "subject_label" not in payload["items"][0]["sources"][0]
    assert "正文必须直接出现国家名" in prompt
    assert "随口@grok" in prompt


def test_review_includes_existing_regions_and_all_raw_members_of_selected_event():
    existing = [{"name": "法国", "basis": "identity", "evidence": "Kylian Mbappé"}]
    original = source(1, content="Kylian Mbappé posted a statement.")
    second = source(2, content="The statement also discusses China.")
    other_event = source(3, content="Unrelated Japan story.")
    display = {**original, "subject_label": "Kylian Mbappé", "related_regions": existing,
               "related_tweet_urls": [second["tweet_url"]]}
    result, callback = resolve(report(display, candidates=[original, second, other_event]), [
        review(display, [{"name": "中国", "basis": "content", "evidence": "China"}]),
    ])

    request_item = callback.call_args.args[0]["items"][0]
    assert request_item["existing_regions"] == existing
    assert [row["tweet_url"] for row in request_item["sources"]] == [original["tweet_url"], second["tweet_url"]]
    assert result[0]["related_regions"] == [{"name": "中国", "basis": "content", "evidence": "China"}]


def test_summary_subject_label_and_other_event_cannot_supply_source_evidence():
    first = source(1, content="An unnamed person made a statement.",
                   subject_label="Elon Musk", summary="Elon Musk visited China.")
    second = source(2, content="A Japanese venue in Japan.")
    result, _ = resolve(report(first, second, candidates=[first, second]), [
        review(first, [
            {"name": "美国", "basis": "identity", "evidence": "Elon Musk"},
            {"name": "中国", "basis": "content", "evidence": "China"},
            {"name": "日本", "basis": "content", "evidence": "Japan"},
        ], reason=""),
        review(second),
    ])

    assert result[0]["related_regions"] == []
    assert len(result[0]["region_review_notes"]) == 3
    assert result[0]["region_review_reason"]


def test_legacy_report_without_candidate_pool_uses_display_source_but_not_summary():
    item = source(1, content="马霍海滩的飞机气流", summary="This happened in France.")
    result, callback = resolve(report(item), [review(item, [
        {"name": "荷属圣马丁", "basis": "identity", "evidence": "马霍海滩"},
        {"name": "法国", "basis": "content", "evidence": "France"},
    ])])

    assert [region["name"] for region in result[0]["related_regions"]] == ["荷属圣马丁"]
    assert len(callback.call_args.args[0]["items"][0]["sources"]) == 1
    assert len(result[0]["region_review_notes"]) == 1


def test_empty_candidate_pool_also_falls_back_to_display_source():
    item = source(1, content="Grok Bot 更新")
    result, _ = resolve(report(item, candidates=[]), [review(item, [
        {"name": "美国", "basis": "identity", "evidence": "Grok Bot"},
    ])])

    assert result[0]["related_regions"][0]["name"] == "美国"


def test_global_release_scope_is_valid_with_original_product_identity_evidence():
    item = source(1, content="侠盗猎车手 VI 下载大小和预加载日期", subject_label="侠盗猎车手VI",
                  related_regions=[{"name": "美国", "basis": "identity", "evidence": "侠盗猎车手 VI"}])
    result, callback = resolve(report(item), [review(item, [
        {"name": "全球", "basis": "identity", "evidence": "侠盗猎车手 VI"},
    ], reason="本条讨论面向全球发行的预加载信息，国别不是事件范围。")])

    assert result[0]["related_regions"] == [{"name": "全球", "basis": "identity", "evidence": "侠盗猎车手 VI"}]
    prompt = callback.call_args.args[1]
    assert "不能用开发商、发行商的国别替代事件范围" in prompt
    assert "不要因为作品、人物或产品知名度高" in prompt


def test_global_scope_can_also_be_supported_by_explicit_source_text():
    item = source(1, content="《沙丘：第三部》全球门票现已开售。")
    result, _ = resolve(report(item), [review(item, [
        {"name": "全球", "basis": "content", "evidence": "全球门票"},
    ])])

    assert result[0]["related_regions"][0]["name"] == "全球"


def test_persons_cultural_identity_can_support_country_without_posters_location():
    item = source(1, author="nauqtm", author_name="のあ韓国在住", content="きゃにょん好像要离婚了，在直播上哭了。")
    result, callback = resolve(report(item), [review(item, [
        {"name": "日本", "basis": "identity", "evidence": "きゃにょん"},
    ], reason="主体为日本人物，个人生活话题的文化语境关联日本。")])

    assert [region["name"] for region in result[0]["related_regions"]] == ["日本"]
    assert "不因发帖者名写韩国在住就标韩国" in callback.call_args.args[1]


def test_explicit_unresolved_reason_is_preserved_without_fabricating_tags():
    item = source(1, content="What is happening here?", related_regions=[])
    result, _ = resolve(report(item), [review(item, reason="仅有不明媒体内容，无法确认主体或地区。")])

    assert result == [{"tweet_url": item["tweet_url"], "related_regions": [],
                       "region_review_notes": [], "region_review_reason": "仅有不明媒体内容，无法确认主体或地区。"}]


@pytest.mark.parametrize("change", [
    {"related_regions": [], "reason": " "},
    {"related_regions": [], "reason": None},
    {"related_regions": None},
    {"related_regions": [{"name": "美国", "basis": "language", "evidence": "English"}]},
    {"related_regions": [{"name": "美国", "basis": "identity"}]},
])
def test_invalid_region_schema_or_unexplained_empty_result_is_rejected(change):
    item = source(1)
    row = {**review(item), **change}
    with pytest.raises(RegionResolverError):
        resolve(report(item), [row])


@pytest.mark.parametrize("row_mode", ["omitted", "duplicate", "unknown", "not_object"])
def test_response_must_cover_every_displayed_url_exactly_once(row_mode):
    item = source(1)
    rows = {
        "omitted": [],
        "duplicate": [review(item), review(item)],
        "unknown": [review(source(99))],
        "not_object": [None],
    }[row_mode]
    with pytest.raises(RegionResolverError, match="omitted|duplicate|unknown|object"):
        resolve(report(item), rows)


@pytest.mark.parametrize("response", [None, {}, {"items": None}])
def test_response_requires_an_items_array(response):
    callback = Mock(return_value=response)
    with pytest.raises(RegionResolverError, match="items array"):
        RegionResolver().resolve(report(source(1)), callback)


@pytest.mark.parametrize("data", [
    None,
    {"groups": "invalid"},
    {"groups": [None]},
    {"groups": [{"items": [None]}]},
    report(source(1), source(1)),
    report(source(1, related_tweet_urls="bad")),
    report(source(1, related_tweet_urls=["https://x.com/other/status/2"]), candidates=[source(1)]),
])
def test_invalid_display_or_missing_raw_event_member_fails_before_request(data):
    callback = Mock()
    with pytest.raises(RegionResolverError):
        RegionResolver().resolve(data, callback)
    callback.assert_not_called()


def test_empty_report_does_not_call_model():
    callback = Mock()
    assert RegionResolver().resolve({"groups": []}, callback) == []
    callback.assert_not_called()
