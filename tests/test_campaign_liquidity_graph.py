from __future__ import annotations

import copy

import pytest

from smc_ict_4.campaign_policy.liquidity_graph import (
    Lifecycle,
    LiquidityGraph,
    LiquidityGraphError,
    LiquidityNode,
    LiquiditySide,
    NodeRole,
    ParentIdentity,
    SourceIdentity,
    TargetRelation,
    TargetRoute,
)


SYMBOL = "BTCUSDT"


def node(
    source_id: str,
    generation: int,
    *,
    role: NodeRole,
    side: LiquiditySide,
    scale: int,
    band: tuple[float, float],
    observed: int,
    parent: tuple[str, int] | None = None,
    paired_parent_edge: bool = False,
) -> LiquidityNode:
    return LiquidityNode(
        identity=SourceIdentity(source_id, generation),
        symbol=SYMBOL,
        role=role,
        side=side,
        scale_rank=scale,
        lower=band[0],
        upper=band[1],
        observed_time_ns=observed,
        parent=None if parent is None else ParentIdentity(*parent),
        paired_parent_edge=paired_parent_edge,
    )


def graph_with_balance() -> LiquidityGraph:
    graph = LiquidityGraph(SYMBOL)
    graph.observe(
        node(
            "balance-A",
            1,
            role=NodeRole.BALANCE,
            side=LiquiditySide.HIGH,
            scale=3,
            band=(90.0, 110.0),
            observed=1,
        )
    )
    return graph


def test_exact_parent_generation_and_source_generation_supersede_subtree_atomically() -> None:
    graph = graph_with_balance()
    graph.observe(
        node(
            "source-high",
            1,
            role=NodeRole.SOURCE,
            side=LiquiditySide.HIGH,
            scale=3,
            band=(109.0, 110.0),
            observed=2,
            parent=("balance-A", 1),
        )
    )
    graph.observe(
        node(
            "source-child",
            1,
            role=NodeRole.SOURCE,
            side=LiquiditySide.HIGH,
            scale=1,
            band=(107.0, 108.0),
            observed=3,
            parent=("source-high", 1),
        )
    )

    graph.observe(
        node(
            "source-high",
            2,
            role=NodeRole.SOURCE,
            side=LiquiditySide.HIGH,
            scale=3,
            band=(111.0, 112.0),
            observed=10,
            parent=("balance-A", 1),
        )
    )

    assert graph.node(SourceIdentity("source-high", 1)).lifecycle is Lifecycle.SUPERSEDED
    assert graph.node(SourceIdentity("source-child", 1)).lifecycle is Lifecycle.SUPERSEDED
    assert graph.node(SourceIdentity("source-high", 2)).lifecycle is Lifecycle.FRESH
    with pytest.raises(LiquidityGraphError, match="retired parent"):
        graph.observe(
            node(
                "late-child",
                1,
                role=NodeRole.SOURCE,
                side=LiquiditySide.LOW,
                scale=1,
                band=(100.0, 101.0),
                observed=11,
                parent=("source-high", 1),
            )
        )


def test_failed_generation_observation_leaves_prior_subtree_unchanged() -> None:
    graph = graph_with_balance()
    old = node(
        "recursive-source",
        1,
        role=NodeRole.SOURCE,
        side=LiquiditySide.HIGH,
        scale=2,
        band=(109.0, 110.0),
        observed=2,
        parent=("balance-A", 1),
    )
    graph.observe(old)
    before = graph.snapshot()

    with pytest.raises(LiquidityGraphError, match="parent retired by its own observation"):
        graph.observe(
            node(
                "recursive-source",
                2,
                role=NodeRole.SOURCE,
                side=LiquiditySide.HIGH,
                scale=2,
                band=(111.0, 112.0),
                observed=3,
                parent=("recursive-source", 1),
            )
        )
    assert graph.snapshot() == before


def test_child_cannot_be_observed_before_exact_parent_generation() -> None:
    graph = LiquidityGraph(SYMBOL)
    graph.observe(node("late-parent", 1, role=NodeRole.BALANCE, side=LiquiditySide.HIGH, scale=3, band=(90.0, 110.0), observed=10))
    with pytest.raises(LiquidityGraphError, match="cannot precede"):
        graph.observe(node("early-child", 1, role=NodeRole.SOURCE, side=LiquiditySide.HIGH, scale=2, band=(109.0, 110.0), observed=9, parent=("late-parent", 1)))


