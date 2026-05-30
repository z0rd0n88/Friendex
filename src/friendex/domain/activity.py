"""Pure engagement / trending math for Friendex.

These functions translate a member's :class:`~friendex.domain.models.Activity\
Bucket` into a single trending score, bucket members into engagement tiers, and
reset a bucket for a new accounting window. They are pure functions of their
arguments — no globals, no I/O, no mutation of inputs.

**Numeric typing:** scores are plain ``float`` — they are dimensionless
engagement measures, *not* money, so the Decimal-at-the-boundary rule that
governs prices does not apply here. The price engine converts a score into a
``Decimal`` price delta at the point of use
(:func:`friendex.domain.price_engine.compute_activity_return`).

Weights, soft-cap saturation points, and tier percentile cuts are lifted
verbatim from the original monolith (``docs/spec/original-skeleton.md``
§ENGAGEMENT / TRENDING) so the rebuilt economy behaves identically. They are
game-tuning numbers; a follow-up may promote them to ``Settings`` (recorded in
the Phase 4 baton-pass as a deferred note).
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from datetime import datetime

    from friendex.domain.models import ActivityBucket


# Tier strings consumed by :class:`~friendex.application.snapshot_models.UserStats`.
# Narrowed to a :class:`Literal` (#84 L) so a static type checker rejects a
# future typo at every call site.
EngagementTier = Literal["Elite", "High", "Medium", "Low"]

# --- Soft-cap saturation points -------------------------------------------
# ``soft_cap(x, cap) = cap * (1 - exp(-x / cap))`` — monotonic increasing in
# ``x``, approaching ``cap`` asymptotically (diminishing returns).
_CAP_TEXT = 100.0
_CAP_MEDIA = 50.0
_CAP_VOICE_MINUTES = 300.0
_CAP_REACTIONS = 200.0
_CAP_REPLIES = 100.0
_CAP_ROLE_PING_JOIN_MINUTES = 180.0

# --- Per-field score weights ----------------------------------------------
_W_TEXT = 0.5
_W_MEDIA = 2.0
_W_VOICE_MINUTES = 0.1
_W_UNIQUE_CHANNELS = 1.5
_W_REACTIONS = 0.2
_W_REPLIES = 0.3
_W_ROLE_PING_JOINS = 4.0
_W_ROLE_PING_JOIN_MINUTES = 0.3

# --- Engagement-tier percentile cuts (top-down) ---------------------------
_TIER_ELITE_MAX = 0.05
_TIER_HIGH_MAX = 0.30
_TIER_MEDIUM_MAX = 0.70


def _soft_cap(value: float, cap: float) -> float:
    """Saturating transform: monotonic, diminishing returns toward ``cap``."""
    return cap * (1.0 - math.exp(-value / cap))


def calculate_trending_score(bucket: ActivityBucket) -> float:
    """Return a weighted, soft-capped engagement score for a bucket.

    Each raw counter is passed through a soft cap (diminishing returns) and
    summed with its weight. Unique voice channels and role-ping joins are not
    soft-capped — they are deliberately scarce, high-signal events. The score
    is monotonic non-decreasing in every input. The input bucket is not
    mutated.
    """
    text = _soft_cap(bucket.text_msgs, _CAP_TEXT)
    media = _soft_cap(bucket.media_msgs, _CAP_MEDIA)
    voice = _soft_cap(bucket.voice_minutes, _CAP_VOICE_MINUTES)
    reactions = _soft_cap(bucket.reaction_count, _CAP_REACTIONS)
    replies = _soft_cap(bucket.reply_count, _CAP_REPLIES)
    ping_minutes = _soft_cap(bucket.role_ping_join_minutes, _CAP_ROLE_PING_JOIN_MINUTES)
    unique_channels = len(bucket.voice_unique_channels)

    return (
        _W_TEXT * text
        + _W_MEDIA * media
        + _W_VOICE_MINUTES * voice
        + _W_UNIQUE_CHANNELS * unique_channels
        + _W_REACTIONS * reactions
        + _W_REPLIES * replies
        + _W_ROLE_PING_JOINS * bucket.role_ping_joins
        + _W_ROLE_PING_JOIN_MINUTES * ping_minutes
    )


def get_engagement_tier(score: float, all_scores: list[float]) -> EngagementTier:
    """Bucket ``score`` into an engagement tier by descending percentile rank.

    Ranking is 1-indexed from the top: the highest scorer has percentile
    ``1/N``. Cuts: ``<=5%`` Elite, ``<=30%`` High, ``<=70%`` Medium, else Low.
    An empty ``all_scores`` yields ``"Low"``. ``score`` is assumed present in
    ``all_scores`` (the caller derives both from the same population).

    **Tie-safe rank (#82 H4).** The percentile rank is ``(strictly_higher +
    1) / N`` rather than ``sorted_scores.index(score) + 1``. ``list.index``
    returns the **first** matching index, so when several callers share the
    same score every tied user landed at the same first-match index — for
    a 20-way tie at the top, every tied user mapped to position 1 and ALL
    of them earned the Elite tier, breaking the percentile-cut contract.
    Counting strictly-higher scores gives every tied participant the same
    (highest) rank without depending on list ordering, so any two users
    with identical scores always receive the same tier.
    """
    if not all_scores:
        return "Low"

    strictly_higher = sum(1 for s in all_scores if s > score)
    percentile_rank = (strictly_higher + 1) / len(all_scores)

    if percentile_rank <= _TIER_ELITE_MAX:
        return "Elite"
    if percentile_rank <= _TIER_HIGH_MAX:
        return "High"
    if percentile_rank <= _TIER_MEDIUM_MAX:
        return "Medium"
    return "Low"


def reset_activity_bucket(bucket: ActivityBucket, now: datetime) -> ActivityBucket:
    """Return a NEW zeroed bucket starting at ``now``; ``bucket`` is untouched.

    All counters are reset to zero, the unique-channel list to a fresh empty
    list (no aliasing with the input), and ``bucket_start`` to ``now``. The
    ``bucket`` argument is never mutated (immutability rule).
    """
    return replace(
        bucket,
        text_msgs=0,
        media_msgs=0,
        voice_minutes=0.0,
        voice_unique_channels=[],
        reaction_count=0,
        reply_count=0,
        role_ping_joins=0.0,
        role_ping_join_minutes=0.0,
        bucket_start=now,
    )
