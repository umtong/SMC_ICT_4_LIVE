#!/usr/bin/env python3
"""Candidate-04 V55: common-factor boundary-negotiation resolution.

This is a causal signal compiler only. NautilusTrader owns orders, fills, fees,
positions, risk, PnL and account NAV.

V53 showed that the first apparent FVG hold was an unreliable entry and its
0.10-ATR old-range stop was not a completed auction invalidation. V55 preserves
the accepted external-liquidity direction but treats the first retest as the
start of a negotiation. It requires a real counter-auction, then a separate
completed close that resolves the entire prior negotiation range in the common
factor direction. The stop is placed beyond the full negotiation extreme.
Climactic sweeps above 2 ATR are excluded from continuation; the only committed
ablation removes this extension cap while keeping the negotiation state intact.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

import common_factor_accepted_auction_compiler as v52
import common_factor_deleveraging_continuation_compiler as v53
import cross_market_information_transfer_compiler as base
import cross_market_information_transfer_compiler_v2 as v2

SYMBOLS = base.SYMBOLS
SCENARIO = "COMMON_FACTOR_BOUNDARY_NEGOTIATION_RESOLUTION_CONTINUATION"
NEGOTIATION_BARS = 12
MIN_NEGOTIATION_BARS = 3
COOLDOWN_BARS = 20
MAX_EVENT_EXTENSION_ATR = 2.0
MIN_RESOLUTION_COMMON_RETURN = 0.20
MIN_RESOLUTION_COMMON_FLOW = 0.10


@dataclass(frozen=True, slots=True)
class Candidate:
    parent_event_index: int
    first_retest_index: int
    signal_index: int
    side: int
    stop_level: float
    priority: float
    details: dict[str, Any]


def has_real_counterauction(
    frame: pd.DataFrame,
    first_index: int,
    last_index_exclusive: int,
    side: int,
) -> tuple[bool, dict[str, float]]:
    """Require an actual opposite price and executed-flow phase."""
    if side not in (-1, 1) or first_index < 1 or last_index_exclusive <= first_index:
        return False, {}
    counter_bars = 0
    counter_return_sum = 0.0
    counter_flow_min = math.inf
    for index in range(first_index, last_index_exclusive):
        close = float(frame["close"].iloc[index])
        previous = float(frame["close"].iloc[index - 1])
        flow = side * float(frame["flow_60s"].iloc[index])
        directional_change = side * (close - previous)
        if directional_change < 0.0 and math.isfinite(flow) and flow < 0.0:
            counter_bars += 1
            counter_return_sum += directional_change
            counter_flow_min = min(counter_flow_min, flow)
    return counter_bars > 0, {
        "counterauction_bar_count": float(counter_bars),
        "counterauction_directional_price_change": counter_return_sum,
        "counterauction_min_directional_flow": (
            counter_flow_min if math.isfinite(counter_flow_min) else 0.0
        ),
    }


def negotiation_resolution(
    frame: pd.DataFrame,
    factors: dict[str, Any],
    first_retest_index: int,
    resolution_index: int,
    side: int,
    body_cutoff: float,
) -> tuple[bool, dict[str, float]]:
    """Resolve only the range formed before the current completed bar."""
    if (
        side not in (-1, 1)
        or first_retest_index < 1
        or resolution_index - first_retest_index < MIN_NEGOTIATION_BARS - 1
        or resolution_index >= len(frame)
    ):
        return False, {}
    prior = frame.iloc[first_retest_index:resolution_index]
    if len(prior) < MIN_NEGOTIATION_BARS - 1:
        return False, {}
    counter_pass, counter_details = has_real_counterauction(
        frame,
        first_retest_index,
        resolution_index,
        side,
    )
    if not counter_pass:
        return False, counter_details
    row = frame.iloc[resolution_index]
    atr = float(row["atr"])
    values = (
        atr,
        float(row["open"]),
        float(row["close"]),
        float(row["ret_60s_bps"]),
        float(row["flow_60s"]),
        float(row["basis_change_5m"]),
        body_cutoff,
    )
    if not all(math.isfinite(value) for value in values) or atr <= 0.0:
        return False, counter_details
    negotiation_high = float(prior["high"].max())
    negotiation_low = float(prior["low"].min())
    resolution_break = (
        float(row["close"]) > negotiation_high
        if side > 0
        else float(row["close"]) < negotiation_low
    )
    directional_return = side * float(row["ret_60s_bps"])
    directional_flow = side * float(row["flow_60s"])
    directional_index_proxy = side * (
        float(row["ret_60s_bps"]) - float(row["basis_change_5m"])
    )
    common_return = side * float(factors["common_return"].iloc[resolution_index])
    common_flow = side * float(factors["common_flow"].iloc[resolution_index])
    body_atr = abs(float(row["close"]) - float(row["open"])) / atr
    passed = bool(
        resolution_break
        and directional_return > 0.0
        and directional_flow > 0.0
        and directional_index_proxy > 0.0
        and common_return >= MIN_RESOLUTION_COMMON_RETURN
        and common_flow >= MIN_RESOLUTION_COMMON_FLOW
        and body_atr >= max(0.15, body_cutoff)
    )
    return passed, {
        **counter_details,
        "negotiation_high": negotiation_high,
        "negotiation_low": negotiation_low,
        "negotiation_bars_before_resolution": float(len(prior)),
        "resolution_directional_return_60s_bps": directional_return,
        "resolution_directional_flow_60s": directional_flow,
        "resolution_directional_index_proxy_60s_bps": directional_index_proxy,
        "resolution_common_return_factor": common_return,
        "resolution_common_flow_factor": common_flow,
        "resolution_body_atr": body_atr,
        "resolution_body_cutoff": body_cutoff,
        "entire_prior_negotiation_range_broken": float(resolution_break),
    }


def collect_candidates(
    frames: dict[str, pd.DataFrame],
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    stop_buffer_atr: float,
    *,
    maximum_event_extension_atr: float | None = MAX_EVENT_EXTENSION_ATR,
) -> tuple[list[Candidate], dict[str, Any]]:
    btc = frames["BTCUSDT"]
    factors = v52.normalized_factor_state(frames)
    parent_candidates, parent_counts = v53.collect_candidates(
        frames,
        evaluation_start,
        evaluation_end,
        stop_buffer_atr,
        require_oi_contraction=False,
    )
    body_atr = (
        (btc["close"].astype(float) - btc["open"].astype(float)).abs()
        / btc["atr"].astype(float)
    )
    body_cutoff = v52.shifted_quantile(body_atr, 0.50)

    counts: dict[str, Any] = {
        "parent_accepted_retests": len(parent_candidates),
        "non_climactic_parent_states": 0,
        "real_counterauctions": 0,
        "whole_negotiation_resolutions": 0,
        "qualified": 0,
        "cooldown_suppressed": 0,
        "maximum_event_extension_atr": maximum_event_extension_atr,
        "parent_route_counts": parent_counts,
    }
    output: list[Candidate] = []
    last_signal = -10**9

    for parent in parent_candidates:
        event_extension = float(parent.details["event_extension_atr"])
        if (
            maximum_event_extension_atr is not None
            and event_extension > maximum_event_extension_atr
        ):
            continue
        counts["non_climactic_parent_states"] += 1
        side = parent.side
        first_retest_index = parent.signal_index
        boundary = float(parent.details["external_boundary"])
        fvg_low = float(parent.details["fvg_low"])
        fvg_high = float(parent.details["fvg_high"])
        signal_index: int | None = None
        resolution_details: dict[str, float] = {}
        last = min(first_retest_index + NEGOTIATION_BARS, len(btc) - 2)
        for index in range(
            first_retest_index + MIN_NEGOTIATION_BARS - 1,
            last + 1,
        ):
            current = btc.iloc[index]
            current_atr = float(current["atr"])
            if not math.isfinite(current_atr) or current_atr <= 0.0:
                continue
            # A completed close far back inside the old range is a genuine state
            # failure; ordinary negotiation around the boundary is allowed.
            invalidated = (
                float(current["close"]) <= boundary - 0.35 * current_atr
                if side > 0
                else float(current["close"]) >= boundary + 0.35 * current_atr
            )
            if invalidated:
                break
            passed, details = negotiation_resolution(
                btc,
                factors,
                first_retest_index,
                index,
                side,
                float(body_cutoff.iloc[index]),
            )
            if not passed:
                continue
            counts["real_counterauctions"] += 1
            counts["whole_negotiation_resolutions"] += 1
            signal_index = index
            resolution_details = details
            break
        if signal_index is None:
            continue
        if signal_index - last_signal <= COOLDOWN_BARS:
            counts["cooldown_suppressed"] += 1
            continue

        signal_row = btc.iloc[signal_index]
        signal_atr = float(signal_row["atr"])
        negotiation_low = float(resolution_details["negotiation_low"])
        negotiation_high = float(resolution_details["negotiation_high"])
        stop = (
            negotiation_low - stop_buffer_atr * signal_atr
            if side > 0
            else negotiation_high + stop_buffer_atr * signal_atr
        )
        entry = float(signal_row["close"])
        if not math.isfinite(stop) or side * (entry - stop) <= 0.0:
            continue
        event_index = parent.event_index
        pre_oi = float(btc["metric_sum_open_interest"].iloc[event_index - 1])
        signal_oi = float(btc["metric_sum_open_interest"].iloc[signal_index])
        state_oi_change = (
            signal_oi / pre_oi - 1.0
            if all(math.isfinite(value) and value > 0.0 for value in (pre_oi, signal_oi))
            else float("nan")
        )
        priority = (
            abs(float(resolution_details["resolution_common_return_factor"]))
            * abs(float(resolution_details["resolution_common_flow_factor"]))
            * max(float(signal_row["notional_60s"]), 1.0)
        )
        details: dict[str, Any] = {
            "compiler": "candidate-04-v55-negotiation-resolution-v1",
            "market_cause": (
                "a common-factor accepted external-liquidity sweep formed a "
                "real counter-auction around the old boundary/FVG, then the "
                "common market resolved the complete pre-existing negotiation "
                "range in the accepted direction"
            ),
            "state_sequence": [
                "COMMON_FACTOR_ACCEPTED_EXTERNAL_SWEEP",
                "FIRST_FVG_OLD_BOUNDARY_RETEST",
                "REAL_COUNTER_PRICE_AND_EXECUTED_FLOW_AUCTION",
                "COMPLETED_NEGOTIATION_RANGE",
                "COMMON_FACTOR_INDEX_PROXY_RESOLUTION",
                "ENTIRE_PRIOR_NEGOTIATION_RANGE_BREAK",
            ],
            "parent_event_index": event_index,
            "first_retest_index": first_retest_index,
            "signal_index": signal_index,
            "trade_direction": side,
            "external_boundary": boundary,
            "parent_event_extension_atr": event_extension,
            "maximum_event_extension_atr": maximum_event_extension_atr,
            "parent_fvg_low": fvg_low,
            "parent_fvg_high": fvg_high,
            "state_interval_open_interest_change": state_oi_change,
            "structural_stop": stop,
            "minimum_target_net_r": 1.20,
            "risk_multiplier": 1.0,
            **resolution_details,
        }
        output.append(
            Candidate(
                parent_event_index=event_index,
                first_retest_index=first_retest_index,
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
                "event_indices": [
                    item.parent_event_index,
                    item.first_retest_index,
                    item.signal_index,
                ],
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
        "candidate": "candidate-04-v55-common-factor-negotiation-resolution-continuation",
        "compiler": "candidate-04-v55-negotiation-resolution-v1",
        "written_signals": sum(len(rows) for rows in rows_by_symbol.values()),
        "signals_by_symbol": {
            symbol: len(rows) for symbol, rows in rows_by_symbol.items()
        },
        "route_counts": counts,
        "scenario_contract": {
            "parent": "common-factor accepted external sweep and first FVG retest",
            "negotiation": "actual counter-price and counter-flow bars after the first retest",
            "resolution": "common factor breaks the entire prior negotiation range",
            "invalidation": "beyond the complete negotiation extreme",
            "event_quality": "continuation event extension at most 2 ATR in the full router",
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
    parser.add_argument("--disable-extension-cap", action="store_true")
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
        maximum_event_extension_atr=(
            None if args.disable_extension_cap else MAX_EVENT_EXTENSION_ATR
        ),
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
