#!/usr/bin/env python3
"""Causal global 15-second flow-absorption tournament on frozen BTC Week-1.

This candidate starts from a fresh event population rather than reusing the
discarded five-minute contact episodes. Every complete fifteen-second auction is
eligible. An event requires extreme one-sided aggressive quote flow, meaningful
range expansion and weak directional price response. The trade direction is the
opposite of the failed aggression, but no order is submitted until completed
opposite-flow recovery confirms the failed auction.

Baseline waits for the first causal retest and rejection of the event VWAP.
The single ablation removes only that retest and enters on the recovery close.
Targets are already-confirmed, unconsumed one-minute then five-minute liquidity
pools; stops remain beyond the complete failed-auction extreme. Signal discovery
creates no orders, fills, PnL, cash or NAV. Both variants are executed by the
same NautilusTrader BacktestEngine path with current-NAV 3% loss budgeting,
fees, adverse ticks, funding and a market-if-touched take-profit.
"""
from __future__ import annotations

import argparse
from collections import Counter
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
    target_pool_after_complete_confirmation_second,
)
from strategy_event_signal_cost_viable import Candidate07CostViableMITStrategy

from nautilus_trader.model.identifiers import InstrumentId
from smc_ict_4.manifest import write_json_atomic


NS_PER_SECOND = 1_000_000_000
NS_PER_FIFTEEN_SECONDS = 15 * NS_PER_SECOND


