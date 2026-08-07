"""Nested source-liquidity scenario adapter for candidate-07."""
from __future__ import annotations

from collections import Counter
import json
from typing import Any, Iterable, Mapping

import pandas as pd

import diagnose_impact_resilience_1s as impact
from event_signal_data import CausalTradeSignal
from nautilus_trader.model.identifiers import InstrumentId
import run_local_liquidity_sweep_mss_retest as local
from nested_liquidity_sweep import (
    actual_timeframe_target,
    aggregate_thirty_seconds,
    independent_boundary,
    source_first_touches,
    source_timeframe,
)


_BASE_DIAGNOSE = local.diagnose
_BASE_DISCOVER = local.discover_structural_signals
_BASE_SIGNAL_BUILDER = local.build_causal_signals


def diagnose(
    seconds: pd.DataFrame,
    *,
    one_pools: Iterable[impact.Pool],
    five_pools: Iterable[impact.Pool],
    trade_start_ns: int,
    trade_end_ns: int,
    logic: local.LocalSweepMSSLogic,
    require_retest: bool,
    include_higher_sources: bool,
) -> dict[str, Any]:
    """Reuse the proven retest state machine with nested source liquidity."""
    if not require_retest:
        raise ValueError("nested successor freezes the useful retest transition")
    one_pool_list = list(one_pools)
    five_pool_list = list(five_pools)
    bars = local._prepare_local_bars(seconds, logic)
    original_pool_confirmations = impact._pool_confirmations
    pools_15 = original_pool_confirmations(
        bars,
        timeframe="15S",
        radius=logic.source_pivot_radius,
    )
    bars_30 = aggregate_thirty_seconds(bars)
    pools_30 = original_pool_confirmations(
        bars_30,
        timeframe="30S",
        radius=logic.source_pivot_radius,
    )
    source_pools = list(pools_15)
    if include_higher_sources:
        source_pools.extend(pools_30)
        source_pools.extend(one_pool_list)

    source_by_index: dict[int, impact.Pool] = {}
    contact_summary: dict[str, Any] = {}

    def recording_first_touches(
        frame: pd.DataFrame,
        pools: Iterable[impact.Pool],
    ) -> tuple[list[tuple[int, impact.Pool]], dict[str, Any]]:
        selected, summary = source_first_touches(frame, pools)
        source_by_index.update({index: pool for index, pool in selected})
        contact_summary.update(summary)
        return selected, summary

    original_prepare = local._prepare_local_bars
    original_first_touches = local._pool_first_touches
    original_latest = local._latest_opposing_swing
    original_target = local._target_pool

    def distinct_latest(
        pools: Iterable[impact.Pool],
        *,
        direction: str,
        contact_index: int,
        bars: pd.DataFrame,
        logic: local.LocalSweepMSSLogic,
    ) -> impact.Pool | None:
        source = source_by_index.get(contact_index)
        eligible = [item for item in pools if item.timeframe == "15S"]
        if source is not None:
            eligible = [
                item
                for item in eligible
                if independent_boundary(source, item)
            ]
        return original_latest(
            eligible,
            direction=direction,
            contact_index=contact_index,
            bars=bars,
            logic=logic,
        )

    local._prepare_local_bars = lambda _seconds, _logic: bars
    impact._pool_confirmations = (
        lambda frame, *, timeframe, radius: source_pools
        if timeframe == "15S" and frame is bars
        else original_pool_confirmations(frame, timeframe=timeframe, radius=radius)
    )
    local._pool_first_touches = recording_first_touches
    local._latest_opposing_swing = distinct_latest
    local._target_pool = actual_timeframe_target
    try:
        result = _BASE_DIAGNOSE(
            seconds,
            one_pools=one_pool_list,
            five_pools=five_pool_list,
            trade_start_ns=trade_start_ns,
            trade_end_ns=trade_end_ns,
            logic=logic,
            require_retest=True,
        )
    finally:
        local._prepare_local_bars = original_prepare
        impact._pool_confirmations = original_pool_confirmations
        local._pool_first_touches = original_first_touches
        local._latest_opposing_swing = original_latest
        local._target_pool = original_target

    source_counts: Counter[str] = Counter()
    for item in result["scenarios"]:
        timeframe = source_timeframe(str(item["source_pool_id"]))
        item["sweep"]["source_timeframe"] = timeframe
        boundary_pivot = int(str(item["mss"]["boundary_pool_id"]).rsplit("-", 1)[-1])
        item["mss"]["boundary_pivot_ts_ns"] = boundary_pivot
        source_counts[timeframe] += 1

    result["summary"].update(
        {
            "include_higher_sources": include_higher_sources,
            "source_timeframes": (
                ["15S", "30S", "1M"] if include_higher_sources else ["15S"]
            ),
            "entries_by_source_timeframe": dict(sorted(source_counts.items())),
            "independent_source_and_mss_pivots": True,
            "local_15s_pools": len(pools_15),
            "local_30s_pools": len(pools_30),
            "nested_contact_summary": contact_summary,
        }
    )
    return result


