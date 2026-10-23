"""Metric counts must retain their units and control identities across X locales."""

from unittest.mock import MagicMock

import pytest

from scrapers.trending_scraper import TrendingScraper


def metric_node(*, text="", label=""):
    node = MagicMock()
    node.count.return_value = 1
    node.inner_text.return_value = text
    node.get_attribute.side_effect = lambda name, **kwargs: label if name == "aria-label" else ""
    node.locator.return_value.all.return_value = []
    return node


def metric_article(*, text="", label="", controls=None, extra_labels=()):
    group = metric_node(text=text, label=label)
    controls = controls or {}
    labelled_nodes = [group] + list(controls.values()) + [metric_node(label=x) for x in extra_labels]
    article = MagicMock()

    def locator(selector):
        nodes = []
        if selector == '[role="group"]':
            nodes = [group]
        elif selector == "[aria-label]":
            nodes = labelled_nodes
        elif selector in controls:
            nodes = [controls[selector]]
        result = MagicMock()
        result.all.return_value = nodes
        return result

    article.locator.side_effect = locator
    return article


@pytest.mark.parametrize(
    "text,metric,expected",
    [
        ("1.2万", "like", 12_000),
        ("1.2萬 喜歡次數。喜歡", "like", 12_000),
        ("2.5亿 次查看。查看帖子分析", "view", 250_000_000),
        ("2.5億 次觀看", "view", 250_000_000),
        ("8639 次转帖。转帖", "repost", 8639),
        ("8639 次轉貼。轉貼", "repost", 8639),
        ("17137 喜欢次数。喜欢", "like", 17137),
        ("7526275 次查看。查看帖子分析", "view", 7_526_275),
        ("1,234,567 views", "view", 1_234_567),
        ("Likes 1,234", "like", 1234),
        ("23.6K reposts. Repost", "repost", 23_600),
        ("1.2M views", "view", 1_200_000),
        ("0 replies", "reply", 0),
    ],
)
def test_metric_units_and_localized_accessible_labels(text, metric, expected):
    assert TrendingScraper()._extract_metric_value(text, metric) == expected


def test_chinese_accessible_group_ignores_bookmark_count():
    article = metric_article(
        text="1200 8639 1.7万 7490 752.6万",
        label="1200 回复、8639 次转帖、17137 喜欢、7490 书签、7526275 次观看",
    )

    expected = {"reply": 1200, "repost": 8639, "like": 17137, "view": 7_526_275}
    scraper = TrendingScraper()
    assert scraper._extract_action_group_metrics(article) == expected
    assert scraper._extract_metrics(article) == expected


def test_chinese_metric_controls_parse_real_accessible_labels():
    article = metric_article(
        controls={
            '[data-testid="reply"]': metric_node(label="1200 回复。回复"),
            '[data-testid="retweet"]': metric_node(label="8639 次转帖。转帖"),
            '[data-testid="like"]': metric_node(label="17137 喜欢次数。喜欢"),
            'a[href$="/analytics"]': metric_node(label="7526275 次查看。查看帖子分析"),
        },
    )

    assert TrendingScraper()._extract_metrics(article) == {
        "reply": 1200, "repost": 8639, "like": 17137, "view": 7_526_275,
    }


@pytest.mark.parametrize(
    "text,expected",
    [
        ("167 1174 4.2万 291万", {"reply": 167, "repost": 1174, "like": 42_000, "view": 2_910_000}),
        ("0 1174 4.2萬 2.5億", {"reply": 0, "repost": 1174, "like": 42_000, "view": 250_000_000}),
        ("0 23.6K 75K 1.2M", {"reply": 0, "repost": 23_600, "like": 75_000, "view": 1_200_000}),
        ("167 1174 unknown 291万", {}),
        ("167 1174", {}),
        ("167 1174 42647", {}),
        ("167 1174 42647 7490 2910000", {}),
    ],
)
def test_positional_fallback_requires_complete_row_and_preserves_zero(text, expected):
    assert TrendingScraper()._extract_action_group_metrics(metric_article(text=text)) == expected


def test_unrelated_bare_aria_count_cannot_be_reused_as_other_metrics():
    article = metric_article(
        controls={'[data-testid="retweet"]': metric_node(text="1174", label="1174")},
        extra_labels=["1174"],
    )

    assert TrendingScraper()._extract_metrics(article) == {
        "reply": 0, "repost": 1174, "like": 0, "view": 0,
    }