@dataclass(frozen=True, slots=True)
class GlobalAbsorptionLogic:
    atr_history_bars: int = 240
    reference_history_bars: int = 960
    signed_flow_quantile: float = 0.95
    quote_volume_quantile: float = 0.80
    imbalance_quantile: float = 0.90
    minimum_imbalance: float = 0.12
    minimum_range_atr: float = 0.35
    maximum_range_atr: float = 4.0
    minimum_excursion_atr: float = 0.35
    maximum_price_efficiency: float = 0.45
    confirmation_bars: int = 2
    confirmation_close_location: float = 0.65
    confirmation_minimum_imbalance: float = 0.05
    retest_bars: int = 4
    retest_close_location: float = 0.60
    stop_buffer_atr: float = 0.05
    minimum_rr: float = 1.25

    def validate(self) -> None:
        for name in (
            "atr_history_bars",
            "reference_history_bars",
            "confirmation_bars",
            "retest_bars",
        ):
            if int(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be positive")
        for name in (
            "signed_flow_quantile",
            "quote_volume_quantile",
            "imbalance_quantile",
            "confirmation_close_location",
            "retest_close_location",
        ):
            value = float(getattr(self, name))
            if not 0.0 < value < 1.0:
                raise ValueError(f"{name} must be in (0, 1)")
        if not 0.0 < self.minimum_imbalance < 1.0:
            raise ValueError("minimum_imbalance must be in (0, 1)")
        if not 0.0 <= self.maximum_price_efficiency <= 1.0:
            raise ValueError("maximum_price_efficiency must be in [0, 1]")
        if not 0.0 <= self.confirmation_minimum_imbalance < 1.0:
            raise ValueError("confirmation_minimum_imbalance must be in [0, 1)")
        if not 0.0 < self.minimum_range_atr < self.maximum_range_atr:
            raise ValueError("range bounds are inconsistent")
        if self.minimum_excursion_atr <= 0.0:
            raise ValueError("minimum_excursion_atr must be positive")
        if self.stop_buffer_atr < 0.0 or self.minimum_rr <= 0.0:
            raise ValueError("stop/RR parameters are inconsistent")


def _aggregate_fifteen_seconds(seconds: pd.DataFrame, logic: GlobalAbsorptionLogic) -> pd.DataFrame:
    """Aggregate causal one-second observations into complete 15-second auctions."""
    logic.validate()
    required = {
        "timestamp_ns",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "taker_buy_quote",
        "taker_sell_quote",
    }
    missing = required.difference(seconds.columns)
    if missing:
        raise ValueError(f"second columns missing: {sorted(missing)}")
    work = seconds.copy().sort_values("timestamp_ns", kind="stable").reset_index(drop=True)
    work["timestamp_ns"] = work["timestamp_ns"].astype("int64")
    work["bucket_15s"] = work["timestamp_ns"] // NS_PER_FIFTEEN_SECONDS
    grouped = work.groupby("bucket_15s", sort=True)
    bars = grouped.agg(
        timestamp_ns=("timestamp_ns", "last"),
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        quote_volume=("quote_volume", "sum"),
        taker_buy_quote=("taker_buy_quote", "sum"),
        taker_sell_quote=("taker_sell_quote", "sum"),
        second_count=("timestamp_ns", "count"),
    ).reset_index(drop=True)
    # Warm-up/trade archives are exact full days, so incomplete edge buckets are
    # invalid rather than silently treated as auctions.
    bars = bars[bars["second_count"] == 15].copy().reset_index(drop=True)
    bars["signed_quote"] = bars["taker_buy_quote"] - bars["taker_sell_quote"]
    bars["imbalance"] = (
        bars["signed_quote"] / bars["quote_volume"].replace(0.0, np.nan)
    ).fillna(0.0)
    bars["vwap"] = (
        bars["quote_volume"] / bars["volume"].replace(0.0, np.nan)
    ).fillna(bars["close"])
    bars["range"] = bars["high"] - bars["low"]
    bars["price_efficiency"] = (
        (bars["close"] - bars["open"]).abs()
        / bars["range"].replace(0.0, np.nan)
    ).fillna(0.0)

    previous = bars["close"].shift(1)
    true_range = pd.concat(
        [
            bars["range"],
            (bars["high"] - previous).abs(),
            (bars["low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    bars["atr"] = true_range.shift(1).rolling(
        logic.atr_history_bars,
        min_periods=logic.atr_history_bars,
    ).median()
    prior_signed = bars["signed_quote"].abs().shift(1)
    prior_quote = bars["quote_volume"].shift(1)
    prior_imbalance = bars["imbalance"].abs().shift(1)
    bars["signed_flow_reference"] = prior_signed.rolling(
        logic.reference_history_bars,
        min_periods=logic.reference_history_bars,
    ).quantile(logic.signed_flow_quantile)
    bars["quote_volume_reference"] = prior_quote.rolling(
        logic.reference_history_bars,
        min_periods=logic.reference_history_bars,
    ).quantile(logic.quote_volume_quantile)
    bars["imbalance_reference"] = prior_imbalance.rolling(
        logic.reference_history_bars,
        min_periods=logic.reference_history_bars,
    ).quantile(logic.imbalance_quantile)
    return bars


def _event_direction(row: pd.Series, logic: GlobalAbsorptionLogic) -> str | None:
    atr = float(row["atr"])
    if not np.isfinite(atr) or atr <= 0.0:
        return None
    signed_reference = float(row["signed_flow_reference"])
    quote_reference = float(row["quote_volume_reference"])
    imbalance_reference = float(row["imbalance_reference"])
    if (
        not np.isfinite(signed_reference)
        or not np.isfinite(quote_reference)
        or not np.isfinite(imbalance_reference)
        or signed_reference <= 0.0
        or quote_reference <= 0.0
    ):
        return None
    range_atr = float(row["range"]) / atr
    imbalance = float(row["imbalance"])
    if not (
        float(abs(row["signed_quote"])) >= signed_reference
        and float(row["quote_volume"]) >= quote_reference
        and abs(imbalance) >= max(logic.minimum_imbalance, imbalance_reference)
        and logic.minimum_range_atr <= range_atr <= logic.maximum_range_atr
        and float(row["price_efficiency"]) <= logic.maximum_price_efficiency
    ):
        return None

    if imbalance > 0.0:
        # Aggressive buyers spent exceptional quote volume without retaining
        # value above the event VWAP; the failed auction is a SHORT hypothesis.
        excursion = (float(row["high"]) - float(row["open"])) / atr
        if excursion >= logic.minimum_excursion_atr and float(row["close"]) <= float(row["vwap"]):
            return "SHORT"
    else:
        excursion = (float(row["open"]) - float(row["low"])) / atr
        if excursion >= logic.minimum_excursion_atr and float(row["close"]) >= float(row["vwap"]):
            return "LONG"
    return None


def _confirmation(
    bars: pd.DataFrame,
    *,
    event_index: int,
    direction: str,
    logic: GlobalAbsorptionLogic,
) -> int | None:
    event = bars.iloc[event_index]
    atr = float(event["atr"])
    event_high = float(event["high"])
    event_low = float(event["low"])
    event_open = float(event["open"])
    end = min(len(bars.index), event_index + 1 + logic.confirmation_bars)
    for index in range(event_index + 1, end):
        row = bars.iloc[index]
        if direction == "LONG":
            if float(row["low"]) <= event_low - logic.stop_buffer_atr * atr:
                return None
            body_ok = float(row["close"]) > float(row["open"])
            structure_ok = float(row["close"]) > event_open
            location_ok = (
                (float(row["close"]) - float(row["low"]))
                / max(float(row["range"]), 1e-12)
                >= logic.confirmation_close_location
            )
            flow_ok = float(row["imbalance"]) >= logic.confirmation_minimum_imbalance
        else:
            if float(row["high"]) >= event_high + logic.stop_buffer_atr * atr:
                return None
            body_ok = float(row["close"]) < float(row["open"])
            structure_ok = float(row["close"]) < event_open
            location_ok = (
                (float(row["high"]) - float(row["close"]))
                / max(float(row["range"]), 1e-12)
                >= logic.confirmation_close_location
            )
            flow_ok = float(row["imbalance"]) <= -logic.confirmation_minimum_imbalance
        if body_ok and structure_ok and location_ok and flow_ok:
            return index
    return None


def _retest(
    bars: pd.DataFrame,
    *,
    event_index: int,
    confirmation_index: int,
    direction: str,
    logic: GlobalAbsorptionLogic,
) -> int | None:
    event = bars.iloc[event_index]
    atr = float(event["atr"])
    event_high = float(event["high"])
    event_low = float(event["low"])
    vwap = float(event["vwap"])
    end = min(len(bars.index), confirmation_index + 1 + logic.retest_bars)
    for index in range(confirmation_index + 1, end):
        row = bars.iloc[index]
        if direction == "LONG":
            if float(row["low"]) <= event_low - logic.stop_buffer_atr * atr:
                return None
            touched = float(row["low"]) <= vwap
            rejected = (
                touched
                and float(row["close"]) > vwap
                and float(row["close"]) > float(row["open"])
                and (
                    (float(row["close"]) - float(row["low"]))
                    / max(float(row["range"]), 1e-12)
                    >= logic.retest_close_location
                )
                and float(row["signed_quote"]) > 0.0
            )
        else:
            if float(row["high"]) >= event_high + logic.stop_buffer_atr * atr:
                return None
            touched = float(row["high"]) >= vwap
            rejected = (
                touched
                and float(row["close"]) < vwap
                and float(row["close"]) < float(row["open"])
                and (
                    (float(row["high"]) - float(row["close"]))
                    / max(float(row["range"]), 1e-12)
                    >= logic.retest_close_location
                )
                and float(row["signed_quote"]) < 0.0
            )
        if rejected:
            return index
    return None


def _first_second_index(timestamps: np.ndarray, observed_ns: int) -> int | None:
    index = int(np.searchsorted(timestamps, int(observed_ns), side="left"))
    if index >= len(timestamps):
        return None
    return index


def diagnose(
    seconds: pd.DataFrame,
    *,
    minute: pd.DataFrame,
    five: pd.DataFrame,
    one_pools: Iterable[impact.Pool],
    five_pools: Iterable[impact.Pool],
    trade_start_ns: int,
    trade_end_ns: int,
    logic: GlobalAbsorptionLogic,
    require_retest: bool,
) -> dict[str, Any]:
    """Discover causal failed-flow reversals; create no simulated PnL or NAV."""
    bars = _aggregate_fifteen_seconds(seconds, logic)
    second_work = seconds.copy().sort_values("timestamp_ns", kind="stable").reset_index(drop=True)
    timestamps = second_work["timestamp_ns"].astype("int64").to_numpy()
    highs = second_work["high"].astype(float).to_numpy()
    lows = second_work["low"].astype(float).to_numpy()
    closes = second_work["close"].astype(float).to_numpy()
    previous_close = np.empty_like(closes)
    previous_close[0] = closes[0]
    previous_close[1:] = closes[:-1]
    target_pools = {"1M": list(one_pools), "5M": list(five_pools)}
    touch_cache: dict[str, int | None] = {}

    counters: Counter[str] = Counter()
    scenarios: list[dict[str, Any]] = []
    block_until = -1
    for event_index, event in bars.iterrows():
        event_ns = int(event["timestamp_ns"])
        if not trade_start_ns <= event_ns < trade_end_ns:
            continue
        if event_index <= block_until:
            counters["EVENT_DURING_ACTIVE_SETUP"] += 1
            continue
        direction = _event_direction(event, logic)
        if direction is None:
            continue
        counters["FAILED_AGGRESSION_EVENT"] += 1

        confirmation_index = _confirmation(
            bars,
            event_index=event_index,
            direction=direction,
            logic=logic,
        )
        if confirmation_index is None:
            counters["NO_OPPOSITE_RECOVERY_CONFIRMATION"] += 1
            block_until = max(block_until, event_index + logic.confirmation_bars)
            continue
        counters["RECOVERY_CONFIRMED"] += 1

        observed_index = confirmation_index
        retest_index: int | None = None
        if require_retest:
            retest_index = _retest(
                bars,
                event_index=event_index,
                confirmation_index=confirmation_index,
                direction=direction,
                logic=logic,
            )
            if retest_index is None:
                counters["NO_VALID_VALUE_RETEST"] += 1
                block_until = max(
                    block_until,
                    confirmation_index + logic.retest_bars,
                )
                continue
            counters["VALUE_RETEST_CONFIRMED"] += 1
            observed_index = retest_index

        observed = bars.iloc[observed_index]
        observed_ns = int(observed["timestamp_ns"])
        entry_second_index = _first_second_index(timestamps, observed_ns)
        if entry_second_index is None:
            counters["NO_EXECUTION_SECOND"] += 1
            continue
        entry = float(observed["close"])
        atr = float(event["atr"])
        stop = (
            float(event["low"]) - logic.stop_buffer_atr * atr
            if direction == "LONG"
            else float(event["high"]) + logic.stop_buffer_atr * atr
        )
        risk = entry - stop if direction == "LONG" else stop - entry
        if risk <= 0.0:
            counters["NONPOSITIVE_RISK"] += 1
            continue
        target = target_pool_after_complete_confirmation_second(
            target_pools,
            direction=direction,
            entry=entry,
            stop=stop,
            entry_index=entry_second_index,
            timestamps=timestamps,
            previous_close=previous_close,
            highs=highs,
            lows=lows,
            touch_cache=touch_cache,
            minimum_rr=logic.minimum_rr,
        )
        if target is None:
            counters["NO_CAUSAL_TARGET_AT_MINIMUM_RR"] += 1
            continue
        pool, expected_rr = target
        scenario_id = f"c07-global-abs-{event_ns}-{direction}"
        counters["ENTRY_READY"] += 1
        scenarios.append(
            {
                "scenario_id": scenario_id,
                "outcome": "ENTRY_READY",
                "direction": direction,
                "entry": entry,
                "stop": stop,
                "target": float(pool.level),
                "expected_rr": float(expected_rr),
                "source_pool_id": pool.pool_id,
                "observed_time_ns": observed_ns,
                "event_index": int(event_index),
                "confirmation_index": int(confirmation_index),
                "retest_index": None if retest_index is None else int(retest_index),
                "event": {
                    "timestamp_ns": event_ns,
                    "open": float(event["open"]),
                    "high": float(event["high"]),
                    "low": float(event["low"]),
                    "close": float(event["close"]),
                    "vwap": float(event["vwap"]),
                    "atr": atr,
                    "signed_quote": float(event["signed_quote"]),
                    "imbalance": float(event["imbalance"]),
                    "quote_volume": float(event["quote_volume"]),
                    "price_efficiency": float(event["price_efficiency"]),
                },
                "confirmation": {
                    "timestamp_ns": int(bars.iloc[confirmation_index]["timestamp_ns"]),
                    "close": float(bars.iloc[confirmation_index]["close"]),
                    "imbalance": float(bars.iloc[confirmation_index]["imbalance"]),
                },
                "retest": (
                    None
                    if retest_index is None
                    else {
                        "timestamp_ns": int(bars.iloc[retest_index]["timestamp_ns"]),
                        "close": float(bars.iloc[retest_index]["close"]),
                        "imbalance": float(bars.iloc[retest_index]["imbalance"]),
                    }
                ),
                "target_pool": {
                    "pool_id": pool.pool_id,
                    "timeframe": pool.timeframe,
                    "level": float(pool.level),
                    "confirmed_ts_ns": int(pool.confirmed_ts_ns),
                },
            }
        )
        block_until = max(
            block_until,
            observed_index + 1,
        )

    active_days = sorted(
        {
            pd.to_datetime(int(item["observed_time_ns"]), unit="ns", utc=True)
            .date()
            .isoformat()
            for item in scenarios
        }
    )
    summary = {
        "require_retest": bool(require_retest),
        "contact_counts": dict(sorted(counters.items())),
        "entry_ready": len(scenarios),
        "active_days": len(active_days),
        "active_day_labels": active_days,
        "orders_or_pnl": False,
        "future_information": False,
    }
    return {"summary": summary, "scenarios": scenarios}


def build_causal_signals(
    *,
    report: Mapping[str, Any],
    upstream_report: Mapping[str, Any],
    instrument_id: InstrumentId,
) -> list[CausalTradeSignal]:
    del upstream_report
    output: list[CausalTradeSignal] = []
    forbidden = {
        "path",
        "mfe",
        "mae",
        "terminal",
        "realized_r",
        "future",
    }
    for item in report.get("scenarios", ()):
        if item.get("outcome") != "ENTRY_READY":
            continue
        observed_ns = int(item["observed_time_ns"])
        details = {
            "structural_family": "global_15s_flow_absorption",
            "event": item["event"],
            "confirmation": item["confirmation"],
            "retest": item.get("retest"),
            "target_pool": item["target_pool"],
            "require_retest": bool(report["summary"]["require_retest"]),
        }
        lower_keys = {str(key).lower() for key in details}
        if forbidden.intersection(lower_keys):
            raise RuntimeError("future-path field leaked into a causal signal")
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
                    "GLOBAL_15S_FLOW_ABSORPTION_RETEST"
                    if report["summary"]["require_retest"]
                    else "GLOBAL_15S_FLOW_ABSORPTION_CONFIRMATION"
                ),
                details_json=json.dumps(details, sort_keys=True),
                observed_time_ns=observed_ns,
                ts_event=observed_ns + 1,
                ts_init=observed_ns + 1,
            )
        )
    output.sort(key=lambda item: (item.ts_event, item.scenario_id))
    return output


def discover_structural_signals(
    *,
    config: Mapping[str, Any],
    bundle: Any,
    start: date,
    end: date,
    require_retest: bool,
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any], dict[str, Any]]:
    logic = GlobalAbsorptionLogic()
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
        minute=minute,
        five=five,
        one_pools=one_pools,
        five_pools=five_pools,
        trade_start_ns=base._utc_ns(start),
        trade_end_ns=base._utc_ns(end),
        logic=logic,
        require_retest=require_retest,
    )
    detector = {
        "summary": selected["summary"],
        "scenarios": selected["scenarios"],
    }
    contract = {
        "family": "global_15s_flow_absorption",
        "variant": "first_value_retest" if require_retest else "confirmation_close",
        "logic": asdict(logic),
        "one_minute_preconsumption": one_pre,
        "five_minute_preconsumption": five_pre,
        "detector_population": "every complete causal BTCUSDT fifteen-second auction",
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
    return bundle.seconds, detector, selected, contract


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
    metrics["execution_contract"]["selected_route"] = (
        "global extreme-flow absorption -> opposite recovery -> "
        + ("first event-VWAP retest rejection" if require_retest else "recovery close")
        + " -> causal opposite liquidity"
    )
    metrics["execution_contract"]["take_profit_order_type"] = "MARKET_IF_TOUCHED"
    metrics["execution_contract"]["target_cost_viability_required"] = True
    write_json_atomic(destination / "metrics.json", base._json_safe(metrics))
    return metrics


def _compact(metrics: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "daily_geometric_growth": metrics.get("daily_geometric_growth"),
        "net_return": metrics.get("net_return"),
        "trades": metrics.get("trades"),
        "wins": metrics.get("wins"),
        "losses": metrics.get("losses"),
        "profit_factor": metrics.get("profit_factor"),
        "max_drawdown": metrics.get("max_drawdown"),
        "active_days": metrics.get("active_days"),
        "single_winner_share": metrics.get("single_winner_share"),
        "weekly_gate": metrics.get("weekly_gate"),
        "structural_summary": (
            metrics.get("structural_contract", {})
            .get("selected_summary")
        ),
        "logic": metrics.get("structural_contract", {}).get("logic"),
        "implementation_clean": metrics.get("structural_contract", {}).get(
            "implementation_clean"
        ),
        "signal_contract": metrics.get("signal_contract"),
    }


def run(args: argparse.Namespace) -> int:
    args.output.resolve().mkdir(parents=True, exist_ok=True)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    # The setup is intraday and becomes economically stale well before the old
    # two-hour research horizon. This changes the scenario horizon, not risk.
    config["max_hold_minutes"] = 30
    config_path = args.output.resolve() / "frozen_config.json"
    write_json_atomic(config_path, config)

    baseline = _run_variant(
        args=args,
        config_path=config_path,
        variant="baseline_retest",
        require_retest=True,
    )
    ablation = _run_variant(
        args=args,
        config_path=config_path,
        variant="ablation_no_retest",
        require_retest=False,
    )
    variants = {
        "baseline_retest": _compact(baseline),
        "ablation_no_retest": _compact(ablation),
    }
    passed = [
        name
        for name, metrics in variants.items()
        if bool((metrics.get("weekly_gate") or {}).get("passed"))
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
        "family": "global_15s_flow_absorption",
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
    write_json_atomic(args.output.resolve() / "summary.json", summary)
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
