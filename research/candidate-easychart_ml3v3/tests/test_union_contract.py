from __future__ import annotations

from contracts_v5 import ObjectKind, V5TradePlan
from domain import Side
from opportunity_union import EasyChartML3V3OpportunityUnion


def plan(plan_id: str, target: float = 102.0) -> V5TradePlan:
    return V5TradePlan(
        plan_id=plan_id,
        causal_event_id=f"raw-{plan_id}",
        symbol="BTCUSDT",
        family="TEST_FAMILY",
        side=Side.LONG,
        observed_time_ns=2_000_000_000,
        entry=100.0,
        stop=99.0,
        target=target,
        gross_rr=target - 100.0,
        setup_id=f"setup-{plan_id}",
        higher_zone_id="h",
        higher_zone_kind=ObjectKind.HORIZONTAL_SUPPORT,
        higher_strength_ratio=1.0,
        lower_zone_id="l",
        lower_zone_kind="ORDER_BLOCK",
        lower_strength_ratio=1.0,
        trigger_zone_id="t",
        trigger_strength_ratio=1.0,
        target_zone_id="o",
        target_zone_kind=ObjectKind.HORIZONTAL_RESISTANCE,
        overlap_lower=99.9,
        overlap_upper=100.1,
        interaction_time_ns=1_000_000_000,
        trigger_time_ns=2_000_000_000,
        scenario_path="ACCEPTANCE",
        setup_observed_time_ns=500_000_000,
        trigger_zone_kind="ORDER_BLOCK",
        source_rule_count=1,
        rule_provenance=("TEST",),
        scale_name="TEST",
        higher_timeframe_minutes=15,
        decision_timeframe_minutes=5,
        trigger_timeframe_minutes=1,
    )


def test_namespaces_and_collapses_only_exact_geometry() -> None:
    union = EasyChartML3V3OpportunityUnion.__new__(EasyChartML3V3OpportunityUnion)
    union.symbol = "BTCUSDT"
    union.tick_size = 0.1
    union.minimum_gross_rr = 1.0
    union.generators = {"a": object(), "b": object()}
    union.detectors = {}
    union._plans = []
    union._trace = []
    union._counts = {}
    union._plan_maps = {"a": {}, "b": {}}
    union._seen_plan_ids = set()
    union._seen_geometry = {}

    first = union._namespace("a", plan("one"))
    exact = union._namespace("b", plan("two"))
    variant = union._namespace("b", plan("three", target=103.0))

    assert first is not None
    assert exact is None
    assert variant is not None
    assert first.plan_id.startswith("ml3v3-a-")
    assert first.causal_event_id == variant.causal_event_id
    assert first.family.startswith("A::")
    assert union._counts["exact_geometry_duplicate_collapsed"] == 1
