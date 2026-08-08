#!/usr/bin/env python3
"""Causal clock-phase order-flow study over one continuous 2024-2026 span.

Candidate 23 established that the first minute of UTC quarter-hour openings can
carry medium-horizon information, but pooled all 96 daily clock phases.  This
candidate keeps the economic mechanism and adds an online phase memory:

* predictors are available only after the opening one-minute bar closes;
* burst thresholds use earlier observations from the same UTC phase only;
* candidate outcomes enter memory only after their 240-minute horizon matures;
* phase estimates are shrunk toward the contemporaneous route-wide history;
* selected evaluation trades are separated by a global 240-minute refractory;
* 20 bps round-trip cost is deducted before every memory and promotion test.

This is a mechanism screen, not an account backtest.  A policy is promoted to a
single-account NautilusTrader implementation only if the fixed causal rule is
positive in 2024 development, 2025 validation, and untouched 2026 data.
"""
from __future__ import annotations

import argparse
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date, timedelta
import heapq
import json
import math
from pathlib import Path
from typing import Any, Deque

import numpy as np
import pandas as pd

import analyze_continuous as c30

EVALUATION_START = date(2024, 1, 1)
EVALUATION_END = date(2026, 7, 31)
HORIZON_MINUTES = 240
ROUND_TRIP_COST_RATE = 0.0020
PHASE_HISTORY_DAYS = 90
OUTCOME_HISTORY_DAYS = 365
MIN_PHASE_OBSERVATIONS = 6
MIN_ROUTE_OBSERVATIONS = 40
ROUTE_PRIOR_WEIGHT = 12.0
GLOBAL_REFRACTORY_MINUTES = 240

ROUTES = (
    "PHASE_FLOW_CONTINUATION",
    "PHASE_CROWDED_ABSORPTION_REVERSAL",
)


class PhaseStudyError(RuntimeError):
    """Raised when the observational or causal-memory contract is violated."""


@dataclass(frozen=True, slots=True)
class Outcome:
    observed_ns: int
    value: float


@dataclass(frozen=True, slots=True)
class PendingOutcome:
    maturity_ns: int
    sequence: int
    phase_key: tuple[int, str]
    route: str
    outcome: Outcome


def _numeric(frame: pd.DataFrame, column: str) -> np.ndarray:
    return pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=np.float64)


def _boolean(values: pd.Series) -> np.ndarray:
    if values.dtype == bool:
        return values.to_numpy(dtype=np.bool_, copy=True)
    return (
        values.astype(str)
        .str.strip()
        .str.lower()
        .isin({"true", "1", "yes"})
        .to_numpy(dtype=np.bool_, copy=True)
    )


def _quantile(values: Deque[tuple[int, float]], q: float) -> float:
    clean = np.fromiter(
        (value for _, value in values if math.isfinite(value)),
        dtype=np.float64,
    )
    return float(np.quantile(clean, q)) if clean.size else float("nan")


def _prune_pairs(values: Deque[tuple[int, float]], cutoff_ns: int) -> None:
    while values and values[0][0] < cutoff_ns:
        values.popleft()


def _prune_outcomes(values: Deque[Outcome], cutoff_ns: int) -> None:
    while values and values[0].observed_ns < cutoff_ns:
        values.popleft()


def _stats(values: Deque[Outcome]) -> dict[str, float | int]:
    clean = np.fromiter(
        (item.value for item in values if math.isfinite(item.value)),
        dtype=np.float64,
    )
    if clean.size == 0:
        return {"n": 0, "mean": 0.0, "median": 0.0, "hit_rate": 0.0}
    return {
        "n": int(clean.size),
        "mean": float(clean.mean()),
        "median": float(np.median(clean)),
        "hit_rate": float((clean > 0.0).mean()),
    }


