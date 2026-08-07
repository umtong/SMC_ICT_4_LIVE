#!/usr/bin/env python3
"""Causal local-liquidity sweep -> MSS -> break-retest Nautilus tournament.

The population is every causally confirmed fifteen-second swing pool, not the
previous five-minute event sample. A pool is consumed at its literal first touch
after two completed right-side bars confirm the pivot. A reversal setup requires
attack-side aggressive quote flow through that pool, a finite penetration, and
a completed close back inside the swept level. The setup may trade only after a
completed displacement close breaks the latest opposing local swing (MSS).

Baseline waits for the first rejection retest of the broken swing. The single
ablation removes only this retest and enters at the MSS close. The stop remains
beyond the complete sweep extreme. Targets are the nearest favorable, already
confirmed and still-unconsumed 15-second, one-minute, then five-minute liquidity
pools with at least 1.25 structural R. Signal discovery creates no orders, fills,
PnL, cash or NAV. Both variants use the same NautilusTrader BacktestEngine with
current full-NAV 3% loss budgeting, fees, adverse ticks, funding and MIT exits.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

import backtest as base
import backtest_pre_attack_value as replay
import diagnose_impact_resilience_1s as impact
from diagnose_aggtrade_resilience_v2 import preconsume_before_event_window
from diagnose_failed_flow import aggregate_flow
from event_signal_data import CausalTradeSignal
from run_aggtrade_resilience_second_safe import (
    first_touch_after_complete_confirmation_second,
)
from run_global_flow_absorption import (
    GlobalAbsorptionLogic,
    _aggregate_fifteen_seconds,
)
from strategy_event_signal_cost_viable import Candidate07CostViableMITStrategy

from nautilus_trader.model.identifiers import InstrumentId
from smc_ict_4.manifest import write_json_atomic


NS_PER_SECOND = 1_000_000_000
NS_PER_FIFTEEN_SECONDS = 15 * NS_PER_SECOND


@dataclass(frozen=True, slots=True)
class LocalSweepMSSLogic:
    source_pivot_radius: int = 2
    atr_history_bars: int = 240
    reference_history_bars: int = 960
    attack_signed_flow_quantile: float = 0.75
    attack_quote_volume_quantile: float = 0.65
    attack_imbalance_quantile: float = 0.65
    minimum_attack_imbalance: float = 0.08
    minimum_penetration_atr: float = 0.03
    maximum_penetration_atr: float = 1.25
    minimum_wick_fraction: float = 0.25
    maximum_event_efficiency: float = 0.55
    mss_context_bars: int = 32
    maximum_mss_bars: int = 8
    displacement_body_quantile: float = 0.70
    minimum_body_atr: float = 0.15
    displacement_close_location: float = 0.65
    displacement_minimum_imbalance: float = 0.03
    maximum_retest_bars: int = 6
    retest_close_location: float = 0.60
    stop_buffer_atr: float = 0.05
    minimum_rr: float = 1.25

    def validate(self) -> None:
        for name in (
            "source_pivot_radius",
            "atr_history_bars",
            "reference_history_bars",
            "mss_context_bars",
            "maximum_mss_bars",
            "maximum_retest_bars",
        ):
            if int(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be positive")
        for name in (
            "attack_signed_flow_quantile",
            "attack_quote_volume_quantile",
            "attack_imbalance_quantile",
            "displacement_body_quantile",
            "minimum_wick_fraction",
            "maximum_event_efficiency",
            "displacement_close_location",
            "retest_close_location",
        ):
            value = float(getattr(self, name))
            if not 0.0 < value < 1.0:
                raise ValueError(f"{name} must be in (0, 1)")
        if not 0.0 < self.minimum_attack_imbalance < 1.0:
            raise ValueError("minimum_attack_imbalance must be in (0, 1)")
        if not 0.0 <= self.displacement_minimum_imbalance < 1.0:
            raise ValueError("displacement_minimum_imbalance must be in [0, 1)")
        if not 0.0 < self.minimum_penetration_atr < self.maximum_penetration_atr:
            raise ValueError("penetration bounds are inconsistent")
        if self.minimum_body_atr <= 0.0 or self.minimum_rr <= 0.0:
            raise ValueError("body/RR parameters must be positive")
        if self.stop_buffer_atr < 0.0:
            raise ValueError("stop_buffer_atr must be non-negative")


def _aggregation_logic(logic: LocalSweepMSSLogic) -> GlobalAbsorptionLogic:
    """Use the shared complete-clock aggregation with this candidate's history."""
    return GlobalAbsorptionLogic(
        atr_history_bars=logic.atr_history_bars,
        reference_history_bars=logic.reference_history_bars,
        signed_flow_quantile=logic.attack_signed_flow_quantile,
        quote_volume_quantile=logic.attack_quote_volume_quantile,
        imbalance_quantile=logic.attack_imbalance_quantile,
        minimum_imbalance=logic.minimum_attack_imbalance,
        minimum_range_atr=0.01,
        maximum_range_atr=20.0,
        minimum_excursion_atr=0.01,
        maximum_price_efficiency=0.99,
        confirmation_bars=1,
        confirmation_close_location=0.51,
        confirmation_minimum_imbalance=0.0,
        retest_bars=1,
        retest_close_location=0.51,
        stop_buffer_atr=logic.stop_buffer_atr,
        minimum_rr=logic.minimum_rr,
    )