def test_parent_retirement_invalidates_all_descendants_without_partial_state() -> None:
    graph = graph_with_balance()
    graph.observe(
        node(
            "edge",
            1,
            role=NodeRole.SOURCE,
            side=LiquiditySide.LOW,
            scale=2,
            band=(90.0, 91.0),
            observed=2,
            parent=("balance-A", 1),
        )
    )
    graph.observe(
        node(
            "nested-objective",
            1,
            role=NodeRole.OBJECTIVE,
            side=LiquiditySide.HIGH,
            scale=2,
            band=(99.0, 100.0),
            observed=3,
            parent=("edge", 1),
        )
    )

    graph.invalidate(SourceIdentity("balance-A", 1), time_ns=20, reason="BALANCE_FAILED")

    assert graph.node(SourceIdentity("balance-A", 1)).lifecycle is Lifecycle.INVALIDATED
    assert graph.node(SourceIdentity("edge", 1)).lifecycle is Lifecycle.INVALIDATED
    assert graph.node(SourceIdentity("nested-objective", 1)).lifecycle is Lifecycle.INVALIDATED


def test_rejection_does_not_skip_nearer_obstacle_for_paired_parent_edge() -> None:
    graph = graph_with_balance()
    source = node(
        "attacked-high",
        1,
        role=NodeRole.SOURCE,
        side=LiquiditySide.HIGH,
        scale=3,
        band=(109.0, 110.0),
        observed=2,
        parent=("balance-A", 1),
    )
    paired = node(
        "paired-low",
        1,
        role=NodeRole.OBJECTIVE,
        side=LiquiditySide.LOW,
        scale=3,
        band=(90.0, 91.0),
        observed=2,
        parent=("balance-A", 1),
        paired_parent_edge=True,
    )
    # The first fresh obstacle remains the target even though it is lower scale
    # and is not the paired parent edge.
    internal = node(
            "internal-low",
            1,
            role=NodeRole.OBJECTIVE,
            side=LiquiditySide.LOW,
            scale=1,
            band=(106.0, 107.0),
            observed=2,
            parent=("balance-A", 1),
        )
    graph.observe(internal)
    graph.observe(source)
    graph.observe(paired)

    other_parent = node(
        "balance-B",
        1,
        role=NodeRole.BALANCE,
        side=LiquiditySide.LOW,
        scale=3,
        band=(95.0, 105.0),
        observed=3,
    )
    graph.observe(other_parent)
    graph.observe(
        node(
            "closer-unpaired-low",
            1,
            role=NodeRole.OBJECTIVE,
            side=LiquiditySide.LOW,
            scale=3,
            band=(105.0, 106.0),
            observed=3,
            parent=("balance-B", 1),
        )
    )

    selected = graph.select_target(source.identity, route=TargetRoute.REJECTION, decision_time_ns=5)
    assert selected is not None
    assert selected.target == internal.identity
    assert selected.relation is TargetRelation.FIRST_OUTWARD_OBJECTIVE
    assert selected.target_price == 107.0

    with pytest.raises(LiquidityGraphError, match="does not intersect"):
        graph.consume_touch(internal.identity, time_ns=6, low=103.0, high=104.0)
    graph.consume_touch(internal.identity, time_ns=7, low=106.5, high=107.5)
    assert graph.node(internal.identity).lifecycle is Lifecycle.CONSUMED


