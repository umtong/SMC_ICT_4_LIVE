#!/usr/bin/env python3
"""Causal route and post-decision forward-path diagnostic for Candidate 37.

No orders, fills, positions, PnL or NAV are simulated here. Future bars only
label geometry after a decision is fixed; NautilusTrader remains the sole
execution and account engine.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from diagnostic_support import (
    DiagnosticError, forward_label, geometry, load_symbol, manifest_map, percentiles,
)
from router import RouteConfig, SYMBOLS, route_universe


MINUTE_NS = 60_000_000_000


def route_config(config: dict[str, Any]) -> RouteConfig:
    allowed = asdict(RouteConfig())
    for key, value in config.get("strategy", {}).items():
        if key in allowed:
            allowed[key] = value
    return RouteConfig(**allowed)


def diagnose(*, input_root: Path, config_path: Path, output: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if tuple(config.get("symbols", ())) != SYMBOLS:
        raise DiagnosticError(f"universe must be exactly {SYMBOLS}")
    params = route_config(config)
    horizon = int(config.get("diagnostic", {}).get("forward_horizon_minutes", 90))
    lockout = int(config.get("diagnostic", {}).get("episode_lockout_minutes", 30))
    history_bars = max(240, params.activity_lookback + params.atr_period + params.ramp_bars + 10)

    manifests = manifest_map(input_root)
    bars_by_symbol: dict[str, list[Any]] = {}
    times_by_symbol: dict[str, list[int]] = {}
    input_summary: dict[str, Any] = {}
    for symbol in SYMBOLS:
        manifest, directory = manifests[symbol]
        bars, times = load_symbol(symbol=symbol, manifest=manifest, directory=directory)
        bars_by_symbol[symbol], times_by_symbol[symbol] = bars, times
        input_summary[symbol] = {
            "core_start": manifest["core_start"], "core_end": manifest["core_end"],
            "rows": manifest["rows"],
        }
    reference_times = times_by_symbol["BTCUSDT"]
    for symbol in SYMBOLS[1:]:
        if times_by_symbol[symbol] != reference_times:
            raise DiagnosticError(f"{symbol} minute clock differs from BTCUSDT")

    evaluated = selected = duplicate_rejections = lockout_rejections = 0
    ambiguity_rejections = invalid_geometry = 0
    seen: set[tuple[int, str, int]] = set()
    last_selected_ts: int | None = None
    states, symbols, outcomes, state_outcomes = Counter(), Counter(), Counter(), Counter()
    scores: list[float] = []
    geometry_rr: list[float] = []
    cost_r: list[float] = []
    favorable_r: list[float] = []
    adverse_r: list[float] = []
    markouts: dict[str, list[float]] = {str(value): [] for value in (5, 15, 30, 60, 90)}
    rows: list[dict[str, Any]] = []

    final_index = len(reference_times) - horizon - 1
    for index in range(history_bars, max(history_bars, final_index + 1)):
        timestamp = reference_times[index]
        evaluated += 1
        start = max(0, index - history_bars + 1)
        winner, decisions = route_universe(
            bars_by_symbol={
                symbol: bars_by_symbol[symbol][start : index + 1]
                for symbol in SYMBOLS
            },
            features_by_symbol=None,
            config=params,
        )
        if winner is None:
            if any(item.actionable for item in decisions.values()):
                ambiguity_rejections += 1
            continue
        key = (winner.episode_ts, winner.state, winner.side)
        if key in seen:
            duplicate_rejections += 1
            continue
        if last_selected_ts is not None and timestamp - last_selected_ts < lockout * MINUTE_NS:
            lockout_rejections += 1
            seen.add(key)
            continue
        seen.add(key)
        last_selected_ts = timestamp
        valid, rr, risk = geometry(winner)
        if not valid:
            invalid_geometry += 1
            continue
        label = forward_label(
            bars=bars_by_symbol[winner.symbol], index=index,
            decision=winner, horizon=horizon,
        )
        selected += 1
        states[winner.state] += 1
        symbols[winner.symbol] += 1
        outcomes[label["outcome"]] += 1
        state_outcomes[f"{winner.state}:{label['outcome']}"] += 1
        scores.append(float(winner.score))
        geometry_rr.append(rr)
        favorable_r.append(float(label["max_favorable_r"]))
        adverse_r.append(float(label["max_adverse_r"]))
        for offset, value in label["markout_r"].items():
            if value is not None and math.isfinite(float(value)):
                markouts[offset].append(float(value))
        bps = 2.0 * (
            float(config["all_in_cost_bps_each_side"])
            + float(config["adverse_slippage_bps_each_side"])
        ) + float(config["funding_reserve_bps"])
        estimated_cost_r = winner.entry_reference * bps / 10_000.0 / risk
        cost_r.append(estimated_cost_r)
        rows.append({
            "decision_time_utc": pd.Timestamp(timestamp, unit="ns", tz="UTC").isoformat(),
            "decision_ts": timestamp, "episode_ts": winner.episode_ts,
            "symbol": winner.symbol, "state": winner.state, "side": winner.side,
            "score": winner.score, "entry": winner.entry_reference,
            "stop": winner.stop_reference, "objective": winner.objective_reference,
            "geometry_rr": rr, "estimated_round_trip_cost_r": estimated_cost_r,
            "outcome": label["outcome"],
            "first_hit_offset_minutes": label["first_hit_offset_minutes"],
            "max_favorable_r": label["max_favorable_r"],
            "max_adverse_r": label["max_adverse_r"],
            **{f"markout_{key}m_r": value for key, value in label["markout_r"].items()},
            "reason": winner.reasons[0] if winner.reasons else "",
            "diagnostics_json": json.dumps(dict(winner.diagnostics), sort_keys=True),
        })

    non_ambiguous = selected - outcomes.get("AMBIGUOUS_SAME_BAR", 0)
    target_rate = outcomes.get("TARGET_FIRST", 0) / non_ambiguous if non_ambiguous else 0.0
    days = max(1, int((reference_times[-1] - reference_times[0]) // (1_440 * MINUTE_NS)) + 1)
    median_30 = percentiles(markouts["30"])["median"]
    checks = {
        "nonzero_opportunity": selected > 0,
        "at_least_one_independent_episode_per_day": selected >= days,
        "both_mechanisms_observed": len(states) >= 2,
        "valid_geometry": invalid_geometry == 0,
        "target_first_rate_above_cost_aware_triage_floor": target_rate >= 0.42,
        "median_30m_markout_positive": median_30 is not None and median_30 > 0.0,
        "same_minute_four_symbol_clock": True,
        "future_input_violations": 0,
    }
    assessment = (
        "ELIGIBLE_FOR_SHORT_NAUTILUS_EXECUTION_DIAGNOSTIC"
        if all(checks.values()) else "REVISE_OR_REJECT_BEFORE_EXECUTION"
    )
    output.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(output / "burst_routes.csv", index=False)
    result = {
        "schema": "candidate-37-burst-shape-forward-diagnostic-v1",
        "claim_scope": "STRUCTURAL_AND_FORWARD_PATH_DIAGNOSTIC_NO_ORDER_SIMULATION_NO_PNL_NO_NAV_CLAIM",
        "candidate": "candidate-37-burst-shape-propagation-router",
        "input": input_summary, "route_config": asdict(params),
        "calendar_days": days, "minutes_evaluated": evaluated,
        "selected_independent_routes": selected,
        "routes_per_calendar_day": selected / days,
        "duplicate_episode_rejections": duplicate_rejections,
        "causal_lockout_rejections": lockout_rejections,
        "global_ambiguity_rejections": ambiguity_rejections,
        "selected_states": dict(sorted(states.items())),
        "selected_symbols": dict(sorted(symbols.items())),
        "forward_outcomes": dict(sorted(outcomes.items())),
        "state_forward_outcomes": dict(sorted(state_outcomes.items())),
        "target_first_rate_excluding_same_bar_ambiguity": target_rate,
        "score_distribution": percentiles(scores),
        "geometry_rr_distribution": percentiles(geometry_rr),
        "estimated_round_trip_cost_r_distribution": percentiles(cost_r),
        "max_favorable_r_distribution": percentiles(favorable_r),
        "max_adverse_r_distribution": percentiles(adverse_r),
        "markout_r_distributions": {key: percentiles(value) for key, value in markouts.items()},
        "invalid_geometry_decisions": invalid_geometry,
        "same_minute_four_symbol_clock": True, "future_input_violations": 0,
        "gate_checks": checks, "next_stage_assessment": assessment,
    }
    (output / "burst_diagnostic.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = diagnose(
        input_root=args.input_root.resolve(), config_path=args.config.resolve(),
        output=args.output.resolve(),
    )
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
