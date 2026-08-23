from __future__ import annotations

from smc_ict_4.campaign_policy.attack_ledger import AttackOutcome
from smc_ict_4.campaign_policy.liquidity_graph import Lifecycle, NodeRole, TargetRoute
from smc_ict_4.campaign_policy.structural_stream import StructuralLiquidityStream
from smc_ict_4.episode_policy_live.domain import Bar


NS_MIN = 60_000_000_000


def bar(index: int, *, high: float, low: float, close: float | None = None) -> Bar:
    opened = index * 5 * NS_MIN
    close = (high + low) / 2 if close is None else close
    return Bar(
        symbol="BTCUSDT",
        interval_minutes=5,
        open_time_ns=opened,
        close_time_ns=opened + 5 * NS_MIN,
        open=close,
        high=high,
        low=low,
        close=close,
        volume=1.0,
        quote_volume=100.0,
        taker_buy_quote_volume=50.0,
    )


def day_then_first_bar(stream: StructuralLiquidityStream) -> None:
    for i in range(288):
        stream.push(bar(i, high=110.0, low=90.0, close=100.0))
    stream.push(bar(288, high=105.0, low=95.0, close=100.0))


def test_completed_prior_day_creates_exact_parent_and_edges() -> None:
    stream = StructuralLiquidityStream("BTCUSDT", 0.1)
    day_then_first_bar(stream)
    balances = [node for node in stream.graph.nodes() if node.role is NodeRole.BALANCE and node.scale_rank == 1440]
    sources = [node for node in stream.graph.nodes() if node.role is NodeRole.SOURCE and node.scale_rank == 1440]
    assert len(balances) == 1
    assert balances[0].generation == 1
    assert len(sources) == 2
    assert {node.parent.key for node in sources} == {balances[0].identity}
    edge_objectives = [node for node in stream.graph.nodes() if node.role is NodeRole.OBJECTIVE and node.scale_rank == 1440]
    assert len(edge_objectives) == 2
    assert {node.parent.key for node in edge_objectives} == {balances[0].identity}


def test_confirmed_pivot_is_not_visible_or_consumed_before_confirmation() -> None:
    stream = StructuralLiquidityStream("BTCUSDT", 0.1)
    day_then_first_bar(stream)
    base = 289
    highs = [101.0, 102.0, 108.0, 103.0]
    for offset, high in enumerate(highs):
        update = stream.push(bar(base + offset, high=high, low=99.0))
        assert not [node for node in update.new_objectives if node.scale_rank == 5]
    confirmation = stream.push(bar(base + 4, high=102.0, low=99.0))
    objectives = [node for node in confirmation.new_objectives if node.scale_rank == 5]
    assert len(objectives) == 1
    assert objectives[0].lifecycle is Lifecycle.FRESH
    assert objectives[0].observed_time_ns == confirmation.time_ns


def test_first_touch_does_not_consume_source_and_fresh_extreme_extends_attack() -> None:
    from smc_ict_4.campaign_policy.attack_ledger import SourceKey

    stream = StructuralLiquidityStream("BTCUSDT", 0.1)
    day_then_first_bar(stream)
    high_source = next(node for node in stream.graph.nodes() if node.role is NodeRole.SOURCE and node.side.value == "HIGH")
    stream.push(bar(289, high=110.2, low=108.0, close=109.0))
    campaign = stream.ledger.campaign(SourceKey(high_source.source_id, high_source.generation))
    assert campaign is not None
    assert len(campaign.attacks) == 1
    assert stream.graph.node(high_source.identity).lifecycle is Lifecycle.ACTIVE
    stream.push(bar(290, high=111.0, low=108.5, close=109.0))
    campaign = stream.ledger.campaign(SourceKey(high_source.source_id, high_source.generation))
    assert len(campaign.attacks) == 1
    assert campaign.attacks[0].extreme == 111.0
    assert stream.graph.node(high_source.identity).lifecycle is Lifecycle.ACTIVE


def test_departure_confirmed_opposing_pivot_then_reattack_appends_lineage() -> None:
    from smc_ict_4.campaign_policy.attack_ledger import SourceKey

    stream = StructuralLiquidityStream("BTCUSDT", 0.1)
    day_then_first_bar(stream)
    source = next(node for node in stream.graph.nodes() if node.role is NodeRole.SOURCE and node.side.value == "HIGH")
    stream.push(bar(289, high=110.5, low=108.0, close=109.0))
    # A low pivot occurs after inward departure and becomes known two bars later.
    stream.push(bar(290, high=106.0, low=103.0, close=104.0))
    stream.push(bar(291, high=105.0, low=100.0, close=101.0))
    stream.push(bar(292, high=106.0, low=102.0, close=104.0))
    stream.push(bar(293, high=107.0, low=103.0, close=105.0))
    key = SourceKey(source.source_id, source.generation)
    campaign = stream.ledger.campaign(key)
    assert campaign.attacks[-1].outcome is AttackOutcome.RESPONSE_COMPLETED
    stream.push(bar(294, high=111.0, low=108.0, close=109.0))
    campaign = stream.ledger.campaign(key)
    assert [attack.ordinal for attack in campaign.attacks] == [1, 2]