def _prepare_local_bars(
    seconds: pd.DataFrame,
    logic: LocalSweepMSSLogic,
) -> pd.DataFrame:
    logic.validate()
    bars = _aggregate_fifteen_seconds(seconds, _aggregation_logic(logic))
    bars["body"] = bars["close"] - bars["open"]
    bars["body_atr"] = bars["body"].abs() / bars["atr"].replace(0.0, np.nan)
    bars["body_reference"] = bars["body_atr"].shift(1).rolling(
        logic.reference_history_bars,
        min_periods=logic.reference_history_bars,
    ).quantile(logic.displacement_body_quantile)
    bars["close_location"] = (
        (bars["close"] - bars["low"])
        / bars["range"].replace(0.0, np.nan)
    ).fillna(0.5)
    return bars


def _pool_first_touches(
    bars: pd.DataFrame,
    pools: Iterable[impact.Pool],
) -> tuple[list[tuple[int, impact.Pool]], dict[str, int]]:
    """Return one unambiguous local pool per literal first-touch bar."""
    pool_list = list(pools)
    timestamps = bars["timestamp_ns"].astype("int64").to_numpy()
    highs = bars["high"].astype(float).to_numpy()
    lows = bars["low"].astype(float).to_numpy()
    closes = bars["close"].astype(float).to_numpy()
    previous_close = np.empty_like(closes)
    previous_close[0] = closes[0]
    previous_close[1:] = closes[:-1]
    by_index: dict[int, list[impact.Pool]] = defaultdict(list)
    never_touched = 0
    for pool in pool_list:
        touch = first_touch_after_complete_confirmation_second(
            pool,
            timestamps=timestamps,
            previous_close=previous_close,
            highs=highs,
            lows=lows,
        )
        if touch is None:
            never_touched += 1
        else:
            by_index[int(touch)].append(pool)

    selected: list[tuple[int, impact.Pool]] = []
    counters: Counter[str] = Counter()
    for index, touched in sorted(by_index.items()):
        sides = {item.side for item in touched}
        if len(sides) > 1:
            counters["opposite_side_ambiguous_touch_bars"] += 1
            counters["opposite_side_pools_consumed"] += len(touched)
            continue
        if len(touched) > 1:
            counters["same_side_collision_bars"] += 1
            counters["same_side_extra_pools_consumed"] += len(touched) - 1
        anchor = float(previous_close[index])
        selected.append(
            (index, min(touched, key=lambda item: abs(item.level - anchor)))
        )
    return selected, {
        "source_pools": len(pool_list),
        "source_pools_never_touched": never_touched,
        "raw_first_touch_bars": len(by_index),
        "selected_first_touch_events": len(selected),
        **dict(sorted(counters.items())),
    }


