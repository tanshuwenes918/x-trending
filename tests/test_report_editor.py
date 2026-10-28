"""Event organization preserves coverage and keeps unseen media separate."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from config.settings import CATEGORY_GROUPS
from processors.report_editor import ReportEditor, ReportEditorError


TECH = "AI / Tech / Creator Tools"
SPORTS = "Gaming / Sports / Youth Culture"
ENTERTAINMENT = "Music / Dance / Entertainment"
NEWS = "News / Society / Sensitive Topics"


def candidate(number, **fields):
    return {
        "tweet_url": f"https://x.com/reporter/status/{number}",
        "author": "reporter",
        "author_name": "Reporter",
        "content": f"Original source {number}",
        "source_categories": ["News"],
        "score": number * 100,
        **fields,
    }


def event(*sources, group=NEWS, subject_label="", related_regions=None, sensitive=False, **fields):
    return {
        "tweet_urls": [source["tweet_url"] for source in sources],
        "group": group,
        "subject_label": subject_label,
        "related_regions": related_regions or [],
        "sensitive": sensitive,
        **fields,
    }


def organize(sources, events):
    callback = Mock(return_value={"events": events})
    return ReportEditor().organize(sources, callback), callback


def test_same_specific_event_gets_stable_id_but_distinct_brazil_story_stays_separate():
    first = candidate(1, content="Flávio Bolsonaro flew to the United States, a newspaper reports.")
    repost = candidate(2, content="Another outlet reports the same flight to the United States.")
    other = candidate(3, content="Lula gave a speech about a separate election topic.")
    sources = [repost, other, first]
    before = deepcopy(sources)

    result, callback = organize(sources, [event(first, repost, subject_label="弗拉维奥出境", sensitive=True), event(other, sensitive=True)])
    reversed_result, _ = organize(list(reversed(sources)), [event(other, sensitive=True), event(repost, first, subject_label="另一个自由标题", sensitive=True)])

    assert sources == before
    assert [row["tweet_url"] for row in result] == [source["tweet_url"] for source in sources]
    by_url = {row["tweet_url"]: row for row in result}
    assert by_url[first["tweet_url"]]["event_id"] == by_url[repost["tweet_url"]]["event_id"]
    assert by_url[first["tweet_url"]]["event_id"] != by_url[other["tweet_url"]]["event_id"]
    assert {row["tweet_url"]: row["event_id"] for row in result} == {row["tweet_url"]: row["event_id"] for row in reversed_result}
    assert by_url[first["tweet_url"]]["editorial_sensitive"] is True
    payload, prompt = callback.call_args.args
    assert payload["allowed_groups"] == list(CATEGORY_GROUPS)
    assert payload["candidates"][0]["content"] == repost["content"]
    assert "不能把所有巴西政治合并" in prompt


def test_known_media_accounts_can_have_readable_subjects_without_inventing_events():
    mbappe = candidate(1, author="KMbappe", author_name="Kylian Mbappé", content="", media_types=["image"])
    chiikawa = candidate(2, author="ngnchiikawa", author_name="ちいかわアニメ火金", content="", media_types=["image"])
    result, callback = organize([mbappe, chiikawa], [
        event(mbappe, group=SPORTS, subject_label="Kylian Mbappé", related_regions=[{"name": "法国", "basis": "identity", "evidence": "Kylian Mbappé"}]),
        event(chiikawa, group=ENTERTAINMENT, subject_label="Chiikawa（吉伊卡哇）", related_regions=[{"name": "日本", "basis": "identity", "evidence": "ngnchiikawa"}]),
    ])

    assert result[0]["subject_label"] == "Kylian Mbappé"
    assert result[1]["subject_label"] == "Chiikawa（吉伊卡哇）"
    assert result[1]["related_regions"][0]["name"] == "日本"
    assert all("summary" not in row and "score" not in row for row in result)
    prompt = callback.call_args.args[1]
    assert "严禁仅凭语言推国家" in prompt
    assert "不能凭常识或未看到的图片添加中国" in prompt


def test_regions_require_real_source_evidence_but_unsupported_tag_does_not_discard_events():
    post = candidate(1, author="KMbappe", author_name="Kylian Mbappé", content="", media_types=["image"])
    result, _ = organize([post], [event(post, group=SPORTS, related_regions=[
        {"name": "法国", "basis": "identity", "evidence": "Kylian Mbappé"},
        {"name": "中国", "basis": "content", "evidence": "China"},
    ])])

    assert result[0]["related_regions"] == [{"name": "法国", "basis": "identity", "evidence": "Kylian Mbappé"}]
    assert len(result[0]["region_review_notes"]) == 1
    assert "中国" in result[0]["region_review_notes"][0]


def test_explicit_multi_region_evidence_and_media_alt_are_kept_once():
    post = candidate(1, content="France and China discussed an exchange.", media_alt_texts=["A venue in Japan."])
    result, _ = organize([post], [event(post, related_regions=[
        {"name": "法国", "basis": "content", "evidence": "France"},
        {"name": "中国", "basis": "content", "evidence": "China"},
        {"name": "日本", "basis": "media_alt", "evidence": "Japan"},
        {"name": "法国", "basis": "content", "evidence": "France"},
    ])])

    assert [region["name"] for region in result[0]["related_regions"]] == ["法国", "中国", "日本"]
    assert result[0]["region_review_notes"] == []


def test_separate_unseen_media_from_same_author_cannot_be_merged():
    first = candidate(1, content="", media_types=["image"], media_urls=["https://pbs.twimg.com/media/assetA?name=small"])
    second = candidate(2, content="", media_types=["image"], media_urls=["https://pbs.twimg.com/media/assetB?name=small"])

    with pytest.raises(ReportEditorError, match="media-only"):
        organize([first, second], [event(first, second)])


def test_same_x_image_at_different_sizes_can_share_an_event():
    first = candidate(1, content="", media_types=["image"], media_urls=["https://pbs.twimg.com/media/sameAsset?format=jpg&name=small"])
    second = candidate(2, content="", media_types=["image"], media_urls=["https://pbs.twimg.com/media/sameAsset?format=jpg&name=large"])

    result, _ = organize([first, second], [event(first, second)])

    assert result[0]["event_id"] == result[1]["event_id"]


def test_distinct_pairs_of_shared_media_cannot_be_combined_as_one_event():
    sources = [candidate(number, content="", media_types=["image"], media_urls=[f"https://pbs.twimg.com/media/asset{number // 2}"])
               for number in range(4)]

    with pytest.raises(ReportEditorError, match="Independent media-only"):
        organize(sources, [event(*sources)])


def test_generic_alt_and_global_source_do_not_support_country_tags():
    post = candidate(1, content="", author="unknown", author_name="Unknown", media_types=["image"], media_alt_texts=["Image"], source_categories=["Global Trending"])
    result, _ = organize([post], [event(post, related_regions=[
        {"name": "美国", "basis": "media_alt", "evidence": "Image"},
        {"name": "日本", "basis": "identity", "evidence": "Global Trending"},
    ])])

    assert result[0]["related_regions"] == []
    assert len(result[0]["region_review_notes"]) == 2


@pytest.mark.parametrize("bad_events", [
    [],
    [{"tweet_urls": ["https://x.com/other/status/1"], "group": TECH}],
])
def test_missing_or_unknown_sources_fail_instead_of_dropping_candidates(bad_events):
    with pytest.raises(ReportEditorError, match="omitted|unknown"):
        organize([candidate(1)], bad_events)


def test_duplicate_event_membership_fails():
    post = candidate(1)

    with pytest.raises(ReportEditorError, match="more than one"):
        organize([post], [event(post), event(post)])
    with pytest.raises(ReportEditorError, match="more than one"):
        organize([post], [event(post, post)])


@pytest.mark.parametrize("changes", [
    {"group": "Homepage"},
    {"subject_label": None},
    {"sensitive": "false"},
    {"related_regions": None},
    {"related_regions": [{"name": "美国", "basis": "language", "evidence": "English"}]},
    {"related_regions": [{"name": "美国", "basis": "identity"}]},
])
def test_invalid_event_metadata_is_rejected(changes):
    post = candidate(1)
    model_event = event(post)
    model_event.update(changes)

    with pytest.raises(ReportEditorError):
        organize([post], [model_event])


@pytest.mark.parametrize("sources", [None, [None], [{}], [candidate(1), candidate(1)]])
def test_invalid_candidate_inputs_fail_before_model_call(sources):
    callback = Mock()

    with pytest.raises(ReportEditorError):
        ReportEditor().organize(sources, callback)

    callback.assert_not_called()


def test_empty_candidates_do_not_call_the_model():
    callback = Mock()

    assert ReportEditor().organize([], callback) == []
    callback.assert_not_called()
