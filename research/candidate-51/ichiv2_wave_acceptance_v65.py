#!/usr/bin/env python3
"""Fresh-data causal lifecycle and acceptance audit for the ichiV2 fan engine.

The v63 anatomy found a real but weak four-asset one-slot clue: 36 selected
trades had positive ex-best expectancy and a 60-minute favorable-excursion
median above cost, yet the final strategy was sparse and nine hard stops erased
most of the ROI gross profit.  Inspection also showed that short interruptions
of the 1.002 fan-gain condition split one market expansion into repeated signal
IDs and sequential re-entries.

This experiment was specified after v63, so it uses five new chronological
periods which v63 did not evaluate.  It does not search a number or threshold.
It compares three causal opportunity clocks while retaining the public ROI,
stop, exit and 19bp cost:

1. ``continuous_boolean_run`` -- the v63 baseline, first bar of each literal
   entry-signal run;
2. ``fan_wave`` -- one entry per expansion wave.  A wave ends only after the
   public EMA5/EMA120 exit, fan magnitude <= 1, or three consecutive completed
   fan declines (the same public three-bar fan memory);
3. ``fan_wave_one_bar_acceptance`` -- after the first wave signal, one completed
   five-minute bar must close above the signal close while fan magnitude remains
   higher; entry is the following open.

The prediction is explicit: the wave clock should remove repeated trades from
one causal expansion; one-bar acceptance should reduce immediate <=10 minute
hard stops, at the cost of missing some fastest winners.  If stop concentration
does not fall and ex-best expectancy does not improve, the acceptance repair is
rejected.  No v63 period is used to judge that repair.  This remains a path
mechanism diagnostic, not final account evidence.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import importlib.util
import json
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
POLICY_ORDER = (
    "continuous_boolean_run",
    "fan_wave",
    "fan_wave_one_bar_acceptance",
)
HERE = Path(__file__).resolve().parent


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


V63 = _load(HERE / "ichiv2_claim_anatomy_v63.py", "candidate51_ichiv2_v63_for_v65")
V59 = _load(HERE / "forced_unwind_geometry_v59_fixed.py", "candidate51_v59_for_v65")


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    raise TypeError(type(value))


def _segmented_features(minute: pd.DataFrame) -> pd.DataFrame:
    five = V63._complete_five_minute_bars(minute)
    if five.empty:
        return five
    times = pd.DatetimeIndex(five["open_time"])
    five["segment_id"] = (
        times.to_series(index=five.index)
        .diff()
        .ne(pd.Timedelta(minutes=5))
        .cumsum()
        .astype(int)
    )
    pieces: list[pd.DataFrame] = []
    for segment_id, group in five.groupby("segment_id", sort=True):
        source = group.drop(columns=["segment_id"]).reset_index(drop=True)
        enriched = V63._features(source)
        enriched["segment_id"] = int(segment_id)
        enriched["segment_bar_index"] = np.arange(len(enriched), dtype=int)
        pieces.append(enriched)
    return pd.concat(pieces, ignore_index=True)


def _wave_start_indices(frame: pd.DataFrame) -> list[int]:
    starts: list[int] = []
    active = False
    decline_count = 0
    prior_fan: float | None = None
    for index, row in frame.iterrows():
        fan = float(row["fan_magnitude"]) if pd.notna(row["fan_magnitude"]) else math.nan
        if active:
            if bool(row["exit_signal"]) or (math.isfinite(fan) and fan <= 1.0):
                active = False
                decline_count = 0
            elif prior_fan is not None and math.isfinite(fan):
                decline_count = decline_count + 1 if fan < prior_fan else 0
                if decline_count >= V63.FAN_SHIFT_COUNT:
                    active = False
                    decline_count = 0
        if not active and bool(row["entry_signal"]):
            starts.append(int(index))
            active = True
            decline_count = 0
        prior_fan = fan if math.isfinite(fan) else prior_fan
    return starts


def _accepted_index(frame: pd.DataFrame, signal_index: int) -> int | None:
    acceptance_index = signal_index + 1
    entry_index = acceptance_index + 1
    if entry_index >= len(frame):
        return None
    signal = frame.iloc[signal_index]
    acceptance = frame.iloc[acceptance_index]
    required = (
        signal["close"],
        signal["fan_magnitude"],
        acceptance["close"],
        acceptance["fan_magnitude"],
        acceptance["close_ema_5"],
        acceptance["close_ema_120"],
    )
    if not all(math.isfinite(float(value)) for value in required):
        return None
    accepted = (
        float(acceptance["close"]) > float(signal["close"])
        and float(acceptance["fan_magnitude"]) > float(signal["fan_magnitude"])
        and float(acceptance["close_ema_5"]) > float(acceptance["close_ema_120"])
    )
    return acceptance_index if accepted else None


def _policy_indices(frame: pd.DataFrame) -> dict[str, list[tuple[int, int, str]]]:
    continuous = [int(value) for value in frame.index[frame["signal_episode_start"]]]
    waves = _wave_start_indices(frame)
    accepted: list[tuple[int, int, str]] = []
    for original in waves:
        acceptance = _accepted_index(frame, original)
        if acceptance is not None:
            accepted.append((original, acceptance, "one_completed_bar_accepted"))
    return {
        "continuous_boolean_run": [(index, index, "immediate") for index in continuous],
        "fan_wave": [(index, index, "immediate") for index in waves],
        "fan_wave_one_bar_acceptance": accepted,
    }


def _records_for_symbol(
    *,
    symbol: str,
    features: pd.DataFrame,
    start: date,
    end: date,
    period_label: str,
    split: str,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for segment_id, segment in features.groupby("segment_id", sort=True):
        source = segment.drop(columns=["segment_id"]).reset_index(drop=True)
        for policy, indices in _policy_indices(source).items():
            for original_index, decision_index, transition in indices:
                signal_time = pd.Timestamp(source.iloc[original_index]["close_time"])
                if not (start <= signal_time.date() <= end):
                    continue
                path = V63._simulate_source_path(source, decision_index)
                if path is None:
                    continue
                original = source.iloc[original_index]
                decision = source.iloc[decision_index]
                records.append(
                    {
                        "event_id": f"{policy}:{symbol}:{signal_time.isoformat()}:{segment_id}",
                        "symbol": symbol,
                        "period_label": period_label,
                        "split": split,
                        "policy": policy,
                        "segment_id": int(segment_id),
                        "original_signal_time": signal_time,
                        "decision_time": pd.Timestamp(decision["close_time"]),
                        "entry_transition": transition,
                        "signal_to_decision_minutes": float(
                            (pd.Timestamp(decision["close_time"]) - signal_time).total_seconds() / 60.0
                        ),
                        "signal_close": float(original["close"]),
                        "decision_close": float(decision["close"]),
                        "signal_fan_magnitude": float(original["fan_magnitude"]),
                        "decision_fan_magnitude": float(decision["fan_magnitude"]),
                        "decision_fan_gain": float(decision["fan_magnitude_gain"]),
                        **path,
                    }
                )
    return records


def _same_clock(group: pd.DataFrame) -> pd.Series:
    work = group.copy()
    work["symbol_priority"] = work["symbol"].map(V63.SYMBOL_PRIORITY).fillna(99).astype(int)
    return work.sort_values(
        ["decision_fan_gain", "decision_fan_magnitude", "symbol_priority", "event_id"],
        ascending=[False, False, True, True],
        kind="stable",
    ).iloc[0]


def _one_slot(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if frame.empty:
        return frame.copy(), frame.copy()
    candidates = pd.DataFrame(
        [_same_clock(group) for _, group in frame.groupby("entry_time", sort=True)]
    ).sort_values(["entry_time", "event_id"], kind="stable")
    selected: list[int] = []
    rejected: list[dict[str, Any]] = []
    occupied_until: pd.Timestamp | None = None
    for index, row in candidates.iterrows():
        entry = pd.Timestamp(row["entry_time"])
        if occupied_until is not None and entry < occupied_until:
            rejected.append({**row.to_dict(), "rejection_reason": "GLOBAL_SLOT_OCCUPIED"})
            continue
        selected.append(index)
        occupied_until = pd.Timestamp(row["exit_time"])
    return candidates.loc[selected].copy(), pd.DataFrame(rejected)


def _summary(series: pd.Series) -> dict[str, Any]:
    values = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna().to_numpy(float)
    if values.size == 0:
        return {"count": 0}
    gains = float(values[values > 0.0].sum())
    losses = float(-values[values < 0.0].sum())
    without_best = np.delete(values, int(np.argmax(values))) if values.size > 1 else np.array([], dtype=float)
    return {
        "count": int(values.size),
        "mean": float(values.mean()),
        "median": float(np.median(values)),
        "win_rate": float(np.mean(values > 0.0)),
        "profit_factor": None if losses <= 0.0 else gains / losses,
        "gross_profit": gains,
        "gross_loss": losses,
        "best": float(values.max()),
        "worst": float(values.min()),
        "mean_without_best": None if without_best.size == 0 else float(without_best.mean()),
    }


def _groups(frame: pd.DataFrame, column: str) -> dict[str, Any]:
    if frame.empty or column not in frame.columns:
        return {}
    return {str(key): _summary(group["net_return"]) for key, group in frame.groupby(column, sort=True, dropna=False)}


def _policy_result(selected: pd.DataFrame, rejected: pd.DataFrame, calendar_days: int) -> dict[str, Any]:
    immediate_stops = selected[
        selected["exit_reason"].eq("stoploss")
        & (pd.to_numeric(selected["hold_minutes"], errors="coerce") <= 10.0001)
    ]
    return {
        "trades": int(len(selected)),
        "trades_per_day": float(len(selected) / max(calendar_days, 1)),
        "net_return": _summary(selected["net_return"]),
        "r_multiple": _summary(selected["r_multiple"]),
        "by_period": _groups(selected, "period_label"),
        "by_split": _groups(selected, "split"),
        "by_symbol": _groups(selected, "symbol"),
        "by_exit_reason": _groups(selected, "exit_reason"),
        "hard_stop_count": int(selected["exit_reason"].eq("stoploss").sum()),
        "immediate_stop_count": int(len(immediate_stops)),
        "immediate_stop_rate": float(len(immediate_stops) / max(len(selected), 1)),
        "collision_rejections": int(len(rejected)),
    }


def run_one(args: argparse.Namespace) -> None:
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    warm_start = start - timedelta(days=4)
    forward_end = end + timedelta(days=2)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    loader = V59._load_target()
    loader._contiguous = V59._contiguous
    records: list[dict[str, Any]] = []
    source: dict[str, Any] = {}
    for symbol in SYMBOLS:
        minute, evidence, missing = loader._load_observed_minutes(
            symbol=symbol,
            start=warm_start,
            end=forward_end,
            cache=Path(args.cache) / symbol,
            candidate05=Path(args.candidate05_path),
            candidate51=Path(args.candidate51_path),
        )
        features = _segmented_features(minute)
        records.extend(
            _records_for_symbol(
                symbol=symbol,
                features=features,
                start=start,
                end=end,
                period_label=args.period_label,
                split=args.split,
            )
        )
        source[symbol] = {
            "evidence": evidence,
            "missing_minute_close_times": [value.isoformat() for value in missing],
            "five_minute_rows": int(len(features)),
            "segments": int(features["segment_id"].nunique()) if not features.empty else 0,
        }
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "period_label": args.period_label,
        "split": args.split,
        "start": args.start,
        "end": args.end,
        "calendar_days": (end - start).days + 1,
        "frozen_contract": {
            "source_rules": "unchanged v63 report-compatible ichiV2 ROI/stop/exit/cost",
            "wave_end": "EMA5/EMA120 exit, fan <= 1, or three consecutive completed fan declines",
            "acceptance": "next completed 5m close above signal close, fan magnitude higher, EMA5 above EMA120",
            "entry_after_acceptance": "following five-minute open",
            "threshold_search": "none",
            "evaluation_periods": "not present in v63",
            "one_global_slot": True,
        },
        "source": source,
        "records": records,
    }
    (output / "result.json").write_text(json.dumps(payload, indent=2, sort_keys=True, default=_json_default) + "\n")


def aggregate(args: argparse.Namespace) -> None:
    paths = sorted(Path(args.results_root).rglob("result.json"))
    if not paths:
        raise RuntimeError("no result files")
    payloads = [json.loads(path.read_text()) for path in paths]
    frames = [pd.DataFrame(payload["records"]) for payload in payloads if payload.get("records")]
    records = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    for column in ("original_signal_time", "decision_time", "entry_time", "exit_time"):
        if column in records.columns:
            records[column] = pd.to_datetime(records[column], utc=True, errors="coerce")
    calendar_days = sum(int(payload["calendar_days"]) for payload in payloads)
    results: dict[str, Any] = {}
    selected_outputs: list[pd.DataFrame] = []
    rejected_outputs: list[pd.DataFrame] = []
    for policy in POLICY_ORDER:
        source = records[records["policy"].eq(policy)].copy()
        selected, rejected = _one_slot(source)
        selected["policy"] = policy
        if not rejected.empty:
            rejected["policy"] = policy
        results[policy] = _policy_result(selected, rejected, calendar_days)
        selected_outputs.append(selected)
        rejected_outputs.append(rejected)
    baseline = results["continuous_boolean_run"]
    wave = results["fan_wave"]
    accepted = results["fan_wave_one_bar_acceptance"]
    assessment = {
        "wave_clock_reduces_literal_signal_fragmentation": wave["trades"] < baseline["trades"],
        "acceptance_reduces_immediate_stop_rate": accepted["immediate_stop_rate"] < wave["immediate_stop_rate"],
        "acceptance_improves_ex_best_expectancy": accepted["net_return"].get("mean_without_best", -math.inf) > wave["net_return"].get("mean_without_best", -math.inf),
        "acceptance_retains_nonzero_fresh_opportunities": accepted["trades"] > 0,
    }
    result = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_runs": len(payloads),
        "calendar_days": calendar_days,
        "raw_policy_records": int(len(records)),
        "policies": results,
        "predeclared_assessment": assessment,
        "diagnostic_conclusion": "wave_acceptance_supported" if all(assessment.values()) else "wave_acceptance_not_supported",
        "next_inference": (
            "If supported, preserve the fan-wave opportunity clock and acceptance transition as a candidate component, then diagnose its remaining stop/ROI geometry on a new period. If unsupported, retain only v63 favorable-excursion evidence and stop modifying this public family."
        ),
        "truth_boundary": "Fresh-data path anatomy is not a continuous NautilusTrader account and does not meet the final frequency or growth target.",
    }
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "ANATOMY.json").write_text(json.dumps(result, indent=2, sort_keys=True, default=_json_default) + "\n")
    records.to_csv(output / "CANDIDATES.csv", index=False)
    pd.concat(selected_outputs, ignore_index=True).to_csv(output / "SELECTED.csv", index=False)
    nonempty = [frame for frame in rejected_outputs if not frame.empty]
    (pd.concat(nonempty, ignore_index=True) if nonempty else pd.DataFrame()).to_csv(output / "REJECTED.csv", index=False)
    lines = [
        "# ichiV2 fan-wave acceptance v65",
        "",
        f"- fresh sampled days: {calendar_days}",
        f"- conclusion: **{result['diagnostic_conclusion']}**",
        "",
        "| policy | trades | trades/day | mean net | median net | PF | ex-best net | hard stops | <=10m stops |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for policy in POLICY_ORDER:
        value = results[policy]
        net = value["net_return"]
        pf = net.get("profit_factor")
        lines.append(
            f"| {policy} | {value['trades']} | {value['trades_per_day']:.3f} | {100 * net.get('mean', 0.0):.3f}% | {100 * net.get('median', 0.0):.3f}% | {'na' if pf is None else f'{pf:.2f}'} | {100 * net.get('mean_without_best', 0.0):.3f}% | {value['hard_stop_count']} | {value['immediate_stop_count']} |"
        )
    lines += ["", "## Predeclared assessment", ""]
    for key, value in assessment.items():
        lines.append(f"- {key}: `{value}`")
    lines += ["", "## Next inference", "", result["next_inference"], "", "## Truth boundary", "", result["truth_boundary"], ""]
    (output / "ANATOMY.md").write_text("\n".join(lines))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    for name in ("start", "end", "period_label", "split", "output"):
        run.add_argument(f"--{name.replace('_', '-')}", dest=name, required=True)
    run.add_argument("--cache", default=".cache/candidate-51-ichiv2-wave-v65")
    run.add_argument("--candidate05-path", default="research/candidate-05")
    run.add_argument("--candidate51-path", default="research/candidate-51")
    run.set_defaults(func=run_one)
    aggregate_parser = sub.add_parser("aggregate")
    aggregate_parser.add_argument("--results-root", required=True)
    aggregate_parser.add_argument("--output", required=True)
    aggregate_parser.set_defaults(func=aggregate)
    return root


def main() -> None:
    args = parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
