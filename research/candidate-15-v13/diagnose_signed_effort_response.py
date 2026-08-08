#!/usr/bin/env python3
"""Candidate 15 V13 causal signed-effort/result state diagnostic.

The diagnostic separates causal roles:

1. Context / attack: prior-extreme signed taker effort on one completed minute.
2. Latent state: the same minute's *result* relative to the signed effort.
3. Transition: a separately completed next minute with independent aggressor
   initiative and price progress.
4. Evaluation: forward return starts only at the transition close.

It deliberately does not call bar-level trade-flow "displayed-liquidity
absorption" because public depth is unavailable on these frozen intervals. The
state names are therefore FLOW_RESULT_ACCEPTANCE and
FLOW_RESULT_FAILURE_REVERSAL. This is a mechanism diagnostic, not a backtest or
NAV claim.
"""
from __future__ import annotations

import argparse
from collections import Counter, deque
from dataclasses import asdict, dataclass
from datetime import date, timedelta
import json
from math import isfinite, log
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import diagnose_cross_predictive_spillover as common
from run_diagnostic import _load_candidate15_v11_runner

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
CANDIDATE15 = REPO / "research" / "candidate-15"
SYMBOLS = common.SYMBOLS
ROLLING_MINUTES = 1440
MIN_HISTORY = 360
RESPONSE_MINUTES = 10
URGENT_ROUND_TRIP_COST = 0.0016


@dataclass(frozen=True, slots=True)
class AttackState:
    interval: str
    index: int
    ts_ns: int
    timestamp: str
    symbol: str
    attack_direction: int
    family: str
    effort_score: float
    effort_threshold: float
    normalized_effort: float
    signed_flow: float
    volume_ratio: float
    directional_clv: float
    directional_body_efficiency: float
    range_fraction: float
    event_open: float
    event_high: float
    event_low: float
    event_close: float


@dataclass(frozen=True, slots=True)
class Forecast:
    interval: str
    event_ts_ns: int
    transition_ts_ns: int
    event_timestamp: str
    transition_timestamp: str
    symbol: str
    family: str
    attack_direction: int
    trade_direction: int
    effort_score: float
    normalized_effort: float
    event_directional_clv: float
    event_directional_body_efficiency: float
    event_range_fraction: float
    transition_signed_flow: float
    transition_directional_body_efficiency: float
    transition_midpoint_progress: float
    response_10m: float
    directional_response_10m: float
    directional_response_after_16bps: float
    mfe_10m: float
    mae_10m: float


def _quantile(history: deque[float], probability: float) -> float | None:
    if len(history) < MIN_HISTORY:
        return None
    value = float(np.quantile(np.asarray(history, dtype="float64"), probability))
    return value if isfinite(value) else None


def _sign(value: float) -> int:
    return 1 if value > 0.0 else -1 if value < 0.0 else 0


def _load(
    interval: str,
    data_dir: Path,
) -> tuple[pd.DatetimeIndex, dict[str, dict[str, np.ndarray]], dict[str, Any]]:
    protocol = json.loads((CANDIDATE15 / "protocol-v11.json").read_text(encoding="utf-8"))
    selected = protocol["selection"]["intervals"][interval]
    start = date.fromisoformat(selected["start"])
    end_exclusive = date.fromisoformat(selected["end_exclusive"])
    warmup_start = start - timedelta(days=max(3, int(protocol["selection"]["warmup_days"])))
    runner = _load_candidate15_v11_runner()
    frames: dict[str, pd.DataFrame] = {}
    manifests: list[dict[str, Any]] = []
    for symbol in SYMBOLS:
        frame, manifest = runner.load_symbol_bars(
            symbol,
            warmup_start,
            end_exclusive - timedelta(days=1),
            data_dir,
        )
        frames[symbol] = frame
        manifests.extend(manifest)
    index, arrays = common._aligned_arrays(frames)
    # V12 common arrays intentionally kept only universal fields. V13 requires
    # volume; align it through the same synchronized index without changing the
    # audited loader.
    for symbol, frame in frames.items():
        arrays[symbol]["volume"] = frame.loc[index, "volume"].to_numpy(dtype="float64")
    metadata = {
        "interval": interval,
        "start": start.isoformat(),
        "end_exclusive": end_exclusive.isoformat(),
        "warmup_start": warmup_start.isoformat(),
        "manifest_files": len(manifests),
        "manifest_sha256": sorted(item["sha256"] for item in manifests),
    }
    return index, arrays, metadata


