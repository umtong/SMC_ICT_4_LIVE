from __future__ import annotations

from smc_ict_4.episode_policy_live.domain import Bar, Pivot
from smc_ict_4.episode_policy_live.structural_liquidity import (
    DefenseBand,
    FeasibleTrendChannelBook,
    MatureBalanceTracker,
    StructuralNode,
    StructureRole,
    destination_first_geometry,
    event_local_locations,
    structural_stop,
)


MINUTE = 60_000_000_000


def bar(
    serial: int,
    *,
    open_: float = 100.0,
    high: float = 101.0,
    low: float = 99.0,
    close: float = 100.0,
    interval: int = 5,
) -> Bar:
    return Bar(
        symbol="BTCUSDT",
        interval_minutes=interval,
        open_time_ns=serial * interval * MINUTE,
        close_time_ns=(serial + 1) * interval * MINUTE,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1.0,
        quote_volume=100.0,
        taker_buy_quote_volume=50.0,
        trade_count=1,
    )


def pivot(book: FeasibleTrendChannelBook, serial: int, side: str, price: float, name: str) -> Pivot:
    return Pivot(
        pivot_id=name,
        symbol="BTCUSDT",
        timeframe_minutes=5,
        side=side,
        price=price,
        event_time_ns=book.bars[serial].close_time_ns,
        observed_time_ns=book.bars[-1].close_time_ns,
        serial=len(book.bars) - 1,
        strength=1.0,
    )


def node(
    name: str,
    side: str,
    role: StructureRole,
    price: float,
    *,
    consumed: int | None = None,
) -> StructuralNode:
    return StructuralNode(
        node_id=name,
        symbol="BTCUSDT",
        side=side,
        kind="SWING",
        role=role,
        timeframe_minutes=5,
        observed_time_ns=1,
        lower=price - 0.1,
        upper=price + 0.1,
        anchor_serial=0,
        consumed_time_ns=consumed,
    )


def test_channel_main_edge_and_line_alias_wait_for_opposite_fourth_point() -> None:
    book = FeasibleTrendChannelBook("BTCUSDT", 5, 0.1)
    rows = [
        bar(0, high=104.0, low=99.0, close=101.0),
        bar(1, open_=102.0, high=106.0, low=101.0, close=104.0),
        bar(2, open_=103.0, high=105.0, low=102.0, close=103.0),
        bar(3, open_=104.0, high=106.0, low=103.0, close=104.0),
    ]
    for item in rows:
        book.observe_bar(item)
    book.observe_pivot(pivot(book, 0, "LOW", 99.0, "L1"))
    book.observe_pivot(pivot(book, 1, "HIGH", 106.0, "H1"))
    line, channel = book.observe_pivot(pivot(book, 2, "LOW", 102.0, "L2"))
    assert line is not None and channel is not None

    before = book.projected_nodes(book.bars[-1].close_time_ns + 1, 3)
    assert line.line_id not in {item.node_id for item in before}
    assert f"{channel.channel_id}:LOWER" not in {item.node_id for item in before}
    assert f"{channel.channel_id}:UPPER" in {item.node_id for item in before}

    upper = book._edge_value(channel, "UPPER", 4)
    book.observe_bar(bar(4, open_=upper, high=upper + 0.5, low=upper - 0.2, close=upper))
    after = book.projected_nodes(book.bars[-1].close_time_ns + 1, 4)
    assert line.line_id not in {item.node_id for item in after}
    assert f"{channel.channel_id}:LOWER" in {item.node_id for item in after}


def test_chained_line_creates_a_new_version_and_supersedes_the_old_one() -> None:
    book = FeasibleTrendChannelBook("BTCUSDT", 5, 0.1)
    for serial, low in enumerate((99.0, 101.0, 102.0, 104.0, 105.0)):
        book.observe_bar(
            bar(serial, open_=low + 1.0, high=low + 5.0, low=low, close=low + 2.0)
        )
    book.observe_pivot(pivot(book, 0, "LOW", 99.0, "P1"))
    first, _ = book.observe_pivot(pivot(book, 2, "LOW", 102.0, "P2"))
    second, _ = book.observe_pivot(pivot(book, 4, "LOW", 105.0, "P3"))
    assert first is not None and second is not None
    assert second.version == first.version + 1
    assert first.superseded_time_ns == second.observed_time_ns


