"""Regression tests for homepage coverage, media posts and category allocation."""

from copy import deepcopy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from config.settings import CATEGORY_GROUPS, DEFAULT_CATEGORIES, HOMEPAGE_CATEGORY, HOMEPAGE_GROUP_NAME
from processors import data_processor as processor_module
from processors.data_processor import DataProcessor


TECH_GROUP = "AI / Tech / Creator Tools"
SPORTS_GROUP = "Gaming / Sports / Youth Culture"
NEWS_GROUP = "News / Society / Sensitive Topics"


def tweet(number, *, hours_ago=1, **fields):
    return {
        "author": "creator",
        "content": f"Post {number}",
        "created_at": (datetime.now(ZoneInfo("UTC")) - timedelta(hours=hours_ago)).isoformat(),
        "tweet_url": f"https://x.com/creator/status/{number}",
        **fields,
    }


def process_categories(categories):
    raw = {
        "Global": {
            category: [{"trending_term": category, "tweets": items}]
            for category, items in categories.items()
        }
    }
    original = deepcopy(raw)
    report = DataProcessor().process(raw)
    assert raw == original, "Processing must not modify the saved raw candidates"
    return report


def items_in(report, group_name):
    return next(group["items"] for group in report["groups"] if group["name"] == group_name)


def urls_in(report, group_name):
    return [item["tweet_url"] for item in items_in(report, group_name)]


def diagnostic(report, url):
    return next(record for record in report["processing_diagnostics"]["tweets"] if record["tweet_url"] == url)


def test_homepage_media_survives_without_a_homepage_display_group(monkeypatch):
    monkeypatch.setattr(processor_module, "MAX_ITEMS_PER_GROUP", 2)
    first = tweet(
        1, author="KMbappe", author_name="Kylian Mbappé", content="", views=47_000_000,
        media_urls=["https://pbs.twimg.com/media/first.jpg"], media_types=["photo"],
        media_alt_texts=["Image"], homepage_rank=1, source_categories=[HOMEPAGE_CATEGORY],
    )
    second = tweet(2, views=1, homepage_rank=2)
    third = tweet(3, views=100_000_000, homepage_rank=3)

    report = process_categories({HOMEPAGE_CATEGORY: [first, second, third]})

    assert [group["name"] for group in report["groups"]] == list(CATEGORY_GROUPS)
    assert urls_in(report, NEWS_GROUP) == [third["tweet_url"], first["tweet_url"]]
    assert len(report["editorial_candidates"]) == 3  # All candidates reach the semantic editor.
    item = items_in(report, NEWS_GROUP)[1]
    for field in ("author_name", "media_urls", "media_types", "media_alt_texts", "homepage_rank"):
        assert item[field] == first[field]
    assert item["source_categories"] == [HOMEPAGE_CATEGORY]
    assert "Kylian Mbappé" in item["summary"]
    assert "无正文" in item["summary"] and "请查看原帖" in item["summary"]
    assert report["top_actions"] == []
    assert diagnostic(report, second["tweet_url"])["exclusion_reason"] == "group_quota"


@pytest.mark.parametrize("media", [{"media_urls": ["https://example.com/photo.jpg"]}, {"media_types": ["video"]}])
def test_media_without_author_text_is_valid_but_has_no_default_action(media):
    source = tweet(1, content="", **media)

    report = process_categories({"Sports": [source]})

    assert urls_in(report, SPORTS_GROUP) == [source["tweet_url"]]
    assert DataProcessor().validate_tweet(source)
    assert "无正文" in items_in(report, SPORTS_GROUP)[0]["summary"]
    assert report["top_actions"] == []