def discover(
    *,
    config: Mapping[str, Any],
    bundle: Any,
    start: Any,
    end: Any,
    require_retest: bool,
    include_higher_sources: bool,
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any], dict[str, Any]]:
    original_diagnose = local.diagnose
    local.diagnose = (
        lambda seconds, **kwargs: diagnose(
            seconds,
            **kwargs,
            include_higher_sources=include_higher_sources,
        )
    )
    try:
        bars, upstream, selected, contract = _BASE_DISCOVER(
            config=config,
            bundle=bundle,
            start=start,
            end=end,
            require_retest=require_retest,
        )
    finally:
        local.diagnose = original_diagnose
    contract.update(
        {
            "family": "nested_liquidity_sweep_mss_retest",
            "variant": (
                "15S_30S_1M_sources" if include_higher_sources else "15S_sources_only"
            ),
            "source_timeframes": selected["summary"]["source_timeframes"],
            "detector_population": (
                "literal first touch of nested causal 15S/30S/1M swing liquidity"
            ),
            "target_hierarchy": (
                "15S then 30S then 1M then 5M unconsumed causal liquidity"
            ),
            "independent_source_and_mss_pivots": True,
            "broken_level_retest_required": True,
            "selected_summary": selected["summary"],
        }
    )
    return bars, upstream, selected, contract


def build_causal_signals(
    *,
    report: Mapping[str, Any],
    upstream_report: Mapping[str, Any],
    instrument_id: InstrumentId,
) -> list[CausalTradeSignal]:
    base_signals = _BASE_SIGNAL_BUILDER(
        report=report,
        upstream_report=upstream_report,
        instrument_id=instrument_id,
    )
    output: list[CausalTradeSignal] = []
    for signal in base_signals:
        details = json.loads(signal.details_json)
        details["structural_family"] = "nested_liquidity_sweep_mss_retest"
        details["source_timeframes"] = report["summary"]["source_timeframes"]
        serialized = json.dumps(details, sort_keys=True)
        for forbidden in ("path", "mfe", "mae", "terminal", "realized_r"):
            if forbidden in serialized.lower():
                raise RuntimeError(f"future-path field leaked into signal: {forbidden}")
        output.append(
            CausalTradeSignal(
                instrument_id=signal.instrument_id,
                scenario_id=signal.scenario_id,
                direction=signal.direction,
                entry_reference=signal.entry_reference,
                stop_price=signal.stop_price,
                target_price=signal.target_price,
                expected_rr=signal.expected_rr,
                source_pool_id=signal.source_pool_id,
                signal_kind="NESTED_LIQUIDITY_SWEEP_MSS_RETEST",
                details_json=serialized,
                observed_time_ns=signal.observed_time_ns,
                ts_event=signal.ts_event,
                ts_init=signal.ts_init,
            )
        )
    return output
