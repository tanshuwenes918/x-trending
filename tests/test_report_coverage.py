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


def test_homepage_first_image_post_survives_and_follows_feed_order(monkeypatch):
    monkeypatch.setattr(processor_module, "MAX_ITEMS_PER_GROUP", 2)
    first = tweet(
        1, author="KMbappe", author_name="Kylian Mbappé", content="", views=47_000_000,
        media_urls=["https://pbs.twimg.com/media/first.jpg"], media_types=["photo"],
        media_alt_texts=["Image"], homepage_rank=1, source_categories=[HOMEPAGE_CATEGORY],
    )
    second = tweet(2, views=1, homepage_rank=2)
    third = tweet(3, views=100_000_000, homepage_rank=3)

    report = process_categories({HOMEPAGE_CATEGORY: [first, second, third]})

    assert report["groups"][0]["name"] == HOMEPAGE_GROUP_NAME
    assert urls_in(report, HOMEPAGE_GROUP_NAME) == [first["tweet_url"], second["tweet_url"]]
    item = items_in(report, HOMEPAGE_GROUP_NAME)[0]
    for field in ("author_name", "media_urls", "media_types", "media_alt_texts", "homepage_rank"):
        assert item[field] == first[field]
    assert item["source_categories"] == [HOMEPAGE_CATEGORY]
    assert "Kylian Mbappé" in item["summary"]
    assert "无正文" in item["summary"] and "待查看原帖" in item["summary"]
    assert report["top_actions"] == []
    assert diagnostic(report, third["tweet_url"])["exclusion_reason"] == "group_quota"


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

    assert urls_in(report, HOMEPAGE_GROUP_NAME) == [item["tweet_url"] for item in recent]
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

    assert urls_in(report, HOMEPAGE_GROUP_NAME) == [home["tweet_url"]]
    assert urls_in(report, SPORTS_GROUP) == [next_sport["tweet_url"]]
    selected_home = items_in(report, HOMEPAGE_GROUP_NAME)[0]
    assert selected_home["content"] == richer_copy["content"]
    assert selected_home["source_categories"] == [HOMEPAGE_CATEGORY, "Sports"]
    all_urls = [item["tweet_url"] for group in report["groups"] for item in group["items"]]
    assert len(all_urls) == len(set(all_urls)) == 2


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

    assert "作者正文" in items_in(report, HOMEPAGE_GROUP_NAME)[0]["summary"]
    assert report["top_actions"] == []
