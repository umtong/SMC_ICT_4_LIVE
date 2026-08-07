#!/usr/bin/env python3
"""Candidate-04 V51: cross-market asymmetric liquidity-raid reversal.

This module is a causal scenario compiler, not a backtest engine. It emits
completed-observation trade intents only. NautilusTrader remains the sole
owner of orders, fills, fees, positions, risk, PnL and account NAV.

Market sequence:
  known BTC external dealing-range edge -> first BTC sweep -> at least two of
  ETH/SOL/XRP reject the analogous sweep -> BTC closes back inside -> futures
  and index-proxy reverse together -> pre-sweep internal structure breaks with
  displacement/FVG -> a later FVG retest holds -> reversal toward pre-existing
  opposite external liquidity.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

import cross_market_information_transfer_compiler as base
import cross_market_information_transfer_compiler_v2 as v2

SYMBOLS = base.SYMBOLS
FOLLOWERS = base.FOLLOWERS
SCENARIO = "CROSS_MARKET_ASYMMETRIC_LIQUIDITY_RAID_REVERSAL"
THRESHOLD_WINDOW = 720
THRESHOLD_MIN = 240
RANGE_MINUTES = 60
RECLAIM_BARS = 3
MSS_LOOKBACK = 5
DISPLACEMENT_BARS = 6
RETEST_BARS = 10
COOLDOWN_BARS = 20
MIN_PEER_REJECTIONS = 2


@dataclass(frozen=True, slots=True)
class Candidate:
    symbol: str
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


def causal_range_edges(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Pre-event 60-minute dealing-range edges, never including current bar."""
    high = (
        frame["high"].astype(float).shift(1)
        .rolling(RANGE_MINUTES, min_periods=RANGE_MINUTES).max()
    )
    low = (
        frame["low"].astype(float).shift(1)
        .rolling(RANGE_MINUTES, min_periods=RANGE_MINUTES).min()
    )
    return high, low


def directional_fvg(
    frame: pd.DataFrame,
    index: int,
    side: int,
    minimum_gap: float,
) -> tuple[float, float] | None:
    if index < 2 or side not in (-1, 1) or not math.isfinite(minimum_gap) or minimum_gap < 0.0:
        return None
    current = frame.iloc[index]
    two_back = frame.iloc[index - 2]
    if side > 0:
        low, high = float(two_back["high"]), float(current["low"])
    else:
        low, high = float(current["high"]), float(two_back["low"])
    if not all(math.isfinite(value) for value in (low, high)) or high - low < minimum_gap:
        return None
    return low, high


def peer_rejection_count(
    frames: dict[str, pd.DataFrame],
    edges: dict[str, tuple[pd.Series, pd.Series]],
    index: int,
    sweep_direction: int,
    peer_return_cutoffs: dict[str, pd.Series],
) -> tuple[int, dict[str, Any]]:
    rejected = 0
    details: dict[str, Any] = {}
    for symbol in FOLLOWERS:
        frame = frames[symbol]
        row = frame.iloc[index]
        upper, lower = edges[symbol]
        boundary = float(upper.iloc[index] if sweep_direction > 0 else lower.iloc[index])
        cutoff = float(peer_return_cutoffs[symbol].iloc[index])
        if not all(math.isfinite(value) for value in (boundary, cutoff)) or cutoff <= 0.0:
            details[symbol] = {"eligible": False}
            continue
        wick_swept = (
            float(row["high"]) > boundary
            if sweep_direction > 0
            else float(row["low"]) < boundary
        )
        directional_response = sweep_direction * float(row["ret_60s_bps"])
        accepted = wick_swept and directional_response >= cutoff
        is_rejection = not accepted
        rejected += int(is_rejection)
        details[symbol] = {
            "eligible": True,
            "analogous_boundary": boundary,
            "wick_swept": bool(wick_swept),
            "directional_response_bps": directional_response,
            "past_only_response_cutoff_bps": cutoff,
            "peer_rejected_sweep": bool(is_rejection),
        }
    return rejected, details


