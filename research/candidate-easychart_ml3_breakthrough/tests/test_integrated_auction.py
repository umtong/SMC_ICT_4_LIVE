from __future__ import annotations

from domain import Candle, Side
from integrated_auction import IntegratedAuctionBundle
from intrinsic_auction import (
    AuctionEpisode,
    EpisodeKind,
    EpisodePhase,
    IntrinsicSwing,
    PoolRole,
)

NS = 60_000_000_000


def bar(minutes: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(ts_close_ns=minutes * NS, open=o, high=h, low=l, close=c, volume=100.0)


def add_swing(
    bundle: IntegratedAuctionBundle,
    *,
    name: str,
    side: str,
    price: float,
    event_minute: int,
    observed_minute: int,
    timeframe: int,
    threshold: float = 1.0,
):
    return bundle.liquidity.register(
        IntrinsicSwing(
            name,
            side,
            price,
            event_minute * NS,
            observed_minute * NS,
            threshold,
            timeframe,
            threshold * 2.0,
        )
    )


def seed_ascending_structure(bundle: IntegratedAuctionBundle, timeframe: int) -> None:
    for idx, (low, high) in enumerate(((100.0, 110.0), (102.0, 112.0), (104.0, 114.0))):
        base = 10 + idx * 20
        add_swing(
            bundle,
            name=f"{timeframe}-l-{idx}",
            side="LOW",
            price=low,
            event_minute=base,
            observed_minute=base + 2,
            timeframe=timeframe,
        )
        add_swing(
            bundle,
            name=f"{timeframe}-h-{idx}",
            side="HIGH",
            price=high,
            event_minute=base + 8,
            observed_minute=base + 10,
            timeframe=timeframe,
        )


def seed_descending_structure(bundle: IntegratedAuctionBundle, timeframe: int) -> None:
    for idx, (low, high) in enumerate(((104.0, 114.0), (102.0, 112.0), (100.0, 110.0))):
        base = 10 + idx * 20
        add_swing(
            bundle,
            name=f"{timeframe}-dl-{idx}",
            side="LOW",
            price=low,
            event_minute=base,
            observed_minute=base + 2,
            timeframe=timeframe,
        )
        add_swing(
            bundle,
            name=f"{timeframe}-dh-{idx}",
            side="HIGH",
            price=high,
            event_minute=base + 8,
            observed_minute=base + 10,
            timeframe=timeframe,
        )


def test_hierarchical_context_recognizes_parallel_ascending_wick_structure() -> None:
    bundle = IntegratedAuctionBundle("BTCUSDT", 0.1, 1.0)
    seed_ascending_structure(bundle, 15)
    seed_ascending_structure(bundle, 60)
    context = bundle._context(90 * NS, 108.0)
    assert context.structure_60 > 0.5
    assert context.structure_15 > 0.5
    assert context.macro_side is Side.LONG
    assert context.channel_15 is not None
    assert context.channel_15.direction == "ASCENDING"
    assert context.channel_15.lower < context.channel_15.upper


def test_accepted_break_cannot_fight_both_higher_scales_without_common_impulse() -> None:
    bundle = IntegratedAuctionBundle("BTCUSDT", 0.1, 1.0)
    seed_descending_structure(bundle, 15)
    seed_descending_structure(bundle, 60)
    source = add_swing(
        bundle,
        name="break-high",
        side="HIGH",
        price=109.0,
        event_minute=75,
        observed_minute=77,
        timeframe=15,
    )
    snapshot = bundle._snapshot_for_event(
        pool=source,
        side=Side.LONG,
        bar=bar(90, 109.0, 111.0, 108.8, 110.5),
        kind=EpisodeKind.ACCEPTED_BREAK_RETEST,
        roles=(PoolRole.VALUE_BOUNDARY,),
    )
    assert snapshot.structural_alignment < 0.0
    assert not snapshot.compatible


def test_external_liquidity_reclaim_can_reverse_higher_scale_at_edge() -> None:
    bundle = IntegratedAuctionBundle("BTCUSDT", 0.1, 1.0)
    seed_descending_structure(bundle, 15)
    seed_descending_structure(bundle, 60)
    source = add_swing(
        bundle,
        name="external-low",
        side="LOW",
        price=99.0,
        event_minute=75,
        observed_minute=77,
        timeframe=60,
    )
    snapshot = bundle._snapshot_for_event(
        pool=source,
        side=Side.LONG,
        bar=bar(90, 99.5, 100.5, 97.8, 99.4),
        kind=EpisodeKind.LIQUIDITY_RECLAIM,
        roles=(PoolRole.REACTION_OBSTACLE, PoolRole.EXTERNAL_STOP_POOL),
    )
    assert snapshot.structural_alignment < 0.0
    assert snapshot.compatible


def test_integrated_stop_uses_prior_noise_beyond_one_tick() -> None:
    bundle = IntegratedAuctionBundle("BTCUSDT", 0.1, 1.0)
    source = add_swing(
        bundle,
        name="source-low",
        side="LOW",
        price=100.0,
        event_minute=1,
        observed_minute=2,
        timeframe=15,
        threshold=2.0,
    )
    target = add_swing(
        bundle,
        name="target-high",
        side="HIGH",
        price=106.0,
        event_minute=3,
        observed_minute=4,
        timeframe=15,
        threshold=2.0,
    )
    bundle.one_minute_ranges.extend([1.0] * 60)
    bundle.dc[5].ranges.extend([4.0] * 60)
    episode = AuctionEpisode(
        episode_id="noise-aware",
        pool_id=source.pool_id,
        kind=EpisodeKind.LIQUIDITY_RECLAIM,
        phase=EpisodePhase.WAIT_RESPONSE,
        side=Side.LONG,
        interaction_time_ns=10 * NS,
        phase_time_ns=11 * NS,
        source_lower=source.lower,
        source_upper=source.upper,
        source_center=source.center,
        sweep_extreme=98.0,
        break_extreme=98.0,
        origin_lower=100.0,
        origin_upper=100.5,
        retest_time_ns=12 * NS,
        retest_extreme=99.9,
        causal_event_id="noise-aware",
        source_roles=(PoolRole.EXTERNAL_STOP_POOL.value,),
    )
    plan = bundle._build_plan(episode, bar(13, 100.0, 101.2, 99.8, 100.6), 2.0)
    assert plan is not None
    assert plan.stop < 97.9
    assert plan.target == target.lower
    assert "HIERARCHICAL_DIRECTION_LIQUIDITY_EVENT_ENTRY" in plan.scale_name
