#!/usr/bin/env python3
"""V50: reverse a follower expansion which fails as BTC completes discovery.

The V48/V49 same-direction information-transfer family failed economically:
post-break entries chased an already completed catch-up, while pre-break entries
had no cost-aware distance to the local boundary. This compiler tests the
opposite structural implication without changing the BTC information event.

Causal sequence:

1. BTC completes a tail, efficient, basis-aligned information event with
   material OI creation.
2. At the same completed minute a follower also expands in the BTC direction:
   its return and flow are above their own shifted 75th percentiles and its
   futures-index basis change aligns.
3. Within three later completed minutes, the follower closes through its event
   open in the opposite direction while opposite return, executed flow and basis
   change all align. This is a failed cross-market expansion, not a blind fade.
4. Entry is submitted on the next minute opposite the BTC event. The stop lies
   beyond the complete expansion-to-failure excursion.
5. No measured-move target is declared. The unchanged causal target registry
   must find pre-existing external liquidity with at least 1.2 net R before the
   Nautilus strategy can enter.

All thresholds are past-only. NautilusTrader remains the sole owner of orders,
fills, fees, positions, risk, margin, liquidation, PnL and NAV.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

import cross_market_information_transfer_compiler_v2 as adapter


base = adapter.base

CANDIDATE = "candidate-04-v50-cross-market-follower-failure-reversal"
COMPILER = "candidate-04-cross-market-follower-failure-reversal-v1"
SCENARIO = "CROSS_MARKET_FOLLOWER_EXPANSION_FAILURE_REVERSAL"
FOLLOWER_EXPANSION_QUANTILE = 0.75
FAILURE_BARS = 3
COOLDOWN_BARS = 15

_ORIGINAL_WRITE_OUTPUTS = base.write_outputs


def leader_thresholds(data: pd.DataFrame) -> dict[str, pd.Series]:
    return {
        "return": base.shifted_quantile(data["ret_60s_bps"].abs(), 0.90),
        "flow": base.shifted_quantile(data["flow_60s"].abs(), 0.75),
        "efficiency": base.shifted_quantile(data["eff_60s"], 0.70),
        "oi": base.shifted_positive_median(data["metric_oi_change_15m"]),
    }


def follower_failure_thresholds(data: pd.DataFrame) -> dict[str, pd.Series]:
    return {
        "return": base.shifted_quantile(
            data["ret_60s_bps"].abs(),
            FOLLOWER_EXPANSION_QUANTILE,
        ),
        "flow": base.shifted_quantile(
            data["flow_60s"].abs(),
            FOLLOWER_EXPANSION_QUANTILE,
        ),
    }


def follower_expansion(
    data: pd.DataFrame,
    index: int,
    leader_side: int,
    thresholds: dict[str, pd.Series],
) -> tuple[bool, dict[str, float | int | str]]:
    if leader_side not in (-1, 1) or not 0 <= index < len(data):
        return False, {}
    row = data.iloc[index]
    directional_return = leader_side * float(row["ret_60s_bps"])
    directional_flow = leader_side * float(row["flow_60s"])
    directional_basis = leader_side * float(row["basis_change_5m"])
    return_cutoff = float(thresholds["return"].iloc[index])
    flow_cutoff = float(thresholds["flow"].iloc[index])
    values = (
        directional_return,
        directional_flow,
        directional_basis,
        return_cutoff,
        flow_cutoff,
        float(row["open"]),
        float(row["high"]),
        float(row["low"]),
        float(row["close"]),
        float(row["notional_60s"]),
    )
    passed = bool(
        all(math.isfinite(value) for value in values)
        and directional_return >= return_cutoff > 0.0
        and directional_flow >= flow_cutoff > 0.0
        and directional_basis > 0.0
    )
    return passed, {
        "follower_expansion_index": index,
        "follower_expansion_open": values[5],
        "follower_expansion_high": values[6],
        "follower_expansion_low": values[7],
        "follower_expansion_close": values[8],
        "follower_expansion_notional_60s": values[9],
        "follower_directional_return_60s_bps": directional_return,
        "follower_directional_flow_60s": directional_flow,
        "follower_directional_basis_change_5m_bps": directional_basis,
        "follower_return_cutoff": return_cutoff,
        "follower_flow_cutoff": flow_cutoff,
    }


def expansion_failure(
    data: pd.DataFrame,
    event_index: int,
    index: int,
    leader_side: int,
    event_open: float,
) -> tuple[bool, dict[str, float | int | str]]:
    reversal_side = -leader_side
    if (
        reversal_side not in (-1, 1)
        or not (event_index < index < len(data))
        or not math.isfinite(event_open)
    ):
        return False, {}
    row = data.iloc[index]
    close = float(row["close"])
    directional_return = reversal_side * float(row["ret_60s_bps"])
    directional_flow = reversal_side * float(row["flow_60s"])
    directional_basis = reversal_side * float(row["basis_change_5m"])
    reclaim_distance = reversal_side * (close - event_open)
    notional = float(row["notional_60s"])
    values = (
        close,
        directional_return,
        directional_flow,
        directional_basis,
        reclaim_distance,
        notional,
    )
    passed = bool(
        all(math.isfinite(value) for value in values)
        and directional_return > 0.0
        and directional_flow > 0.0
        and directional_basis > 0.0
        and reclaim_distance > 0.0
    )
    return passed, {
        "follower_failure_index": index,
        "follower_failure_delay_bars": index - event_index,
        "follower_failure_close": close,
        "follower_failure_directional_return_60s_bps": directional_return,
        "follower_failure_directional_flow_60s": directional_flow,
        "follower_failure_directional_basis_change_5m_bps": directional_basis,
        "follower_failure_reclaim_distance": reclaim_distance,
        "follower_failure_notional_60s": notional,
    }


def collect_candidates(
    frames: dict[str, pd.DataFrame],
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    stop_buffer_atr: float,
) -> tuple[list[Any], dict[str, Any]]:
    leader = frames["BTCUSDT"]
    leader_threshold = leader_thresholds(leader)
    follower_threshold = {
        symbol: follower_failure_thresholds(frames[symbol])
        for symbol in base.FOLLOWERS
    }
    counts: dict[str, Any] = {
        "leader_information_events": 0,
        "leader_events_without_follower_expansion": 0,
        "leader_events_without_failure": 0,
        "invalid_failure_stop_geometry": 0,
        "cooldown_suppressed": 0,
        "follower_expansions": {symbol: 0 for symbol in base.FOLLOWERS},
        "confirmed_failures": {symbol: 0 for symbol in base.FOLLOWERS},
        "selected_followers": {symbol: 0 for symbol in base.FOLLOWERS},
    }
    selected: list[Any] = []
    last_signal_index = -10**12

    for leader_index in range(1, len(leader) - 1):
        timestamp = leader.index[leader_index]
        if timestamp < evaluation_start or timestamp > evaluation_end:
            continue
        leader_row = leader.iloc[leader_index]
        leader_passed, leader_side = base.leader_information_event(
            leader_row,
            return_cutoff=float(leader_threshold["return"].iloc[leader_index]),
            flow_cutoff=float(leader_threshold["flow"].iloc[leader_index]),
            efficiency_cutoff=float(
                leader_threshold["efficiency"].iloc[leader_index]
            ),
            oi_cutoff=float(leader_threshold["oi"].iloc[leader_index]),
        )
        if not leader_passed:
            continue
        counts["leader_information_events"] += 1
        leader_details = {
            "leader_symbol": "BTCUSDT",
            "leader_event_index": leader_index,
            "leader_event_time": timestamp.isoformat(),
            "leader_side": leader_side,
            "leader_return_60s_bps": float(leader_row["ret_60s_bps"]),
            "leader_flow_60s": float(leader_row["flow_60s"]),
            "leader_efficiency_60s": float(leader_row["eff_60s"]),
            "leader_basis_change_5m_bps": float(
                leader_row["basis_change_5m"]
            ),
            "leader_oi_change_15m": float(
                leader_row["metric_oi_change_15m"]
            ),
            "leader_return_cutoff": float(
                leader_threshold["return"].iloc[leader_index]
            ),
            "leader_flow_cutoff": float(
                leader_threshold["flow"].iloc[leader_index]
            ),
            "leader_efficiency_cutoff": float(
                leader_threshold["efficiency"].iloc[leader_index]
            ),
            "leader_oi_creation_cutoff": float(
                leader_threshold["oi"].iloc[leader_index]
            ),
        }
        candidates: list[Any] = []
        any_expansion = False

        for symbol in base.FOLLOWERS:
            data = frames[symbol]
            expanded, expansion_details = follower_expansion(
                data,
                leader_index,
                leader_side,
                follower_threshold[symbol],
            )
            if not expanded:
                continue
            any_expansion = True
            counts["follower_expansions"][symbol] += 1
            event_open = float(expansion_details["follower_expansion_open"])
            upper = min(leader_index + FAILURE_BARS, len(data) - 2)

            for signal_index in range(leader_index + 1, upper + 1):
                if data.index[signal_index] > evaluation_end:
                    break
                failed, failure_details = expansion_failure(
                    data,
                    leader_index,
                    signal_index,
                    leader_side,
                    event_open,
                )
                if not failed:
                    continue
                reversal_side = -leader_side
                stop = base.structural_stop(
                    data,
                    leader_index,
                    signal_index,
                    reversal_side,
                    stop_buffer_atr,
                )
                close = float(data["close"].iloc[signal_index])
                if (
                    not math.isfinite(stop)
                    or reversal_side * (close - stop) <= 0.0
                ):
                    counts["invalid_failure_stop_geometry"] += 1
                    break
                counts["confirmed_failures"][symbol] += 1
                details = {
                    **leader_details,
                    **expansion_details,
                    **failure_details,
                    "follower_symbol": symbol,
                    "trade_side": reversal_side,
                    "cross_market_failure_contract": (
                        "follower tail expansion with flow and basis alignment "
                        "closed back through its event open within three completed "
                        "minutes while opposite return, flow and basis aligned"
                    ),
                    "target_contract": (
                        "unchanged pre-existing external liquidity registry; no "
                        "measured move or compiler-created destination"
                    ),
                    "compiler": COMPILER,
                }
                candidates.append(
                    base.Candidate(
                        symbol=symbol,
                        leader_index=leader_index,
                        signal_index=signal_index,
                        side=reversal_side,
                        stop_level=stop,
                        confirmation_notional=float(
                            failure_details["follower_failure_notional_60s"]
                        ),
                        details=details,
                    )
                )
                break

        if not any_expansion:
            counts["leader_events_without_follower_expansion"] += 1
            continue
        chosen = base.select_candidate(candidates)
        if chosen is None:
            counts["leader_events_without_failure"] += 1
            continue
        if chosen.signal_index - last_signal_index < COOLDOWN_BARS:
            counts["cooldown_suppressed"] += 1
            continue
        selected.append(chosen)
        last_signal_index = chosen.signal_index
        counts["selected_followers"][chosen.symbol] += 1

    return selected, counts


def write_outputs(
    output: Path,
    candidates: list[Any],
    counts: dict[str, Any],
    frames: dict[str, pd.DataFrame],
    nt_frames: dict[str, pd.DataFrame],
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
) -> None:
    _ORIGINAL_WRITE_OUTPUTS(
        output,
        candidates,
        counts,
        frames,
        nt_frames,
        evaluation_start,
        evaluation_end,
    )
    for symbol in base.SYMBOLS:
        summary_path = output / symbol / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary.update(
            {
                "candidate": CANDIDATE,
                "scenario": SCENARIO,
                "rejected_family": "same-direction cross-market catch-up V48/V49",
            }
        )
        summary_path.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    aggregate_path = output / "summary.json"
    aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
    aggregate.update(
        {
            "candidate": CANDIDATE,
            "compiler": COMPILER,
            "scenario": SCENARIO,
            "controlled_change": (
                "replace same-direction follower catch-up with a completed "
                "cross-market expansion-failure reversal"
            ),
            "scenario_contract": {
                "leader": (
                    "tail efficient BTC flow and return with basis alignment "
                    "and material OI creation"
                ),
                "follower_expansion": (
                    "same-minute follower return and flow above own shifted 75th "
                    "percentiles with basis alignment"
                ),
                "failure": (
                    "within three completed minutes price crosses the expansion "
                    "open while opposite return, flow and basis align"
                ),
                "selection": "earliest completed failure, then highest failure notional",
                "stop": "complete follower expansion-to-failure excursion",
                "target": (
                    "pre-existing causal external liquidity selected before "
                    "Nautilus submission"
                ),
                "execution": "one-account NautilusTrader BacktestNode",
            },
            "constants": {
                "threshold_window": base.THRESHOLD_WINDOW,
                "threshold_min_periods": base.THRESHOLD_MIN,
                "follower_expansion_quantile": FOLLOWER_EXPANSION_QUANTILE,
                "failure_bars": FAILURE_BARS,
                "cooldown_bars": COOLDOWN_BARS,
            },
        }
    )
    aggregate_path.write_text(
        json.dumps(aggregate, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(aggregate, indent=2, sort_keys=True))


base.SCENARIO = SCENARIO
base.collect_candidates = collect_candidates
base.write_outputs = write_outputs


if __name__ == "__main__":
    base.main()