def _sweep_direction(
    row: pd.Series,
    pool: impact.Pool,
    logic: LocalSweepMSSLogic,
) -> str | None:
    atr = float(row["atr"])
    if not np.isfinite(atr) or atr <= 0.0:
        return None
    for name in (
        "signed_flow_reference",
        "quote_volume_reference",
        "imbalance_reference",
    ):
        if not np.isfinite(float(row[name])):
            return None
    range_ = max(float(row["range"]), 1e-12)
    signed = float(row["signed_quote"])
    imbalance = float(row["imbalance"])
    flow_ok = (
        abs(signed) >= float(row["signed_flow_reference"])
        and float(row["quote_volume"]) >= float(row["quote_volume_reference"])
        and abs(imbalance)
        >= max(
            logic.minimum_attack_imbalance,
            float(row["imbalance_reference"]),
        )
    )
    if not flow_ok or float(row["price_efficiency"]) > logic.maximum_event_efficiency:
        return None

    if pool.side == "UPPER":
        penetration = (float(row["high"]) - pool.level) / atr
        wick = (float(row["high"]) - max(float(row["open"]), float(row["close"]))) / range_
        if (
            logic.minimum_penetration_atr <= penetration <= logic.maximum_penetration_atr
            and float(row["close"]) < pool.level
            and wick >= logic.minimum_wick_fraction
            and signed > 0.0
            and imbalance > 0.0
        ):
            return "SHORT"
    else:
        penetration = (pool.level - float(row["low"])) / atr
        wick = (min(float(row["open"]), float(row["close"])) - float(row["low"])) / range_
        if (
            logic.minimum_penetration_atr <= penetration <= logic.maximum_penetration_atr
            and float(row["close"]) > pool.level
            and wick >= logic.minimum_wick_fraction
            and signed < 0.0
            and imbalance < 0.0
        ):
            return "LONG"
    return None


def _latest_opposing_swing(
    pools: Iterable[impact.Pool],
    *,
    direction: str,
    contact_index: int,
    bars: pd.DataFrame,
    logic: LocalSweepMSSLogic,
) -> impact.Pool | None:
    contact_ns = int(bars.iloc[contact_index]["timestamp_ns"])
    contact_close = float(bars.iloc[contact_index]["close"])
    earliest_ns = contact_ns - logic.mss_context_bars * NS_PER_FIFTEEN_SECONDS
    side = "UPPER" if direction == "LONG" else "LOWER"
    eligible = [
        item
        for item in pools
        if item.side == side
        and earliest_ns <= int(item.confirmed_ts_ns) < contact_ns
        and (
            item.level > contact_close
            if direction == "LONG"
            else item.level < contact_close
        )
    ]
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda item: (
            int(item.confirmed_ts_ns),
            -abs(item.level - contact_close),
        ),
    )


def _mss_index(
    bars: pd.DataFrame,
    *,
    contact_index: int,
    direction: str,
    boundary: impact.Pool,
    event_extreme: float,
    event_atr: float,
    logic: LocalSweepMSSLogic,
) -> tuple[int | None, str]:
    end = min(len(bars.index), contact_index + 1 + logic.maximum_mss_bars)
    for index in range(contact_index + 1, end):
        row = bars.iloc[index]
        if direction == "LONG":
            if float(row["low"]) <= event_extreme - logic.stop_buffer_atr * event_atr:
                return None, "SOURCE_INVALIDATED_BEFORE_MSS"
            crossed = float(row["close"]) > boundary.level
            directional_body = float(row["body"]) > 0.0
            location_ok = float(row["close_location"]) >= logic.displacement_close_location
            flow_ok = float(row["imbalance"]) >= logic.displacement_minimum_imbalance
        else:
            if float(row["high"]) >= event_extreme + logic.stop_buffer_atr * event_atr:
                return None, "SOURCE_INVALIDATED_BEFORE_MSS"
            crossed = float(row["close"]) < boundary.level
            directional_body = float(row["body"]) < 0.0
            location_ok = float(row["close_location"]) <= 1.0 - logic.displacement_close_location
            flow_ok = float(row["imbalance"]) <= -logic.displacement_minimum_imbalance
        body_reference = float(row["body_reference"])
        body_ok = (
            np.isfinite(body_reference)
            and float(row["body_atr"])
            >= max(logic.minimum_body_atr, body_reference)
        )
        if crossed and directional_body and location_ok and flow_ok and body_ok:
            return index, "MSS_CONFIRMED"
    return None, "MSS_NOT_CONFIRMED_WITHIN_WINDOW"


