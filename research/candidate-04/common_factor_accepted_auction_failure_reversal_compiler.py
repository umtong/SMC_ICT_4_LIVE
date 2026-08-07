#!/usr/bin/env python3
"""Candidate-04 V54: common-factor accepted-auction failure reversal.

This module compiles causal trade intents only. NautilusTrader owns all orders,
fills, fees, positions, risk, PnL and NAV.

The parent state is V53's complete accepted-auction/deleveraging continuation
hypothesis. V54 does not enter against that state immediately. It waits for the
apparently held retest to fail, the old external boundary to be lost, the robust
four-asset return/order-flow factor and index proxy to reverse, and an opposite
MSS/displacement/FVG to form. Entry occurs only after a separate later inside
retest of the inverse FVG holds. This turns a systematic continuation failure
into a complete trapped-auction reversal scenario rather than a sign inversion.
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
import cross_market_smt_liquidity_reversal_compiler as shared

SYMBOLS = base.SYMBOLS
SCENARIO = "COMMON_FACTOR_ACCEPTED_AUCTION_FAILURE_REVERSAL"
FAILURE_BARS = 6
FAILURE_MSS_LOOKBACK = 5
RETEST_BARS = 8
COOLDOWN_BARS = 20
MIN_OPPOSITE_COMMON_RETURN = 0.20
MIN_OPPOSITE_COMMON_FLOW = 0.10


@dataclass(frozen=True, slots=True)
class Candidate:
    parent_event_index: int
    parent_retest_index: int
    failure_index: int
    signal_index: int
    side: int
    stop_level: float
    priority: float
    details: dict[str, Any]


def opposite_failure_confirmation(
    frame: pd.DataFrame,
    factors: dict[str, Any],
    index: int,
    parent_side: int,
    boundary: float,
    internal_boundary: float,
    body_cutoff: float,
    *,
    require_opposite_common_factor: bool = True,
) -> tuple[bool, dict[str, float]]:
    reversal_side = -parent_side
    row = frame.iloc[index]
    atr = float(row["atr"])
    values = (
        atr,
        float(row["open"]),
        float(row["close"]),
        float(row["ret_60s_bps"]),
        float(row["flow_60s"]),
        float(row["basis_change_5m"]),
        boundary,
        internal_boundary,
        body_cutoff,
    )
    if not all(math.isfinite(value) for value in values) or atr <= 0.0:
        return False, {}
    common_return = reversal_side * float(factors["common_return"].iloc[index])
    common_flow = reversal_side * float(factors["common_flow"].iloc[index])
    directional_return = reversal_side * float(row["ret_60s_bps"])
    directional_flow = reversal_side * float(row["flow_60s"])
    directional_index_proxy = reversal_side * (
        float(row["ret_60s_bps"]) - float(row["basis_change_5m"])
    )
    body_atr = abs(float(row["close"]) - float(row["open"])) / atr
    old_boundary_failed = parent_side * (float(row["close"]) - boundary) <= -0.05 * atr
    mss = reversal_side * (float(row["close"]) - internal_boundary) > 0.0
    common_pass = (
        common_return >= MIN_OPPOSITE_COMMON_RETURN
        and common_flow >= MIN_OPPOSITE_COMMON_FLOW
    )
    passed = bool(
        old_boundary_failed
        and mss
        and directional_return > 0.0
        and directional_flow > 0.0
        and directional_index_proxy > 0.0
        and body_atr >= max(0.20, body_cutoff)
        and (common_pass or not require_opposite_common_factor)
    )
    return passed, {
        "reversal_directional_return_60s_bps": directional_return,
        "reversal_directional_flow_60s": directional_flow,
        "reversal_directional_index_proxy_60s_bps": directional_index_proxy,
        "opposite_common_return_factor": common_return,
        "opposite_common_flow_factor": common_flow,
        "opposite_common_factor_required": float(require_opposite_common_factor),
        "failure_displacement_body_atr": body_atr,
        "failure_body_cutoff": body_cutoff,
        "failure_internal_boundary": internal_boundary,
        "old_external_boundary_failed": float(old_boundary_failed),
        "failure_mss": float(mss),
    }


def inside_retest_holds(
    frame: pd.DataFrame,
    factors: dict[str, Any],
    index: int,
    reversal_side: int,
    boundary: float,
    fvg: tuple[float, float],
    *,
    require_opposite_common_factor: bool = True,
) -> tuple[bool, dict[str, float]]:
    row = frame.iloc[index]
    low, high = fvg
    midpoint = 0.5 * (low + high)
    touched = float(row["high"]) >= low and float(row["low"]) <= high
    close_holds_fvg = float(row["close"]) >= midpoint if reversal_side > 0 else float(row["close"]) <= midpoint
    inside_old_range = reversal_side * (float(row["close"]) - boundary) > 0.0
    directional_flow = reversal_side * float(row["flow_60s"])
    common_return = reversal_side * float(factors["common_return"].iloc[index])
    common_flow = reversal_side * float(factors["common_flow"].iloc[index])
    common_pass = common_return >= 0.0 and common_flow >= 0.0
    passed = bool(
        touched
        and close_holds_fvg
        and inside_old_range
        and math.isfinite(directional_flow)
        and directional_flow >= 0.0
        and (common_pass or not require_opposite_common_factor)
    )
    return passed, {
        "inside_retest_directional_flow_60s": directional_flow,
        "inside_retest_opposite_common_return_factor": common_return,
        "inside_retest_opposite_common_flow_factor": common_flow,
        "inverse_fvg_midpoint": midpoint,
    }


def collect_candidates(
    frames: dict[str, pd.DataFrame],
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    stop_buffer_atr: float,
    *,
    require_opposite_common_factor: bool = True,
) -> tuple[list[Candidate], dict[str, Any]]:
    btc = frames["BTCUSDT"]
    factors = v52.normalized_factor_state(frames)
    parent_candidates, parent_counts = v53.collect_candidates(
        frames,
        evaluation_start,
        evaluation_end,
        stop_buffer_atr,
        require_oi_contraction=True,
    )
    body_atr = (
        (btc["close"].astype(float) - btc["open"].astype(float)).abs()
        / btc["atr"].astype(float)
    )
    body_cutoff = v52.shifted_quantile(body_atr, 0.55)

    counts: dict[str, Any] = {
        "parent_forced_deleveraging_states": len(parent_candidates),
        "old_boundary_failures": 0,
        "opposite_mss_displacement_fvg": 0,
        "later_inside_retests": 0,
        "oi_contraction_retained": 0,
        "qualified": 0,
        "cooldown_suppressed": 0,
        "require_opposite_common_factor": require_opposite_common_factor,
        "parent_route_counts": parent_counts,
    }
    output: list[Candidate] = []
    last_signal = -10**9

    for parent in parent_candidates:
        parent_side = parent.side
        reversal_side = -parent_side
        boundary = float(parent.details["external_boundary"])
        sweep_extreme = float(parent.details["sweep_extreme"])
        event_index = parent.event_index
        parent_retest_index = parent.signal_index
        pre_failure = btc.iloc[
            max(parent_retest_index - FAILURE_MSS_LOOKBACK + 1, 0) : parent_retest_index + 1
        ]
        if len(pre_failure) < 2:
            continue
        internal_boundary = (
            float(pre_failure["high"].max())
            if reversal_side > 0
            else float(pre_failure["low"].min())
        )
        failure_index: int | None = None
        failure_details: dict[str, float] = {}
        inverse_fvg: tuple[float, float] | None = None
        upper_failure = min(parent_retest_index + FAILURE_BARS, len(btc) - 2)
        for index in range(parent_retest_index + 1, upper_failure + 1):
            passed, details = opposite_failure_confirmation(
                btc,
                factors,
                index,
                parent_side,
                boundary,
                internal_boundary,
                float(body_cutoff.iloc[index]),
                require_opposite_common_factor=require_opposite_common_factor,
            )
            if not passed:
                continue
            atr = float(btc["atr"].iloc[index])
            candidate_fvg = shared.directional_fvg(
                btc,
                index,
                reversal_side,
                0.005 * atr,
            )
            if candidate_fvg is None:
                continue
            failure_index = index
            failure_details = details
            inverse_fvg = candidate_fvg
            break
        if failure_index is None or inverse_fvg is None:
            continue
        counts["old_boundary_failures"] += 1
        counts["opposite_mss_displacement_fvg"] += 1

        signal_index: int | None = None
        retest_details: dict[str, float] = {}
        oi_details: dict[str, float] = {}
        upper_retest = min(failure_index + RETEST_BARS, len(btc) - 2)
        for index in range(failure_index + 1, upper_retest + 1):
            current = btc.iloc[index]
            invalidated = (
                float(current["high"]) >= sweep_extreme
                if reversal_side < 0
                else float(current["low"]) <= sweep_extreme
            )
            if invalidated:
                break
            held, details = inside_retest_holds(
                btc,
                factors,
                index,
                reversal_side,
                boundary,
                inverse_fvg,
                require_opposite_common_factor=require_opposite_common_factor,
            )
            if not held:
                continue
            counts["later_inside_retests"] += 1
            oi_pass, state_details = v53.state_oi_contraction(
                btc,
                event_index,
                index,
                float(parent.details["past_only_oi_contraction_cutoff"]),
                require_contraction=True,
            )
            if not oi_pass:
                continue
            counts["oi_contraction_retained"] += 1
            signal_index = index
            retest_details = details
            oi_details = state_details
            break
        if signal_index is None:
            continue
        if signal_index - last_signal <= COOLDOWN_BARS:
            counts["cooldown_suppressed"] += 1
            continue

        signal_row = btc.iloc[signal_index]
        signal_atr = float(signal_row["atr"])
        path = btc.iloc[event_index : signal_index + 1]
        path_extreme = (
            float(path["low"].min())
            if reversal_side > 0
            else float(path["high"].max())
        )
        stop = path_extreme - reversal_side * stop_buffer_atr * signal_atr
        entry = float(signal_row["close"])
        if not math.isfinite(stop) or reversal_side * (entry - stop) <= 0.0:
            continue
        priority = (
            abs(float(failure_details["opposite_common_return_factor"]))
            * abs(float(failure_details["opposite_common_flow_factor"]))
            * max(float(signal_row["notional_60s"]), 1.0)
        )
        fvg_low, fvg_high = inverse_fvg
        details: dict[str, Any] = {
            "compiler": "candidate-04-v54-accepted-auction-failure-v1",
            "market_cause": (
                "a common-factor accepted and apparently defended external "
                "liquidity sweep lost the old boundary with opposite common "
                "flow, index-proxy displacement and an inverse FVG, trapping "
                "the accepted-auction participants"
            ),
            "state_sequence": [
                "V53_COMMON_FACTOR_DELEVERAGING_PARENT_STATE",
                "APPARENT_CONTINUATION_RETEST",
                "OLD_EXTERNAL_BOUNDARY_FAILURE",
                "OPPOSITE_COMMON_FACTOR_AND_INDEX_PROXY_TURN",
                "OPPOSITE_MSS_DISPLACEMENT_INVERSE_FVG",
                "SEPARATE_LATER_INSIDE_RETEST",
                "OI_CONTRACTION_REMAINS_UNREPAIRED",
            ],
            "parent_event_index": event_index,
            "parent_retest_index": parent_retest_index,
            "failure_index": failure_index,
            "signal_index": signal_index,
            "parent_trade_direction": parent_side,
            "trade_direction": reversal_side,
            "external_boundary": boundary,
            "parent_sweep_extreme": sweep_extreme,
            "failure_internal_boundary": internal_boundary,
            "inverse_fvg_low": fvg_low,
            "inverse_fvg_high": fvg_high,
            "inverse_fvg_midpoint": 0.5 * (fvg_low + fvg_high),
            "structural_stop": stop,
            "minimum_target_net_r": 1.20,
            "risk_multiplier": 1.0,
            **failure_details,
            **retest_details,
            **oi_details,
        }
        output.append(
            Candidate(
                parent_event_index=event_index,
                parent_retest_index=parent_retest_index,
                failure_index=failure_index,
                signal_index=signal_index,
                side=reversal_side,
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
                    item.parent_retest_index,
                    item.failure_index,
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
        "candidate": "candidate-04-v54-common-factor-accepted-auction-failure-reversal",
        "compiler": "candidate-04-v54-accepted-auction-failure-v1",
        "written_signals": sum(len(rows) for rows in rows_by_symbol.values()),
        "signals_by_symbol": {
            symbol: len(rows) for symbol, rows in rows_by_symbol.items()
        },
        "route_counts": counts,
        "scenario_contract": {
            "parent": "complete V53 accepted-auction/deleveraging state",
            "failure": (
                "old boundary loss plus opposite index-proxy/common-factor MSS "
                "and inverse FVG"
            ),
            "entry": "separate later inside retest of inverse FVG",
            "inventory": "parent OI contraction must remain unrepaired",
            "invalidation": "beyond complete parent-sweep-to-entry excursion",
            "target": "nearest pre-existing intact opposite external liquidity",
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
    parser.add_argument("--disable-opposite-common-factor", action="store_true")
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
        require_opposite_common_factor=not args.disable_opposite_common_factor,
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
