"""Tests for ``friendex.domain.activity``.

Activity scoring is pure ``float`` math (scores are not money). The trending
score is monotonic non-decreasing in every activity input, the engagement tier
is a percentile bucketing, and ``reset_activity_bucket`` returns a brand-new
zeroed bucket without mutating its input.
"""

from datetime import UTC, datetime

import pytest

from friendex.domain.activity import (
    calculate_trending_score,
    get_engagement_tier,
    reset_activity_bucket,
)
from friendex.domain.models import ActivityBucket

# The eight activity fields that feed the trending score, paired with a sample
# increment used to assert per-field monotonicity.
_MONOTONIC_FIELDS: list[tuple[str, object]] = [
    ("text_msgs", 10),
    ("media_msgs", 5),
    ("voice_minutes", 30.0),
    ("voice_unique_channels", ["c1", "c2"]),
    ("reaction_count", 25),
    ("reply_count", 8),
    ("role_ping_joins", 2.0),
    ("role_ping_join_minutes", 20.0),
]


# ---------------------------------------------------------------------------
# calculate_trending_score
# ---------------------------------------------------------------------------


def test_empty_bucket_scores_zero() -> None:
    assert calculate_trending_score(ActivityBucket()) == 0.0


def test_score_is_non_negative() -> None:
    bucket = ActivityBucket(text_msgs=5, media_msgs=2)
    assert calculate_trending_score(bucket) >= 0.0


@pytest.mark.parametrize(("field", "value"), _MONOTONIC_FIELDS)
def test_score_monotonic_per_input(field: str, value: object) -> None:
    base = ActivityBucket()
    base_score = calculate_trending_score(base)

    bumped = ActivityBucket(**{field: value})
    bumped_score = calculate_trending_score(bumped)

    assert bumped_score > base_score


@pytest.mark.parametrize(("field", "value"), _MONOTONIC_FIELDS)
def test_score_non_decreasing_when_field_grows(field: str, value: object) -> None:
    # Growing a single field from a non-trivial baseline must not lower the
    # score (monotonic non-decreasing).
    baseline = ActivityBucket(
        text_msgs=20,
        media_msgs=5,
        voice_minutes=60.0,
        voice_unique_channels=["x"],
        reaction_count=30,
        reply_count=10,
        role_ping_joins=1.0,
        role_ping_join_minutes=15.0,
    )
    smaller = calculate_trending_score(baseline)

    if field == "voice_unique_channels":
        grown_value: object = [*baseline.voice_unique_channels, "y", "z"]
    elif isinstance(value, float):
        grown_value = getattr(baseline, field) + value
    else:
        grown_value = getattr(baseline, field) + int(value)  # type: ignore[arg-type]

    grown = ActivityBucket(
        **{
            "text_msgs": baseline.text_msgs,
            "media_msgs": baseline.media_msgs,
            "voice_minutes": baseline.voice_minutes,
            "voice_unique_channels": list(baseline.voice_unique_channels),
            "reaction_count": baseline.reaction_count,
            "reply_count": baseline.reply_count,
            "role_ping_joins": baseline.role_ping_joins,
            "role_ping_join_minutes": baseline.role_ping_join_minutes,
            field: grown_value,
        }
    )
    larger = calculate_trending_score(grown)
    assert larger >= smaller


def test_score_soft_caps_diminishing_returns() -> None:
    # Soft-capped inputs exhibit diminishing returns: the marginal gain from
    # 100→200 text messages is smaller than from 0→100.
    low = calculate_trending_score(ActivityBucket(text_msgs=100))
    high = calculate_trending_score(ActivityBucket(text_msgs=200))
    first_gain = low - calculate_trending_score(ActivityBucket(text_msgs=0))
    second_gain = high - low
    assert second_gain < first_gain


def test_does_not_mutate_input_bucket() -> None:
    bucket = ActivityBucket(text_msgs=10, voice_unique_channels=["a"])
    calculate_trending_score(bucket)
    assert bucket.text_msgs == 10
    assert bucket.voice_unique_channels == ["a"]


# ---------------------------------------------------------------------------
# get_engagement_tier
# ---------------------------------------------------------------------------


def test_tier_empty_scores_returns_low() -> None:
    assert get_engagement_tier(5.0, []) == "Low"


