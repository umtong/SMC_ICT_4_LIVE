from __future__ import annotations

from domain import Candle, Side
from intrinsic_auction import (
    AdaptiveDirectionalChange,
    AuctionEpisode,
    EpisodeKind,
    EpisodePhase,
    IntrinsicAuctionBundle,
    IntrinsicSwing,
    LiquidityBook,
    PoolRole,
)

NS = 60_000_000_000


def candle(i: int, o: float, h: float, l: float, c: float, volume: float = 100.0) -> Candle:
    return Candle(ts_close_ns=i * NS, open=o, high=h, low=l, close=c, volume=volume)


def test_directional_change_observed_after_extreme() -> None:
    dc = AdaptiveDirectionalChange("BTCUSDT", 5, 0.1, range_window=12, threshold_multiplier=1.0)
    bars = []
    price = 100.0
    for i in range(1, 15):
        bars.append(candle(i, price, price + 1.0, price - 0.5, price + 0.5))
        price += 0.5
    bars.append(candle(15, price, price + 0.2, price - 3.0, price - 2.5))
    swings = []
    for bar in bars:
        swings.extend(dc.on_bar(bar))
    assert swings
    assert swings[0].side == "HIGH"
    assert swings[0].observed_time_ns > swings[0].event_time_ns


def test_liquidity_clusters_nearby_equal_extremes() -> None:
    book = LiquidityBook("BTCUSDT", 0.1)
    first = IntrinsicSwing("a", "LOW", 100.0, 1, 2, 2.0, 15, 4.0)
    second = IntrinsicSwing("b", "LOW", 100.2, 3, 4, 2.0, 15, 3.0)
    p1 = book.register(first)
    p2 = book.register(second)
    assert p1.pool_id == p2.pool_id
    assert p2.member_count == 2
    assert p2.lower < 100.0 < p2.upper


def test_nearest_target_cannot_skip_intervening_liquidity() -> None:
    book = LiquidityBook("BTCUSDT", 0.1)
    source = book.register(IntrinsicSwing("l", "LOW", 100.0, 1, 2, 2.0, 15, 2.0))
    near = book.register(IntrinsicSwing("h1", "HIGH", 103.0, 3, 4, 2.0, 5, 2.0))
    book.register(IntrinsicSwing("h2", "HIGH", 108.0, 5, 6, 2.0, 15, 2.0))
    target = book.target_for(Side.LONG, 101.0, 10, exclude_pool_id=source.pool_id)
    assert target is near


def test_reclaim_plan_uses_sweep_extreme_and_preexisting_target() -> None:
    bundle = IntrinsicAuctionBundle("BTCUSDT", 0.1, 1.0)
    source = bundle.liquidity.register(
        IntrinsicSwing("low", "LOW", 100.0, 1, 2, 2.0, 15, 4.0)
    )
    target = bundle.liquidity.register(
        IntrinsicSwing("high", "HIGH", 105.0, 3, 4, 2.0, 15, 4.0)
    )
    episode = AuctionEpisode(
        episode_id="e",
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
        causal_event_id="e",
    )
    bar = candle(13, 100.2, 101.0, 99.9, 100.8)
    plan = bundle._build_plan(episode, bar, 2.0)
    assert plan is not None
    assert plan.stop == 97.9
    assert plan.target == target.lower
    assert plan.gross_rr >= 1.0
    assert plan.causal_event_id == "e"


def test_accepted_break_stop_invalidates_old_range_reentry() -> None:
    bundle = IntrinsicAuctionBundle("BTCUSDT", 0.1, 1.0)
    source = bundle.liquidity.register(
        IntrinsicSwing("high-source", "HIGH", 100.0, 1, 2, 2.0, 15, 4.0)
    )
    bundle.liquidity.register(
        IntrinsicSwing("high-target", "HIGH", 110.0, 3, 4, 2.0, 15, 4.0)
    )
    episode = AuctionEpisode(
        episode_id="accepted",
        pool_id=source.pool_id,
        kind=EpisodeKind.ACCEPTED_BREAK_RETEST,
        phase=EpisodePhase.WAIT_RESPONSE,
        side=Side.LONG,
        interaction_time_ns=10 * NS,
        phase_time_ns=11 * NS,
        source_lower=source.lower,
        source_upper=source.upper,
        source_center=source.center,
        sweep_extreme=101.0,
        break_extreme=103.0,
        origin_lower=source.lower,
        origin_upper=source.upper,
        retest_time_ns=12 * NS,
        retest_extreme=source.lower - 0.05,
        causal_event_id="accepted",
    )
    bar = candle(13, source.upper, 102.0, source.lower - 0.05, 101.5)
    plan = bundle._build_plan(episode, bar, 2.0)
    assert plan is not None
    assert plan.stop < source.lower
    assert plan.scenario_path == "ACCEPTANCE"