def test_homepage_filters_old_and_empty_posts_then_fills_from_later_entries(monkeypatch):
    monkeypatch.setattr(processor_module, "MAX_ITEMS_PER_GROUP", 2)
    old = tweet(1, hours_ago=30, homepage_rank=1)
    empty = tweet(2, content="", media_alt_texts=["An unverified description"], homepage_rank=2)
    invalid_time = tweet(3, created_at="not-a-date", homepage_rank=3)
    recent = [tweet(4, homepage_rank=4), tweet(5, homepage_rank=5)]

    report = process_categories({HOMEPAGE_CATEGORY: [old, empty, invalid_time, *recent]})

    assert urls_in(report, NEWS_GROUP) == [item["tweet_url"] for item in recent]
    assert diagnostic(report, old["tweet_url"])["exclusion_reason"] == "outside_lookback"
    assert diagnostic(report, empty["tweet_url"])["exclusion_reason"] == "invalid_fields"
    assert diagnostic(report, invalid_time["tweet_url"])["exclusion_reason"] == "invalid_timestamp"
    counts = report["processing_diagnostics"]["counts"]
    assert counts["raw_occurrences"] == counts["unique_urls"] == 5
    assert counts["valid_unique_urls"] == counts["selected_unique_urls"] == 2
    assert counts["excluded_unique_urls"] == 3
    assert counts["outside_lookback_occurrences"] == 1
    assert counts["invalid_fields_occurrences"] == 1
    assert counts["invalid_timestamp_occurrences"] == 1


def test_cross_category_candidate_can_use_second_group_when_first_group_is_full(monkeypatch):
    monkeypatch.setattr(processor_module, "MAX_ITEMS_PER_GROUP", 1)
    tech_leader = tweet(1, views=1000)
    shared = tweet(2, views=100)

    report = process_categories({"Technology": [tech_leader, shared], "Sports": [shared]})

    assert urls_in(report, TECH_GROUP) == [tech_leader["tweet_url"]]
    assert urls_in(report, SPORTS_GROUP) == [shared["tweet_url"]]
    item = items_in(report, SPORTS_GROUP)[0]
    assert item["source_category"] == "Sports"
    assert item["source_categories"] == ["Technology", "Sports"]
    record = diagnostic(report, shared["tweet_url"])
    assert record["selected_group"] == SPORTS_GROUP
    assert record["occurrence_count"] == 2
    assert [occurrence["raw_index"] for occurrence in record["occurrences"]] == [2, 3]
    assert report["processing_diagnostics"]["counts"]["duplicate_occurrences"] == 1


def test_cross_category_tweet_is_reassigned_to_fill_an_exclusive_source_group(monkeypatch):
    monkeypatch.setattr(processor_module, "MAX_ITEMS_PER_GROUP", 1)
    shared = tweet(1, views=1000)
    tech_only = tweet(2, views=100)

    report = process_categories({"Technology": [shared, tech_only], "Sports": [shared]})

    assert urls_in(report, TECH_GROUP) == [tech_only["tweet_url"]]
    assert urls_in(report, SPORTS_GROUP) == [shared["tweet_url"]]
    assert report["summary"]["eligible_tweet_count"] == 2


def test_category_duplicate_of_homepage_post_is_replaced_by_next_candidate(monkeypatch):
    monkeypatch.setattr(processor_module, "MAX_ITEMS_PER_GROUP", 1)
    home = tweet(1, content="", media_types=["photo"], homepage_rank=1)
    richer_copy = {**home, "content": "The original author has supplied context", "views": 1000}
    next_sport = tweet(2, views=10)

    report = process_categories({HOMEPAGE_CATEGORY: [home], "Sports": [richer_copy, next_sport]})

    assert HOMEPAGE_GROUP_NAME not in [group["name"] for group in report["groups"]]
    assert urls_in(report, SPORTS_GROUP) == [home["tweet_url"]]
    selected_home = items_in(report, SPORTS_GROUP)[0]
    assert selected_home["content"] == richer_copy["content"]
    assert selected_home["source_categories"] == [HOMEPAGE_CATEGORY, "Sports"]
    all_urls = [item["tweet_url"] for group in report["groups"] for item in group["items"]]
    assert len(all_urls) == len(set(all_urls)) == 1
    assert len(report["editorial_candidates"]) == 2