def test_tier_elite_top_percentile() -> None:
    # A clear top scorer among 20 lands in the top 5% → Elite.
    others = [float(i) for i in range(19)]
    scores = [*others, 1000.0]
    assert get_engagement_tier(1000.0, scores) == "Elite"


def test_tier_high_bucket() -> None:
    # Rank 3 of 20 → percentile 0.15 → High (>0.05, <=0.30).
    scores = [float(100 - i) for i in range(20)]
    assert get_engagement_tier(98.0, scores) == "High"


def test_tier_medium_bucket() -> None:
    # Rank 10 of 20 → percentile 0.50 → Medium (>0.30, <=0.70).
    scores = [float(100 - i) for i in range(20)]
    assert get_engagement_tier(91.0, scores) == "Medium"


def test_tier_low_bucket() -> None:
    # Rank 20 of 20 → percentile 1.0 → Low (>0.70).
    scores = [float(100 - i) for i in range(20)]
    assert get_engagement_tier(81.0, scores) == "Low"


@pytest.mark.parametrize(
    ("rank", "expected"),
    [
        (1, "Elite"),  # 1/20 = 0.05  -> <=0.05 Elite
        (2, "High"),  # 2/20 = 0.10  -> <=0.30 High
        (6, "High"),  # 6/20 = 0.30  -> <=0.30 High (boundary)
        (7, "Medium"),  # 7/20 = 0.35  -> <=0.70 Medium
        (14, "Medium"),  # 14/20 = 0.70 -> <=0.70 Medium (boundary)
        (15, "Low"),  # 15/20 = 0.75 -> Low
    ],
)
def test_tier_boundaries(rank: int, expected: str) -> None:
    # Distinct descending scores; the score at position ``rank`` (1-indexed).
    scores = [float(1000 - i) for i in range(20)]
    target = scores[rank - 1]
    assert get_engagement_tier(target, scores) == expected


# ---------------------------------------------------------------------------
# reset_activity_bucket
# ---------------------------------------------------------------------------


def test_reset_returns_new_zeroed_bucket() -> None:
    now = datetime(2026, 5, 23, 12, 0, 0, tzinfo=UTC)
    bucket = ActivityBucket(
        text_msgs=10,
        media_msgs=5,
        voice_minutes=60.0,
        voice_unique_channels=["a", "b"],
        reaction_count=20,
        reply_count=8,
        role_ping_joins=2.0,
        role_ping_join_minutes=15.0,
    )
    fresh = reset_activity_bucket(bucket, now)

    assert fresh.text_msgs == 0
    assert fresh.media_msgs == 0
    assert fresh.voice_minutes == 0.0
    assert fresh.voice_unique_channels == []
    assert fresh.reaction_count == 0
    assert fresh.reply_count == 0
    assert fresh.role_ping_joins == 0.0
    assert fresh.role_ping_join_minutes == 0.0
    assert fresh.bucket_start == now


def test_reset_returns_distinct_instance() -> None:
    now = datetime(2026, 5, 23, 12, 0, 0, tzinfo=UTC)
    bucket = ActivityBucket(text_msgs=10)
    fresh = reset_activity_bucket(bucket, now)
    assert fresh is not bucket


def test_reset_does_not_mutate_input() -> None:
    now = datetime(2026, 5, 23, 12, 0, 0, tzinfo=UTC)
    original_start = datetime(2026, 5, 1, 0, 0, 0, tzinfo=UTC)
    bucket = ActivityBucket(
        text_msgs=10,
        voice_unique_channels=["a", "b"],
        bucket_start=original_start,
    )
    reset_activity_bucket(bucket, now)

    assert bucket.text_msgs == 10
    assert bucket.voice_unique_channels == ["a", "b"]
    assert bucket.bucket_start == original_start


def test_reset_input_channel_list_is_independent() -> None:
    # The fresh bucket's list must not share identity with the input's list.
    now = datetime(2026, 5, 23, 12, 0, 0, tzinfo=UTC)
    bucket = ActivityBucket(voice_unique_channels=["a"])
    fresh = reset_activity_bucket(bucket, now)
    fresh.voice_unique_channels.append("b")
    assert bucket.voice_unique_channels == ["a"]