def _break_retest_index(
    bars: pd.DataFrame,
    *,
    mss_index: int,
    direction: str,
    boundary_level: float,
    event_extreme: float,
    event_atr: float,
    logic: LocalSweepMSSLogic,
) -> tuple[int | None, str]:
    end = min(len(bars.index), mss_index + 1 + logic.maximum_retest_bars)
    for index in range(mss_index + 1, end):
        row = bars.iloc[index]
        range_ = max(float(row["range"]), 1e-12)
        if direction == "LONG":
            if float(row["low"]) <= event_extreme - logic.stop_buffer_atr * event_atr:
                return None, "SOURCE_INVALIDATED_DURING_RETEST"
            touched = float(row["low"]) <= boundary_level
            rejected = (
                touched
                and float(row["close"]) > boundary_level
                and float(row["close"]) > float(row["open"])
                and (float(row["close"]) - float(row["low"])) / range_
                >= logic.retest_close_location
                and float(row["signed_quote"]) > 0.0
            )
        else:
            if float(row["high"]) >= event_extreme + logic.stop_buffer_atr * event_atr:
                return None, "SOURCE_INVALIDATED_DURING_RETEST"
            touched = float(row["high"]) >= boundary_level
            rejected = (
                touched
                and float(row["close"]) < boundary_level
                and float(row["close"]) < float(row["open"])
                and (float(row["high"]) - float(row["close"])) / range_
                >= logic.retest_close_location
                and float(row["signed_quote"]) < 0.0
            )
        if rejected:
            return index, "BREAK_RETEST_CONFIRMED"
    return None, "BREAK_RETEST_NOT_CONFIRMED"


def _first_touch_on_seconds(
    pool: impact.Pool,
    *,
    timestamps: np.ndarray,
    previous_close: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    cache: dict[str, int | None],
) -> int | None:
    if pool.pool_id not in cache:
        cache[pool.pool_id] = first_touch_after_complete_confirmation_second(
            pool,
            timestamps=timestamps,
            previous_close=previous_close,
            highs=highs,
            lows=lows,
        )
    return cache[pool.pool_id]


def _target_pool(
    pools_by_timeframe: Mapping[str, Iterable[impact.Pool]],
    *,
    direction: str,
    entry: float,
    stop: float,
    entry_index: int,
    timestamps: np.ndarray,
    previous_close: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    touch_cache: dict[str, int | None],
    minimum_rr: float,
) -> tuple[impact.Pool, float] | None:
    risk = entry - stop if direction == "LONG" else stop - entry
    if risk <= 0.0:
        return None
    side = "UPPER" if direction == "LONG" else "LOWER"
    entry_second = int(timestamps[entry_index]) // NS_PER_SECOND
    for timeframe in ("15S", "1M", "5M"):
        candidates = [
            item
            for item in pools_by_timeframe.get(timeframe, ())
            if item.side == side
            and int(item.confirmed_ts_ns) // NS_PER_SECOND < entry_second
            and (
                item.level > entry
                if direction == "LONG"
                else item.level < entry
            )
        ]
        candidates.sort(key=lambda item: abs(item.level - entry))
        for pool in candidates:
            rr = abs(pool.level - entry) / risk
            if rr < minimum_rr:
                continue
            first_touch = _first_touch_on_seconds(
                pool,
                timestamps=timestamps,
                previous_close=previous_close,
                highs=highs,
                lows=lows,
                cache=touch_cache,
            )
            if first_touch is None or first_touch > entry_index:
                return pool, rr
    return None


def _entry_second_index(timestamps: np.ndarray, observed_ns: int) -> int | None:
    index = int(np.searchsorted(timestamps, int(observed_ns), side="left"))
    return None if index >= len(timestamps) else index