def test_acceptance_uses_first_fresh_obstacle_without_scale_skipping() -> None:
    graph = graph_with_balance()
    source = node(
        "attacked-high",
        1,
        role=NodeRole.SOURCE,
        side=LiquiditySide.HIGH,
        scale=2,
        band=(109.0, 110.0),
        observed=2,
        parent=("balance-A", 1),
    )
    graph.observe(source)
    micro = node(
            "micro-high",
            1,
            role=NodeRole.OBJECTIVE,
            side=LiquiditySide.HIGH,
            scale=1,
            band=(111.0, 112.0),
            observed=3,
            parent=("balance-A", 1),
        )
    graph.observe(micro)
    near = node(
        "near-high",
        1,
        role=NodeRole.OBJECTIVE,
        side=LiquiditySide.HIGH,
        scale=2,
        band=(113.0, 114.0),
        observed=3,
        parent=("balance-A", 1),
    )
    graph.observe(near)
    future = node(
        "future-high",
        1,
        role=NodeRole.OBJECTIVE,
        side=LiquiditySide.HIGH,
        scale=4,
        band=(112.0, 112.5),
        observed=20,
        parent=("balance-A", 1),
    )
    graph.observe(future)

    selected = graph.select_target(source.identity, route=TargetRoute.ACCEPTANCE, decision_time_ns=10)
    assert selected is not None
    assert selected.target == micro.identity
    assert selected.relation is TargetRelation.FIRST_OUTWARD_OBJECTIVE

    graph.consume_touch(micro.identity, time_ns=11, low=111.5, high=112.5)
    selected_near = graph.select_target(
        source.identity, route=TargetRoute.ACCEPTANCE, decision_time_ns=15
    )
    assert selected_near is not None and selected_near.target == near.identity
    graph.consume_touch(near.identity, time_ns=16, low=113.5, high=114.5)
    later = graph.select_target(source.identity, route=TargetRoute.ACCEPTANCE, decision_time_ns=20)
    assert later is not None and later.target == future.identity


def test_signal_reference_price_excludes_objectives_already_behind_entry() -> None:
    graph = graph_with_balance()
    source = node(
        "attacked-high", 1, role=NodeRole.SOURCE, side=LiquiditySide.HIGH,
        scale=2, band=(109.0, 110.0), observed=2, parent=("balance-A", 1),
    )
    behind = node(
        "behind-entry", 1, role=NodeRole.OBJECTIVE, side=LiquiditySide.HIGH,
        scale=1, band=(111.0, 112.0), observed=3, parent=("balance-A", 1),
    )
    ahead = node(
        "ahead-entry", 1, role=NodeRole.OBJECTIVE, side=LiquiditySide.HIGH,
        scale=1, band=(114.0, 115.0), observed=3, parent=("balance-A", 1),
    )
    for item in (source, behind, ahead):
        graph.observe(item)

    selected = graph.select_target(
        source.identity,
        route=TargetRoute.ACCEPTANCE,
        decision_time_ns=10,
        reference_price=113.0,
    )
    assert selected is not None and selected.target == ahead.identity


def test_active_campaign_does_not_drop_new_source_and_time_alone_never_expires_nodes() -> None:
    graph = graph_with_balance()
    first = node(
        "first-source",
        1,
        role=NodeRole.SOURCE,
        side=LiquiditySide.HIGH,
        scale=2,
        band=(109.0, 110.0),
        observed=2,
        parent=("balance-A", 1),
    )
    graph.observe(first)
    graph.activate(first.identity, time_ns=3, reason="CAMPAIGN_ATTACK")
    second = node(
        "second-source",
        1,
        role=NodeRole.SOURCE,
        side=LiquiditySide.LOW,
        scale=2,
        band=(90.0, 91.0),
        observed=1_000_000_000,
        parent=("balance-A", 1),
    )
    graph.observe(second)

    assert graph.node(first.identity).lifecycle is Lifecycle.ACTIVE
    assert graph.node(second.identity).lifecycle is Lifecycle.FRESH
    # There is intentionally no clock-expiry operation; an arbitrarily late
    # causal decision still sees the structurally fresh node.
    assert graph.node(second.identity).lifecycle is Lifecycle.FRESH


def test_snapshot_restore_is_deterministic_and_rejects_live_orphan() -> None:
    graph = graph_with_balance()
    source = node(
        "edge",
        1,
        role=NodeRole.SOURCE,
        side=LiquiditySide.HIGH,
        scale=2,
        band=(109.0, 110.0),
        observed=2,
        parent=("balance-A", 1),
    )
    graph.observe(source)
    graph.activate(source.identity, time_ns=3, reason="ATTACK")

    snapshot = graph.snapshot()
    restored = LiquidityGraph.restore(copy.deepcopy(snapshot))
    assert restored.snapshot() == snapshot
    assert restored.children(SourceIdentity("balance-A", 1), recursive=True) == (
        restored.node(source.identity),
    )

    orphan = copy.deepcopy(snapshot)
    orphan["nodes"] = [item for item in orphan["nodes"] if item["source_id"] != "balance-A"]
    with pytest.raises(LiquidityGraphError, match="missing parent"):
        LiquidityGraph.restore(orphan)
