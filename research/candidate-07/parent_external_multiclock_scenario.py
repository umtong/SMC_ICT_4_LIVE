"""Parent-external liquidity sweep with local multiclock execution.

The rejected multiclock portfolio treated every confirmed fifteen-second swing
as a reversal source. This successor changes only source ownership: a tradable
sweep must be the literal first touch of a causally confirmed, still-unconsumed
one-minute or five-minute swing pool. The sweep bar, flow qualification, local
MSS, first broken-level retest, target hierarchy, risk geometry and execution
cost model remain unchanged.

Pattern and scenario are deliberately separate:

- the local detector may continue to observe every fifteen-second recoil;
- only a recoil which actually consumes parent external liquidity is routed as a
  reversal scenario.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import contextmanager
from copy import deepcopy
import json
from typing import Any, Iterable, Iterator, Mapping

import numpy as np
import pandas as pd

import diagnose_impact_resilience_1s as impact
from diagnose_aggtrade_resilience_v2 import preconsume_before_event_window
from diagnose_failed_flow import aggregate_flow
from event_signal_data import CausalTradeSignal
from multiclock_ensemble_scenario import select_first_retests
from multiclock_sweep_mss_scenario import discover_five_second
from nautilus_trader.model.identifiers import InstrumentId
import run_local_liquidity_sweep_mss_retest as local
from run_aggtrade_resilience_second_safe import (
    first_touch_after_complete_confirmation_second,
)


_BASE_DISCOVER_FIFTEEN = local.discover_structural_signals
_BASE_SIGNAL_BUILDER = local.build_causal_signals
_PARENT_PRIORITY = {"1M": 1, "5M": 2}


def parent_source_first_touches(
    bars: pd.DataFrame,
    pools: Iterable[impact.Pool],
) -> tuple[list[tuple[int, impact.Pool]], dict[str, Any]]:
    """Consume one highest-timeframe parent pool at each literal touch bar."""
    pool_list = list(pools)
    if any(pool.timeframe not in _PARENT_PRIORITY for pool in pool_list):
        raise ValueError("parent source pools must be one-minute or five-minute")
    timestamps = bars["timestamp_ns"].astype("int64").to_numpy()
    highs = bars["high"].astype(float).to_numpy()
    lows = bars["low"].astype(float).to_numpy()
    closes = bars["close"].astype(float).to_numpy()
    previous_close = np.empty_like(closes)
    previous_close[0] = closes[0]
    previous_close[1:] = closes[:-1]

    by_index: dict[int, list[impact.Pool]] = defaultdict(list)
    never: Counter[str] = Counter()
    source_counts: Counter[str] = Counter(pool.timeframe for pool in pool_list)
    for pool in pool_list:
        touch = first_touch_after_complete_confirmation_second(
            pool,
            timestamps=timestamps,
            previous_close=previous_close,
            highs=highs,
            lows=lows,
        )
        if touch is None:
            never[pool.timeframe] += 1
        else:
            by_index[int(touch)].append(pool)

    selected: list[tuple[int, impact.Pool]] = []
    selected_counts: Counter[str] = Counter()
    counters: Counter[str] = Counter()
    for index, touched in sorted(by_index.items()):
        if len({pool.side for pool in touched}) > 1:
            counters["opposite_side_ambiguous_touch_bars"] += 1
            counters["opposite_side_pools_consumed"] += len(touched)
            continue
        highest = max(_PARENT_PRIORITY[pool.timeframe] for pool in touched)
        finalists = [
            pool
            for pool in touched
            if _PARENT_PRIORITY[pool.timeframe] == highest
        ]
        if len(touched) > 1:
            counters["same_side_parent_collision_bars"] += 1
            counters["same_side_extra_parent_pools_consumed"] += len(touched) - 1
        anchor = float(previous_close[index])
        chosen = min(finalists, key=lambda pool: abs(pool.level - anchor))
        selected.append((index, chosen))
        selected_counts[chosen.timeframe] += 1

    return selected, {
        "source_pool_counts": dict(sorted(source_counts.items())),
        "never_touched_counts": dict(sorted(never.items())),
        "raw_first_touch_bars": len(by_index),
        "selected_first_touch_events": len(selected),
        "selected_source_counts": dict(sorted(selected_counts.items())),
        **dict(sorted(counters.items())),
    }


def _parent_source_context(bundle: Any) -> tuple[list[impact.Pool], dict[str, Any]]:
    logic = impact.ImpactLogic()
    minute = impact._minute_features(
        bundle.minute_positioning.frame,
        atr_period=logic.minute_atr_period,
    )
    five = aggregate_flow(bundle.minute_positioning.frame, 5, logic.oi_period)
    five["timestamp_ns"] = five["timestamp_ns"].astype("int64")
    event_start_ns = int(bundle.seconds.iloc[0]["timestamp_ns"])

    one_all = impact._pool_confirmations(
        minute,
        timeframe="1M",
        radius=logic.one_minute_pivot_radius,
    )
    five_all = impact._pool_confirmations(
        five,
        timeframe="5M",
        radius=logic.five_minute_pivot_radius,
    )
    one_pools, one_pre = preconsume_before_event_window(
        one_all,
        minute,
        event_start_ns=event_start_ns,
    )
    five_pools, five_pre = preconsume_before_event_window(
        five_all,
        minute,
        event_start_ns=event_start_ns,
    )
    source_pools = [*one_pools, *five_pools]
    return source_pools, {
        "source_timeframes": ["1M", "5M"],
        "one_minute_total_confirmed": len(one_all),
        "five_minute_total_confirmed": len(five_all),
        "one_minute_available_at_event_window": len(one_pools),
        "five_minute_available_at_event_window": len(five_pools),
        "one_minute_preconsumption": one_pre,
        "five_minute_preconsumption": five_pre,
        "event_window_start_ns": event_start_ns,
    }


@contextmanager
def _parent_contact_patch(
    source_pools: Iterable[impact.Pool],
) -> Iterator[None]:
    original = local._pool_first_touches
    frozen = tuple(source_pools)

    def select_parent(
        bars: pd.DataFrame,
        _local_pools: Iterable[impact.Pool],
    ) -> tuple[list[tuple[int, impact.Pool]], dict[str, Any]]:
        return parent_source_first_touches(bars, frozen)

    local._pool_first_touches = select_parent
    try:
        yield
    finally:
        local._pool_first_touches = original


def _annotate_parent_report(
    selected: dict[str, Any],
    *,
    parent_context: Mapping[str, Any],
    execution_timeframe: str,
) -> None:
    counts: Counter[str] = Counter()
    for item in selected.get("scenarios", ()):
        pool_id = str(item["source_pool_id"])
        source_timeframe = "5M" if pool_id.startswith("5M") else "1M"
        item["sweep"]["source_timeframe"] = source_timeframe
        item["source_timeframe"] = source_timeframe
        item["execution_timeframe"] = execution_timeframe
        item["structural_family"] = "parent_external_multiclock_first_retest"
        counts[source_timeframe] += 1
    selected["summary"].update(
        {
            "family": "parent_external_liquidity_sweep_local_retest",
            "source_timeframes": ["1M", "5M"],
            "execution_timeframe": execution_timeframe,
            "entries_by_source_timeframe": dict(sorted(counts.items())),
            "parent_source_context": deepcopy(dict(parent_context)),
            "local_fifteen_second_sweeps_are_detector_only": True,
            "orders_or_pnl": False,
            "future_information": False,
        }
    )


def discover_parent_fifteen_second(
    *,
    config: Mapping[str, Any],
    bundle: Any,
    start: Any,
    end: Any,
    require_retest: bool,
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any], dict[str, Any]]:
    if not require_retest:
        raise ValueError("parent-external successor freezes first-retest entry")
    source_pools, parent_context = _parent_source_context(bundle)
    with _parent_contact_patch(source_pools):
        bars, upstream, selected, contract = _BASE_DISCOVER_FIFTEEN(
            config=config,
            bundle=bundle,
            start=start,
            end=end,
            require_retest=True,
        )
    _annotate_parent_report(
        selected,
        parent_context=parent_context,
        execution_timeframe="15S",
    )
    upstream["summary"] = selected["summary"]
    upstream["scenarios"] = selected["scenarios"]
    contract.update(
        {
            "family": "parent_external_multiclock_first_retest",
            "variant": "parent_1M_5M_source_15S_execution",
            "source_timeframes": ["1M", "5M"],
            "execution_timeframe": "15S",
            "detector_population": (
                "literal first touch of causal still-unconsumed one-minute or "
                "five-minute swing liquidity"
            ),
            "local_fifteen_second_sweeps_are_detector_only": True,
            "parent_source_context": parent_context,
            "selected_summary": selected["summary"],
            "orders_or_pnl_created_by_preprocessor": False,
            "future_information": False,
        }
    )
    return bars, upstream, selected, contract


def discover_parent_five_second(
    *,
    config: Mapping[str, Any],
    bundle: Any,
    start: Any,
    end: Any,
    require_retest: bool,
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any], dict[str, Any]]:
    if not require_retest:
        raise ValueError("parent-external successor freezes first-retest entry")
    source_pools, parent_context = _parent_source_context(bundle)
    with _parent_contact_patch(source_pools):
        bars, upstream, selected, contract = discover_five_second(
            config=config,
            bundle=bundle,
            start=start,
            end=end,
            require_retest=True,
        )
    _annotate_parent_report(
        selected,
        parent_context=parent_context,
        execution_timeframe="5S",
    )
    upstream["summary"] = selected["summary"]
    upstream["scenarios"] = selected["scenarios"]
    contract.update(
        {
            "family": "parent_external_multiclock_first_retest",
            "variant": "parent_1M_5M_source_5S_execution",
            "source_timeframes": ["1M", "5M"],
            "execution_timeframe": "5S",
            "detector_population": (
                "literal first touch of causal still-unconsumed one-minute or "
                "five-minute swing liquidity"
            ),
            "local_fifteen_second_sweeps_are_detector_only": True,
            "parent_source_context": parent_context,
            "selected_summary": selected["summary"],
            "orders_or_pnl_created_by_preprocessor": False,
            "future_information": False,
        }
    )
    return bars, upstream, selected, contract


def discover_parent_ensemble(
    *,
    config: Mapping[str, Any],
    bundle: Any,
    start: Any,
    end: Any,
    require_retest: bool,
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any], dict[str, Any]]:
    if not require_retest:
        raise ValueError("parent-external successor freezes first-retest entry")
    bars_5, upstream_5, selected_5, contract_5 = discover_parent_five_second(
        config=config,
        bundle=bundle,
        start=start,
        end=end,
        require_retest=True,
    )
    _, upstream_15, selected_15, contract_15 = discover_parent_fifteen_second(
        config=config,
        bundle=bundle,
        start=start,
        end=end,
        require_retest=True,
    )
    scenarios, arbitration = select_first_retests(selected_5, selected_15)
    for item in scenarios:
        item["structural_family"] = "parent_external_multiclock_first_retest"
    active_days = sorted(
        {
            pd.to_datetime(int(item["observed_time_ns"]), unit="ns", utc=True)
            .date()
            .isoformat()
            for item in scenarios
        }
    )
    source_counts: Counter[str] = Counter(
        str(item.get("source_timeframe")) for item in scenarios
    )
    summary = {
        "family": "parent_external_multiclock_first_retest",
        "require_retest": True,
        "source_timeframes": ["1M", "5M"],
        "execution_timeframes": ["5S", "15S"],
        "entry_ready": len(scenarios),
        "active_days": len(active_days),
        "active_day_labels": active_days,
        "entries_by_source_timeframe": dict(sorted(source_counts.items())),
        "five_second_summary": selected_5["summary"],
        "fifteen_second_summary": selected_15["summary"],
        "selection": arbitration,
        "local_fifteen_second_sweeps_are_detector_only": True,
        "orders_or_pnl": False,
        "future_information": False,
    }
    selected = {"summary": summary, "scenarios": scenarios}
    upstream = {
        "summary": summary,
        "scenarios": scenarios,
        "five_second_upstream": upstream_5,
        "fifteen_second_upstream": upstream_15,
    }
    contract = {
        "family": "parent_external_multiclock_first_retest",
        "source_timeframes": ["1M", "5M"],
        "execution_timeframes": ["5S", "15S"],
        "episode_arbitration": arbitration["selection_rule"],
        "five_second_contract": contract_5,
        "fifteen_second_contract": contract_15,
        "selected_summary": summary,
        "local_fifteen_second_sweeps_are_detector_only": True,
        "single_pending_or_open_slot": True,
        "orders_or_pnl_created_by_preprocessor": False,
        "future_information": False,
        "implementation_clean": bool(contract_5.get("implementation_clean"))
        and bool(contract_15.get("implementation_clean")),
    }
    return bars_5, upstream, selected, contract


def build_parent_ensemble_signals(
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
    scenarios = {
        str(item["scenario_id"]): item
        for item in report.get("scenarios", ())
    }
    output: list[CausalTradeSignal] = []
    for signal in base_signals:
        scenario = scenarios[signal.scenario_id]
        details = json.loads(signal.details_json)
        details.update(
            {
                "structural_family": "parent_external_multiclock_first_retest",
                "source_timeframe": scenario["source_timeframe"],
                "source_scope": "parent_external_liquidity",
                "execution_timeframe": scenario["execution_timeframe"],
                "episode_key": scenario["episode_key"],
                "episode_selection": scenario["episode_selection"],
            }
        )
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
                signal_kind="PARENT_EXTERNAL_MULTICLOCK_FIRST_RETEST",
                details_json=serialized,
                observed_time_ns=signal.observed_time_ns,
                ts_event=signal.ts_event,
                ts_init=signal.ts_init,
            )
        )
    output.sort(key=lambda signal: (signal.ts_event, signal.scenario_id))
    return output


__all__ = [
    "build_parent_ensemble_signals",
    "discover_parent_ensemble",
    "discover_parent_fifteen_second",
    "discover_parent_five_second",
    "parent_source_first_touches",
]