def test_objective_consumes_only_on_later_actual_touch() -> None:
    stream = StructuralLiquidityStream("BTCUSDT", 0.1)
    day_then_first_bar(stream)
    base = 289
    for offset, high in enumerate([101.0, 102.0, 108.0, 103.0, 102.0]):
        update = stream.push(bar(base + offset, high=high, low=99.0))
    objective = next(node for node in update.new_objectives if node.scale_rank == 5)
    assert stream.graph.node(objective.identity).lifecycle is Lifecycle.FRESH
    stream.push(bar(base + 5, high=108.1, low=106.0))
    assert stream.graph.node(objective.identity).lifecycle is Lifecycle.CONSUMED


def test_high_source_rejection_selects_exact_parent_low_edge_until_actual_touch() -> None:
    stream = StructuralLiquidityStream("BTCUSDT", 0.1)
    day_then_first_bar(stream)
    source = next(
        node for node in stream.graph.nodes()
        if node.role is NodeRole.SOURCE and node.scale_rank == 1440 and node.side.value == "HIGH"
    )
    low_edge = next(
        node for node in stream.graph.nodes()
        if node.role is NodeRole.OBJECTIVE and node.scale_rank == 1440 and node.side.value == "LOW"
    )
    # The high attack may consume its same-side edge objective, but must leave
    # the rejection route's exact-parent low edge available.
    attack = stream.push(bar(289, high=110.2, low=108.0, close=109.0))
    assert stream.graph.node(source.identity).lifecycle is Lifecycle.ACTIVE
    assert stream.graph.node(low_edge.identity).lifecycle is Lifecycle.FRESH
    selected = stream.graph.select_target(
        source.identity,
        route=TargetRoute.REJECTION,
        decision_time_ns=attack.time_ns,
    )
    assert selected is not None
    assert selected.target == low_edge.identity

    stream.push(bar(290, high=100.0, low=89.9, close=95.0))
    assert stream.graph.node(low_edge.identity).lifecycle is Lifecycle.CONSUMED


def test_new_balance_edge_objective_is_not_retroactively_consumed_on_observation_bar() -> None:
    stream = StructuralLiquidityStream("BTCUSDT", 0.1)
    for i in range(288):
        stream.push(bar(i, high=110.0, low=90.0, close=100.0))
    update = stream.push(bar(288, high=110.5, low=108.0, close=109.0))
    high_edge = next(
        node for node in update.new_objectives
        if node.scale_rank == 1440 and node.side.value == "HIGH"
    )
    assert stream.graph.node(high_edge.identity).lifecycle is Lifecycle.FRESH
    stream.push(bar(289, high=110.5, low=108.0, close=109.0))
    assert stream.graph.node(high_edge.identity).lifecycle is Lifecycle.CONSUMED


def test_no_ttl_and_prefix_invariance() -> None:
    bars = [bar(i, high=110.0, low=90.0, close=100.0) for i in range(288)]
    bars += [bar(288 + i, high=105.0, low=95.0, close=100.0) for i in range(20)]
    first = StructuralLiquidityStream("BTCUSDT", 0.1)
    second = StructuralLiquidityStream("BTCUSDT", 0.1)
    for item in bars:
        first.push(item)
    for item in bars[:300]:
        second.push(item)
    prefix_snapshot = second.snapshot()
    for item in bars[300:]:
        second.push(item)
    assert second.snapshot() == first.snapshot()
    assert all(node.lifecycle is Lifecycle.FRESH for node in first.graph.nodes() if node.role is NodeRole.SOURCE)
    assert prefix_snapshot["graph"]["nodes"]


def test_new_completed_day_atomically_supersedes_exact_parent_generation() -> None:
    stream = StructuralLiquidityStream("BTCUSDT", 0.1)
    for i in range(288):
        stream.push(bar(i, high=110.0, low=90.0, close=100.0))
    stream.push(bar(288, high=120.0, low=80.0, close=100.0))
    old = next(node for node in stream.graph.nodes() if node.role is NodeRole.BALANCE and node.scale_rank == 1440)
    for i in range(289, 576):
        stream.push(bar(i, high=120.0, low=80.0, close=100.0))
    update = stream.push(bar(576, high=105.0, low=95.0, close=100.0))
    current = [node for node in stream.graph.nodes() if node.role is NodeRole.BALANCE and node.scale_rank == 1440 and node.live]
    assert len(current) == 1 and current[0].generation == 2
    assert stream.graph.node(old.identity).lifecycle is Lifecycle.SUPERSEDED
    assert {node.parent.generation for node in update.new_sources if node.scale_rank == 1440} == {2}


def test_first_equal_pool_generation_starts_at_one() -> None:
    stream = StructuralLiquidityStream("BTCUSDT", 0.1)
    day_then_first_bar(stream)
    parent = next(
        node for node in stream.graph.nodes()
        if node.role is NodeRole.BALANCE and node.scale_rank == 1440
    )
    from smc_ict_4.episode_policy_live.domain import Pivot

    base = parent.observed_time_ns
    first = Pivot("p1", "BTCUSDT", 15, "HIGH", 105.0, base + 1, base + 2, 1, 1.0)
    second = Pivot("p2", "BTCUSDT", 15, "HIGH", 105.01, base + 3, base + 4, 2, 1.0)
    events = []
    ledger_events = []
    assert stream._observe_equal_pool(first, events, ledger_events) is None
    source = stream._observe_equal_pool(second, events, ledger_events)
    assert source is not None
    assert source.generation == 1
    assert source.parent is not None and source.parent.key == parent.identity