def phase_gate(
    phase_values: Deque[Outcome],
    route_values: Deque[Outcome],
) -> tuple[bool, dict[str, float | int]]:
    """Return the fixed empirical-Bayes phase decision from matured outcomes."""
    phase = _stats(phase_values)
    route = _stats(route_values)
    phase_n = int(phase["n"])
    route_n = int(route["n"])
    if phase_n < MIN_PHASE_OBSERVATIONS or route_n < MIN_ROUTE_OBSERVATIONS:
        return False, {
            "phase_n": phase_n,
            "route_n": route_n,
            "phase_mean": float(phase["mean"]),
            "phase_median": float(phase["median"]),
            "route_mean": float(route["mean"]),
            "route_hit_rate": float(route["hit_rate"]),
            "shrunk_mean": 0.0,
        }
    shrunk = (
        phase_n * float(phase["mean"])
        + ROUTE_PRIOR_WEIGHT * float(route["mean"])
    ) / (phase_n + ROUTE_PRIOR_WEIGHT)
    decision = (
        shrunk > 0.0
        and float(phase["median"]) > -0.5 * ROUND_TRIP_COST_RATE
        and float(route["mean"]) > 0.0
        and float(route["hit_rate"]) > 0.50
    )
    return decision, {
        "phase_n": phase_n,
        "route_n": route_n,
        "phase_mean": float(phase["mean"]),
        "phase_median": float(phase["median"]),
        "route_mean": float(route["mean"]),
        "route_hit_rate": float(route["hit_rate"]),
        "shrunk_mean": float(shrunk),
    }


def _segment(stamp: pd.Timestamp) -> str:
    if stamp.year == 2024:
        return "DEVELOPMENT_2024"
    if stamp.year == 2025:
        return "VALIDATION_2025"
    if stamp.year == 2026:
        return "UNTOUCHED_2026"
    return "WARMUP"


def _summary(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "trades": 0,
            "positive_rate": 0.0,
            "mean_net_return": 0.0,
            "median_net_return": 0.0,
            "profit_factor": 0.0,
            "largest_positive_share": 1.0,
        }
    values = pd.to_numeric(frame["net_return_240m"], errors="coerce").dropna()
    if values.empty:
        return {
            "trades": 0,
            "positive_rate": 0.0,
            "mean_net_return": 0.0,
            "median_net_return": 0.0,
            "profit_factor": 0.0,
            "largest_positive_share": 1.0,
        }
    positive = values[values > 0.0]
    negative = values[values < 0.0]
    if negative.empty:
        profit_factor = 1_000_000.0 if not positive.empty else 0.0
    else:
        profit_factor = float(positive.sum() / abs(negative.sum()))
    return {
        "trades": int(values.size),
        "positive_rate": float((values > 0.0).mean()),
        "mean_net_return": float(values.mean()),
        "median_net_return": float(values.median()),
        "profit_factor": profit_factor,
        "largest_positive_share": (
            float(positive.max() / positive.sum()) if not positive.empty else 1.0
        ),
        "mean_gross_return": float(
            pd.to_numeric(frame.loc[values.index, "gross_return_240m"], errors="coerce").mean()
        ),
        "median_entry_range_risk": float(
            pd.to_numeric(frame.loc[values.index, "entry_range_risk"], errors="coerce").median()
        ),
    }


def _promotion(selected: pd.DataFrame) -> dict[str, Any]:
    total = _summary(selected)
    segments = {
        name: _summary(selected[selected["segment"] == name])
        for name in ("DEVELOPMENT_2024", "VALIDATION_2025", "UNTOUCHED_2026")
    }
    checks = {
        "at_least_30_independent_trades": total["trades"] >= 30,
        "at_least_5_trades_each_segment": all(
            item["trades"] >= 5 for item in segments.values()
        ),
        "total_positive_rate_at_least_55pct": total["positive_rate"] >= 0.55,
        "total_mean_net_positive": total["mean_net_return"] > 0.0,
        "total_median_net_positive": total["median_net_return"] > 0.0,
        "total_profit_factor_at_least_1_25": total["profit_factor"] >= 1.25,
        "largest_positive_share_at_most_35pct": total["largest_positive_share"] <= 0.35,
        "each_segment_mean_net_positive": all(
            item["mean_net_return"] > 0.0 for item in segments.values()
        ),
        "each_segment_positive_rate_at_least_45pct": all(
            item["positive_rate"] >= 0.45 for item in segments.values()
        ),
    }
    return {
        "total": total,
        "segments": segments,
        "checks": checks,
        "promote": all(checks.values()),
    }