def test_deduplication_within_a_group_fills_all_available_slots(monkeypatch):
    monkeypatch.setattr(processor_module, "MAX_ITEMS_PER_GROUP", 2)
    shared = tweet(1, views=1000)
    backup = tweet(2, views=1)

    report = process_categories({"Technology": [shared], "Science": [shared, backup]})

    assert urls_in(report, TECH_GROUP) == [shared["tweet_url"], backup["tweet_url"]]
    assert items_in(report, TECH_GROUP)[0]["source_categories"] == ["Technology", "Science"]
    assert report["summary"]["raw_tweet_count"] == 3
    assert report["summary"]["eligible_tweet_count"] == 2


def test_no_homepage_input_keeps_existing_group_structure_and_score_order():
    low = tweet(1, views=1)
    high = tweet(2, views=100)

    report = process_categories({"Technology": [low, high]})

    assert HOMEPAGE_CATEGORY not in DEFAULT_CATEGORIES
    assert [group["name"] for group in report["groups"]] == list(CATEGORY_GROUPS)
    assert urls_in(report, TECH_GROUP) == [high["tweet_url"], low["tweet_url"]]
    assert report["summary"]["group_count"] == 6
    assert len(report["top_actions"]) == 2


def test_diagnostics_retain_missing_urls_unmapped_sources_and_distinct_raw_indices():
    missing_url = tweet(1, tweet_url="")
    unknown = tweet(2)

    report = process_categories({"Technology": [missing_url, missing_url], "Unmapped": [unknown]})

    diagnostics = report["processing_diagnostics"]
    assert diagnostics["counts"]["raw_occurrences"] == 3
    assert diagnostics["counts"]["unique_urls"] == 1
    assert diagnostics["counts"]["missing_url_occurrences"] == 2
    assert diagnostics["counts"]["invalid_fields_occurrences"] == 2
    assert diagnostics["counts"]["unmapped_category_occurrences"] == 1
    assert diagnostics["counts"]["selected_unique_urls"] == 0
    missing_records = [record for record in diagnostics["tweets"] if not record["tweet_url"]]
    assert len(missing_records) == 2
    assert [record["occurrences"][0]["raw_index"] for record in missing_records] == [1, 2]
    assert diagnostic(report, unknown["tweet_url"])["exclusion_reason"] == "unmapped_category"


def test_unknown_homepage_text_and_sensitive_cross_category_post_have_no_default_actions():
    home = tweet(1, content="An event is being discussed", views=1000)
    sensitive = tweet(2, views=100)

    report = process_categories({
        HOMEPAGE_CATEGORY: [home], "Technology": [sensitive], "News": [sensitive],
    })

    assert "作者正文" in items_in(report, NEWS_GROUP)[0]["summary"]
    assert report["top_actions"] == []


def metadata_for(source, group, event_id=None, **overrides):
    return {
        "tweet_url": source["tweet_url"], "editorial_group": group,
        "event_id": event_id or source["tweet_url"], "subject_label": "",
        "related_regions": [], "editorial_sensitive": False, **overrides,
    }


def test_semantic_classification_puts_homepage_athlete_into_sports():
    athlete = tweet(1, author="KMbappe", author_name="Kylian Mbappé", content="", media_types=["image"], views=50_000_000)
    report = process_categories({HOMEPAGE_CATEGORY: [athlete], "News": [athlete]})
    metadata = [metadata_for(athlete, SPORTS_GROUP, subject_label="Kylian Mbappé",
                             related_regions=[{"name": "法国", "basis": "identity", "evidence": "Kylian Mbappé"}])]

    DataProcessor().apply_editorial_metadata(report, metadata)

    item = items_in(report, SPORTS_GROUP)[0]
    assert item["tweet_url"] == athlete["tweet_url"]
    assert "Kylian Mbappé" in item["summary"] and "图片动态" in item["summary"]
    assert item["related_regions"][0]["name"] == "法国"
    assert "作者" not in item["summary"]
    assert [group["name"] for group in report["groups"]] == list(CATEGORY_GROUPS)
    assert report["editorial_applied"] is True