def common_reversal_confirmation(
    frame: pd.DataFrame,
    index: int,
    trade_side: int,
    internal_boundary: float,
    flow_cutoff: float,
    body_cutoff: float,
) -> tuple[bool, dict[str, float]]:
    if index < 2 or trade_side not in (-1, 1):
        return False, {}
    row = frame.iloc[index]
    atr = float(row["atr"])
    values = (
        atr,
        float(row["open"]),
        float(row["close"]),
        float(row["ret_60s_bps"]),
        float(row["flow_60s"]),
        float(row["basis_change_5m"]),
        internal_boundary,
        flow_cutoff,
        body_cutoff,
    )
    if not all(math.isfinite(value) for value in values) or atr <= 0.0 or flow_cutoff <= 0.0:
        return False, {}
    directional_return = trade_side * float(row["ret_60s_bps"])
    directional_flow = trade_side * float(row["flow_60s"])
    directional_index_proxy = trade_side * (
        float(row["ret_60s_bps"]) - float(row["basis_change_5m"])
    )
    body_atr = abs(float(row["close"]) - float(row["open"])) / atr
    mss = trade_side * (float(row["close"]) - internal_boundary) > 0.0
    passed = (
        directional_return > 0.0
        and directional_flow >= flow_cutoff
        and directional_index_proxy > 0.0
        and body_atr >= max(0.20, body_cutoff)
        and mss
    )
    return passed, {
        "directional_return_60s_bps": directional_return,
        "directional_flow_60s": directional_flow,
        "directional_index_proxy_60s_bps": directional_index_proxy,
        "displacement_body_atr": body_atr,
        "pre_sweep_internal_boundary": internal_boundary,
        "internal_structure_broken": float(mss),
        "flow_cutoff": flow_cutoff,
        "body_cutoff": body_cutoff,
    }


def fvg_retest_holds(
    frame: pd.DataFrame,
    index: int,
    side: int,
    fvg: tuple[float, float],
) -> bool:
    row = frame.iloc[index]
    low, high = fvg
    midpoint = 0.5 * (low + high)
    touched = float(row["high"]) >= low and float(row["low"]) <= high
    close_holds = float(row["close"]) >= midpoint if side > 0 else float(row["close"]) <= midpoint
    directional_close = side * (float(row["close"]) - float(row["open"])) >= 0.0
    return bool(touched and close_holds and directional_close)