def diagnose(
    seconds: pd.DataFrame,
    *,
    one_pools: Iterable[impact.Pool],
    five_pools: Iterable[impact.Pool],
    trade_start_ns: int,
    trade_end_ns: int,
    logic: LocalSweepMSSLogic,
    require_retest: bool,
) -> dict[str, Any]:
    """Build sweep/MSS scenarios without orders, PnL, cash or NAV."""
    bars = _prepare_local_bars(seconds, logic)
    local_pools = impact._pool_confirmations(
        bars,
        timeframe="15S",
        radius=logic.source_pivot_radius,
    )
    contact_events, contact_summary = _pool_first_touches(bars, local_pools)

    second_work = seconds.copy().sort_values("timestamp_ns", kind="stable").reset_index(drop=True)
    timestamps = second_work["timestamp_ns"].astype("int64").to_numpy()
    highs = second_work["high"].astype(float).to_numpy()
    lows = second_work["low"].astype(float).to_numpy()
    closes = second_work["close"].astype(float).to_numpy()
    previous_close = np.empty_like(closes)
    previous_close[0] = closes[0]
    previous_close[1:] = closes[:-1]
    target_touch_cache: dict[str, int | None] = {}
    target_pools = {
        "15S": local_pools,
        "1M": list(one_pools),
        "5M": list(five_pools),
    }

    counters: Counter[str] = Counter()
    scenarios: list[dict[str, Any]] = []
    for contact_index, source_pool in contact_events:
        contact_ns = int(bars.iloc[contact_index]["timestamp_ns"])
        if not trade_start_ns <= contact_ns < trade_end_ns:
            continue
        row = bars.iloc[contact_index]
        direction = _sweep_direction(row, source_pool, logic)
        if direction is None:
            counters["FIRST_TOUCH_NOT_QUALIFIED_SWEEP"] += 1
            continue
        counters["QUALIFIED_SWEEP"] += 1
        boundary = _latest_opposing_swing(
            local_pools,
            direction=direction,
            contact_index=contact_index,
            bars=bars,
            logic=logic,
        )
        if boundary is None:
            counters["NO_CAUSAL_OPPOSING_SWING_FOR_MSS"] += 1
            continue
        event_atr = float(row["atr"])
        event_extreme = (
            float(row["low"])
            if direction == "LONG"
            else float(row["high"])
        )
        mss_index, mss_reason = _mss_index(
            bars,
            contact_index=contact_index,
            direction=direction,
            boundary=boundary,
            event_extreme=event_extreme,
            event_atr=event_atr,
            logic=logic,
        )
        if mss_index is None:
            counters[mss_reason] += 1
            continue
        counters["MSS_CONFIRMED"] += 1

        observed_index = mss_index
        retest_index: int | None = None
        if require_retest:
            retest_index, retest_reason = _break_retest_index(
                bars,
                mss_index=mss_index,
                direction=direction,
                boundary_level=boundary.level,
                event_extreme=event_extreme,
                event_atr=event_atr,
                logic=logic,
            )
            if retest_index is None:
                counters[retest_reason] += 1
                continue
            counters["BREAK_RETEST_CONFIRMED"] += 1
            observed_index = retest_index

        observed = bars.iloc[observed_index]
        observed_ns = int(observed["timestamp_ns"])
        entry_index = _entry_second_index(timestamps, observed_ns)
        if entry_index is None:
            counters["NO_EXECUTION_SECOND"] += 1
            continue
        entry = float(observed["close"])
        stop = (
            event_extreme - logic.stop_buffer_atr * event_atr
            if direction == "LONG"
            else event_extreme + logic.stop_buffer_atr * event_atr
        )
        risk = entry - stop if direction == "LONG" else stop - entry
        if risk <= 0.0:
            counters["NONPOSITIVE_RISK"] += 1
            continue
        target = _target_pool(
            target_pools,
            direction=direction,
            entry=entry,
            stop=stop,
            entry_index=entry_index,
            timestamps=timestamps,
            previous_close=previous_close,
            highs=highs,
            lows=lows,
            touch_cache=target_touch_cache,
            minimum_rr=logic.minimum_rr,
        )
        if target is None:
            counters["NO_CAUSAL_TARGET_AT_MINIMUM_RR"] += 1
            continue
        target_pool, expected_rr = target
        counters["ENTRY_READY"] += 1
        scenario_id = f"c07-local-sweep-{contact_ns}-{direction}"
        scenarios.append(
            {
                "scenario_id": scenario_id,
                "outcome": "ENTRY_READY",
                "direction": direction,
                "entry": entry,
                "stop": stop,
                "target": float(target_pool.level),
                "expected_rr": float(expected_rr),
                "source_pool_id": source_pool.pool_id,
                "observed_time_ns": observed_ns,
                "sweep": {
                    "timestamp_ns": contact_ns,
                    "pool_id": source_pool.pool_id,
                    "pool_side": source_pool.side,
                    "pool_level": float(source_pool.level),
                    "pool_pivot_ts_ns": int(source_pool.pivot_ts_ns),
                    "pool_confirmed_ts_ns": int(source_pool.confirmed_ts_ns),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "atr": event_atr,
                    "event_extreme": event_extreme,
                    "signed_quote": float(row["signed_quote"]),
                    "imbalance": float(row["imbalance"]),
                    "quote_volume": float(row["quote_volume"]),
                },
                "mss": {
                    "timestamp_ns": int(bars.iloc[mss_index]["timestamp_ns"]),
                    "boundary_pool_id": boundary.pool_id,
                    "boundary_level": float(boundary.level),
                    "boundary_confirmed_ts_ns": int(boundary.confirmed_ts_ns),
                    "close": float(bars.iloc[mss_index]["close"]),
                    "body_atr": float(bars.iloc[mss_index]["body_atr"]),
                    "imbalance": float(bars.iloc[mss_index]["imbalance"]),
                },
                "retest": (
                    None
                    if retest_index is None
                    else {
                        "timestamp_ns": int(bars.iloc[retest_index]["timestamp_ns"]),
                        "boundary_level": float(boundary.level),
                        "close": float(bars.iloc[retest_index]["close"]),
                        "imbalance": float(bars.iloc[retest_index]["imbalance"]),
                    }
                ),
                "target_pool": {
                    "pool_id": target_pool.pool_id,
                    "timeframe": target_pool.timeframe,
                    "level": float(target_pool.level),
                    "confirmed_ts_ns": int(target_pool.confirmed_ts_ns),
                },
            }
        )

    active_days = sorted(
        {
            pd.to_datetime(int(item["observed_time_ns"]), unit="ns", utc=True)
            .date()
            .isoformat()
            for item in scenarios
        }
    )
    return {
        "summary": {
            "require_retest": bool(require_retest),
            "local_pools": len(local_pools),
            "contact_summary": contact_summary,
            "diagnostic_counts": dict(sorted(counters.items())),
            "entry_ready": len(scenarios),
            "active_days": len(active_days),
            "active_day_labels": active_days,
            "orders_or_pnl": False,
            "future_information": False,
        },
        "scenarios": scenarios,
    }