def test_same_event_uses_hottest_post_without_summing_metrics_then_backfills(monkeypatch):
    monkeypatch.setattr(processor_module, "MAX_ITEMS_PER_GROUP", 2)
    hotter = tweet(1, content="Noah Wyle wins a title", views=1000, likes=100)
    duplicate = tweet(2, content="The same Noah Wyle title announced", views=900, likes=99)
    other = tweet(3, content="A different film release", views=100)
    group = "Music / Dance / Entertainment"
    report = process_categories({"music": [hotter, duplicate, other]})
    metadata = [metadata_for(hotter, group, "same-award"), metadata_for(duplicate, group, "same-award"), metadata_for(other, group)]

    DataProcessor().apply_editorial_metadata(report, metadata)

    assert urls_in(report, group) == [hotter["tweet_url"], other["tweet_url"]]
    representative = items_in(report, group)[0]
    assert representative["views"] == 1000 and representative["likes"] == 100
    assert representative["related_tweet_urls"] == [duplicate["tweet_url"]]
    assert diagnostic(report, duplicate["tweet_url"])["exclusion_reason"] == "same_event"
    assert report["summary"]["event_count"] == 2


def test_semantic_selection_keeps_five_hottest_distinct_events():
    sources = [tweet(index, views=index * 1000) for index in range(1, 9)]
    report = process_categories({"Technology": sources})

    DataProcessor().apply_editorial_metadata(report, [metadata_for(source, TECH_GROUP) for source in sources])

    assert urls_in(report, TECH_GROUP) == [source["tweet_url"] for source in reversed(sources[3:])]
    assert report["summary"]["max_items_per_group"] == 5
    assert len(report["editorial_candidates"]) == 8  # No early quota could hide the hottest valid event.


def test_different_brazil_events_remain_distinct_and_sensitive():
    posts = [tweet(1, content="Flavio flight report"), tweet(2, content="Lula speech"), tweet(3, content="Brazil election commentary")]
    report = process_categories({"News": posts})
    DataProcessor().apply_editorial_metadata(report, [metadata_for(source, NEWS_GROUP, editorial_sensitive=True) for source in posts])

    assert len(items_in(report, NEWS_GROUP)) == 3
    assert report["top_actions"] == []


def test_chiikawa_media_uses_identified_ip_while_preserving_source_account():
    source = tweet(1, author="ngnchiikawa", author_name="ちいかわアニメ火金", content="", media_types=["image"])
    group = "Music / Dance / Entertainment"
    report = process_categories({"anime": [source]})
    DataProcessor().apply_editorial_metadata(report, [metadata_for(source, group, subject_label="Chiikawa（吉伊卡哇）")])

    item = items_in(report, group)[0]
    assert "Chiikawa（吉伊卡哇）" in item["summary"]
    assert item["author"] == "ngnchiikawa" and item["author_name"] == "ちいかわアニメ火金"
    assert "具体内容请查看原帖" in item["summary"]


@pytest.mark.parametrize("change", ["missing", "duplicate", "unknown_group", "conflict"])
def test_invalid_semantic_selection_leaves_original_report_unchanged(change):
    sources = [tweet(1), tweet(2)]
    report = process_categories({"Technology": sources})
    original = deepcopy(report)
    metadata = [metadata_for(source, TECH_GROUP) for source in sources]
    if change == "missing":
        metadata.pop()
    elif change == "duplicate":
        metadata.append(metadata[0])
    elif change == "unknown_group":
        metadata[0]["editorial_group"] = "Made up"
    else:
        metadata[1].update(event_id=metadata[0]["event_id"], editorial_group=SPORTS_GROUP)
    with pytest.raises(ValueError):
        DataProcessor().apply_editorial_metadata(report, metadata)
    assert report == original


def test_reprocessing_saved_raw_candidates_uses_original_time_window():
    source = tweet(1, created_at="2026-10-27T11:39:09Z")
    report = DataProcessor().process({"Global": {"Sports": [{"tweets": [source]}]}}, reference_time="2026-10-28T07:20:35Z")
    assert report["timestamp"] == "2026-10-28T15:20:35+08:00"
    assert urls_in(report, SPORTS_GROUP) == [source["tweet_url"]]