def collect_candidates(
    frames: dict[str, pd.DataFrame],
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    stop_buffer_atr: float,
    minimum_peer_rejections: int = MIN_PEER_REJECTIONS,
) -> tuple[list[Candidate], dict[str, Any]]:
    btc = frames["BTCUSDT"]
    edges = {symbol: causal_range_edges(frame) for symbol, frame in frames.items()}
    peer_return_cutoffs = {
        symbol: shifted_quantile(frame["ret_60s_bps"].abs(), 0.70)
        for symbol, frame in frames.items()
    }
    flow_cutoff = shifted_quantile(btc["flow_60s"].abs(), 0.55)
    body_atr = (
        (btc["close"].astype(float) - btc["open"].astype(float)).abs()
        / btc["atr"].astype(float)
    )
    body_cutoff = shifted_quantile(body_atr, 0.55)

    counts: dict[str, Any] = {
        "btc_first_external_sweeps": 0,
        "peer_asymmetry_pass": 0,
        "range_reclaims": 0,
        "common_reversal_confirmations": 0,
        "displacement_fvgs": 0,
        "later_retests": 0,
        "qualified": 0,
        "minimum_peer_rejections": minimum_peer_rejections,
        "cooldown_suppressed": 0,
    }
    selected: list[Candidate] = []
    last_signal = -10**9

    for event_index, timestamp in enumerate(btc.index):
        if timestamp < evaluation_start or timestamp >= evaluation_end:
            continue
        if event_index < max(RANGE_MINUTES, MSS_LOOKBACK + 2):
            continue
        if event_index - last_signal <= COOLDOWN_BARS:
            continue
        row = btc.iloc[event_index]
        previous = btc.iloc[event_index - 1]
        upper = float(edges["BTCUSDT"][0].iloc[event_index])
        lower = float(edges["BTCUSDT"][1].iloc[event_index])
        atr = float(row["atr"])
        if not all(math.isfinite(value) for value in (upper, lower, atr)) or atr <= 0.0:
            continue
        high_sweep = (
            float(previous["close"]) <= upper
            and float(row["high"]) >= upper + 0.02 * atr
        )
        low_sweep = (
            float(previous["close"]) >= lower
            and float(row["low"]) <= lower - 0.02 * atr
        )
        if high_sweep == low_sweep:
            continue
        sweep_direction = 1 if high_sweep else -1
        boundary = upper if high_sweep else lower
        sweep_extreme = float(row["high"] if high_sweep else row["low"])
        counts["btc_first_external_sweeps"] += 1

        rejected_count, peer_details = peer_rejection_count(
            frames,
            edges,
            event_index,
            sweep_direction,
            peer_return_cutoffs,
        )
        if rejected_count < minimum_peer_rejections:
            continue
        counts["peer_asymmetry_pass"] += 1

        trade_side = -sweep_direction
        pre_segment = btc.iloc[event_index - MSS_LOOKBACK : event_index]
        internal_boundary = (
            float(pre_segment["low"].min())
            if trade_side < 0
            else float(pre_segment["high"].max())
        )
        reclaim_index: int | None = None
        for idx in range(
            event_index,
            min(event_index + RECLAIM_BARS, len(btc) - 1) + 1,
        ):
            close = float(btc["close"].iloc[idx])
            if (
                (sweep_direction > 0 and close < boundary)
                or (sweep_direction < 0 and close > boundary)
            ):
                reclaim_index = idx
                break
        if reclaim_index is None:
            continue
        counts["range_reclaims"] += 1

        confirmation_index: int | None = None
        confirmation_details: dict[str, float] = {}
        fvg: tuple[float, float] | None = None
        upper_confirmation = min(reclaim_index + DISPLACEMENT_BARS, len(btc) - 2)
        for idx in range(reclaim_index, upper_confirmation + 1):
            passed, details = common_reversal_confirmation(
                btc,
                idx,
                trade_side,
                internal_boundary,
                float(flow_cutoff.iloc[idx]),
                float(body_cutoff.iloc[idx]),
            )
            if not passed:
                continue
            current_atr = float(btc["atr"].iloc[idx])
            candidate_fvg = directional_fvg(
                btc,
                idx,
                trade_side,
                0.005 * current_atr,
            )
            if candidate_fvg is None:
                continue
            confirmation_index = idx
            confirmation_details = details
            fvg = candidate_fvg
            break
        if confirmation_index is None or fvg is None:
            continue
        counts["common_reversal_confirmations"] += 1
        counts["displacement_fvgs"] += 1

        signal_index: int | None = None
        upper_retest = min(confirmation_index + RETEST_BARS, len(btc) - 2)
        for idx in range(confirmation_index + 1, upper_retest + 1):
            current = btc.iloc[idx]
            if (
                (trade_side < 0 and float(current["high"]) >= sweep_extreme)
                or (trade_side > 0 and float(current["low"]) <= sweep_extreme)
            ):
                break
            if fvg_retest_holds(btc, idx, trade_side, fvg):
                signal_index = idx
                break
        if signal_index is None:
            continue
        counts["later_retests"] += 1
        if signal_index - last_signal <= COOLDOWN_BARS:
            counts["cooldown_suppressed"] += 1
            continue

        signal_atr = float(btc["atr"].iloc[signal_index])
        stop = sweep_extreme + sweep_direction * stop_buffer_atr * signal_atr
        entry = float(btc["close"].iloc[signal_index])
        if not math.isfinite(stop) or trade_side * (entry - stop) <= 0.0:
            continue
        fvg_low, fvg_high = fvg
        event_notional = float(btc["notional_60s"].iloc[event_index])
        priority = (
            rejected_count
            * abs(float(btc["ret_60s_bps"].iloc[event_index]))
            * max(event_notional, 1.0)
        )
        details: dict[str, Any] = {
            "compiler": "candidate-04-v51-asymmetric-liquidity-raid-v1",
            "market_cause": (
                "idiosyncratic BTC external-liquidity raid rejected by the "
                "common crypto market"
            ),
            "state_sequence": [
                "PRE_EVENT_60M_EXTERNAL_DEALING_RANGE",
                "BTC_FIRST_EXTERNAL_LIQUIDITY_SWEEP",
                "TWO_OF_THREE_PEERS_REJECT_ANALOGOUS_SWEEP",
                "BTC_REENTRY_INSIDE_OLD_RANGE",
                "FUTURES_INDEX_PROXY_COMMON_REVERSAL",
                "PRE_SWEEP_INTERNAL_MSS_WITH_DISPLACEMENT_FVG",
                "LATER_FVG_RETEST_HOLDS",
            ],
            "event_time": timestamp.isoformat(),
            "sweep_direction": sweep_direction,
            "trade_direction": trade_side,
            "external_boundary": boundary,
            "sweep_extreme": sweep_extreme,
            "event_extension_atr": (
                sweep_direction * (sweep_extreme - boundary) / atr
            ),
            "peer_rejection_count": rejected_count,
            "peer_states": peer_details,
            "reclaim_index": reclaim_index,
            "confirmation_index": confirmation_index,
            "signal_index": signal_index,
            "fvg_low": fvg_low,
            "fvg_high": fvg_high,
            "fvg_midpoint": 0.5 * (fvg_low + fvg_high),
            "structural_stop": stop,
            "minimum_target_net_r": 1.20,
            "risk_multiplier": 1.0,
            **confirmation_details,
        }
        selected.append(
            Candidate(
                symbol="BTCUSDT",
                event_index=event_index,
                signal_index=signal_index,
                side=trade_side,
                stop_level=stop,
                priority=priority,
                details=details,
            )
        )
        counts["qualified"] += 1
        last_signal = signal_index
    return selected, counts


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
        timestamp = frames[item.symbol].index[item.signal_index]
        if not evaluation_start <= timestamp < evaluation_end:
            continue
        observe_time = nt_frames[item.symbol].index[item.signal_index]
        rows_by_symbol[item.symbol].append(
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
    aggregate = {
        "candidate": "candidate-04-v51-asymmetric-liquidity-raid-reversal",
        "compiler": "candidate-04-v51-asymmetric-liquidity-raid-v1",
        "traded_symbol": "BTCUSDT",
        "context_symbols": list(FOLLOWERS),
        "written_signals": sum(len(rows) for rows in rows_by_symbol.values()),
        "signals_by_symbol": {
            symbol: len(rows) for symbol, rows in rows_by_symbol.items()
        },
        "route_counts": counts,
        "scenario_contract": {
            "liquidity": (
                "pre-event 60-minute dealing-range edge known before the sweep"
            ),
            "asymmetry": (
                "BTC sweeps while at least two of ETH/SOL/XRP reject the "
                "analogous normalized sweep"
            ),
            "confirmation": (
                "BTC range reentry, futures-index-proxy reversal, pre-sweep "
                "MSS and displacement/FVG"
            ),
            "entry": "a separate later FVG retest that holds",
            "invalidation": "beyond the complete liquidity-raid extreme",
            "target": (
                "nearest pre-existing intact opposite external liquidity; "
                "no measured move"
            ),
        },
    }
    (output / "summary.json").write_text(
        json.dumps(aggregate, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(aggregate, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rich-root", required=True, type=Path)
    parser.add_argument("--config-root", required=True, type=Path)
    parser.add_argument("--kline-root", required=True, type=Path)
    parser.add_argument("--evaluation-start", required=True)
    parser.add_argument("--evaluation-end", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--stop-buffer-atr", type=float, default=0.05)
    parser.add_argument(
        "--minimum-peer-rejections",
        type=int,
        default=MIN_PEER_REJECTIONS,
    )
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
        args.minimum_peer_rejections,
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
