"""Data processing and transformation logic."""

import logging
import re
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from config.settings import (
    CATEGORY_GROUPS,
    COUNTRIES,
    HOMEPAGE_CATEGORY,
    LOOKBACK_HOURS,
    MAX_ITEMS_PER_GROUP,
    TIMEZONE,
)

logger = logging.getLogger(__name__)


class DataProcessor:
    """Process scraped tweets into the daily operations report shape."""

    def process(self, raw_data: Dict[str, Dict[str, List[Dict[str, Any]]]], *, reference_time=None) -> Dict[str, Any]:
        """Process raw scraped data."""
        logger.info("Processing scraped data...")

        now = self._parse_datetime(reference_time) if reference_time else datetime.now(ZoneInfo(TIMEZONE))
        if not now:
            raise ValueError("Saved source data has no valid collection timestamp")
        cutoff = now - timedelta(hours=LOOKBACK_HOURS)
        candidates, diagnostics, _ = self._collect_candidates(raw_data, cutoff)
        groups = self._empty_groups()
        selected = {}
        # The homepage is a source, never an extra display section. Unknown
        # homepage-only sources stay in the candidate pool for semantic editing.
        # The source grouping below is also usable when the optional LLM is off.
        for candidate in candidates.values():
            if not candidate["groups"]:
                candidate["groups"] = ["News / Society / Sensitive Topics"]
        # Aggregate every source before selecting. A full first source group must
        # not prevent a tweet from filling a slot in another source group.
        group_urls = {name: [] for name in CATEGORY_GROUPS}
        for tweet_url, candidate in sorted(
            candidates.items(), key=lambda pair: self._score(pair[1]["item"]), reverse=True,
        ):
            if tweet_url not in selected:
                self._assign_to_group(tweet_url, candidates, group_urls, selected, set())

        for tweet_url, candidate in candidates.items():
            record = diagnostics["tweets_by_key"][tweet_url]
            group_name = selected.get(tweet_url)
            if not group_name:
                record.update(status="excluded", exclusion_reason="group_quota")
                continue

            item = candidate["item"]
            category = next((category for category in record["source_categories"]
                             if self._group_for_category(category) == group_name), HOMEPAGE_CATEGORY)
            source = next(
                (source for source in candidate["sources"] if source["category"] == category),
                candidate["sources"][0],
            )
            term = source["trending_term"] if source["category"] == category else category
            item.update(
                country=source["country"], source_category=category,
                source_categories=record["source_categories"], trending_term=term,
                score=self._score(item),
            )
            item["summary"] = self._fallback_summary(item, term)
            groups[group_name]["items"].append(item)
            record.update(status="selected", selected_group=group_name)

        for group in groups.values():
            group["items"].sort(key=lambda item: item.get("score", 0), reverse=True)

        group_list = list(groups.values())
        eligible_tweet_count = sum(len(group["items"]) for group in group_list)
        records = list(diagnostics.pop("tweets_by_key").values())
        for record in records:
            record["occurrence_count"] = len(record["occurrences"])
            if "status" not in record:
                record.update(status="excluded", exclusion_reason=record["occurrences"][0]["reason"])
        diagnostics["tweets"] = records
        diagnostics["counts"].update(
            valid_unique_urls=len(candidates),
            selected_unique_urls=eligible_tweet_count,
            excluded_unique_urls=sum(bool(record["tweet_url"]) and record["status"] == "excluded" for record in records),
            quota_excluded_unique_urls=sum(record.get("exclusion_reason") == "group_quota" for record in records),
        )

        editorial_candidates = []
        records_by_url = {record["tweet_url"]: record for record in records if record["tweet_url"]}
        for tweet_url, candidate in candidates.items():
            item = deepcopy(candidate["item"])
            source = candidate["sources"][0]
            item.update(
                country=source["country"], source_category=source["category"],
                source_categories=records_by_url[tweet_url]["source_categories"],
                trending_term=source["trending_term"], score=self._score(item),
            )
            editorial_candidates.append(item)

        return {
            "timestamp": now.isoformat(),
            "timezone": TIMEZONE,
            "lookback_hours": LOOKBACK_HOURS,
            "countries": COUNTRIES,
            "groups": group_list,
            "top_actions": self._fallback_top_actions(group_list),
            "editorial_candidates": editorial_candidates,
            "processing_diagnostics": diagnostics,
            "summary": {
                "country_count": len(COUNTRIES),
                "group_count": len(group_list),
                "raw_tweet_count": diagnostics["counts"]["raw_occurrences"],
                "eligible_tweet_count": eligible_tweet_count,
                "max_items_per_group": MAX_ITEMS_PER_GROUP,
            },
        }

    def apply_editorial_metadata(self, data: dict, metadata: list) -> dict:
        """Select the hottest representative of each event, then each group's top N.

        Metadata is validated before changing the report so failed editing can be
        retried from the complete original pool. Engagement counts are never
        added across different posts about an event.
        """
        source_items = {item["tweet_url"]: item for item in data.get("editorial_candidates", [])}
        if not source_items:
            raise ValueError("Editorial selection requires a nonempty candidate pool")
        by_url = {}
        events = {}
        for entry in metadata:
            url = entry.get("tweet_url")
            group = entry.get("editorial_group")
            event_id = entry.get("event_id")
            if url not in source_items or url in by_url or group not in CATEGORY_GROUPS or not event_id:
                raise ValueError("Editorial metadata must cover each candidate once with a valid event/group")
            by_url[url] = entry
            event = events.setdefault(event_id, {"group": group, "members": []})
            if event["group"] != group:
                raise ValueError("The same event cannot occupy multiple report groups")
            event["members"].append(url)
        if set(by_url) != set(source_items):
            raise ValueError("Editorial metadata omitted source candidates")

        groups = self._empty_groups()
        representatives = []
        representative_for = {}
        for event_id, event in events.items():
            members = sorted(event["members"], key=lambda url: (-self._score(source_items[url]), url))
            representative_url = members[0]
            item = deepcopy(source_items[representative_url])
            meta = by_url[representative_url]
            for key in ("subject_label", "related_regions", "region_review_notes"):
                item[key] = deepcopy(meta.get(key, [] if key != "subject_label" else ""))
            item.update(
                event_id=event_id, editorial_group=event["group"],
                editorial_sensitive=any(by_url[url].get("editorial_sensitive", False) for url in members),
                related_tweet_urls=members[1:], event_source_count=len(members), score=self._score(item),
                source_categories=list(dict.fromkeys(category for url in members
                                                    for category in source_items[url].get("source_categories", []))),
            )
            item["summary"] = self._fallback_summary(item, item.get("trending_term", ""))
            representatives.append(item)
            for url in members:
                representative_for[url] = representative_url

        selected_urls = set()
        for item in sorted(representatives, key=lambda item: (-item["score"], item["tweet_url"])):
            target = groups[item["editorial_group"]]["items"]
            if len(target) < MAX_ITEMS_PER_GROUP:
                target.append(item)
                selected_urls.add(item["tweet_url"])

        diagnostics = deepcopy(data.get("processing_diagnostics", {"counts": {}, "tweets": []}))
        for record in diagnostics.get("tweets", []):
            url = record.get("tweet_url")
            if url not in representative_for:
                continue
            record.pop("selected_group", None)
            record.pop("exclusion_reason", None)
            record["event_id"] = by_url[url]["event_id"]
            if url in selected_urls:
                record.update(status="selected", selected_group=by_url[url]["editorial_group"])
            else:
                representative = representative_for[url]
                reason = "same_event" if representative != url else "group_quota"
                record.update(status="excluded", exclusion_reason=reason, representative_url=representative)
        diagnostics.setdefault("counts", {}).update(
            event_count=len(events), same_event_excluded_urls=len(source_items) - len(events),
            selected_unique_urls=len(selected_urls),
            excluded_unique_urls=sum(bool(record.get("tweet_url")) and record.get("status") == "excluded"
                                     for record in diagnostics.get("tweets", [])),
            quota_excluded_unique_urls=len(events) - len(selected_urls),
        )
        group_list = list(groups.values())
        summary = {**data.get("summary", {}), "group_count": len(group_list),
                   "eligible_tweet_count": len(selected_urls), "max_items_per_group": MAX_ITEMS_PER_GROUP,
                   "event_count": len(events)}
        data.update(groups=group_list, processing_diagnostics=diagnostics,
                    summary=summary, top_actions=self._fallback_top_actions(group_list),
                    editorial_applied=True, editorial_metadata=deepcopy(metadata))
        return data

    def validate_tweet(self, tweet: Dict[str, Any]) -> bool:
        """Validate a tweet record."""
        required_fields = ["author", "created_at", "tweet_url"]
        has_content = bool(tweet.get("content") or tweet.get("media_urls") or tweet.get("media_types"))
        return all(tweet.get(field) for field in required_fields) and has_content

    def _collect_candidates(self, raw_data: dict, cutoff: datetime):
        """Keep URL provenance and filtering reasons before applying any quotas."""
        candidates = {}
        records = {}
        homepage_orders = {}
        has_homepage = False
        counts = {
            "raw_occurrences": 0, "unique_urls": 0, "duplicate_occurrences": 0,
            "missing_url_occurrences": 0, "invalid_fields_occurrences": 0,
            "invalid_timestamp_occurrences": 0, "outside_lookback_occurrences": 0,
            "unmapped_category_occurrences": 0,
        }
        for country, category_map in raw_data.items():
            if not isinstance(category_map, dict):
                logger.warning("Skipping invalid country payload: %s", country)
                continue
            for category, trends in category_map.items():
                is_homepage = self._normalize(category) == self._normalize(HOMEPAGE_CATEGORY)
                has_homepage = has_homepage or is_homepage
                group_name = self._group_for_category(category)
                for trend in trends if isinstance(trends, list) else []:
                    if not isinstance(trend, dict):
                        continue
                    term = self._clean_text(trend.get("trending_term") or trend.get("term"))
                    tweets = trend.get("tweets", [])
                    for tweet in tweets if isinstance(tweets, list) else []:
                        counts["raw_occurrences"] += 1
                        raw_index = counts["raw_occurrences"]
                        raw = tweet if isinstance(tweet, dict) else {}
                        tweet_url = self._clean_text(raw.get("tweet_url"))
                        key = tweet_url or ("missing_url", raw_index)
                        if key in records:
                            counts["duplicate_occurrences"] += 1
                        elif tweet_url:
                            counts["unique_urls"] += 1
                        else:
                            counts["missing_url_occurrences"] += 1
                        record = records.setdefault(key, {
                            "tweet_url": tweet_url, "source_categories": [], "occurrences": [],
                        })
                        for source_category in [category, *self._clean_urls(raw.get("source_categories", []))]:
                            if source_category not in record["source_categories"]:
                                record["source_categories"].append(source_category)
                        occurrence = {
                            "raw_index": raw_index, "country": country,
                            "source_category": category, "reason": "eligible_candidate",
                        }
                        record["occurrences"].append(occurrence)
                        if is_homepage and tweet_url:
                            homepage_orders.setdefault(tweet_url, raw_index)

                        cleaned = self._clean_tweet(raw)
                        tweet_time = self._parse_datetime(cleaned.get("created_at", ""))
                        reason = ""
                        if not cleaned:
                            reason = "invalid_fields"
                        elif not tweet_time:
                            reason = "invalid_timestamp"
                        elif tweet_time < cutoff:
                            reason = "outside_lookback"
                        elif not group_name and not is_homepage:
                            reason = "unmapped_category"
                        if reason:
                            occurrence["reason"] = reason
                            counts[f"{reason}_occurrences"] += 1
                            continue

                        candidate = candidates.setdefault(tweet_url, {
                            "item": cleaned, "groups": [], "sources": [], "homepage_order": None,
                        })
                        self._merge_tweet(candidate["item"], cleaned)
                        candidate["sources"].append({"country": country, "category": category, "trending_term": term})
                        for source_category in record["source_categories"]:
                            source_group = self._group_for_category(source_category)
                            if source_group and source_group not in candidate["groups"]:
                                candidate["groups"].append(source_group)

        for tweet_url, candidate in candidates.items():
            candidate["homepage_order"] = homepage_orders.get(tweet_url)
            candidate["groups"].sort(key=lambda name: list(CATEGORY_GROUPS).index(name))
            if candidate["homepage_order"] is not None and not candidate["item"].get("homepage_rank"):
                candidate["item"]["homepage_rank"] = list(homepage_orders).index(tweet_url) + 1
        return candidates, {"counts": counts, "tweets_by_key": records}, has_homepage

    def _merge_tweet(self, target: dict, incoming: dict) -> None:
        """Fill missing text and combine observed media/metrics for one URL."""
        for field in ("content", "author_name", "homepage_rank"):
            if not target.get(field) and incoming.get(field):
                target[field] = incoming[field]
        for field in ("media_urls", "media_types", "media_alt_texts"):
            target[field] = list(dict.fromkeys([*target.get(field, []), *incoming.get(field, [])]))
        for field in ("likes", "retweets", "replies", "views"):
            target[field] = max(target.get(field, 0), incoming.get(field, 0))

    def _assign_to_group(self, tweet_url: str, candidates: dict, group_urls: dict, selected: dict, visited: set) -> bool:
        """Find capacity, moving already selected cross-category tweets if needed."""
        eligible_groups = [name for name in candidates[tweet_url]["groups"] if name not in visited]
        for name in eligible_groups:
            if len(group_urls[name]) < MAX_ITEMS_PER_GROUP:
                group_urls[name].append(tweet_url)
                selected[tweet_url] = name
                return True
        for name in eligible_groups:
            if name in visited:
                continue
            visited.add(name)
            for previous_url in reversed(group_urls[name][:]):
                if self._assign_to_group(previous_url, candidates, group_urls, selected, visited):
                    group_urls[name].remove(previous_url)
                    group_urls[name].append(tweet_url)
                    selected[tweet_url] = name
                    return True
        return False

    def _empty_groups(self) -> Dict[str, Dict[str, Any]]:
        return {
            group_name: {
                "name": group_name,
                "source_categories": source_categories,
                "items": [],
            }
            for group_name, source_categories in CATEGORY_GROUPS.items()
        }

    def _group_for_category(self, category: str) -> Optional[str]:
        normalized = self._normalize(category)
        for group_name, source_categories in CATEGORY_GROUPS.items():
            if normalized in {self._normalize(item) for item in source_categories}:
                return group_name
        return None

    def _clean_tweet(self, tweet: Any) -> Dict[str, Any]:
        if not isinstance(tweet, dict):
            return {}

        cleaned = {
            "author": self._clean_text(tweet.get("author")),
            "content": self._clean_text(tweet.get("content")),
            "created_at": self._clean_text(tweet.get("created_at")),
            "likes": self._to_int(tweet.get("likes")),
            "retweets": self._to_int(tweet.get("retweets")),
            "replies": self._to_int(tweet.get("replies")),
            "views": self._to_int(tweet.get("views")),
            "media_urls": self._clean_urls(tweet.get("media_urls", [])),
            "media_types": self._clean_urls(tweet.get("media_types", [])),
            "media_alt_texts": self._clean_urls(tweet.get("media_alt_texts", [])),
            "author_name": self._clean_text(tweet.get("author_name")),
            "tweet_url": self._clean_text(tweet.get("tweet_url")),
        }
        if self._to_int(tweet.get("homepage_rank")) > 0:
            cleaned["homepage_rank"] = self._to_int(tweet["homepage_rank"])

        return cleaned if self.validate_tweet(cleaned) else {}

    def _parse_datetime(self, value: str) -> Optional[datetime]:
        if not value:
            return None

        text = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            logger.debug("Could not parse tweet datetime: %s", value)
            return None

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
        return parsed.astimezone(ZoneInfo(TIMEZONE))

    def _score(self, tweet: Dict[str, Any]) -> float:
        views = tweet.get("views", 0)
        likes = tweet.get("likes", 0)
        retweets = tweet.get("retweets", 0)
        replies = tweet.get("replies", 0)

        if views:
            return views * 0.4 + likes * 2 + retweets * 4 + replies * 3
        return likes * 2 + retweets * 4 + replies * 3

    def _fallback_summary(self, tweet: Dict[str, Any], trend_term: str) -> str:
        if not tweet.get("content"):
            subject = self._truncate(tweet.get("subject_label") or tweet.get("author_name") or tweet.get("author") or "未识别账号", 40)
            kind = "视频" if "video" in tweet.get("media_types", []) else "图片" if any(
                value in {"image", "photo"} for value in tweet.get("media_types", [])
            ) else "媒体"
            return f"{subject}（{kind}动态）：该帖无正文，具体内容请查看原帖。"
        headline = self._headline_from_content(tweet.get("content", "")) or "热门动态"
        if tweet.get("source_category") == HOMEPAGE_CATEGORY:
            return self._truncate(f"首页热点（{headline}）：作者正文：{tweet['content']}", 120)
        topic = self._category_label(trend_term)
        description = self._fallback_description(tweet.get("content", ""))
        return self._truncate(f"{topic}（{headline}）：{description}", 120)

    def _fallback_top_actions(self, groups: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        candidates = []
        for group in groups:
            if group.get("name") == "News / Society / Sensitive Topics":
                continue
            candidates.extend(
                item for item in group.get("items", [])
                if item.get("content") and not item.get("editorial_sensitive") and not any(
                    self._group_for_category(category) == "News / Society / Sensitive Topics"
                    for category in item.get("source_categories", [])
                )
            )

        actions = []
        for item in sorted(candidates, key=lambda tweet: tweet.get("score", 0), reverse=True):
            if len(actions) >= 5:
                break
            actions.append(
                {
                    "title": self._headline_from_content(item.get("content", ""))
                    or self._category_label(item.get("trending_term", "")),
                    "action": "把这条热度内容改成 AI 音乐/AI 视频模板挑战，提供口播、BGM 和分镜素材包，并用评论区关键词引导用户到生成页。",
                    "tweet_url": item.get("tweet_url", ""),
                    "likes": item.get("likes", 0),
                    "retweets": item.get("retweets", 0),
                    "replies": item.get("replies", 0),
                    "views": item.get("views", 0),
                }
            )
            if "related_regions" in item:
                actions[-1]["related_regions"] = deepcopy(item["related_regions"])
        return actions

    def _headline_from_content(self, content: str) -> str:
        text = self._clean_text(content)
        if not text:
            return ""
        if self._is_mostly_ascii(text):
            return self._english_headline(text)

        separators = ["。", ".", "!", "?", "！", "？", "；", ":"]
        first_sentence = text
        for separator in separators:
            if separator in first_sentence:
                first_sentence = first_sentence.split(separator, 1)[0]
        return self._truncate(first_sentence, 28)

    def _english_headline(self, text: str) -> str:
        normalized = self._clean_text(text)
        introducing_match = re.search(
            r"\b(?:introducing|launching|announcing|released?|new trailer for)\s+(.+?)(?:[:.!?]|$)",
            normalized,
            flags=re.IGNORECASE,
        )
        if introducing_match:
            subject = self._truncate(introducing_match.group(1).strip(" \"'"), 18)
            return f"{subject}新消息" if subject else "海外新品发布"

        brand_match = re.search(r"\b([A-Z][A-Za-z0-9&.-]*(?:\s+[A-Z][A-Za-z0-9&.-]*){0,3})\b", normalized)
        if brand_match:
            subject = self._truncate(brand_match.group(1), 18)
            return f"{subject}热度上升"

        return "海外热点发酵"

    def _fallback_description(self, content: str) -> str:
        text = self._clean_text(content)
        if not text:
            return "该推文热度较高，可作为今日 AI 音乐/AI 视频素材观察。"

        lowered = text.lower()
        if any(keyword in lowered for keyword in ["launch", "introducing", "released", "trailer", "announced"]):
            return "原推文发布了新产品、新预告或新消息，适合拆成短视频资讯、口播脚本或 BGM 情绪模板。"
        if any(keyword in lowered for keyword in ["ai", "model", "tool", "app", "workflow"]):
            return "原推文讨论 AI、工具或创作流程变化，适合评估能否转成产品演示、教程短视频或生成模板。"
        if any(keyword in lowered for keyword in ["music", "song", "dance", "video", "movie", "anime"]):
            return "原推文围绕音乐、视频或娱乐内容发酵，适合作为 AI 配乐、AI MV 或角色配音挑战参考。"
        if any(keyword in lowered for keyword in ["meme", "viral", "trend"]):
            return "原推文呈现社媒热点或梗传播，适合观察是否能做成低门槛二创视频模板。"
        return "原推文正在获得较高互动，可作为今日 AI 音乐/AI 视频热点素材池候选。"

    def _category_label(self, value: str) -> str:
        labels = {
            "technology": "科技趋势",
            "science": "科学话题",
            "business&finance": "商业科技",
            "cryptocurrency": "加密话题",
            "music": "音乐热点",
            "dance": "舞蹈热点",
            "celebrity": "明星娱乐",
            "movies&tv": "影视话题",
            "anime": "动画话题",
            "meme": "梗文化",
            "relationship": "社交关系",
            "fashion": "时尚话题",
            "beauty": "美妆话题",
            "food": "美食话题",
            "pets": "宠物话题",
            "gaming": "游戏热点",
            "sports": "体育热点",
            "cars": "汽车话题",
            "travel": "旅行话题",
            "nature&outdoors": "户外话题",
            "health&fitness": "健康健身",
            "home&garden": "家居生活",
            "news": "新闻社会",
            "religion": "宗教社会",
        }
        return labels.get(self._normalize(value), self._clean_text(value) or "热门话题")

    def _is_mostly_ascii(self, text: str) -> bool:
        if not text:
            return False
        ascii_count = sum(1 for char in text if ord(char) < 128)
        return ascii_count / len(text) > 0.75

    def _clean_text(self, value: Any) -> str:
        if value is None:
            return ""
        return " ".join(str(value).split())

    def _clean_urls(self, value: Any) -> List[str]:
        if not isinstance(value, list):
            return []
        return [self._clean_text(url) for url in value if self._clean_text(url)]

    def _to_int(self, value: Any) -> int:
        if value is None or value == "":
            return 0
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)

        text = str(value).strip().replace(",", "")
        multiplier = 1
        if text.lower().endswith("k"):
            multiplier = 1_000
            text = text[:-1]
        elif text.lower().endswith("m"):
            multiplier = 1_000_000
            text = text[:-1]
        elif text.lower().endswith("b"):
            multiplier = 1_000_000_000
            text = text[:-1]

        try:
            return int(float(text) * multiplier)
        except ValueError:
            return 0

    def _normalize(self, value: str) -> str:
        return self._clean_text(value).lower().replace(" ", "")

    def _truncate(self, text: str, limit: int) -> str:
        value = self._clean_text(text)
        if len(value) <= limit:
            return value
        return value[: limit - 3] + "..."