def analyze(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    times = pd.DatetimeIndex(pd.to_datetime(frame["time"], utc=True, errors="raise"))
    times_ns = np.fromiter(
        (pd.Timestamp(value).value for value in times),
        dtype=np.int64,
        count=len(times),
    )
    if times_ns.size == 0 or np.any(np.diff(times_ns) <= 0):
        raise PhaseStudyError("minute clock must be non-empty, unique and monotonic")

    open_price = _numeric(frame, "open")
    high = _numeric(frame, "high")
    low = _numeric(frame, "low")
    close = _numeric(frame, "close")
    quote = _numeric(frame, "quote_volume")
    taker_buy_quote = _numeric(frame, "taker_buy_quote_volume")
    oi = _numeric(frame, "sum_open_interest")
    premium = _numeric(frame, "premium_index")
    metrics_ready = _boolean(frame["metrics_ready"])
    basis_ready = _boolean(frame["basis_ready"])

    flow = np.divide(
        2.0 * taker_buy_quote,
        quote,
        out=np.full(len(frame), np.nan),
        where=quote > 0.0,
    ) - 1.0
    bar_return = np.log(close / open_price)
    oi_change_4h = np.full(len(frame), np.nan)
    oi_change_4h[HORIZON_MINUTES:] = (
        oi[HORIZON_MINUTES:] / oi[:-HORIZON_MINUTES] - 1.0
    )

    phase_flow: dict[int, Deque[tuple[int, float]]] = defaultdict(deque)
    phase_quote: dict[int, Deque[tuple[int, float]]] = defaultdict(deque)
    phase_return: dict[int, Deque[tuple[int, float]]] = defaultdict(deque)
    phase_premium: dict[int, Deque[tuple[int, float]]] = defaultdict(deque)
    phase_outcomes: dict[tuple[int, str], Deque[Outcome]] = defaultdict(deque)
    route_outcomes: dict[str, Deque[Outcome]] = defaultdict(deque)
    pending: list[tuple[int, int, PendingOutcome]] = []
    pending_sequence = 0
    candidate_rows: list[dict[str, Any]] = []
    selected_rows: list[dict[str, Any]] = []
    last_selected_index = -10**9

    evaluation_open = pd.Timestamp(EVALUATION_START, tz="UTC")
    evaluation_close = pd.Timestamp(EVALUATION_END + timedelta(days=1), tz="UTC")
    quarter_indices = np.flatnonzero(times.minute % 15 == 0)

    for index_value in quarter_indices:
        index = int(index_value)
        now_ns = int(times_ns[index])
        while pending and pending[0][0] <= now_ns:
            _, _, item = heapq.heappop(pending)
            phase_outcomes[item.phase_key].append(item.outcome)
            route_outcomes[item.route].append(item.outcome)

        phase_cutoff = now_ns - PHASE_HISTORY_DAYS * 86_400 * 1_000_000_000
        outcome_cutoff = now_ns - OUTCOME_HISTORY_DAYS * 86_400 * 1_000_000_000
        phase = int(times[index].hour * 4 + times[index].minute // 15)
        for values in (
            phase_flow[phase],
            phase_quote[phase],
            phase_return[phase],
            phase_premium[phase],
        ):
            _prune_pairs(values, phase_cutoff)
        for route in ROUTES:
            _prune_outcomes(phase_outcomes[(phase, route)], outcome_cutoff)
            _prune_outcomes(route_outcomes[route], outcome_cutoff)

        current_flow = float(flow[index])
        current_quote = float(quote[index])
        current_return = float(bar_return[index])
        current_premium = float(premium[index])
        history_ready = (
            len(phase_flow[phase]) >= 30
            and len(phase_quote[phase]) >= 30
            and len(phase_return[phase]) >= 30
            and len(phase_premium[phase]) >= 30
        )
        route = "NO_CANDIDATE"
        trade_side = 0
        thresholds: dict[str, float] = {}

        if (
            history_ready
            and index >= HORIZON_MINUTES
            and index + HORIZON_MINUTES < len(frame)
            and index + 1 < len(frame)
            and metrics_ready[index]
            and basis_ready[index]
            and all(
                math.isfinite(value)
                for value in (
                    current_flow,
                    current_quote,
                    current_return,
                    current_premium,
                    float(oi_change_4h[index]),
                )
            )
        ):
            thresholds = {
                "abs_flow_q80": _quantile(phase_flow[phase], 0.80),
                "quote_q80": _quantile(phase_quote[phase], 0.80),
                "abs_return_q50": _quantile(phase_return[phase], 0.50),
                "abs_return_q25": _quantile(phase_return[phase], 0.25),
                "abs_premium_q85": _quantile(phase_premium[phase], 0.85),
            }
            flow_side = 1 if current_flow > 0.0 else -1 if current_flow < 0.0 else 0
            return_side = 1 if current_return > 0.0 else -1 if current_return < 0.0 else 0
            burst = (
                flow_side != 0
                and abs(current_flow) >= thresholds["abs_flow_q80"]
                and current_quote >= thresholds["quote_q80"]
            )
            initiative = (
                flow_side == return_side
                and abs(current_return) >= thresholds["abs_return_q50"]
            )
            absorbed = (
                flow_side != 0
                and (
                    return_side == -flow_side
                    or abs(current_return) <= thresholds["abs_return_q25"]
                )
            )
            not_crowded = (
                flow_side * current_premium <= thresholds["abs_premium_q85"]
            )
            crowd_aligned = (
                flow_side * current_premium >= thresholds["abs_premium_q85"]
            )
            leverage_present = float(oi_change_4h[index]) > 0.0
            if burst and initiative and leverage_present and not_crowded:
                route = "PHASE_FLOW_CONTINUATION"
                trade_side = flow_side
            elif burst and absorbed and leverage_present and crowd_aligned:
                route = "PHASE_CROWDED_ABSORPTION_REVERSAL"
                trade_side = -flow_side

        if route in ROUTES and trade_side != 0:
            entry_index = index + 1
            exit_index = entry_index + HORIZON_MINUTES - 1
            entry_price = float(open_price[entry_index])
            gross = trade_side * math.log(float(close[exit_index]) / entry_price)
            net = gross - ROUND_TRIP_COST_RATE
            maturity_ns = int(times_ns[exit_index])
            outcome = Outcome(observed_ns=maturity_ns, value=float(net))
            phase_key = (phase, route)
            gate, memory = phase_gate(
                phase_outcomes[phase_key],
                route_outcomes[route],
            )
            pending_sequence += 1
            pending_item = PendingOutcome(
                maturity_ns=maturity_ns,
                sequence=pending_sequence,
                phase_key=phase_key,
                route=route,
                outcome=outcome,
            )
            heapq.heappush(
                pending,
                (pending_item.maturity_ns, pending_item.sequence, pending_item),
            )
            base_record = {
                "signal_time": times[index].isoformat(),
                "segment": _segment(times[index]),
                "phase": phase,
                "route": route,
                "trade_side": trade_side,
                "flow": current_flow,
                "quote_volume": current_quote,
                "bar_return": current_return,
                "oi_change_4h": float(oi_change_4h[index]),
                "premium_index": current_premium,
                "entry_time": times[entry_index].isoformat(),
                "entry_price": entry_price,
                "exit_time": times[exit_index].isoformat(),
                "gross_return_240m": float(gross),
                "net_return_240m": float(net),
                "entry_range_risk": float(
                    (high[index] - low[index]) / entry_price
                ),
                "phase_gate": bool(gate),
                **thresholds,
                **memory,
            }
            candidate_rows.append(base_record)
            is_evaluation = evaluation_open <= times[index] < evaluation_close
            independent = index - last_selected_index >= GLOBAL_REFRACTORY_MINUTES
            if gate and is_evaluation and independent:
                selected_rows.append(base_record)
                last_selected_index = index

        if math.isfinite(current_flow):
            phase_flow[phase].append((now_ns, abs(current_flow)))
        if math.isfinite(current_quote):
            phase_quote[phase].append((now_ns, current_quote))
        if math.isfinite(current_return):
            phase_return[phase].append((now_ns, abs(current_return)))
        if math.isfinite(current_premium):
            phase_premium[phase].append((now_ns, abs(current_premium)))

    candidates = pd.DataFrame(candidate_rows)
    selected = pd.DataFrame(selected_rows)
    diagnostics = {
        "quarter_hour_rows": int(len(quarter_indices)),
        "candidate_events": int(len(candidates)),
        "selected_independent_events": int(len(selected)),
        "candidate_route_counts": (
            {str(key): int(value) for key, value in candidates["route"].value_counts().sort_index().items()}
            if not candidates.empty
            else {}
        ),
        "selected_route_counts": (
            {str(key): int(value) for key, value in selected["route"].value_counts().sort_index().items()}
            if not selected.empty
            else {}
        ),
        "selected_phase_count": (
            int(selected["phase"].nunique()) if not selected.empty else 0
        ),
        "global_refractory_minutes": GLOBAL_REFRACTORY_MINUTES,
    }
    return candidates, selected, diagnostics


def run(input_root: Path, output: Path, symbol: str) -> dict[str, Any]:
    frame, input_chunks = c30._load(input_root.resolve(), symbol)
    candidates, selected, diagnostics = analyze(frame)
    output.mkdir(parents=True, exist_ok=True)
    candidates.to_csv(output / "candidate_events.csv", index=False)
    selected.to_csv(output / "selected_events.csv", index=False)

    overall = _promotion(selected)
    by_route = {
        route: _promotion(selected[selected["route"] == route])
        for route in ROUTES
    }
    promoted_routes = [
        route for route, result in by_route.items() if result["promote"]
    ]
    result = {
        "schema_version": 1,
        "candidate": "candidate-31-causal-quarter-hour-phase-memory",
        "role": "multi-year causal mechanism screen; no account or PnL claim",
        "symbol": symbol,
        "input_start": c30.INPUT_START.isoformat(),
        "input_end": c30.INPUT_END.isoformat(),
        "evaluation_start": EVALUATION_START.isoformat(),
        "evaluation_end": EVALUATION_END.isoformat(),
        "evaluation_calendar_days": (EVALUATION_END - EVALUATION_START).days + 1,
        "continuous_observation_grid": True,
        "horizon_minutes": HORIZON_MINUTES,
        "round_trip_cost_rate": ROUND_TRIP_COST_RATE,
        "phase_history_days": PHASE_HISTORY_DAYS,
        "outcome_history_days": OUTCOME_HISTORY_DAYS,
        "phase_memory_contract": (
            "only outcomes whose 240-minute exit timestamp is at or before the "
            "current signal timestamp enter phase or route memory"
        ),
        "diagnostics": diagnostics,
        "overall": overall,
        "routes": by_route,
        "promoted_routes": promoted_routes,
        "promote": bool(promoted_routes),
        "decision": (
            "PROMOTE_FIXED_PHASE_ROUTE_TO_CONTINUOUS_NAUTILUS_ACCOUNT"
            if promoted_routes
            else "DISCARD_OR_REDESIGN_PHASE_MEMORY_BEFORE_ACCOUNT_BACKTEST"
        ),
        "input_chunks": input_chunks,
    }
    (output / "study.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--symbol", default="BTCUSDT")
    args = parser.parse_args()
    run(args.input_root, args.output, args.symbol)


if __name__ == "__main__":
    main()