def _seed_micro_history(bundle: IntrinsicAuctionBundle, start: int = 40, end: int = 99) -> None:
    price = 100.0
    for i in range(start, end + 1):
        if i == end:
            bar = candle(i, 100.3, 100.4, 99.9, 100.0)
        else:
            bar = candle(i, price, price + 0.2, price - 0.1, price + 0.1)
            price = 100.0 if price > 100.3 else price + 0.02
        bundle.on_bar(1, bar)


def _seed_five_history(bundle: IntrinsicAuctionBundle, start: int = 40, end: int = 95) -> None:
    for i in range(start, end + 1, 5):
        center = 100.0 + ((i // 5) % 3) * 0.05
        bundle.on_bar(5, candle(i, center, center + 0.20, center - 0.20, center + 0.05))


def test_reclaim_episode_emits_one_complete_plan_after_first_mitigation_response() -> None:
    bundle = IntrinsicAuctionBundle("BTCUSDT", 0.1, 1.0)
    _seed_five_history(bundle)
    source = bundle.liquidity.register(
        IntrinsicSwing("source-low", "LOW", 100.0, NS, 2 * NS, 2.0, 15, 4.0)
    )
    target = bundle.liquidity.register(
        IntrinsicSwing("target-high", "HIGH", 105.0, 3 * NS, 4 * NS, 2.0, 15, 4.0)
    )
    _seed_micro_history(bundle)

    bundle.on_bar(5, candle(100, 100.2, 100.4, 99.0, 100.1))
    assert len(bundle.episodes) == 1
    episode = next(iter(bundle.episodes.values()))
    assert episode.kind is EpisodeKind.LIQUIDITY_RECLAIM
    assert episode.side is Side.LONG
    assert PoolRole.EXTERNAL_STOP_POOL.value in episode.source_roles

    assert bundle.on_bar(1, candle(101, 100.1, 101.5, 100.0, 101.2)) == []
    episode = next(iter(bundle.episodes.values()))
    assert episode.phase is EpisodePhase.WAIT_RETEST
    assert episode.origin_lower is not None and episode.origin_upper is not None

    plans = bundle.on_bar(1, candle(102, 100.1, 101.8, 100.0, 101.7))
    assert len(plans) == 1
    plan = plans[0]
    assert plan.causal_event_id == episode.causal_event_id
    assert plan.family == EpisodeKind.LIQUIDITY_RECLAIM.value
    assert plan.stop == 98.9
    assert plan.target == target.lower
    assert plan.gross_rr >= 1.0
    assert source.consumed_time_ns == 102 * NS


def test_accepted_break_requires_next_five_minute_hold_then_retest_response() -> None:
    bundle = IntrinsicAuctionBundle("BTCUSDT", 0.1, 1.0)
    _seed_five_history(bundle)
    bundle.liquidity.register(
        IntrinsicSwing("range-low", "LOW", 95.0, NS, 2 * NS, 2.0, 15, 4.0)
    )
    source = bundle.liquidity.register(
        IntrinsicSwing("source-high", "HIGH", 100.0, 3 * NS, 4 * NS, 2.0, 15, 4.0)
    )
    target = bundle.liquidity.register(
        IntrinsicSwing("target-high", "HIGH", 110.0, 5 * NS, 6 * NS, 2.0, 5, 4.0)
    )
    _seed_micro_history(bundle)

    bundle.on_bar(5, candle(100, 100.0, 101.6, 99.9, 101.3))
    episode = next(iter(bundle.episodes.values()))
    assert episode.phase is EpisodePhase.BREAK_PENDING
    assert episode.side is Side.LONG
    assert PoolRole.VALUE_BOUNDARY.value in episode.source_roles

    bundle.on_bar(5, candle(105, 101.3, 102.1, 101.0, 101.7))
    episode = next(iter(bundle.episodes.values()))
    assert episode.kind is EpisodeKind.ACCEPTED_BREAK_RETEST
    assert episode.phase is EpisodePhase.WAIT_RETEST

    plans = bundle.on_bar(1, candle(106, 100.3, 101.7, 100.2, 101.4))
    assert len(plans) == 1
    plan = plans[0]
    assert plan.scenario_path == "ACCEPTANCE"
    assert plan.target == target.lower
    assert plan.stop < source.lower
    assert plan.gross_rr >= 1.0


def test_failed_break_becomes_opposite_reclaim_in_same_causal_episode() -> None:
    bundle = IntrinsicAuctionBundle("BTCUSDT", 0.1, 1.0)
    _seed_five_history(bundle)
    bundle.liquidity.register(
        IntrinsicSwing("range-low", "LOW", 95.0, NS, 2 * NS, 2.0, 15, 4.0)
    )
    source = bundle.liquidity.register(
        IntrinsicSwing("source-high", "HIGH", 100.0, 3 * NS, 4 * NS, 2.0, 15, 4.0)
    )
    bundle.on_bar(5, candle(100, 100.0, 101.6, 99.9, 101.3))
    original = next(iter(bundle.episodes.values()))
    original_id = original.episode_id

    bundle.on_bar(5, candle(105, 101.3, 101.4, 99.8, 100.1))
    converted = next(iter(bundle.episodes.values()))
    assert converted.episode_id == original_id
    assert converted.kind is EpisodeKind.LIQUIDITY_RECLAIM
    assert converted.side is Side.SHORT
    assert converted.phase is EpisodePhase.WAIT_DISPLACEMENT
    assert source.engaged_event_id == original_id


def test_touched_reaction_obstacle_remains_first_full_exit_target() -> None:
    book = LiquidityBook("BTCUSDT", 0.1)
    source = book.register(IntrinsicSwing("low", "LOW", 100.0, 1, 2, 2.0, 15, 2.0))
    target = book.register(IntrinsicSwing("high", "HIGH", 105.0, 3, 4, 2.0, 15, 2.0))
    assert book.target_for(Side.LONG, 101.0, 10, exclude_pool_id=source.pool_id) is target
    book.observe_touch(Candle(ts_close_ns=11, open=104.0, high=target.lower, low=103.9, close=104.4), 1)
    assert target.objective_spent_time_ns == 11
    assert book.target_for(Side.LONG, 101.0, 12, exclude_pool_id=source.pool_id) is target


def test_directional_change_rejects_non_monotonic_time() -> None:
    dc = AdaptiveDirectionalChange("BTCUSDT", 5, 0.1)
    dc.on_bar(candle(1, 100.0, 101.0, 99.0, 100.5))
    try:
        dc.on_bar(candle(1, 100.5, 101.5, 100.0, 101.0))
    except ValueError as exc:
        assert "strictly increasing" in str(exc)
    else:
        raise AssertionError("duplicate timestamp was accepted")


def test_same_five_minute_boundary_event_shares_one_causal_identity() -> None:
    bundle = IntrinsicAuctionBundle("BTCUSDT", 0.1, 1.0)
    _seed_five_history(bundle)
    low_5 = bundle.liquidity.register(
        IntrinsicSwing("low5", "LOW", 100.0, NS, 2 * NS, 2.0, 5, 3.0)
    )
    low_5.member_ids.append("low5b")
    low_5.member_prices.append(100.1)
    low_15 = bundle.liquidity.register(
        IntrinsicSwing("low15", "LOW", 99.9, 3 * NS, 4 * NS, 2.0, 15, 3.0)
    )
    bundle.liquidity.register(
        IntrinsicSwing("range-high", "HIGH", 105.0, 5 * NS, 6 * NS, 2.0, 15, 3.0)
    )
    bundle.on_bar(5, candle(100, 100.4, 100.5, 99.0, 100.2))
    assert len(bundle.episodes) == 2
    causal_ids = {episode.causal_event_id for episode in bundle.episodes.values()}
    assert causal_ids == {bundle._causal_event_id(100 * NS)}


def test_old_equal_extreme_starts_new_pool_after_intrinsic_horizon() -> None:
    book = LiquidityBook("BTCUSDT", 0.1)
    first = book.register(IntrinsicSwing("a", "HIGH", 100.0, 1, 2, 1.0, 5, 1.0))
    for index in range(2, 16):
        side = "LOW" if index % 2 == 0 else "HIGH"
        price = 95.0 - index if side == "LOW" else 105.0 + index
        book.register(IntrinsicSwing(str(index), side, price, index * 2, index * 2 + 1, 1.0, 5, 1.0))
    later = book.register(IntrinsicSwing("later", "HIGH", 100.1, 100, 101, 1.0, 5, 1.0))
    assert later.pool_id != first.pool_id


def test_one_minute_turn_is_internal_noise_until_equal_extrema_cluster() -> None:
    book = LiquidityBook("BTCUSDT", 0.1)
    source = book.register(IntrinsicSwing("low", "LOW", 100.0, 1, 2, 1.0, 15, 1.0))
    micro = book.register(IntrinsicSwing("micro", "HIGH", 102.0, 3, 4, 1.0, 1, 1.0))
    assert micro not in book.eligible_sources(10)
    assert book.target_for(Side.LONG, 101.0, 10, exclude_pool_id=source.pool_id) is None
    clustered = book.register(IntrinsicSwing("micro2", "HIGH", 102.05, 5, 6, 1.0, 1, 1.0))
    assert clustered is micro
    assert clustered.member_count == 2
    assert book.target_for(Side.LONG, 101.0, 10, exclude_pool_id=source.pool_id) is clustered