def _bar_geometry(
    arrays: dict[str, dict[str, np.ndarray]],
    symbol: str,
    i: int,
    pressure_direction: int,
) -> tuple[float, float, float] | None:
    open_ = float(arrays[symbol]["open"][i])
    high = float(arrays[symbol]["high"][i])
    low = float(arrays[symbol]["low"][i])
    close = float(arrays[symbol]["close"][i])
    if min(open_, high, low, close) <= 0.0 or high <= low:
        return None
    span = high - low
    clv = (2.0 * close - high - low) / span
    body_efficiency = (close - open_) / span
    range_fraction = span / close
    directional_clv = pressure_direction * clv
    directional_body = pressure_direction * body_efficiency
    if not all(isfinite(v) for v in (directional_clv, directional_body, range_fraction)):
        return None
    return directional_clv, directional_body, range_fraction


def _transition(
    state: AttackState,
    arrays: dict[str, dict[str, np.ndarray]],
    i: int,
) -> tuple[int, float, float, float] | None:
    symbol = state.symbol
    open_ = float(arrays[symbol]["open"][i])
    high = float(arrays[symbol]["high"][i])
    low = float(arrays[symbol]["low"][i])
    close = float(arrays[symbol]["close"][i])
    signed_flow = float(arrays[symbol]["signed_flow"][i])
    if min(open_, high, low, close) <= 0.0 or high <= low:
        return None
    midpoint = 0.5 * (state.event_high + state.event_low)
    attack = state.attack_direction
    if state.family == "FLOW_RESULT_ACCEPTANCE_CONTINUATION":
        trade_direction = attack
        flow_ok = _sign(signed_flow) == trade_direction
        if trade_direction > 0:
            price_ok = close > state.event_close and low >= midpoint
            midpoint_progress = (close - midpoint) / state.event_close
        else:
            price_ok = close < state.event_close and high <= midpoint
            midpoint_progress = (midpoint - close) / state.event_close
    elif state.family == "FLOW_RESULT_FAILURE_REVERSAL":
        trade_direction = -attack
        flow_ok = _sign(signed_flow) == trade_direction
        if trade_direction > 0:
            price_ok = close > midpoint and close > open_
            midpoint_progress = (close - midpoint) / state.event_close
        else:
            price_ok = close < midpoint and close < open_
            midpoint_progress = (midpoint - close) / state.event_close
    else:
        return None
    if not flow_ok or not price_ok:
        return None
    body = trade_direction * (close - open_) / (high - low)
    if body <= 0.0 or midpoint_progress <= 0.0:
        return None
    return trade_direction, signed_flow, body, midpoint_progress


def _summarize(rows: list[Forecast]) -> dict[str, Any]:
    if not rows:
        return {
            "forecasts": 0,
            "directional_hit_rate": 0.0,
            "mean_directional_response_10m_bps": 0.0,
            "median_directional_response_10m_bps": 0.0,
            "mean_after_16bps_bps": 0.0,
            "median_after_16bps_bps": 0.0,
            "median_mfe_10m_bps": 0.0,
            "median_mae_10m_bps": 0.0,
        }
    directional = np.asarray([row.directional_response_10m for row in rows])
    after = np.asarray([row.directional_response_after_16bps for row in rows])
    mfe = np.asarray([row.mfe_10m for row in rows])
    mae = np.asarray([row.mae_10m for row in rows])
    return {
        "forecasts": len(rows),
        "directional_hit_rate": float(np.mean(directional > 0.0)),
        "mean_directional_response_10m_bps": float(np.mean(directional) * 10000.0),
        "median_directional_response_10m_bps": float(np.median(directional) * 10000.0),
        "mean_after_16bps_bps": float(np.mean(after) * 10000.0),
        "median_after_16bps_bps": float(np.median(after) * 10000.0),
        "median_mfe_10m_bps": float(np.median(mfe) * 10000.0),
        "median_mae_10m_bps": float(np.median(mae) * 10000.0),
    }


