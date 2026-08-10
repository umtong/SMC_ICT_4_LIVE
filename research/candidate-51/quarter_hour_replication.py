#!/usr/bin/env python3
"""Frozen untouched replication of Candidate 51's quarter-hour mechanism.

This is a causal mechanism diagnostic, not a portfolio or matching engine.  It
reuses the checksum-verified Binance Vision loader and event construction from
``quarter_hour_anatomy.py``.  The discovery result is frozen before this run:

* entry time: quarter-hour event + 30 minutes;
* exit boundary: quarter-hour event + 480 minutes;
* primary scenario: reversal during the first 30 minutes, followed by aligned
  three-minute taker flow and aligned one-minute price response observable
  through minute 29;
* external controls: consensus-flow, abs-flow>=0.50 and all nonzero events;
* phase controls: identical construction at minute offsets 03, 07 and 11;
* same-timestamp four-asset arbitration: largest initial absolute first-ten-
  second imbalance, then fixed BTC/ETH/SOL/XRP priority;
* one global slot: greedy non-overlap from entry to the frozen 8-hour boundary;
* cost screen: 15 bp round trip, with 10 and 20 bp sensitivity.

No rule or threshold is selected from the replication results.  Promotion to a
NautilusTrader strategy remains a separate decision after this evidence is
written.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import quarter_hour_anatomy as base  # type: ignore  # noqa: E402

PRIMARY_SCENARIO = "sc_reversal30_realign_price"
CONTROL_SCENARIOS = (
    "sc_consensus_flow",
    "sc_abs50",
    "sc_all",
)
SCENARIOS = (PRIMARY_SCENARIO, *CONTROL_SCENARIOS)
RETURN_COLUMN = "dir_bps_d30_h480"
ENTRY_DELAY_MIN = 30
EXIT_HORIZON_MIN = 480
ROUND_TRIP_COSTS_BP = (10.0, 15.0, 20.0)
STOP_TARGET_PAIRS_BP = (
    (50.0, 75.0),
    (50.0, 100.0),
    (75.0, 100.0),
    (75.0, 150.0),
    (100.0, 150.0),
    (100.0, 200.0),
)
SYMBOL_PRIORITY = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}
PHASE_PRIORITY = {name: index for index, name in enumerate(base.PHASE_OFFSETS)}


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"not JSON serializable: {type(value)!r}")


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _profit_factor(values: np.ndarray) -> float | None:
    gains = float(values[values > 0.0].sum())
    losses = float(-values[values < 0.0].sum())
    return None if losses <= 0.0 else gains / losses


def _safe_float(value: Any) -> float | None:
    return float(value) if _finite(value) else None


def _event_path(
    minute_frame: pd.DataFrame,
    event_time: pd.Timestamp,
    direction: int,
) -> dict[str, Any]:
    entry_time = event_time + pd.Timedelta(minutes=ENTRY_DELAY_MIN)
    exit_time = event_time + pd.Timedelta(minutes=EXIT_HORIZON_MIN)
    entry_value = minute_frame["open"].get(entry_time)
    exit_value = minute_frame["open"].get(exit_time)
    if not _finite(entry_value) or not _finite(exit_value):
        return {"path_ready": False}
    entry = float(entry_value)
    exit_price = float(exit_value)
    if entry <= 0.0 or exit_price <= 0.0 or direction not in (-1, 1):
        return {"path_ready": False}

    # The position is entered at the frozen +30 minute open and is flattened at
    # the +480 minute open.  Minute bars from +30 through +479 are therefore the
    # executable intraposition path.  Barrier ties in one minute are reported as
    # ambiguous rather than resolved optimistically.
    path = minute_frame.loc[
        (minute_frame.index >= entry_time) & (minute_frame.index < exit_time),
        ["high", "low", "close"],
    ].copy()
    expected = EXIT_HORIZON_MIN - ENTRY_DELAY_MIN
    if len(path) != expected:
        return {
            "path_ready": False,
            "path_rows": int(len(path)),
            "expected_path_rows": expected,
        }

    highs = pd.to_numeric(path["high"], errors="coerce").to_numpy(dtype=float)
    lows = pd.to_numeric(path["low"], errors="coerce").to_numpy(dtype=float)
    closes = pd.to_numeric(path["close"], errors="coerce").to_numpy(dtype=float)
    if not (np.isfinite(highs).all() and np.isfinite(lows).all() and np.isfinite(closes).all()):
        return {"path_ready": False}

    if direction > 0:
        favourable = np.log(highs / entry) * 10_000.0
        adverse = np.log(lows / entry) * 10_000.0
        close_path = np.log(closes / entry) * 10_000.0
    else:
        favourable = np.log(entry / lows) * 10_000.0
        adverse = np.log(entry / highs) * 10_000.0
        close_path = np.log(entry / closes) * 10_000.0

    gross = direction * math.log(exit_price / entry) * 10_000.0
    mfe_index = int(np.argmax(favourable))
    mae_index = int(np.argmin(adverse))
    result: dict[str, Any] = {
        "path_ready": True,
        "entry_time": entry_time,
        "exit_time": exit_time,
        "entry_price": entry,
        "exit_price": exit_price,
        "gross_bps": gross,
        "mfe_bps": float(np.max(favourable)),
        "mae_bps": float(np.min(adverse)),
        "time_to_mfe_min": mfe_index,
        "time_to_mae_min": mae_index,
        "close_60m_bps": float(close_path[min(59, len(close_path) - 1)]),
        "close_120m_bps": float(close_path[min(119, len(close_path) - 1)]),
        "close_240m_bps": float(close_path[min(239, len(close_path) - 1)]),
    }

    for stop_bp, target_bp in STOP_TARGET_PAIRS_BP:
        label = f"s{int(stop_bp)}_t{int(target_bp)}"
        outcome = "TIME"
        exit_bps = gross
        exit_minute = expected
        for index, (fav, adv) in enumerate(zip(favourable, adverse, strict=True)):
            target_hit = fav >= target_bp
            stop_hit = adv <= -stop_bp
            if target_hit and stop_hit:
                outcome = "AMBIGUOUS"
                exit_bps = math.nan
                exit_minute = index
                break
            if target_hit:
                outcome = "TARGET"
                exit_bps = target_bp
                exit_minute = index
                break
            if stop_hit:
                outcome = "STOP"
                exit_bps = -stop_bp
                exit_minute = index
                break
        result[f"barrier_{label}_outcome"] = outcome
        result[f"barrier_{label}_gross_bps"] = exit_bps
        result[f"barrier_{label}_exit_min"] = exit_minute
    return result


def _prepare_events(frame: pd.DataFrame, *, symbol: str, start: date, end: date,
                    period_label: str, regime: str) -> pd.DataFrame:
    phase_frames = [
        base.build_events(
            frame,
            start,
            end,
            phase=phase,
            phase_offset=offset,
        )
        for phase, offset in base.PHASE_OFFSETS.items()
    ]
    events = pd.concat(phase_frames, ignore_index=True)
    events["event_time"] = pd.to_datetime(events["event_time"], utc=True, errors="raise")
    events["symbol"] = symbol
    events["period_label"] = period_label
    events["regime"] = regime
    events["selection_score"] = pd.to_numeric(
        events["abs_flow_open_10s"], errors="coerce"
    )
    events["symbol_priority"] = SYMBOL_PRIORITY[symbol]
    events["phase_priority"] = events["phase"].map(PHASE_PRIORITY).astype(int)

    path_rows: list[dict[str, Any]] = []
    relevant = events[events[list(SCENARIOS)].fillna(False).any(axis=1)].copy()
    for row in relevant.itertuples(index=False):
        path = _event_path(
            frame,
            pd.Timestamp(row.event_time),
            int(row.direction),
        )
        path["event_time"] = pd.Timestamp(row.event_time)
        path["phase"] = str(row.phase)
        path_rows.append(path)
    if path_rows:
        path_frame = pd.DataFrame(path_rows)
        events = events.merge(path_frame, on=["event_time", "phase"], how="left", validate="one_to_one")
    else:
        events["path_ready"] = False

    # Assert the independently recomputed frozen return equals the discovery
    # module's boundary return whenever both are available.
    comparable = events[events["path_ready"].fillna(False)].copy()
    if not comparable.empty:
        left = pd.to_numeric(comparable[RETURN_COLUMN], errors="coerce").to_numpy(dtype=float)
        right = pd.to_numeric(comparable["gross_bps"], errors="coerce").to_numpy(dtype=float)
        finite = np.isfinite(left) & np.isfinite(right)
        if finite.any() and float(np.max(np.abs(left[finite] - right[finite]))) > 1e-7:
            raise RuntimeError("frozen boundary return disagrees with independently recomputed path")
    return events


def run_one(args: argparse.Namespace) -> None:
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    frame, evidence = base.load_minute_frame(
        symbol=args.symbol,
        start=start,
        end=end,
        cache=Path(args.cache),
        source_path=Path(args.source_path),
    )
    events = _prepare_events(
        frame,
        symbol=args.symbol,
        start=start,
        end=end,
        period_label=args.period_label,
        regime=args.regime,
    )
    keep_columns = [
        "event_time", "phase", "phase_offset_minute", "symbol", "period_label", "regime",
        "direction", "abs_flow_open_10s", "flow_open_10s", "notional_open_10s_burst",
        "aligned_ret_1m_bps", "aligned_lag_consensus_3", "dir_bps_d1_h30",
        "state30_aligned_flow_3m", "state30_aligned_ret_60s_bps",
        "state30_notional_burst", "selection_score", "symbol_priority", "phase_priority",
        RETURN_COLUMN, "path_ready", "entry_time", "exit_time", "entry_price", "exit_price",
        "gross_bps", "mfe_bps", "mae_bps", "time_to_mfe_min", "time_to_mae_min",
        "close_60m_bps", "close_120m_bps", "close_240m_bps",
        *SCENARIOS,
    ]
    for stop_bp, target_bp in STOP_TARGET_PAIRS_BP:
        label = f"s{int(stop_bp)}_t{int(target_bp)}"
        keep_columns.extend([
            f"barrier_{label}_outcome",
            f"barrier_{label}_gross_bps",
            f"barrier_{label}_exit_min",
        ])
    existing = [column for column in keep_columns if column in events.columns]
    compact = events[existing].copy()
    for scenario in SCENARIOS:
        compact[scenario] = compact[scenario].fillna(False).astype(bool)
    result = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "frozen_contract": {
            "primary_scenario": PRIMARY_SCENARIO,
            "controls": list(CONTROL_SCENARIOS),
            "entry_delay_min": ENTRY_DELAY_MIN,
            "exit_horizon_min": EXIT_HORIZON_MIN,
            "return_column": RETURN_COLUMN,
            "round_trip_costs_bp": list(ROUND_TRIP_COSTS_BP),
            "arbitration": "max initial abs first-ten-second flow; fixed symbol priority",
            "global_slot": "greedy non-overlap by frozen entry/exit interval",
        },
        "symbol": args.symbol,
        "period_label": args.period_label,
        "regime": args.regime,
        "start": args.start,
        "end": args.end,
        "calendar_days": (end - start).days + 1,
        "event_rows": int(len(compact)),
        "path_ready_rows": int(compact.get("path_ready", pd.Series(dtype=bool)).fillna(False).sum()),
        "events": compact.to_dict(orient="records"),
        "raw_evidence": evidence,
    }
    payload = json.dumps(result, indent=2, sort_keys=True, default=_json_default) + "\n"
    (output / "result.json").write_text(payload, encoding="utf-8")
    print(json.dumps({
        "symbol": args.symbol,
        "period_label": args.period_label,
        "events": len(compact),
        "path_ready": result["path_ready_rows"],
    }, indent=2))


def _load_results(root: Path) -> tuple[list[dict[str, Any]], pd.DataFrame]:
    paths = sorted(root.rglob("result.json"))
    if not paths:
        raise RuntimeError(f"no result.json under {root}")
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    frames: list[pd.DataFrame] = []
    for payload in payloads:
        frame = pd.DataFrame(payload["events"])
        frame["event_time"] = pd.to_datetime(frame["event_time"], utc=True, errors="raise")
        for candidate in ("entry_time", "exit_time"):
            if candidate in frame:
                frame[candidate] = pd.to_datetime(frame[candidate], utc=True, errors="coerce")
        for scenario in SCENARIOS:
            frame[scenario] = frame[scenario].fillna(False).astype(bool)
        frames.append(frame)
    events = pd.concat(frames, ignore_index=True).sort_values(
        ["event_time", "phase_priority", "symbol_priority"], kind="stable"
    )
    return payloads, events


def _arbitrate_and_nonoverlap(events: pd.DataFrame, scenario: str, phase: str) -> pd.DataFrame:
    eligible = events[
        events[scenario]
        & events["phase"].eq(phase)
        & events["path_ready"].fillna(False).astype(bool)
        & pd.to_numeric(events["gross_bps"], errors="coerce").notna()
    ].copy()
    if eligible.empty:
        return eligible
    eligible = eligible.sort_values(
        ["event_time", "selection_score", "symbol_priority"],
        ascending=[True, False, True],
        kind="stable",
    )
    # One asset can be selected for an episode timestamp before enforcing the
    # portfolio's longer one-position interval.
    selected = eligible.groupby("event_time", sort=True, as_index=False).head(1).copy()
    chosen: list[int] = []
    occupied_until: pd.Timestamp | None = None
    for idx, row in selected.sort_values("entry_time", kind="stable").iterrows():
        entry_time = pd.Timestamp(row["entry_time"])
        exit_time = pd.Timestamp(row["exit_time"])
        if occupied_until is not None and entry_time < occupied_until:
            continue
        chosen.append(idx)
        occupied_until = exit_time
    return selected.loc[chosen].sort_values("entry_time").copy()


def _day_block_bootstrap(values: pd.DataFrame, column: str, *, seed: int = 51051,
                         iterations: int = 10_000) -> dict[str, Any]:
    if values.empty:
        return {"iterations": iterations, "count": 0}
    frame = values[["entry_time", column]].dropna().copy()
    if frame.empty:
        return {"iterations": iterations, "count": 0}
    frame["day"] = pd.to_datetime(frame["entry_time"], utc=True).dt.floor("D")
    daily = [group[column].to_numpy(dtype=float) for _, group in frame.groupby("day", sort=True)]
    if not daily:
        return {"iterations": iterations, "count": 0}
    rng = np.random.default_rng(seed)
    means = np.empty(iterations, dtype=float)
    for index in range(iterations):
        selected = rng.integers(0, len(daily), size=len(daily))
        sample = np.concatenate([daily[item] for item in selected])
        means[index] = float(np.mean(sample))
    return {
        "iterations": iterations,
        "count": int(len(frame)),
        "days": int(len(daily)),
        "mean": float(frame[column].mean()),
        "ci90": [float(np.quantile(means, 0.05)), float(np.quantile(means, 0.95))],
        "ci95": [float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))],
        "probability_mean_positive": float(np.mean(means > 0.0)),
        "probability_mean_ge_15bp": float(np.mean(means >= 15.0)),
    }


def _concentration(values: np.ndarray) -> dict[str, Any]:
    clean = values[np.isfinite(values)]
    if clean.size == 0:
        return {"count": 0}
    ordered = np.sort(clean)[::-1]
    total = float(clean.sum())
    output: dict[str, Any] = {
        "count": int(clean.size),
        "sum_bps": total,
        "best_bps": float(ordered[0]),
        "worst_bps": float(np.min(clean)),
    }
    for amount in (1, 3, 5):
        if clean.size > amount:
            remainder = ordered[amount:]
            output[f"mean_without_top_{amount}"] = float(np.mean(remainder))
        if total > 0.0:
            output[f"top_{amount}_positive_sum_share"] = float(
                np.maximum(ordered[:amount], 0.0).sum() / total
            )
    return output


def _summarize(frame: pd.DataFrame, *, scenario: str, phase: str,
               total_calendar_days: int) -> dict[str, Any]:
    values = pd.to_numeric(frame["gross_bps"], errors="coerce").dropna().to_numpy(dtype=float)
    n = int(values.size)
    result: dict[str, Any] = {
        "scenario": scenario,
        "phase": phase,
        "completed_episodes": n,
        "calendar_days": total_calendar_days,
        "episodes_per_calendar_day": n / total_calendar_days if total_calendar_days else None,
    }
    if n == 0:
        return result
    result.update({
        "gross_mean_bps": float(np.mean(values)),
        "gross_median_bps": float(np.median(values)),
        "gross_hit_rate": float(np.mean(values > 0.0)),
        "gross_profit_factor": _profit_factor(values),
        "gross_q10_bps": float(np.quantile(values, 0.10)),
        "gross_q25_bps": float(np.quantile(values, 0.25)),
        "gross_q75_bps": float(np.quantile(values, 0.75)),
        "gross_q90_bps": float(np.quantile(values, 0.90)),
        "mfe_mean_bps": float(pd.to_numeric(frame["mfe_bps"], errors="coerce").mean()),
        "mae_mean_bps": float(pd.to_numeric(frame["mae_bps"], errors="coerce").mean()),
        "mfe_median_bps": float(pd.to_numeric(frame["mfe_bps"], errors="coerce").median()),
        "mae_median_bps": float(pd.to_numeric(frame["mae_bps"], errors="coerce").median()),
        "median_time_to_mfe_min": float(pd.to_numeric(frame["time_to_mfe_min"], errors="coerce").median()),
        "median_time_to_mae_min": float(pd.to_numeric(frame["time_to_mae_min"], errors="coerce").median()),
        "by_symbol": {},
        "by_period": {},
        "by_regime": {},
        "cost_sensitivity": {},
        "barriers": {},
        "concentration": _concentration(values),
    })
    for cost in ROUND_TRIP_COSTS_BP:
        net = values - cost
        key = f"net_{int(cost)}bp"
        result["cost_sensitivity"][key] = {
            "mean_bps": float(np.mean(net)),
            "median_bps": float(np.median(net)),
            "hit_rate": float(np.mean(net > 0.0)),
            "profit_factor": _profit_factor(net),
            "sum_bps": float(np.sum(net)),
        }
    for group_name, target in (("symbol", "by_symbol"), ("period_label", "by_period"), ("regime", "by_regime")):
        for label, group in frame.groupby(group_name, sort=True):
            group_values = pd.to_numeric(group["gross_bps"], errors="coerce").dropna().to_numpy(dtype=float)
            if group_values.size == 0:
                continue
            result[target][str(label)] = {
                "count": int(group_values.size),
                "gross_mean_bps": float(np.mean(group_values)),
                "net15_mean_bps": float(np.mean(group_values) - 15.0),
                "net15_hit_rate": float(np.mean(group_values > 15.0)),
                "net15_profit_factor": _profit_factor(group_values - 15.0),
            }
    net15_frame = frame.copy()
    net15_frame["net15_bps"] = pd.to_numeric(net15_frame["gross_bps"], errors="coerce") - 15.0
    result["day_block_bootstrap_net15"] = _day_block_bootstrap(net15_frame, "net15_bps")

    for stop_bp, target_bp in STOP_TARGET_PAIRS_BP:
        label = f"s{int(stop_bp)}_t{int(target_bp)}"
        outcome_column = f"barrier_{label}_outcome"
        gross_column = f"barrier_{label}_gross_bps"
        if outcome_column not in frame:
            continue
        unambiguous = frame[frame[outcome_column].ne("AMBIGUOUS")].copy()
        barrier_values = pd.to_numeric(unambiguous[gross_column], errors="coerce").dropna().to_numpy(dtype=float)
        counts = frame[outcome_column].fillna("NOT_READY").value_counts().to_dict()
        result["barriers"][label] = {
            "stop_bps": stop_bp,
            "target_bps": target_bp,
            "outcome_counts": {str(key): int(value) for key, value in counts.items()},
            "ambiguous_share": float(np.mean(frame[outcome_column].eq("AMBIGUOUS"))),
            "unambiguous_count": int(barrier_values.size),
            "gross_mean_bps": _safe_float(np.mean(barrier_values)) if barrier_values.size else None,
            "net15_mean_bps": _safe_float(np.mean(barrier_values) - 15.0) if barrier_values.size else None,
            "net15_hit_rate": _safe_float(np.mean(barrier_values > 15.0)) if barrier_values.size else None,
            "net15_profit_factor": _profit_factor(barrier_values - 15.0) if barrier_values.size else None,
            "median_exit_min": _safe_float(pd.to_numeric(unambiguous[f"barrier_{label}_exit_min"], errors="coerce").median()),
        }
    return result


def _phase_spread(summaries: Mapping[str, dict[str, Any]], scenario: str) -> dict[str, Any]:
    quarter = summaries.get(f"quarter_hour:{scenario}", {})
    placebo = [
        summaries.get(f"{phase}:{scenario}", {})
        for phase in base.PHASE_OFFSETS
        if phase != "quarter_hour"
    ]
    gross_placebo = [item.get("gross_mean_bps") for item in placebo if _finite(item.get("gross_mean_bps"))]
    net_placebo = [
        item.get("cost_sensitivity", {}).get("net_15bp", {}).get("mean_bps")
        for item in placebo
        if _finite(item.get("cost_sensitivity", {}).get("net_15bp", {}).get("mean_bps"))
    ]
    result = {
        "scenario": scenario,
        "quarter_hour_completed": quarter.get("completed_episodes", 0),
        "placebo_completed_median": float(np.median([
            item.get("completed_episodes", 0) for item in placebo
        ])) if placebo else None,
    }
    if gross_placebo and _finite(quarter.get("gross_mean_bps")):
        result["gross_spread_vs_placebo_median_bps"] = float(
            quarter["gross_mean_bps"] - np.median(gross_placebo)
        )
    quarter_net = quarter.get("cost_sensitivity", {}).get("net_15bp", {}).get("mean_bps")
    if net_placebo and _finite(quarter_net):
        result["net15_spread_vs_placebo_median_bps"] = float(
            quarter_net - np.median(net_placebo)
        )
    return result


def _write_markdown(result: dict[str, Any], output: Path) -> None:
    summaries = result["summaries"]
    lines = [
        "# Frozen quarter-hour replication",
        "",
        f"- source runs: {result['source_runs']}",
        f"- untouched calendar days: {result['total_calendar_days']}",
        f"- symbols: {', '.join(result['symbols'])}",
        f"- periods: {', '.join(result['period_labels'])}",
        f"- primary scenario: `{PRIMARY_SCENARIO}`",
        f"- frozen entry/exit: +{ENTRY_DELAY_MIN}m open to +{EXIT_HORIZON_MIN}m open",
        "- arbitration: largest initial absolute first-ten-second taker imbalance at a timestamp",
        "- account approximation: one global non-overlapping 8-hour slot",
        "- primary cost screen: 15 bp round trip",
        "- this is mechanism replication, not a NautilusTrader NAV backtest",
        "",
        "## Global one-slot quarter-hour results",
        "",
        "| scenario | n | n/day | gross bp | net15 bp | net15 hit | net15 PF | bootstrap 95% CI |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for scenario in SCENARIOS:
        item = summaries.get(f"quarter_hour:{scenario}", {})
        net = item.get("cost_sensitivity", {}).get("net_15bp", {})
        bootstrap = item.get("day_block_bootstrap_net15", {})
        ci = bootstrap.get("ci95")
        ci_text = "na" if not ci else f"[{ci[0]:.2f}, {ci[1]:.2f}]"
        lines.append(
            "| {scenario} | {n} | {rate} | {gross} | {net} | {hit} | {pf} | {ci} |".format(
                scenario=scenario,
                n=item.get("completed_episodes", 0),
                rate="na" if not _finite(item.get("episodes_per_calendar_day")) else f"{item['episodes_per_calendar_day']:.3f}",
                gross="na" if not _finite(item.get("gross_mean_bps")) else f"{item['gross_mean_bps']:.2f}",
                net="na" if not _finite(net.get("mean_bps")) else f"{net['mean_bps']:.2f}",
                hit="na" if not _finite(net.get("hit_rate")) else f"{100.0 * net['hit_rate']:.1f}",
                pf="na" if not _finite(net.get("profit_factor")) else f"{net['profit_factor']:.2f}",
                ci=ci_text,
            )
        )
    lines.extend([
        "",
        "## Primary scenario by period",
        "",
        "| period | n | gross bp | net15 bp | net15 hit | PF |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    primary = summaries.get(f"quarter_hour:{PRIMARY_SCENARIO}", {})
    for period, item in primary.get("by_period", {}).items():
        lines.append(
            "| {period} | {n} | {gross:.2f} | {net:.2f} | {hit:.1f} | {pf} |".format(
                period=period,
                n=item["count"],
                gross=item["gross_mean_bps"],
                net=item["net15_mean_bps"],
                hit=100.0 * item["net15_hit_rate"],
                pf="na" if not _finite(item.get("net15_profit_factor")) else f"{item['net15_profit_factor']:.2f}",
            )
        )
    lines.extend([
        "",
        "## Quarter-hour phase specificity",
        "",
        "| scenario | qh n | placebo median n | gross spread bp | net15 spread bp |",
        "|---|---:|---:|---:|---:|",
    ])
    for item in result["phase_spreads"]:
        lines.append(
            "| {scenario} | {qn} | {pn} | {gross} | {net} |".format(
                scenario=item["scenario"],
                qn=item.get("quarter_hour_completed", 0),
                pn="na" if not _finite(item.get("placebo_completed_median")) else f"{item['placebo_completed_median']:.0f}",
                gross="na" if not _finite(item.get("gross_spread_vs_placebo_median_bps")) else f"{item['gross_spread_vs_placebo_median_bps']:.2f}",
                net="na" if not _finite(item.get("net15_spread_vs_placebo_median_bps")) else f"{item['net15_spread_vs_placebo_median_bps']:.2f}",
            )
        )
    lines.extend([
        "",
        "## Interpretation contract",
        "",
        "This untouched replication freezes the discovery scenario, timing, arbitration and 8-hour slot before observing these periods. A positive mean is still not a deployment result. Promotion requires a NautilusTrader continuous four-asset account with actual order lifecycle, risk sizing from current NAV, fees, adverse slippage, market impact, funding and restart-safe state handling.",
        "",
    ])
    (output / "REPLICATION.md").write_text("\n".join(lines), encoding="utf-8")


def aggregate(args: argparse.Namespace) -> None:
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    payloads, events = _load_results(Path(args.results_root))
    expected = int(args.expected_runs)
    if expected and len(payloads) != expected:
        raise RuntimeError(f"expected {expected} source runs, found {len(payloads)}")
    periods = sorted({str(payload["period_label"]) for payload in payloads})
    symbols = sorted({str(payload["symbol"]) for payload in payloads}, key=lambda item: SYMBOL_PRIORITY[item])
    total_calendar_days = sum(
        int(payload["calendar_days"])
        for payload in payloads
        if str(payload["symbol"]) == symbols[0]
    )

    summaries: dict[str, dict[str, Any]] = {}
    selected_frames: list[pd.DataFrame] = []
    for phase in base.PHASE_OFFSETS:
        for scenario in SCENARIOS:
            selected = _arbitrate_and_nonoverlap(events, scenario, phase)
            selected["scenario"] = scenario
            selected["selected_phase"] = phase
            selected_frames.append(selected)
            summaries[f"{phase}:{scenario}"] = _summarize(
                selected,
                scenario=scenario,
                phase=phase,
                total_calendar_days=total_calendar_days,
            )
    selected_events = pd.concat(selected_frames, ignore_index=True) if selected_frames else pd.DataFrame()
    phase_spreads = [_phase_spread(summaries, scenario) for scenario in SCENARIOS]
    primary = summaries[f"quarter_hour:{PRIMARY_SCENARIO}"]
    period_values = list(primary.get("by_period", {}).values())
    positive_periods = sum(1 for item in period_values if item.get("net15_mean_bps", -math.inf) > 0.0)
    result = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_runs": len(payloads),
        "symbols": symbols,
        "period_labels": periods,
        "total_calendar_days": total_calendar_days,
        "frozen_contract": payloads[0]["frozen_contract"],
        "summaries": summaries,
        "phase_spreads": phase_spreads,
        "primary_robustness": {
            "positive_net15_periods": positive_periods,
            "total_periods_with_events": len(period_values),
            "positive_period_share": positive_periods / len(period_values) if period_values else None,
            "worst_period_net15_mean_bps": min(
                (item["net15_mean_bps"] for item in period_values), default=None
            ),
            "median_period_net15_mean_bps": float(np.median([
                item["net15_mean_bps"] for item in period_values
            ])) if period_values else None,
        },
        "selection_counts": {
            f"{key[0]}:{key[1]}": int(value)
            for key, value in selected_events.groupby(["selected_phase", "scenario"]).size().items()
        } if not selected_events.empty else {},
    }
    (output / "REPLICATION.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )
    if not selected_events.empty:
        selected_events.to_csv(output / "SELECTED_EVENTS.csv", index=False)
    else:
        pd.DataFrame().to_csv(output / "SELECTED_EVENTS.csv", index=False)
    summary_rows: list[dict[str, Any]] = []
    for key, item in summaries.items():
        net = item.get("cost_sensitivity", {}).get("net_15bp", {})
        summary_rows.append({
            "key": key,
            "phase": item.get("phase"),
            "scenario": item.get("scenario"),
            "completed_episodes": item.get("completed_episodes", 0),
            "episodes_per_calendar_day": item.get("episodes_per_calendar_day"),
            "gross_mean_bps": item.get("gross_mean_bps"),
            "net15_mean_bps": net.get("mean_bps"),
            "net15_hit_rate": net.get("hit_rate"),
            "net15_profit_factor": net.get("profit_factor"),
            "bootstrap_ci95_low": item.get("day_block_bootstrap_net15", {}).get("ci95", [None, None])[0],
            "bootstrap_ci95_high": item.get("day_block_bootstrap_net15", {}).get("ci95", [None, None])[1],
        })
    pd.DataFrame(summary_rows).to_csv(output / "SUMMARY.csv", index=False)
    _write_markdown(result, output)
    print(json.dumps({
        "source_runs": len(payloads),
        "calendar_days": total_calendar_days,
        "primary": summaries.get(f"quarter_hour:{PRIMARY_SCENARIO}"),
        "output": str(output),
    }, indent=2, default=_json_default))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--symbol", required=True)
    run.add_argument("--start", required=True)
    run.add_argument("--end", required=True)
    run.add_argument("--period-label", required=True)
    run.add_argument("--regime", required=True)
    run.add_argument("--output", required=True)
    run.add_argument("--cache", default=".cache/candidate-51-quarter-hour-replication")
    run.add_argument("--source-path", default="research/candidate-05")
    run.set_defaults(func=run_one)
    agg = sub.add_parser("aggregate")
    agg.add_argument("--results-root", required=True)
    agg.add_argument("--output", required=True)
    agg.add_argument("--expected-runs", type=int, default=0)
    agg.set_defaults(func=aggregate)
    return root


def main() -> None:
    args = parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