def build_causal_signals(
    *,
    report: Mapping[str, Any],
    upstream_report: Mapping[str, Any],
    instrument_id: InstrumentId,
) -> list[CausalTradeSignal]:
    del upstream_report
    output: list[CausalTradeSignal] = []
    for item in report.get("scenarios", ()):
        if item.get("outcome") != "ENTRY_READY":
            continue
        observed_ns = int(item["observed_time_ns"])
        details = {
            "structural_family": "local_15s_liquidity_sweep_mss_retest",
            "sweep": item["sweep"],
            "mss": item["mss"],
            "retest": item.get("retest"),
            "target_pool": item["target_pool"],
            "require_retest": bool(report["summary"]["require_retest"]),
        }
        serialized = json.dumps(details, sort_keys=True).lower()
        for forbidden in ("path", "mfe", "mae", "terminal", "realized_r"):
            if forbidden in serialized:
                raise RuntimeError(f"future-path field leaked into signal: {forbidden}")
        output.append(
            CausalTradeSignal(
                instrument_id=instrument_id,
                scenario_id=str(item["scenario_id"]),
                direction=str(item["direction"]),
                entry_reference=float(item["entry"]),
                stop_price=float(item["stop"]),
                target_price=float(item["target"]),
                expected_rr=float(item["expected_rr"]),
                source_pool_id=str(item["source_pool_id"]),
                signal_kind=(
                    "LOCAL_15S_SWEEP_MSS_BREAK_RETEST"
                    if report["summary"]["require_retest"]
                    else "LOCAL_15S_SWEEP_MSS_CLOSE"
                ),
                details_json=json.dumps(details, sort_keys=True),
                observed_time_ns=observed_ns,
                ts_event=observed_ns + 1,
                ts_init=observed_ns + 1,
            )
        )
    output.sort(key=lambda item: (item.ts_event, item.scenario_id))
    if len({item.scenario_id for item in output}) != len(output):
        raise RuntimeError("duplicate local sweep scenario identifiers")
    return output


