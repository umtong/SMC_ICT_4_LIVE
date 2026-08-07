#!/usr/bin/env python3
"""Causal BTC-leader information transfer into ETH/SOL/XRP followers.

The candidate does not fit a cross-impact coefficient. It identifies a complete
state sequence using only completed observations:

1. BTC produces a tail 60-second return with aligned executed flow, efficient
   price impact, positive futures-index basis change and material OI creation;
2. a follower has not yet broken its pre-event five-minute structure and its
   direction-adjusted response remains below its own shifted median absolute
   one-minute return;
3. within five later completed minutes the follower independently breaks that
   structure with aligned flow, return, basis and material state-interval OI
   creation.

If several followers confirm, the earliest confirmation wins; ties select the
highest completed confirmation notional. Stops lie beyond the full leader-event
to follower-confirmation excursion. The compiler emits per-symbol intents only.
A separate one-account NautilusTrader runner owns orders, fills, costs, risk,
positions, PnL and NAV.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from pathlib import Path
import json
import math
import os
from typing import Any

import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
FOLLOWERS = ("ETHUSDT", "SOLUSDT", "XRPUSDT")
SCENARIO = "CROSS_MARKET_INFORMATION_TRANSFER_CATCHUP"
THRESHOLD_WINDOW = 720
THRESHOLD_MIN = 240
CONFIRMATION_BARS = 5
STRUCTURE_BARS = 5
COOLDOWN_BARS = 15


@dataclass(frozen=True, slots=True)
class Candidate:
    symbol: str
    leader_index: int
    signal_index: int
    side: int
    stop_level: float
    confirmation_notional: float
    details: dict[str, Any]


def shifted_quantile(
    series: pd.Series,
    quantile: float,
) -> pd.Series:
    return (
        series.astype(float)
        .replace([math.inf, -math.inf], float("nan"))
        .shift(1)
        .rolling(THRESHOLD_WINDOW, min_periods=THRESHOLD_MIN)
        .quantile(quantile)
    )


def shifted_positive_median(series: pd.Series) -> pd.Series:
    positive = series.astype(float).where(series.astype(float) > 0.0)
    return (
        positive.shift(1)
        .rolling(THRESHOLD_WINDOW, min_periods=THRESHOLD_MIN)
        .median()
    )


def leader_information_event(
    row: pd.Series,
    *,
    return_cutoff: float,
    flow_cutoff: float,
    efficiency_cutoff: float,
    oi_cutoff: float,
) -> tuple[bool, int]:
    values = (
        float(row["ret_60s_bps"]),
        float(row["flow_60s"]),
        float(row["eff_60s"]),
        float(row["basis_change_5m"]),
        float(row["metric_oi_change_15m"]),
        return_cutoff,
        flow_cutoff,
        efficiency_cutoff,
        oi_cutoff,
    )
    if not all(math.isfinite(value) for value in values):
        return False, 0
    side = 1 if values[0] > 0.0 else -1 if values[0] < 0.0 else 0
    if side == 0:
        return False, 0
    passed = (
        abs(values[0]) >= return_cutoff > 0.0
        and side * values[1] >= flow_cutoff > 0.0
        and values[2] >= efficiency_cutoff
        and side * values[3] > 0.0
        and values[4] >= oi_cutoff > 0.0
    )
    return passed, side


def follower_underreacted(
    data: pd.DataFrame,
    index: int,
    side: int,
    median_absolute_return: float,
) -> tuple[bool, float]:
    if not (
        STRUCTURE_BARS <= index < len(data)
        and side in (-1, 1)
        and math.isfinite(median_absolute_return)
        and median_absolute_return > 0.0
    ):
        return False, float("nan")
    row = data.iloc[index]
    response = side * float(row["ret_60s_bps"])
    history = data.iloc[index - STRUCTURE_BARS : index]
    boundary = (
        float(history["high"].max())
        if side > 0
        else float(history["low"].min())
    )
    close = float(row["close"])
    structure_unbroken = side * (close - boundary) <= 0.0
    underreaction = response < median_absolute_return
    return bool(structure_unbroken and underreaction), boundary


def state_oi_creation(
    open_interest: pd.Series,
    start_index: int,
    end_index: int,
    cutoff: float,
) -> tuple[bool, float]:
    if not (
        0 <= start_index < end_index < len(open_interest)
        and math.isfinite(cutoff)
        and cutoff > 0.0
    ):
        return False, float("nan")
    start = float(open_interest.iloc[start_index])
    end = float(open_interest.iloc[end_index])
    if not all(math.isfinite(value) and value > 0.0 for value in (start, end)):
        return False, float("nan")
    change = end / start - 1.0
    return change >= cutoff, change


def follower_confirmation(
    data: pd.DataFrame,
    leader_index: int,
    signal_index: int,
    side: int,
    structure: float,
    flow_cutoff: float,
    oi_cutoff: float,
) -> tuple[bool, dict[str, float]]:
    if not (
        leader_index < signal_index < len(data)
        and side in (-1, 1)
        and all(math.isfinite(value) for value in (structure, flow_cutoff, oi_cutoff))
    ):
        return False, {}
    row = data.iloc[signal_index]
    close = float(row["close"])
    flow = side * float(row["flow_60s"])
    return_bps = side * float(row["ret_60s_bps"])
    basis = side * float(row["basis_change_5m"])
    notional = float(row["notional_60s"])
    structure_broken = side * (close - structure) > 0.0
    oi_passed, oi_change = state_oi_creation(
        data["metric_sum_open_interest"].astype(float),
        max(leader_index - 1, 0),
        signal_index,
        oi_cutoff,
    )
    values = (flow, return_bps, basis, notional, oi_change)
    passed = bool(
        structure_broken
        and all(math.isfinite(value) for value in values)
        and flow >= flow_cutoff > 0.0
        and return_bps > 0.0
        and basis > 0.0
        and oi_passed
    )
    return passed, {
        "follower_structure": structure,
        "follower_structure_broken": structure_broken,
        "follower_directional_flow_60s": flow,
        "follower_directional_return_60s_bps": return_bps,
        "follower_directional_basis_change_5m_bps": basis,
        "follower_state_open_interest_change": oi_change,
        "follower_confirmation_notional_60s": notional,
        "follower_flow_cutoff": flow_cutoff,
        "follower_oi_creation_cutoff": oi_cutoff,
    }


def select_candidate(candidates: list[Candidate]) -> Candidate | None:
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda item: (
            item.signal_index,
            -item.confirmation_notional,
            FOLLOWERS.index(item.symbol),
        ),
    )


def structural_stop(
    data: pd.DataFrame,
    start_index: int,
    end_index: int,
    side: int,
    stop_buffer_atr: float,
) -> float:
    segment = data.iloc[start_index : end_index + 1]
    atr = float(data["atr"].iloc[end_index])
    if segment.empty or not math.isfinite(atr) or atr <= 0.0:
        return float("nan")
    extreme = (
        float(segment["low"].min())
        if side > 0
        else float(segment["high"].max())
    )
    return extreme - side * stop_buffer_atr * atr


def collect_candidates(
    frames: dict[str, pd.DataFrame],
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    stop_buffer_atr: float,
) -> tuple[list[Candidate], dict[str, Any]]:
    leader = frames["BTCUSDT"]
    leader_thresholds = {
        "return": shifted_quantile(leader["ret_60s_bps"].abs(), 0.90),
        "flow": shifted_quantile(leader["flow_60s"].abs(), 0.75),
        "efficiency": shifted_quantile(leader["eff_60s"], 0.70),
        "oi": shifted_positive_median(leader["metric_oi_change_15m"]),
    }
    follower_thresholds: dict[str, dict[str, pd.Series]] = {}
    for symbol in FOLLOWERS:
        frame = frames[symbol]
        follower_thresholds[symbol] = {
            "median_return": shifted_quantile(frame["ret_60s_bps"].abs(), 0.50),
            "flow": shifted_quantile(frame["flow_60s"].abs(), 0.75),
            "oi": shifted_positive_median(frame["metric_oi_change_5m"]),
        }

    counts: dict[str, Any] = {
        "leader_information_events": 0,
        "leader_events_without_underreaction": 0,
        "underreaction_states": {symbol: 0 for symbol in FOLLOWERS},
        "confirmed_followers": {symbol: 0 for symbol in FOLLOWERS},
        "selected_followers": {symbol: 0 for symbol in FOLLOWERS},
        "leader_events_without_confirmation": 0,
        "cooldown_suppressed": 0,
    }
    selected: list[Candidate] = []
    last_selected = -10**9

    for index, timestamp in enumerate(leader.index):
        if timestamp < evaluation_start or timestamp > evaluation_end:
            continue
        if index - last_selected < COOLDOWN_BARS:
            continue
        passed, side = leader_information_event(
            leader.iloc[index],
            return_cutoff=float(leader_thresholds["return"].iloc[index]),
            flow_cutoff=float(leader_thresholds["flow"].iloc[index]),
            efficiency_cutoff=float(leader_thresholds["efficiency"].iloc[index]),
            oi_cutoff=float(leader_thresholds["oi"].iloc[index]),
        )
        if not passed:
            continue
        counts["leader_information_events"] += 1
        follower_states: list[tuple[str, float]] = []
        for symbol in FOLLOWERS:
            frame = frames[symbol]
            underreacted, boundary = follower_underreacted(
                frame,
                index,
                side,
                float(follower_thresholds[symbol]["median_return"].iloc[index]),
            )
            if underreacted:
                follower_states.append((symbol, boundary))
                counts["underreaction_states"][symbol] += 1
        if not follower_states:
            counts["leader_events_without_underreaction"] += 1
            continue

        confirmations: list[Candidate] = []
        upper = min(index + CONFIRMATION_BARS, len(leader) - 2)
        for symbol, boundary in follower_states:
            frame = frames[symbol]
            for signal_index in range(index + 1, upper + 1):
                confirmed, details = follower_confirmation(
                    frame,
                    index,
                    signal_index,
                    side,
                    boundary,
                    float(follower_thresholds[symbol]["flow"].iloc[signal_index]),
                    float(follower_thresholds[symbol]["oi"].iloc[index]),
                )
                if not confirmed:
                    continue
                stop = structural_stop(
                    frame,
                    index,
                    signal_index,
                    side,
                    stop_buffer_atr,
                )
                close = float(frame["close"].iloc[signal_index])
                if not math.isfinite(stop) or side * (close - stop) <= 0.0:
                    continue
                leader_row = leader.iloc[index]
                full_details = {
                    **details,
                    "leader_symbol": "BTCUSDT",
                    "leader_event_index": index,
                    "leader_event_time": timestamp.isoformat(),
                    "leader_side": side,
                    "leader_return_60s_bps": float(leader_row["ret_60s_bps"]),
                    "leader_flow_60s": float(leader_row["flow_60s"]),
                    "leader_efficiency_60s": float(leader_row["eff_60s"]),
                    "leader_basis_change_5m_bps": float(leader_row["basis_change_5m"]),
                    "leader_oi_change_15m": float(leader_row["metric_oi_change_15m"]),
                    "leader_return_cutoff": float(leader_thresholds["return"].iloc[index]),
                    "leader_flow_cutoff": float(leader_thresholds["flow"].iloc[index]),
                    "leader_efficiency_cutoff": float(leader_thresholds["efficiency"].iloc[index]),
                    "leader_oi_creation_cutoff": float(leader_thresholds["oi"].iloc[index]),
                    "follower_symbol": symbol,
                    "follower_confirmation_index": signal_index,
                    "confirmation_delay_bars": signal_index - index,
                    "compiler": "candidate-04-cross-market-information-transfer-v1",
                }
                confirmations.append(
                    Candidate(
                        symbol=symbol,
                        leader_index=index,
                        signal_index=signal_index,
                        side=side,
                        stop_level=stop,
                        confirmation_notional=float(details["follower_confirmation_notional_60s"]),
                        details=full_details,
                    )
                )
                counts["confirmed_followers"][symbol] += 1
                break
        chosen = select_candidate(confirmations)
        if chosen is None:
            counts["leader_events_without_confirmation"] += 1
            continue
        if chosen.signal_index - last_selected < COOLDOWN_BARS:
            counts["cooldown_suppressed"] += 1
            continue
        selected.append(chosen)
        counts["selected_followers"][chosen.symbol] += 1
        last_selected = chosen.signal_index
    return selected, counts


def load_frames(
    rich_root: Path,
    config_root: Path,
    kline_root: Path,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    frames: dict[str, pd.DataFrame] = {}
    nt_frames: dict[str, pd.DataFrame] = {}
    for symbol in SYMBOLS:
        config = v22.Config.load(config_root / f"{symbol}.json")
        data, nt_frame = v22._load_data(
            rich_root / symbol,
            kline_root / symbol,
            evaluation_start,
            evaluation_end,
            config,
            download_klines=True,
        )
        frames[symbol] = data
        nt_frames[symbol] = nt_frame
    common = frames["BTCUSDT"].index
    for symbol in SYMBOLS[1:]:
        common = common.intersection(frames[symbol].index)
    if common.empty:
        raise RuntimeError("four-symbol rich streams have no common timestamps")
    for symbol in SYMBOLS:
        frames[symbol] = frames[symbol].loc[common]
        nt_frames[symbol] = nt_frames[symbol].loc[common]
    return frames, nt_frames


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
    rows_by_symbol = {symbol: [] for symbol in SYMBOLS}
    for item in candidates:
        timestamp = frames[item.symbol].index[item.signal_index]
        if not evaluation_start <= timestamp <= evaluation_end:
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
                "event_indices": [item.leader_index, item.signal_index],
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
                {
                    "candidate": "candidate-04-v48-cross-market-information-transfer",
                    "symbol": symbol,
                    "written_signals": len(rows),
                    "route_counts": counts,
                    "performance_calculated": False,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    aggregate = {
        "candidate": "candidate-04-v48-cross-market-information-transfer",
        "compiler": "candidate-04-cross-market-information-transfer-v1",
        "leader": "BTCUSDT",
        "followers": list(FOLLOWERS),
        "written_signals": sum(len(rows) for rows in rows_by_symbol.values()),
        "signals_by_symbol": {symbol: len(rows) for symbol, rows in rows_by_symbol.items()},
        "route_counts": counts,
        "scenario_contract": {
            "leader": "tail efficient BTC flow/return with basis alignment and material OI creation",
            "underreaction": "follower pre-event structure unbroken and response below own past median absolute return",
            "confirmation": "separate follower structure break with flow, return, basis and state-interval OI creation",
            "selection": "earliest confirmation, then highest completed confirmation notional",
            "stop": "full leader-event to follower-confirmation excursion",
            "target": "pre-existing external liquidity selected causally before Nautilus submission",
            "execution": "one-account NautilusTrader BacktestNode",
        },
        "constants": {
            "threshold_window": THRESHOLD_WINDOW,
            "threshold_min_periods": THRESHOLD_MIN,
            "confirmation_bars": CONFIRMATION_BARS,
            "structure_bars": STRUCTURE_BARS,
            "cooldown_bars": COOLDOWN_BARS,
        },
    }
    (output / "summary.json").write_text(
        json.dumps(aggregate, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(aggregate, indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rich-root", type=Path, required=True)
    parser.add_argument("--config-root", type=Path, required=True)
    parser.add_argument("--kline-root", type=Path, required=True)
    parser.add_argument("--evaluation-start", required=True)
    parser.add_argument("--evaluation-end", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stop-buffer-atr", type=float, default=0.08)
    args = parser.parse_args()
    evaluation_start = pd.Timestamp(args.evaluation_start, tz="UTC")
    evaluation_end = pd.Timestamp(args.evaluation_end, tz="UTC") + pd.Timedelta(hours=23, minutes=59)
    frames, nt_frames = load_frames(
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


if __name__ == "__main__":
    main()