def test_balance_requires_two_sided_repeated_defense_and_later_midpoint_traversal() -> None:
    tracker = MatureBalanceTracker("BTCUSDT")
    tracker.register_defense(DefenseBand("R", "RESISTANCE", 109.5, 110.5, 1, 2))
    tracker.register_defense(DefenseBand("S", "SUPPORT", 89.5, 90.5, 2, 1))
    tracker.observe_bar(bar(0, open_=95.0, high=100.0, low=94.0, close=95.0))
    assert tracker.active is None  # one touch is not repeated defense

    tracker.register_defense(DefenseBand("S2", "SUPPORT", 89.5, 90.5, 3, 2))
    tracker.observe_bar(bar(1, open_=95.0, high=98.0, low=94.0, close=95.0))
    assert tracker.active is not None and tracker.active.mature_time_ns is None
    tracker.observe_bar(bar(2, open_=95.0, high=99.0, low=94.0, close=98.0))
    assert tracker.active is not None and tracker.active.mature_time_ns is None
    tracker.observe_bar(bar(3, open_=99.0, high=103.0, low=98.0, close=101.0))
    assert tracker.active is not None and tracker.active.mature_time_ns is not None
    sweep = tracker.observe_bar(bar(4, open_=95.0, high=100.0, low=89.0, close=96.0))
    assert sweep is not None
    assert sweep.side == "LONG" and sweep.target == 109.5


def test_ob_and_fvg_are_locations_only_when_born_inside_event_at_source() -> None:
    bars = [
        bar(0, interval=1, open_=100.0, high=101.0, low=98.8, close=99.0),
        bar(1, interval=1, open_=99.0, high=103.0, low=98.9, close=102.5),
        bar(2, interval=1, open_=102.5, high=104.0, low=101.5, close=103.5),
    ]
    locations = event_local_locations(
        bars,
        side="LONG",
        event_start_time_ns=bars[0].open_time_ns,
        decision_time_ns=bars[-1].close_time_ns,
        source_lower=98.7,
        source_upper=99.2,
        tick_size=0.1,
    )
    assert {item.kind for item in locations} == {"ORDER_BLOCK", "FAIR_VALUE_GAP"}
    excluded = event_local_locations(
        bars,
        side="LONG",
        event_start_time_ns=bars[1].open_time_ns,
        decision_time_ns=bars[-1].close_time_ns,
        source_lower=98.7,
        source_upper=99.2,
        tick_size=0.1,
    )
    assert excluded == []


def test_structural_stop_uses_complete_episode_and_acceptance_origin() -> None:
    assert structural_stop(
        side="LONG",
        micro_stop=99.5,
        event_extreme=98.5,
        tick_size=0.1,
        source_invalidation=98.0,
        location_invalidation=98.7,
        acceptance_origin=97.0,
    ) == 96.9


def test_first_destination_below_one_r_is_not_replaced_by_farther_target() -> None:
    source = node("SOURCE", "LOW", StructureRole.SOURCE, 99.0)
    near = node("NEAR", "HIGH", StructureRole.DESTINATION, 101.0)
    far = node("FAR", "HIGH", StructureRole.DESTINATION, 110.0)
    result = destination_first_geometry(
        side="LONG",
        source=source,
        nodes=(near, far),
        entry=100.0,
        stop=98.0,
        decision_time_ns=10,
        serial=0,
    )
    assert not result.accepted
    assert result.reason == "FIRST_DESTINATION_BELOW_MINIMUM_R"
    assert result.destination is near


def test_spent_destination_is_skipped_but_route_obstacle_is_not() -> None:
    source = node("SOURCE", "LOW", StructureRole.SOURCE, 99.0)
    spent = node("SPENT", "HIGH", StructureRole.DESTINATION, 102.0, consumed=5)
    far = node("FAR", "HIGH", StructureRole.DESTINATION, 105.0)
    obstacle = node("OBSTACLE", "HIGH", StructureRole.ROUTE_OBSTACLE, 103.0)
    blocked = destination_first_geometry(
        side="LONG",
        source=source,
        nodes=(spent, far, obstacle),
        entry=100.0,
        stop=98.0,
        decision_time_ns=10,
        serial=0,
    )
    assert not blocked.accepted
    assert blocked.destination is far
    assert blocked.route_obstacle is obstacle
    assert blocked.reason == "ROUTE_OBSTACLE_BEFORE_DESTINATION"

    accepted = destination_first_geometry(
        side="LONG",
        source=source,
        nodes=(spent, far),
        entry=100.0,
        stop=98.0,
        decision_time_ns=10,
        serial=0,
    )
    assert accepted.accepted and accepted.destination is far
    assert accepted.gross_rr is not None and accepted.gross_rr > 2.0