def discover_structural_signals(
    *,
    config: Mapping[str, Any],
    bundle: Any,
    start: date,
    end: date,
    require_retest: bool,
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any], dict[str, Any]]:
    del config
    logic = LocalSweepMSSLogic()
    logic.validate()
    minute = impact._minute_features(
        bundle.minute_positioning.frame,
        atr_period=impact.ImpactLogic().minute_atr_period,
    )
    five = aggregate_flow(bundle.minute_positioning.frame, 5, 36)
    five["timestamp_ns"] = five["timestamp_ns"].astype("int64")
    event_start_ns = int(bundle.seconds.iloc[0]["timestamp_ns"])
    one_all = impact._pool_confirmations(
        minute,
        timeframe="1M",
        radius=impact.ImpactLogic().one_minute_pivot_radius,
    )
    five_all = impact._pool_confirmations(
        five,
        timeframe="5M",
        radius=impact.ImpactLogic().five_minute_pivot_radius,
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
    selected = diagnose(
        bundle.seconds,
        one_pools=one_pools,
        five_pools=five_pools,
        trade_start_ns=base._utc_ns(start),
        trade_end_ns=base._utc_ns(end),
        logic=logic,
        require_retest=require_retest,
    )
    upstream = {
        "summary": selected["summary"],
        "scenarios": selected["scenarios"],
    }
    contract = {
        "family": "local_15s_liquidity_sweep_mss_retest",
        "variant": "break_retest" if require_retest else "mss_close",
        "logic": asdict(logic),
        "detector_population": (
            "literal first touch of every 15-second pivot confirmed by two "
            "completed right-side bars"
        ),
        "source_pool_reuse": False,
        "target_hierarchy": "15S then 1M then 5M unconsumed causal liquidity",
        "one_minute_preconsumption": one_pre,
        "five_minute_preconsumption": five_pre,
        "selected_summary": selected["summary"],
        "loader_diagnostics": dict(bundle.diagnostics),
        "implementation_clean": (
            int(bundle.diagnostics.get("out_of_order_rows", -1)) == 0
            and int(bundle.diagnostics.get("duplicate_agg_trade_ids", -1)) == 0
            and int(bundle.diagnostics.get("noncontiguous_second_transitions", -1)) == 0
            and int(bundle.diagnostics.get("missing_seconds_from_span", -1)) == 0
        ),
        "orders_or_pnl_created_by_preprocessor": False,
        "future_information": False,
    }
    return bundle.seconds, upstream, selected, contract


class _EmptySignalSafeBacktestEngine:
    """Factory assigned at runtime; delegates every operation to Nautilus."""

    delegate_type: Any = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if self.delegate_type is None:
            raise RuntimeError("delegate_type is not configured")
        self._delegate = self.delegate_type(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def add_data(self, data: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            if len(data) == 0:
                return None
        except TypeError:
            pass
        return self._delegate.add_data(data, *args, **kwargs)


def _run_variant(
    *,
    args: argparse.Namespace,
    config_path: Path,
    variant: str,
    require_retest: bool,
) -> dict[str, Any]:
    destination = args.output.resolve() / variant
    original_discover = replay.discover_structural_signals
    original_builder = replay.build_causal_signals
    original_strategy = replay.Candidate07EventSignalStrategy
    original_engine = replay.BacktestEngine
    _EmptySignalSafeBacktestEngine.delegate_type = original_engine
    replay.discover_structural_signals = (
        lambda *, config, bundle, start, end: discover_structural_signals(
            config=config,
            bundle=bundle,
            start=start,
            end=end,
            require_retest=require_retest,
        )
    )
    replay.build_causal_signals = build_causal_signals
    replay.Candidate07EventSignalStrategy = Candidate07CostViableMITStrategy
    replay.BacktestEngine = _EmptySignalSafeBacktestEngine
    try:
        metrics = replay.run_week(
            config_path=config_path,
            stage=f"week-1-{variant}",
            start=args.start,
            end=args.end,
            output=destination,
            cache_root=args.data_root.resolve(),
            event_warmup_days=args.event_warmup_days,
        )
    finally:
        replay.discover_structural_signals = original_discover
        replay.build_causal_signals = original_builder
        replay.Candidate07EventSignalStrategy = original_strategy
        replay.BacktestEngine = original_engine
        _EmptySignalSafeBacktestEngine.delegate_type = None
    metrics["execution_contract"].update(
        {
            "selected_route": (
                "15S first-touch sweep -> reclaim -> local MSS -> "
                + ("first broken-level retest" if require_retest else "MSS close")
                + " -> nearest causal unconsumed liquidity"
            ),
            "take_profit_order_type": "MARKET_IF_TOUCHED",
            "target_cost_viability_required": True,
        }
    )
    write_json_atomic(destination / "metrics.json", base._json_safe(metrics))
    return metrics


def _compact(metrics: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "daily_geometric_growth": metrics.get("daily_geometric_growth"),
        "net_return": metrics.get("net_return"),
        "trades": metrics.get("trades"),
        "wins": metrics.get("wins"),
        "losses": metrics.get("losses"),
        "win_rate": metrics.get("win_rate"),
        "profit_factor": metrics.get("profit_factor"),
        "max_drawdown": metrics.get("max_drawdown"),
        "active_days": metrics.get("active_days"),
        "single_winner_share": metrics.get("single_winner_share"),
        "weekly_gate": metrics.get("weekly_gate"),
        "structural_summary": metrics.get("structural_contract", {}).get(
            "selected_summary"
        ),
        "logic": metrics.get("structural_contract", {}).get("logic"),
        "implementation_clean": metrics.get("structural_contract", {}).get(
            "implementation_clean"
        ),
        "signal_contract": metrics.get("signal_contract"),
    }


def run(args: argparse.Namespace) -> int:
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    config["max_hold_minutes"] = 30
    config_path = output / "frozen_config.json"
    write_json_atomic(config_path, config)

    baseline = _run_variant(
        args=args,
        config_path=config_path,
        variant="baseline_break_retest",
        require_retest=True,
    )
    ablation = _run_variant(
        args=args,
        config_path=config_path,
        variant="ablation_mss_close",
        require_retest=False,
    )
    variants = {
        "baseline_break_retest": _compact(baseline),
        "ablation_mss_close": _compact(ablation),
    }
    passed = [
        name
        for name, value in variants.items()
        if bool((value.get("weekly_gate") or {}).get("passed"))
    ]
    if passed:
        selected = max(
            passed,
            key=lambda name: (
                float(variants[name]["daily_geometric_growth"]),
                float(variants[name]["profit_factor"] or 0.0),
                int(variants[name]["trades"]),
            ),
        )
        interpretation = "WEEK_1_GATE_PASSED"
    else:
        selected = None
        interpretation = "BASELINE_AND_SINGLE_ABLATION_FAILED"
    summary = {
        "candidate": "candidate-07",
        "family": "local_15s_liquidity_sweep_mss_retest",
        "stage": "week-1",
        "period": {
            "start": args.start.isoformat(),
            "end_exclusive": args.end.isoformat(),
        },
        "source_commit_expected": args.source_commit,
        "engine": "NautilusTrader BacktestEngine",
        "risk_fraction": config["risk_fraction"],
        "maximum_hold_minutes": config["max_hold_minutes"],
        "variants": variants,
        "selected_variant": selected,
        "eligible_for_frozen_week_2": selected is not None,
        "interpretation": interpretation,
    }
    write_json_atomic(output / "summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    candidate_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=candidate_dir / "config.json")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path(".research-data/candidate-07"),
    )
    parser.add_argument("--event-warmup-days", type=int, default=1)
    parser.add_argument("--source-commit", default=None)
    return parser


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