def diagnose(interval: str, output: Path) -> dict[str, Any]:
    index, arrays, metadata = _load(interval, output / "data")
    evaluation_start = pd.Timestamp(metadata["start"], tz="UTC")
    evaluation_end = pd.Timestamp(metadata["end_exclusive"], tz="UTC")

    volume_history = {symbol: deque(maxlen=ROLLING_MINUTES) for symbol in SYMBOLS}
    effort_history = {symbol: deque(maxlen=ROLLING_MINUTES) for symbol in SYMBOLS}
    directional_clv_history = {symbol: deque(maxlen=ROLLING_MINUTES) for symbol in SYMBOLS}
    range_history = {symbol: deque(maxlen=ROLLING_MINUTES) for symbol in SYMBOLS}
    prior_effort_extreme = {symbol: False for symbol in SYMBOLS}
    pending_state: AttackState | None = None
    global_cooldown_until = -1
    forecasts: list[Forecast] = []
    states: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()

    for i, timestamp in enumerate(index):
        # A transition is assessed before the current completed bar can become a
        # new attack state, enforcing separate causal observations.
        if pending_state is not None and i == pending_state.index + 1:
            transition = _transition(pending_state, arrays, i)
            if transition is None:
                counts[f"transition_rejected::{pending_state.family}"] += 1
            elif evaluation_start <= timestamp < evaluation_end:
                trade_direction, signed_flow, body, midpoint_progress = transition
                geometry = common._future_geometry(arrays, pending_state.symbol, i, trade_direction)
                if geometry is None:
                    counts["transition_future_path_incomplete"] += 1
                else:
                    response, mfe, mae = geometry
                    directional_response = trade_direction * response
                    forecasts.append(
                        Forecast(
                            interval=interval,
                            event_ts_ns=pending_state.ts_ns,
                            transition_ts_ns=int(timestamp.value),
                            event_timestamp=pending_state.timestamp,
                            transition_timestamp=timestamp.isoformat(),
                            symbol=pending_state.symbol,
                            family=pending_state.family,
                            attack_direction=pending_state.attack_direction,
                            trade_direction=trade_direction,
                            effort_score=pending_state.effort_score,
                            normalized_effort=pending_state.normalized_effort,
                            event_directional_clv=pending_state.directional_clv,
                            event_directional_body_efficiency=pending_state.directional_body_efficiency,
                            event_range_fraction=pending_state.range_fraction,
                            transition_signed_flow=signed_flow,
                            transition_directional_body_efficiency=body,
                            transition_midpoint_progress=midpoint_progress,
                            response_10m=response,
                            directional_response_10m=directional_response,
                            directional_response_after_16bps=directional_response - URGENT_ROUND_TRIP_COST,
                            mfe_10m=mfe,
                            mae_10m=mae,
                        )
                    )
                    counts[f"transition_accepted::{pending_state.family}"] += 1
                    global_cooldown_until = i + RESPONSE_MINUTES
            pending_state = None

        candidates: list[AttackState] = []
        for symbol in SYMBOLS:
            volume = float(arrays[symbol]["volume"][i])
            signed_flow = float(arrays[symbol]["signed_flow"][i])
            pressure_direction = _sign(signed_flow)
            volume_median = _quantile(volume_history[symbol], 0.50)
            effort_threshold = _quantile(effort_history[symbol], 0.95)
            clv_q25 = _quantile(directional_clv_history[symbol], 0.25)
            clv_q75 = _quantile(directional_clv_history[symbol], 0.75)
            range_q50 = _quantile(range_history[symbol], 0.50)
            range_q75 = _quantile(range_history[symbol], 0.75)
            geometry = (
                _bar_geometry(arrays, symbol, i, pressure_direction)
                if pressure_direction != 0
                else None
            )
            effort_score: float | None = None
            if volume_median is not None and volume_median > 0.0 and volume > 0.0:
                volume_ratio = volume / volume_median
                effort_score = abs(signed_flow) * volume_ratio
            else:
                volume_ratio = 0.0

            above = bool(
                effort_score is not None
                and effort_threshold is not None
                and effort_threshold > 0.0
                and effort_score >= effort_threshold
                and pressure_direction != 0
            )
            first_passage = above and not prior_effort_extreme[symbol]
            prior_effort_extreme[symbol] = above

            if (
                first_passage
                and geometry is not None
                and None not in (clv_q25, clv_q75, range_q50, range_q75)
                and i >= global_cooldown_until
                and pending_state is None
            ):
                directional_clv, directional_body, range_fraction = geometry
                family: str | None = None
                if (
                    directional_clv >= float(clv_q75)
                    and directional_body > 0.0
                    and range_fraction >= float(range_q75)
                ):
                    family = "FLOW_RESULT_ACCEPTANCE_CONTINUATION"
                elif (
                    directional_clv <= float(clv_q25)
                    and directional_body <= 0.0
                    and range_fraction >= float(range_q50)
                ):
                    family = "FLOW_RESULT_FAILURE_REVERSAL"
                if family is None:
                    counts["attack_state_unresolved"] += 1
                else:
                    normalized_effort = effort_score / float(effort_threshold)
                    candidates.append(
                        AttackState(
                            interval=interval,
                            index=i,
                            ts_ns=int(timestamp.value),
                            timestamp=timestamp.isoformat(),
                            symbol=symbol,
                            attack_direction=pressure_direction,
                            family=family,
                            effort_score=effort_score,
                            effort_threshold=float(effort_threshold),
                            normalized_effort=normalized_effort,
                            signed_flow=signed_flow,
                            volume_ratio=volume_ratio,
                            directional_clv=directional_clv,
                            directional_body_efficiency=directional_body,
                            range_fraction=range_fraction,
                            event_open=float(arrays[symbol]["open"][i]),
                            event_high=float(arrays[symbol]["high"][i]),
                            event_low=float(arrays[symbol]["low"][i]),
                            event_close=float(arrays[symbol]["close"][i]),
                        )
                    )

            # Update all thresholds only after the current decision.
            if volume > 0.0 and isfinite(volume):
                volume_history[symbol].append(volume)
            if effort_score is not None and isfinite(effort_score):
                effort_history[symbol].append(effort_score)
            if geometry is not None:
                directional_clv_history[symbol].append(geometry[0])
                range_history[symbol].append(geometry[2])

        if candidates and pending_state is None and i >= global_cooldown_until:
            candidates.sort(key=lambda item: (item.normalized_effort, item.symbol), reverse=True)
            pending_state = candidates[0]
            states.append(asdict(pending_state))
            counts[f"attack_selected::{pending_state.family}"] += 1
            counts["attack_candidates_displaced_by_global_owner"] += len(candidates) - 1

    rows = [asdict(row) for row in forecasts]
    output.mkdir(parents=True, exist_ok=True)
    (output / "states.json").write_text(json.dumps(states, indent=2, sort_keys=True), encoding="utf-8")
    (output / "forecasts.json").write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    result = {
        "schema": "candidate-15-v13-signed-effort-result-diagnostic-v1",
        "diagnostic_only_not_account_backtest": True,
        "interval": interval,
        "metadata": metadata,
        "counts": dict(counts),
        "forecast_rows": len(rows),
        "overall": _summarize(forecasts),
        "by_family": {
            family: _summarize([row for row in forecasts if row.family == family])
            for family in (
                "FLOW_RESULT_ACCEPTANCE_CONTINUATION",
                "FLOW_RESULT_FAILURE_REVERSAL",
            )
        },
        "contract": {
            "attack_effort": "ABS_SIGNED_TAKER_IMBALANCE_X_PRIOR_MEDIAN_VOLUME_RATIO",
            "attack_threshold": "PRIOR_24H_Q95_FIRST_PASSAGE",
            "acceptance_result": "PRIOR_Q75_DIRECTIONAL_CLV_AND_POSITIVE_BODY_AND_PRIOR_Q75_RANGE",
            "failure_result": "PRIOR_Q25_DIRECTIONAL_CLV_AND_NONPOSITIVE_BODY_AND_PRIOR_MEDIAN_RANGE",
            "transition": "SEPARATELY_COMPLETED_NEXT_BAR_WITH_INDEPENDENT_SAME_OR_OPPOSITE_SIGNED_FLOW_AND_PRICE_PROGRESS",
            "evaluation_origin": "TRANSITION_CLOSE_NOT_ATTACK_CLOSE",
            "global_ownership": "MAX_NORMALIZED_EFFORT_ONE_PENDING_STATE_AND_ONE_10M_EPISODE",
            "cost_screen": "FROZEN_16BPS_URGENT_ROUND_TRIP_APPLIED_ONLY_TO_FORWARD_RESPONSE",
            "depth_claim": "NONE_BAR_TRADE_FLOW_IS_NOT_DISPLAYED_LIQUIDITY",
            "unresolved": "NO_TRADE",
        },
    }
    (output / "summary.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("interval", choices=("E01", "E02", "E03", "E04", "E05", "E06"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    diagnose(args.interval, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
