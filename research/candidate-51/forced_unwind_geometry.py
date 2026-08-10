#!/usr/bin/env python3
"""Structural entry/invalidation/objective audit for accepted forced unwinds.

The v57 state audit found that price impulses accompanied by material open-
interest decline, aligned taker flow and last-15-minute acceptance had positive
4--8 hour continuation, while generic sponsored-build continuation failed.
This experiment freezes the state and tests whether it can be expressed as a
complete same-leg scenario rather than a time-return statistic.

No parameter search occurs.  Two logical invalidations are compared:
1. acceptance extreme: the adverse extreme of the final 15 completed minutes;
2. impulse origin: the close immediately before the completed impulse hour.

Three fixed objectives are compared: one impulse-body extension, 2R, and a time
exit with no price target.  Holds are 4h and 8h, matching the only v57 horizons
which survived initial state decomposition.  Minute bars are checksum verified;
no missing price is synthesized, and any geometry window crossing an archive
gap is discarded.  This is a path/geometry diagnostic, not an account engine.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import importlib.util
import json
import math
from pathlib import Path
import sys
from typing import Any, Iterable

import numpy as np
import pandas as pd

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
HOLDS_MIN = (240, 480)
ENTRY_MODES = ("direct", "delayed15")
STOP_MODES = ("acceptance_extreme", "impulse_origin")
TARGET_MODES = ("impulse_extension", "two_r", "time_only")
ROUND_TRIP_COST = 19.0 / 10_000.0


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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


def _profit_factor(values: np.ndarray) -> float | None:
    gains = float(values[values > 0.0].sum())
    losses = float(-values[values < 0.0].sum())
    return None if losses <= 0.0 else gains / losses


def _summary(values: Iterable[Any]) -> dict[str, Any]:
    clean = pd.to_numeric(pd.Series(values), errors="coerce").dropna().to_numpy(float)
    if clean.size == 0:
        return {"count": 0}
    positive = np.sort(clean[clean > 0.0])[::-1]
    positive_sum = float(positive.sum())
    without_best = np.delete(clean, int(np.argmax(clean))) if clean.size > 1 else np.array([])
    return {
        "count": int(clean.size),
        "mean": float(clean.mean()),
        "median": float(np.median(clean)),
        "win_rate": float(np.mean(clean > 0.0)),
        "profit_factor": _profit_factor(clean),
        "gross_profit": positive_sum,
        "gross_loss": float(-clean[clean < 0.0].sum()),
        "q10": float(np.quantile(clean, 0.10)),
        "q90": float(np.quantile(clean, 0.90)),
        "best": float(clean.max()),
        "worst": float(clean.min()),
        "mean_without_best": None if without_best.size == 0 else float(without_best.mean()),
        "top1_positive_share": None if positive_sum <= 0.0 else float(positive[:1].sum() / positive_sum),
    }


def _load_observed_minutes(
    *, symbol: str, start: date, end: date, cache: Path,
    candidate05: Path, candidate51: Path,
) -> tuple[pd.DataFrame, list[dict[str, Any]], list[pd.Timestamp]]:
    base = _load(candidate05 / "features.py", f"fug_base_{symbol}")
    parser = _load(candidate51 / "kline_only_inputs.py", f"fug_kline_{symbol}")
    frames: list[pd.DataFrame] = []
    evidence: list[dict[str, Any]] = []
    day = start
    while day <= end:
        archive, _, item = base.download_checked("klines", symbol, day, cache)
        frames.append(parser._read_kline(archive))
        evidence.append(asdict(item))
        day += timedelta(days=1)
    minute = pd.concat(frames, ignore_index=True).sort_values("close_time_dt")
    minute = minute.drop_duplicates("close_time_dt", keep="last").reset_index(drop=True)
    times = pd.DatetimeIndex(pd.to_datetime(minute["close_time_dt"], utc=True))
    expected = pd.date_range(
        pd.Timestamp(start, tz="UTC") + pd.Timedelta(seconds=59, milliseconds=999),
        pd.Timestamp(end + timedelta(days=1), tz="UTC") - pd.Timedelta(milliseconds=1),
        freq="min",
    )
    unexpected = times.difference(expected)
    if len(unexpected):
        raise RuntimeError(f"unexpected minute timestamps for {symbol}: {unexpected[:5].tolist()}")
    missing = list(expected.difference(times))
    return minute, evidence, missing


def _contiguous(times: pd.DatetimeIndex) -> bool:
    return len(times) <= 1 or bool(np.all(np.diff(times.asi8) == 60_000_000_000))


def _event_geometry(
    minute: pd.DataFrame, event: pd.Series, entry_mode: str,
    stop_mode: str, target_mode: str, hold_min: int,
) -> dict[str, Any] | None:
    side = int(event["side"])
    event_time = pd.Timestamp(event["event_time"])
    entry_signal_time = event_time if entry_mode == "direct" else event_time + pd.Timedelta(minutes=15)
    times = pd.DatetimeIndex(pd.to_datetime(minute["close_time_dt"], utc=True))
    entry_loc = int(times.searchsorted(entry_signal_time, side="right"))
    if entry_loc >= len(minute) or times[entry_loc] - entry_signal_time != pd.Timedelta(minutes=1):
        return None
    entry = float(minute.iloc[entry_loc]["open"])
    if not math.isfinite(entry) or entry <= 0.0:
        return None

    event_loc = int(times.searchsorted(event_time, side="right")) - 1
    acceptance_start = event_loc - 14
    impulse_start = event_loc - 59
    pre_impulse = impulse_start - 1
    if min(acceptance_start, impulse_start, pre_impulse) < 0:
        return None
    context_times = times[pre_impulse : entry_loc + 1]
    if not _contiguous(context_times):
        return None
    acceptance = minute.iloc[acceptance_start : event_loc + 1]
    impulse = minute.iloc[impulse_start : event_loc + 1]
    origin = float(minute.iloc[pre_impulse]["close"])
    impulse_close = float(minute.iloc[event_loc]["close"])
    impulse_body = abs(impulse_close - origin)
    if impulse_body <= 0.0:
        return None

    if stop_mode == "acceptance_extreme":
        stop = float(acceptance["low"].min()) if side > 0 else float(acceptance["high"].max())
    elif stop_mode == "impulse_origin":
        stop = origin
    else:
        raise ValueError(stop_mode)
    if side > 0 and not (0.0 < stop < entry):
        return None
    if side < 0 and not (stop > entry > 0.0):
        return None
    price_risk = abs(entry - stop)
    planned_loss_fraction = price_risk / entry + ROUND_TRIP_COST
    if planned_loss_fraction <= 0.0:
        return None

    if target_mode == "impulse_extension":
        target = entry + side * impulse_body
    elif target_mode == "two_r":
        target = entry + side * (2.0 * price_risk)
    elif target_mode == "time_only":
        target = None
    else:
        raise ValueError(target_mode)
    if target is not None:
        if side > 0 and not target > entry:
            return None
        if side < 0 and not 0.0 < target < entry:
            return None

    end_time = times[entry_loc] + pd.Timedelta(minutes=hold_min)
    end_loc = int(times.searchsorted(end_time, side="left"))
    if end_loc >= len(minute) or times[end_loc] != end_time:
        return None
    path_times = times[entry_loc : end_loc + 1]
    if len(path_times) != hold_min + 1 or not _contiguous(path_times):
        return None
    path = minute.iloc[entry_loc : end_loc + 1]

    exit_price = float(path.iloc[-1]["open"])
    exit_time = times[end_loc]
    outcome = "TIME"
    bars_held = hold_min
    for offset, bar in enumerate(path.itertuples(index=False)):
        hit_stop = float(bar.low) <= stop if side > 0 else float(bar.high) >= stop
        hit_target = False if target is None else (
            float(bar.high) >= target if side > 0 else float(bar.low) <= target
        )
        if hit_stop or hit_target:
            # Conservative OHLC ambiguity contract: if both are touched in the
            # same one-minute bar, the protective stop is assumed first.
            if hit_stop:
                exit_price = stop
                outcome = "STOP"
            else:
                assert target is not None
                exit_price = target
                outcome = "TARGET"
            exit_time = times[entry_loc + offset]
            bars_held = offset
            break

    gross_fraction = side * (exit_price / entry - 1.0)
    net_fraction = gross_fraction - ROUND_TRIP_COST
    r_multiple = net_fraction / planned_loss_fraction
    favourable = (
        path["high"] / entry - 1.0 if side > 0 else 1.0 - path["low"] / entry
    )
    adverse = (
        path["low"] / entry - 1.0 if side > 0 else 1.0 - path["high"] / entry
    )
    return {
        "entry_mode": entry_mode,
        "stop_mode": stop_mode,
        "target_mode": target_mode,
        "hold_min": hold_min,
        "entry_time_geometry": times[entry_loc],
        "entry_price_geometry": entry,
        "stop_price": stop,
        "target_price": target,
        "price_risk_fraction": price_risk / entry,
        "planned_loss_fraction": planned_loss_fraction,
        "impulse_body_fraction": impulse_body / entry,
        "acceptance_range_fraction": (float(acceptance["high"].max()) - float(acceptance["low"].min())) / entry,
        "exit_time_geometry": exit_time,
        "exit_price_geometry": exit_price,
        "outcome": outcome,
        "bars_held": bars_held,
        "gross_fraction": gross_fraction,
        "net_fraction": net_fraction,
        "r_multiple": r_multiple,
        "mfe_fraction": float(favourable.max()),
        "mae_fraction": float(adverse.min()),
    }


def run_one(args: argparse.Namespace) -> None:
    events = pd.read_csv(args.events)
    events["event_time"] = pd.to_datetime(events["event_time"], utc=True, format="mixed")
    events = events[
        events["period_label"].eq(args.period_label)
        & events["state"].eq("FORCED_UNWIND_ACCEPTED")
    ].copy()
    if events.empty:
        raise RuntimeError(f"no accepted forced-unwind events in {args.period_label}")
    priority = {"public_vectorized_no_ema": 0, "impulse_only_2atr": 1}
    events["family_priority"] = events["family"].map(priority).fillna(9)
    events = events.sort_values(
        ["symbol", "event_time", "family_priority", "impulse_atr"],
        ascending=[True, True, True, False], kind="stable",
    ).drop_duplicates(["symbol", "event_time"], keep="first")

    start = date.fromisoformat(args.start) - timedelta(days=1)
    end = date.fromisoformat(args.end) + timedelta(days=1)
    records: list[dict[str, Any]] = []
    source: dict[str, Any] = {}
    for symbol in SYMBOLS:
        symbol_events = events[events["symbol"].eq(symbol)]
        if symbol_events.empty:
            continue
        minute, evidence, missing = _load_observed_minutes(
            symbol=symbol, start=start, end=end, cache=Path(args.cache) / symbol,
            candidate05=Path(args.candidate05_path), candidate51=Path(args.candidate51_path),
        )
        source[symbol] = {
            "evidence": evidence,
            "missing_close_times": [value.isoformat() for value in missing],
        }
        for _, event in symbol_events.iterrows():
            base = event.to_dict()
            base["causal_episode_id"] = f"{symbol}:{pd.Timestamp(event['event_time']).isoformat()}"
            for entry_mode in ENTRY_MODES:
                for stop_mode in STOP_MODES:
                    for target_mode in TARGET_MODES:
                        for hold_min in HOLDS_MIN:
                            geometry = _event_geometry(
                                minute, event, entry_mode, stop_mode, target_mode, hold_min
                            )
                            if geometry is not None:
                                records.append({**base, **geometry})
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "period_label": args.period_label,
        "split": args.split,
        "start": args.start,
        "end": args.end,
        "round_trip_cost_fraction": ROUND_TRIP_COST,
        "state": "FORCED_UNWIND_ACCEPTED",
        "frozen_geometry": {
            "entry_modes": list(ENTRY_MODES),
            "stop_modes": list(STOP_MODES),
            "target_modes": list(TARGET_MODES),
            "holds_min": list(HOLDS_MIN),
            "same_bar_ambiguity": "stop_first",
        },
        "source": source,
        "records": records,
    }
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "result.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"period": args.period_label, "episodes": events["event_time"].nunique(), "records": len(records)}, indent=2))


def _one_slot(frame: pd.DataFrame, hold_min: int) -> pd.DataFrame:
    choices = frame.sort_values(
        ["entry_time_geometry", "impulse_atr", "breadth", "symbol"],
        ascending=[True, False, False, True], kind="stable",
    ).drop_duplicates("causal_episode_id", keep="first")
    selected: list[int] = []
    occupied_until: pd.Timestamp | None = None
    for idx, row in choices.iterrows():
        entry = pd.Timestamp(row["entry_time_geometry"])
        if occupied_until is not None and entry < occupied_until:
            continue
        selected.append(idx)
        occupied_until = entry + pd.Timedelta(minutes=hold_min)
    return choices.loc[selected].copy()


def aggregate(args: argparse.Namespace) -> None:
    paths = sorted(Path(args.results_root).rglob("result.json"))
    if not paths:
        raise RuntimeError("no result files")
    payloads = [json.loads(path.read_text()) for path in paths]
    records = pd.concat([pd.DataFrame(payload["records"]) for payload in payloads], ignore_index=True)
    if records.empty:
        raise RuntimeError("no valid geometry records")
    for column in ("event_time", "entry_time_geometry", "exit_time_geometry"):
        records[column] = pd.to_datetime(records[column], utc=True, format="mixed")

    rows: list[dict[str, Any]] = []
    decisions: dict[str, Any] = {}
    keys = ["entry_mode", "stop_mode", "target_mode", "hold_min"]
    for key, group in records.groupby(keys, sort=True):
        entry_mode, stop_mode, target_mode, hold_min = key
        hold_min = int(hold_min)
        selected = _one_slot(group, hold_min)
        split_stats = {
            str(split): _summary(part["r_multiple"])
            for split, part in selected.groupby("split", sort=True)
        }
        period_stats = {
            str(period): _summary(part["r_multiple"])
            for period, part in selected.groupby("period_label", sort=True)
        }
        overall = _summary(selected["r_multiple"])
        net = _summary(selected["net_fraction"])
        outcome_counts = selected["outcome"].value_counts().sort_index().to_dict()
        positive_periods = sum(1 for item in period_stats.values() if item.get("mean", 0.0) > 0.0)
        populated_periods = sum(1 for item in period_stats.values() if item.get("count", 0))
        post = split_stats.get("post_publication", {})
        reasons: list[str] = []
        if overall.get("mean", 0.0) <= 0.0 or (overall.get("profit_factor") or 0.0) <= 1.0:
            reasons.append("one-slot R expectancy is non-positive")
        if net.get("mean", 0.0) <= 0.0:
            reasons.append("one-slot net return is non-positive")
        if overall.get("mean_without_best") is not None and overall["mean_without_best"] <= 0.0:
            reasons.append("best episode removal eliminates R expectancy")
        if post.get("count", 0) and post.get("mean", 0.0) <= 0.0:
            reasons.append("post-publication R expectancy is non-positive")
        if populated_periods and positive_periods / populated_periods < 0.60:
            reasons.append(f"fewer than 60% of periods are positive ({positive_periods}/{populated_periods})")
        if overall.get("median", 0.0) <= 0.0:
            reasons.append("median trade R is non-positive")
        label = f"{entry_mode}:{stop_mode}:{target_mode}:{hold_min}m"
        decision = {
            "configuration": {
                "entry_mode": entry_mode, "stop_mode": stop_mode,
                "target_mode": target_mode, "hold_min": hold_min,
            },
            "episodes": int(len(selected)),
            "episodes_per_evaluation_day": float(len(selected) / 140.0),
            "r_multiple": overall,
            "net_fraction": net,
            "by_split_r": split_stats,
            "by_period_r": period_stats,
            "outcome_counts": outcome_counts,
            "status": "geometry_survives_initial_falsification" if not reasons else "geometry_rejected",
            "reasons": reasons,
        }
        decisions[label] = decision
        rows.append({
            "configuration": label, "status": decision["status"],
            "episodes": len(selected), "episodes_per_day": decision["episodes_per_evaluation_day"],
            "mean_r": overall.get("mean"), "median_r": overall.get("median"),
            "win_rate_r": overall.get("win_rate"), "profit_factor_r": overall.get("profit_factor"),
            "mean_r_without_best": overall.get("mean_without_best"),
            "mean_net_fraction": net.get("mean"),
            "reasons": " | ".join(reasons),
        })

    surviving = [name for name, item in decisions.items() if item["status"] == "geometry_survives_initial_falsification"]
    result = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_runs": len(payloads),
        "evaluation_days": 140,
        "unique_episodes": int(records["causal_episode_id"].nunique()),
        "round_trip_cost_fraction": ROUND_TRIP_COST,
        "decisions": decisions,
        "surviving_geometries": surviving,
        "decision": "promote_best_fixed_geometry_to_nautilus" if surviving else "do_not_promote_forced_unwind_geometry",
    }
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "GEOMETRY.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )
    table = pd.DataFrame(rows).sort_values(
        ["status", "mean_r", "episodes"], ascending=[True, False, False]
    )
    table.to_csv(output / "GEOMETRY.csv", index=False)
    records.to_csv(output / "RECORDS.csv", index=False)
    md = [
        "# Accepted forced-unwind structural geometry", "",
        f"- source periods: {len(payloads)}", f"- unique episodes: {result['unique_episodes']}",
        f"- decision: **{result['decision']}**", f"- cost screen: {ROUND_TRIP_COST*10000:.0f} bp round trip",
        "- no threshold search; all entry, stop, target and hold choices were frozen before this audit", "",
        "| configuration | trades | trades/day | mean R | median R | PF | net bp | mean R ex-best | status |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in table.to_dict(orient="records"):
        pf = row.get("profit_factor_r")
        md.append(
            f"| {row['configuration']} | {int(row['episodes'])} | {row['episodes_per_day']:.3f} | "
            f"{(row.get('mean_r') or 0):.3f} | {(row.get('median_r') or 0):.3f} | "
            f"{'na' if pd.isna(pf) else f'{pf:.2f}'} | {10000*(row.get('mean_net_fraction') or 0):.2f} | "
            f"{(row.get('mean_r_without_best') or 0):.3f} | {row['status']} |"
        )
    md += ["", "A time-return clue is promoted only when a logical invalidation and same-leg objective preserve positive after-cost R across chronology, after one-slot arbitration and after removing the best episode. A surviving geometry still requires NautilusTrader account validation; a failed geometry means the v57 state remains descriptive rather than tradable.", ""]
    (output / "GEOMETRY.md").write_text("\n".join(md), encoding="utf-8")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    for name in ("period_label", "split", "start", "end", "output"):
        run.add_argument(f"--{name.replace('_', '-')}", dest=name, required=True)
    run.add_argument("--events", default="research/candidate-51/evidence/derivatives-impulse-v57/EVENTS.csv")
    run.add_argument("--cache", default=".cache/candidate-51-forced-unwind-geometry")
    run.add_argument("--candidate05-path", default="research/candidate-05")
    run.add_argument("--candidate51-path", default="research/candidate-51")
    run.set_defaults(func=run_one)
    agg = sub.add_parser("aggregate")
    agg.add_argument("--results-root", required=True)
    agg.add_argument("--output", required=True)
    agg.set_defaults(func=aggregate)
    return root


def main() -> None:
    args = parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
