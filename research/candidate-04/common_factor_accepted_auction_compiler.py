#!/usr/bin/env python3
"""Candidate-04 V52: common-factor accepted-auction continuation.

This is a causal intent compiler, never a backtest engine. It decomposes the
four-asset market into a robust common return/order-flow factor and each asset's
idiosyncratic component. BTC is traded only when an already-known external
liquidity edge is swept and the move is accepted by the common crypto market,
not when a fixed leader is merely followed by laggers.

State sequence:
  shifted 60m external edge -> BTC first sweep -> cross-sectional common return
  and order-flow acceptance -> at least two outside closes -> directional
  displacement/FVG -> a separate later FVG/old-boundary retest -> state-interval
  OI creation and common-factor resumption -> continuation toward pre-existing
  external liquidity.

Orders, fills, costs, risk, positions, PnL and NAV remain solely in
NautilusTrader.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import cross_market_information_transfer_compiler as base
import cross_market_information_transfer_compiler_v2 as v2
import cross_market_smt_liquidity_reversal_compiler as shared

SYMBOLS = base.SYMBOLS
SCENARIO = "COMMON_FACTOR_ACCEPTED_AUCTION_CONTINUATION"
THRESHOLD_WINDOW = 720
THRESHOLD_MIN = 240
RANGE_MINUTES = 60
CLASSIFICATION_BARS = 3
DISPLACEMENT_BARS = 5
RETEST_BARS = 12
COOLDOWN_BARS = 20
MIN_BREADTH = 3
MIN_COMMON_RETURN = 0.35
MIN_COMMON_FLOW = 0.20
MIN_BTC_RETURN = 0.70
MIN_BTC_FLOW = 0.45


@dataclass(frozen=True, slots=True)
class Candidate:
    event_index: int
    signal_index: int
    side: int
    stop_level: float
    priority: float
    details: dict[str, Any]


def shifted_quantile(series: pd.Series, quantile: float) -> pd.Series:
    return (
        series.astype(float)
        .replace([math.inf, -math.inf], float("nan"))
        .shift(1)
        .rolling(THRESHOLD_WINDOW, min_periods=THRESHOLD_MIN)
        .quantile(quantile)
    )


def shifted_positive_median(series: pd.Series) -> pd.Series:
    values = series.astype(float).where(series.astype(float) > 0.0)
    return (
        values.shift(1)
        .rolling(THRESHOLD_WINDOW, min_periods=THRESHOLD_MIN)
        .median()
    )


def normalized_factor_state(
    frames: dict[str, pd.DataFrame],
) -> dict[str, Any]:
    """Build past-only normalized asset states and robust common medians."""
    normalized_return: dict[str, pd.Series] = {}
    normalized_flow: dict[str, pd.Series] = {}
    return_scale: dict[str, pd.Series] = {}
    flow_scale: dict[str, pd.Series] = {}
    for symbol in SYMBOLS:
        frame = frames[symbol]
        r_scale = shifted_quantile(frame["ret_60s_bps"].abs(), 0.70)
        f_scale = shifted_quantile(frame["flow_60s"].abs(), 0.70)
        return_scale[symbol] = r_scale
        flow_scale[symbol] = f_scale
        normalized_return[symbol] = frame["ret_60s_bps"].astype(float) / r_scale
        normalized_flow[symbol] = frame["flow_60s"].astype(float) / f_scale
    return_frame = pd.DataFrame(normalized_return)
    flow_frame = pd.DataFrame(normalized_flow)
    return {
        "normalized_return": normalized_return,
        "normalized_flow": normalized_flow,
        "return_scale": return_scale,
        "flow_scale": flow_scale,
        "common_return": return_frame.median(axis=1, skipna=True),
        "common_flow": flow_frame.median(axis=1, skipna=True),
    }


def common_factor_acceptance(
    factors: dict[str, Any],
    index: int,
    side: int,
    *,
    minimum_breadth: int,
    minimum_common_return: float,
    minimum_common_flow: float,
) -> tuple[bool, dict[str, Any]]:
    if side not in (-1, 1):
        return False, {}
    normalized_return = factors["normalized_return"]
    normalized_flow = factors["normalized_flow"]
    common_return = side * float(factors["common_return"].iloc[index])
    common_flow = side * float(factors["common_flow"].iloc[index])
    btc_return = side * float(normalized_return["BTCUSDT"].iloc[index])
    btc_flow = side * float(normalized_flow["BTCUSDT"].iloc[index])
    breadth_details: dict[str, Any] = {}
    breadth = 0
    for symbol in SYMBOLS:
        r_value = side * float(normalized_return[symbol].iloc[index])
        f_value = side * float(normalized_flow[symbol].iloc[index])
        accepted = bool(
            math.isfinite(r_value)
            and math.isfinite(f_value)
            and r_value > 0.20
            and f_value > 0.0
        )
        breadth += int(accepted)
        breadth_details[symbol] = {
            "directional_normalized_return": r_value,
            "directional_normalized_flow": f_value,
            "accepted_common_direction": accepted,
        }
    values = (common_return, common_flow, btc_return, btc_flow)
    passed = bool(
        all(math.isfinite(value) for value in values)
        and breadth >= minimum_breadth
        and common_return >= minimum_common_return
        and common_flow >= minimum_common_flow
        and btc_return >= MIN_BTC_RETURN
        and btc_flow >= MIN_BTC_FLOW
    )
    return passed, {
        "common_directional_return_factor": common_return,
        "common_directional_flow_factor": common_flow,
        "btc_directional_normalized_return": btc_return,
        "btc_directional_normalized_flow": btc_flow,
        "common_factor_breadth": breadth,
        "asset_factor_states": breadth_details,
        "minimum_common_factor_breadth": minimum_breadth,
        "minimum_common_return_factor": minimum_common_return,
        "minimum_common_flow_factor": minimum_common_flow,
    }


def classify_outside_acceptance(
    frame: pd.DataFrame,
    event_index: int,
    boundary: float,
    side: int,
) -> tuple[int | None, dict[str, Any]]:
    end = event_index + CLASSIFICATION_BARS - 1
    if end >= len(frame):
        return None, {}
    segment = frame.iloc[event_index : end + 1]
    atr = float(frame["atr"].iloc[event_index])
    if not math.isfinite(atr) or atr <= 0.0:
        return None, {}
    outside = (
        segment["close"].astype(float) > boundary
        if side > 0
        else segment["close"].astype(float) < boundary
    )
    final_close = float(segment["close"].iloc[-1])
    index_proxy = side * (
        float(segment["ret_60s_bps"].iloc[-1])
        - float(segment["basis_change_5m"].iloc[-1])
    )
    accepted = bool(
        int(outside.sum()) >= 2
        and side * (final_close - boundary) >= 0.03 * atr
        and math.isfinite(index_proxy)
        and index_proxy > 0.0
    )
    if not accepted:
        return None, {}
    return end, {
        "outside_close_count": int(outside.sum()),
        "classification_final_close": final_close,
        "classification_directional_index_proxy_bps": index_proxy,
    }


def find_displacement(
    frame: pd.DataFrame,
    event_index: int,
    classification_end: int,
    boundary: float,
    side: int,
    body_cutoff: pd.Series,
) -> tuple[int, tuple[float, float], dict[str, float]] | None:
    last = min(classification_end + DISPLACEMENT_BARS, len(frame) - 2)
    for index in range(max(event_index, 2), last + 1):
        row = frame.iloc[index]
        atr = float(row["atr"])
        cutoff = float(body_cutoff.iloc[index])
        if not all(math.isfinite(value) for value in (atr, cutoff)) or atr <= 0.0:
            continue
        body_atr = abs(float(row["close"]) - float(row["open"])) / atr
        directional_body = side * (float(row["close"]) - float(row["open"]))
        if (
            directional_body <= 0.0
            or body_atr < max(0.20, cutoff)
            or side * (float(row["close"]) - boundary) <= 0.0
        ):
            continue
        fvg = shared.directional_fvg(frame, index, side, 0.005 * atr)
        if fvg is None:
            continue
        return index, fvg, {
            "displacement_body_atr": body_atr,
            "displacement_body_cutoff": cutoff,
        }
    return None


def state_oi_creation(
    frame: pd.DataFrame,
    event_index: int,
    retest_index: int,
    cutoff: float,
) -> tuple[bool, float]:
    if not (
        0 < event_index < retest_index < len(frame)
        and math.isfinite(cutoff)
        and cutoff > 0.0
    ):
        return False, float("nan")
    start = float(frame["metric_sum_open_interest"].iloc[event_index - 1])
    end = float(frame["metric_sum_open_interest"].iloc[retest_index])
    if not all(math.isfinite(value) and value > 0.0 for value in (start, end)):
        return False, float("nan")
    change = end / start - 1.0
    return change >= cutoff, change


def retest_holds(
    frame: pd.DataFrame,
    factors: dict[str, Any],
    index: int,
    side: int,
    boundary: float,
    fvg: tuple[float, float],
    minimum_common_return: float,
) -> tuple[bool, dict[str, float]]:
    row = frame.iloc[index]
    low, high = fvg
    midpoint = 0.5 * (low + high)
    touched = float(row["high"]) >= low and float(row["low"]) <= high
    close_holds_fvg = float(row["close"]) >= midpoint if side > 0 else float(row["close"]) <= midpoint
    close_holds_boundary = side * (float(row["close"]) - boundary) > 0.0
    directional_flow = side * float(row["flow_60s"])
    common_return = side * float(factors["common_return"].iloc[index])
    common_flow = side * float(factors["common_flow"].iloc[index])
    passed = bool(
        touched
        and close_holds_fvg
        and close_holds_boundary
        and math.isfinite(directional_flow)
        and directional_flow >= 0.0
        and math.isfinite(common_return)
        and common_return >= minimum_common_return
        and math.isfinite(common_flow)
        and common_flow >= 0.0
    )
    return passed, {
        "retest_directional_flow_60s": directional_flow,
        "retest_common_directional_return_factor": common_return,
        "retest_common_directional_flow_factor": common_flow,
        "fvg_midpoint": midpoint,
    }


def collect_candidates(
    frames: dict[str, pd.DataFrame],
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    stop_buffer_atr: float,
    *,
    minimum_breadth: int = MIN_BREADTH,
    minimum_common_return: float = MIN_COMMON_RETURN,
    minimum_common_flow: float = MIN_COMMON_FLOW,
) -> tuple[list[Candidate], dict[str, Any]]:
    btc = frames["BTCUSDT"]
    factors = normalized_factor_state(frames)
    upper, lower = shared.causal_range_edges(btc)
    body_atr = (
        (btc["close"].astype(float) - btc["open"].astype(float)).abs()
        / btc["atr"].astype(float)
    )
    body_cutoff = shifted_quantile(body_atr, 0.55)
    oi_cutoff = shifted_positive_median(btc["metric_oi_change_15m"])

    counts: dict[str, Any] = {
        "btc_first_external_sweeps": 0,
        "common_factor_acceptance": 0,
        "outside_acceptance": 0,
        "displacement_fvg": 0,
        "retests": 0,
        "oi_creation": 0,
        "qualified": 0,
        "cooldown_suppressed": 0,
        "minimum_breadth": minimum_breadth,
        "minimum_common_return": minimum_common_return,
        "minimum_common_flow": minimum_common_flow,
    }
    output: list[Candidate] = []
    last_signal = -10**9

    for event_index, timestamp in enumerate(btc.index):
        if timestamp < evaluation_start or timestamp >= evaluation_end:
            continue
        if event_index < RANGE_MINUTES or event_index >= len(btc) - 2:
            continue
        row = btc.iloc[event_index]
        previous = btc.iloc[event_index - 1]
        atr = float(row["atr"])
        upper_edge = float(upper.iloc[event_index])
        lower_edge = float(lower.iloc[event_index])
        if not all(math.isfinite(value) for value in (atr, upper_edge, lower_edge)) or atr <= 0.0:
            continue
        high_sweep = (
            float(previous["close"]) <= upper_edge
            and float(row["high"]) >= upper_edge + 0.02 * atr
        )
        low_sweep = (
            float(previous["close"]) >= lower_edge
            and float(row["low"]) <= lower_edge - 0.02 * atr
        )
        if high_sweep == low_sweep:
            continue
        side = 1 if high_sweep else -1
        boundary = upper_edge if side > 0 else lower_edge
        sweep_extreme = float(row["high"] if side > 0 else row["low"])
        counts["btc_first_external_sweeps"] += 1

        factor_pass, factor_details = common_factor_acceptance(
            factors,
            event_index,
            side,
            minimum_breadth=minimum_breadth,
            minimum_common_return=minimum_common_return,
            minimum_common_flow=minimum_common_flow,
        )
        if not factor_pass:
            continue
        counts["common_factor_acceptance"] += 1

        classification_end, acceptance_details = classify_outside_acceptance(
            btc,
            event_index,
            boundary,
            side,
        )
        if classification_end is None:
            continue
        counts["outside_acceptance"] += 1

        displacement = find_displacement(
            btc,
            event_index,
            classification_end,
            boundary,
            side,
            body_cutoff,
        )
        if displacement is None:
            continue
        displacement_index, fvg, displacement_details = displacement
        counts["displacement_fvg"] += 1

        signal_index: int | None = None
        retest_details: dict[str, float] = {}
        oi_change = float("nan")
        last = min(displacement_index + RETEST_BARS, len(btc) - 2)
        for index in range(displacement_index + 1, last + 1):
            current = btc.iloc[index]
            invalidated = (
                float(current["close"]) <= boundary - 0.10 * float(current["atr"])
                if side > 0
                else float(current["close"]) >= boundary + 0.10 * float(current["atr"])
            )
            if invalidated:
                break
            held, details = retest_holds(
                btc,
                factors,
                index,
                side,
                boundary,
                fvg,
                max(minimum_common_return * 0.50, 0.0),
            )
            if not held:
                continue
            counts["retests"] += 1
            oi_pass, change = state_oi_creation(
                btc,
                event_index,
                index,
                float(oi_cutoff.iloc[event_index]),
            )
            if not oi_pass:
                continue
            counts["oi_creation"] += 1
            signal_index = index
            retest_details = details
            oi_change = change
            break
        if signal_index is None:
            continue
        if signal_index - last_signal <= COOLDOWN_BARS:
            counts["cooldown_suppressed"] += 1
            continue

        signal_row = btc.iloc[signal_index]
        signal_atr = float(signal_row["atr"])
        retest_extreme = float(signal_row["low"] if side > 0 else signal_row["high"])
        old_range_invalidation = boundary - side * 0.10 * signal_atr
        stop = (
            min(retest_extreme - stop_buffer_atr * signal_atr, old_range_invalidation)
            if side > 0
            else max(retest_extreme + stop_buffer_atr * signal_atr, old_range_invalidation)
        )
        entry = float(signal_row["close"])
        if not math.isfinite(stop) or side * (entry - stop) <= 0.0:
            continue
        priority = (
            float(factor_details["common_directional_return_factor"])
            * float(factor_details["common_directional_flow_factor"])
            * max(float(signal_row["notional_60s"]), 1.0)
        )
        fvg_low, fvg_high = fvg
        details: dict[str, Any] = {
            "compiler": "candidate-04-v52-common-factor-accepted-auction-v1",
            "market_cause": (
                "a pre-existing BTC external-liquidity sweep was accepted by "
                "the robust common crypto return and order-flow factor and "
                "supported by newly created BTC perpetual inventory"
            ),
            "state_sequence": [
                "SHIFTED_60M_EXTERNAL_LIQUIDITY",
                "BTC_FIRST_EXTERNAL_SWEEP",
                "COMMON_RETURN_AND_ORDER_FLOW_FACTOR_ACCEPTANCE",
                "TWO_OF_THREE_OUTSIDE_CLOSES",
                "DIRECTIONAL_DISPLACEMENT_FVG",
                "SEPARATE_FVG_AND_OLD_BOUNDARY_RETEST",
                "STATE_INTERVAL_OI_CREATION",
                "COMMON_FACTOR_RESUMPTION",
            ],
            "event_time": timestamp.isoformat(),
            "sweep_direction": side,
            "trade_direction": side,
            "external_boundary": boundary,
            "sweep_extreme": sweep_extreme,
            "event_extension_atr": side * (sweep_extreme - boundary) / atr,
            "classification_index": classification_end,
            "displacement_index": displacement_index,
            "signal_index": signal_index,
            "fvg_low": fvg_low,
            "fvg_high": fvg_high,
            "fvg_midpoint": 0.5 * (fvg_low + fvg_high),
            "state_interval_open_interest_change": oi_change,
            "state_interval_oi_creation_cutoff": float(oi_cutoff.iloc[event_index]),
            "old_range_invalidation": old_range_invalidation,
            "structural_stop": stop,
            "minimum_target_net_r": 1.20,
            "risk_multiplier": 1.0,
            **factor_details,
            **acceptance_details,
            **displacement_details,
            **retest_details,
        }
        output.append(
            Candidate(
                event_index=event_index,
                signal_index=signal_index,
                side=side,
                stop_level=stop,
                priority=priority,
                details=details,
            )
        )
        counts["qualified"] += 1
        last_signal = signal_index
    return output, counts


def write_outputs(
    output: Path,
    candidates: list[Candidate],
    counts: dict[str, Any],
    frames: dict[str, pd.DataFrame],
    nt_frames: dict[str, pd.DataFrame],
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    rows_by_symbol: dict[str, list[dict[str, Any]]] = {
        symbol: [] for symbol in SYMBOLS
    }
    for item in candidates:
        timestamp = frames["BTCUSDT"].index[item.signal_index]
        if not evaluation_start <= timestamp < evaluation_end:
            continue
        observe_time = nt_frames["BTCUSDT"].index[item.signal_index]
        rows_by_symbol["BTCUSDT"].append(
            {
                "scenario": SCENARIO,
                "side": item.side,
                "signal_index": item.signal_index,
                "signal_time": timestamp.isoformat(),
                "observe_time": observe_time.isoformat(),
                "observe_time_ns": int(observe_time.value),
                "stop_level": item.stop_level,
                "event_indices": [item.event_index, item.signal_index],
                "details": item.details,
            }
        )
    for symbol, rows in rows_by_symbol.items():
        target = output / symbol
        target.mkdir(parents=True, exist_ok=True)
        (target / "signals.json").write_text(
            json.dumps(rows, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (target / "summary.json").write_text(
            json.dumps(
                {"symbol": symbol, "written_signals": len(rows)},
                indent=2,
                sort_keys=True,
            ) + "\n",
            encoding="utf-8",
        )
    summary = {
        "candidate": "candidate-04-v52-common-factor-accepted-auction-continuation",
        "compiler": "candidate-04-v52-common-factor-accepted-auction-v1",
        "written_signals": sum(len(rows) for rows in rows_by_symbol.values()),
        "signals_by_symbol": {
            symbol: len(rows) for symbol, rows in rows_by_symbol.items()
        },
        "route_counts": counts,
        "scenario_contract": {
            "common_factor": (
                "cross-sectional median of past-only normalized return and "
                "executed-flow states; no fixed leader-lagger coefficient"
            ),
            "acceptance": (
                "common-factor breadth, two outside closes and an index-proxy "
                "confirmation beyond pre-existing BTC external liquidity"
            ),
            "inventory": (
                "raw exchange OI must increase from immediately before the "
                "sweep through the later retest"
            ),
            "entry": "separate later FVG/old-boundary retest with factor resumption",
            "invalidation": "retest extreme or causal return inside the old range",
            "target": "nearest pre-existing intact external liquidity; no measured move",
        },
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rich-root", required=True, type=Path)
    parser.add_argument("--config-root", required=True, type=Path)
    parser.add_argument("--kline-root", required=True, type=Path)
    parser.add_argument("--evaluation-start", required=True)
    parser.add_argument("--evaluation-end", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--stop-buffer-atr", type=float, default=0.05)
    parser.add_argument("--minimum-breadth", type=int, default=MIN_BREADTH)
    parser.add_argument("--minimum-common-return", type=float, default=MIN_COMMON_RETURN)
    parser.add_argument("--minimum-common-flow", type=float, default=MIN_COMMON_FLOW)
    args = parser.parse_args()
    evaluation_start = pd.Timestamp(args.evaluation_start, tz="UTC")
    evaluation_end = pd.Timestamp(args.evaluation_end, tz="UTC")
    frames, nt_frames = v2.load_frames(
        args.rich_root,
        args.config_root,
        args.kline_root,
        evaluation_start,
        evaluation_end,
    )
    candidates, counts = collect_candidates(
        frames,
        evaluation_start,
        evaluation_end,
        args.stop_buffer_atr,
        minimum_breadth=args.minimum_breadth,
        minimum_common_return=args.minimum_common_return,
        minimum_common_flow=args.minimum_common_flow,
    )
    write_outputs(
        args.output,
        candidates,
        counts,
        frames,
        nt_frames,
        evaluation_start,
        evaluation_end,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
