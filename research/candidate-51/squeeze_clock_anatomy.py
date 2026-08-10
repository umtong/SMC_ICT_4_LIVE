#!/usr/bin/env python3
"""Causal clock audit for the recovered public 4h squeeze-release system.

The public backtester resamples one-minute data with pandas defaults (left
labels) and then exposes every resampled row whose *label* is <= current time.
A completed 4h bar is therefore visible at the beginning of its own four-hour
window. The same problem affects the 1h ATR bar.

This experiment freezes the published signal rules and compares only two
clocks: ``source_label_left`` reproduces that availability bug;
``causal_completed`` makes a bar available only after completion. One 4h
release is one causal episode and can create at most one observation. No
parameter search is performed. The output is a diagnostic, not a deployment
backtest.
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
from typing import Any

import numpy as np
import pandas as pd

SOURCE = {
    "repository": "jicheolha/crypto-trading-bot",
    "commit": "99cfa582b239fd9c59a5ac92618a3e36bb73ed76",
    "signal_timeframe": "4h", "atr_timeframe": "1h", "trade_timeframe": "1m",
    "parameters": {
        "bb_period": 19, "bb_std": 2.47, "kc_period": 17,
        "kc_atr_mult": 2.38, "momentum_period": 12, "rsi_period": 14,
        "volume_period": 20, "atr_period": 14, "min_squeeze_bars": 2,
        "min_volume_ratio": 1.0, "rsi_overbought": 75.0,
        "rsi_oversold": 25.0, "atr_stop_mult": 3.45,
        "atr_target_mult": 4.0,
    },
}
CLOCKS = ("source_label_left", "causal_completed")
HORIZONS_MIN = (30, 60, 120, 240, 480, 720, 1440)
ROUND_TRIP_COST_BPS = 19.0
DAYTRADE_LIMITS_MIN = (720, 1440)
MAX_SOURCE_HOLD_MIN = 7 * 24 * 60


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _json_default(value: Any) -> Any:
    if isinstance(value, np.integer): return int(value)
    if isinstance(value, np.floating): return None if not np.isfinite(value) else float(value)
    if isinstance(value, np.bool_): return bool(value)
    if isinstance(value, (pd.Timestamp, datetime, date)): return value.isoformat()
    if isinstance(value, Path): return str(value)
    raise TypeError(type(value))


def _safe_float(value: Any) -> float | None:
    try: number = float(value)
    except (TypeError, ValueError): return None
    return number if math.isfinite(number) else None


def load_minutes(*, symbol: str, start: date, end: date, cache: Path,
                 candidate05_path: Path, candidate51_path: Path) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    source = _load_module(candidate05_path / "features.py", "c51_squeeze_features")
    read_kline = _load_module(candidate51_path / "kline_only_inputs.py", "c51_squeeze_kline")._read_kline
    frames, evidence = [], []
    day = start
    while day <= end:
        archive, _, item = source.download_checked("klines", symbol, day, cache)
        frames.append(read_kline(archive)); evidence.append(asdict(item)); day += timedelta(days=1)
    raw = pd.concat(frames, ignore_index=True).sort_values("open_time_dt")
    raw = raw.drop_duplicates("open_time_dt", keep="last").set_index("open_time_dt")
    for column in ("open", "high", "low", "close", "volume"):
        raw[column] = pd.to_numeric(raw[column], errors="raise").astype(float)
    return raw[["open", "high", "low", "close", "volume"]], evidence


def resample_ohlcv(minutes: pd.DataFrame, rule: str) -> pd.DataFrame:
    frame = minutes.resample(rule, label="left", closed="left").agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"), minute_count=("close", "count"))
    expected = int(pd.Timedelta(rule) / pd.Timedelta(minutes=1))
    return frame[frame["minute_count"].eq(expected)].drop(columns="minute_count")


def indicators(frame: pd.DataFrame) -> pd.DataFrame:
    p = SOURCE["parameters"]; result = frame.copy()
    result["bb_mid"] = result["close"].rolling(p["bb_period"]).mean()
    result["bb_std"] = result["close"].rolling(p["bb_period"]).std()
    result["bb_upper"] = result["bb_mid"] + p["bb_std"] * result["bb_std"]
    result["bb_lower"] = result["bb_mid"] - p["bb_std"] * result["bb_std"]
    result["bb_width"] = (result["bb_upper"] - result["bb_lower"]) / result["bb_mid"]
    tr = pd.concat([result["high"] - result["low"],
                    (result["high"] - result["close"].shift()).abs(),
                    (result["low"] - result["close"].shift()).abs()], axis=1).max(axis=1)
    result["atr"] = tr.rolling(p["atr_period"]).mean()
    result["kc_mid"] = result["close"].ewm(span=p["kc_period"], adjust=False).mean()
    result["kc_upper"] = result["kc_mid"] + p["kc_atr_mult"] * result["atr"]
    result["kc_lower"] = result["kc_mid"] - p["kc_atr_mult"] * result["atr"]
    result["squeeze"] = (result["bb_lower"] > result["kc_lower"]) & (result["bb_upper"] < result["kc_upper"])
    result["squeeze_duration"] = result["squeeze"].groupby((~result["squeeze"]).cumsum()).cumsum()
    result["momentum"] = result["close"] - result["bb_mid"].shift(p["momentum_period"])
    result["momentum_norm"] = result["momentum"] / result["atr"]
    delta = result["close"].diff()
    gain = delta.where(delta > 0.0, 0.0).rolling(p["rsi_period"]).mean()
    loss = (-delta.where(delta < 0.0, 0.0)).rolling(p["rsi_period"]).mean()
    result["rsi"] = 100.0 - 100.0 / (1.0 + gain / loss.replace(0.0, np.nan))
    result["volume_ma"] = result["volume"].rolling(p["volume_period"]).mean()
    result["volume_ratio"] = result["volume"] / result["volume_ma"]
    return result


def release_events(signal: pd.DataFrame) -> pd.DataFrame:
    p = SOURCE["parameters"]; prior = signal.shift(1)
    released = prior["squeeze"].fillna(False).astype(bool) & ~signal["squeeze"].fillna(False).astype(bool)
    eligible = released & prior["squeeze_duration"].ge(p["min_squeeze_bars"]) & signal["volume_ratio"].ge(p["min_volume_ratio"])
    output = signal.loc[eligible].copy()
    if output.empty: return output
    direction = np.where(output["close"] > output["bb_upper"], 1,
                 np.where(output["close"] < output["bb_lower"], -1, np.sign(output["momentum"])))
    output["side"] = direction.astype(int); output = output[output["side"].ne(0)].copy()
    output = output[~((output["side"].gt(0) & output["rsi"].gt(p["rsi_overbought"])) |
                      (output["side"].lt(0) & output["rsi"].lt(p["rsi_oversold"])))].copy()
    output["squeeze_bars"] = prior.loc[output.index, "squeeze_duration"].astype(int)
    output["event_time"] = output.index
    output["event_id"] = [f"{ts.isoformat()}:{int(side):+d}" for ts, side in zip(output.index, output["side"], strict=True)]
    output["outside_band"] = ((output["side"].gt(0) & output["close"].gt(output["bb_upper"])) |
                              (output["side"].lt(0) & output["close"].lt(output["bb_lower"])))
    output["source_score"] = (0.5 + np.minimum(output["squeeze_bars"] / 20.0, 0.2) +
                              np.minimum(output["volume_ratio"] / 3.0, 0.15) +
                              np.minimum(output["momentum_norm"].abs() / 2.0, 0.15))
    return output


def _at_or_after(index: pd.DatetimeIndex, timestamp: pd.Timestamp) -> int | None:
    position = int(index.searchsorted(timestamp, side="left"))
    return position if position < len(index) else None


def _completed_atr(hourly: pd.DataFrame, event_start: pd.Timestamp, clock: str) -> float | None:
    label = event_start if clock == "source_label_left" else event_start + pd.Timedelta(hours=3)
    if label not in hourly.index: return None
    return _safe_float(hourly.at[label, "atr"])


def _entry_timestamp(event_start: pd.Timestamp, clock: str) -> pd.Timestamp:
    return event_start if clock == "source_label_left" else event_start + pd.Timedelta(hours=4)


def _path_metrics(minutes: pd.DataFrame, *, side: int, entry_time: pd.Timestamp,
                  atr: float, clock: str) -> dict[str, Any] | None:
    index = minutes.index; start_pos = _at_or_after(index, entry_time)
    if start_pos is None: return None
    entry_row = minutes.iloc[start_pos]
    entry = float(entry_row["close"] if clock == "source_label_left" else entry_row["open"])
    if not math.isfinite(entry) or entry <= 0.0 or not math.isfinite(atr) or atr <= 0.0: return None
    p = SOURCE["parameters"]
    stop = entry - side * p["atr_stop_mult"] * atr
    target = entry + side * p["atr_target_mult"] * atr
    if stop <= 0.0 or target <= 0.0: return None
    stop_distance = abs(entry - stop)
    result: dict[str, Any] = {"entry_time": index[start_pos], "entry_price": entry, "atr": atr,
        "stop_price": stop, "target_price": target, "risk_fraction": stop_distance / entry}
    def dret(price: float) -> float: return side * (price / entry - 1.0)
    for horizon in HORIZONS_MIN:
        end_pos = int(index.searchsorted(index[start_pos] + pd.Timedelta(minutes=horizon), side="left"))
        if end_pos >= len(index):
            result[f"ret_{horizon}m"] = None; result[f"net19_{horizon}m"] = None
        else:
            gross = dret(float(minutes.iloc[end_pos]["open"]))
            result[f"ret_{horizon}m"] = gross; result[f"net19_{horizon}m"] = gross - ROUND_TRIP_COST_BPS / 10000.0
    max_end_pos = min(int(index.searchsorted(index[start_pos] + pd.Timedelta(minutes=MAX_SOURCE_HOLD_MIN), side="right")), len(index))
    path = minutes.iloc[start_pos:max_end_pos]
    if path.empty: return None
    favourable = path["high"] / entry - 1.0 if side > 0 else 1.0 - path["low"] / entry
    adverse = path["low"] / entry - 1.0 if side > 0 else 1.0 - path["high"] / entry
    result["mfe_7d"] = float(favourable.max()); result["mae_7d"] = float(adverse.min())
    for limit in (*DAYTRADE_LIMITS_MIN, MAX_SOURCE_HOLD_MIN):
        exit_price = None; exit_time = None; reason = None
        path_limit = path.loc[: index[start_pos] + pd.Timedelta(minutes=limit)]
        for timestamp, row in path_limit.iterrows():
            hit_stop = float(row["low"]) <= stop if side > 0 else float(row["high"]) >= stop
            hit_target = float(row["high"]) >= target if side > 0 else float(row["low"]) <= target
            if hit_stop: exit_price, exit_time, reason = stop, timestamp, "STOP"; break
            if hit_target: exit_price, exit_time, reason = target, timestamp, "TARGET"; break
        if exit_price is None:
            deadline = index[start_pos] + pd.Timedelta(minutes=limit)
            time_pos = int(index.searchsorted(deadline, side="left"))
            if time_pos >= len(index): continue
            exit_price = float(minutes.iloc[time_pos]["open"]); exit_time = index[time_pos]; reason = "TIME"
        gross = dret(float(exit_price)); net = gross - ROUND_TRIP_COST_BPS / 10000.0
        key = "7d" if limit == MAX_SOURCE_HOLD_MIN else f"{limit}m"
        result[f"outcome_{key}"] = reason; result[f"gross_{key}"] = gross; result[f"net19_{key}"] = net
        result[f"r_{key}"] = net / max(stop_distance / entry + ROUND_TRIP_COST_BPS / 10000.0, 1e-12)
        result[f"exit_time_{key}"] = exit_time
    return result


def build_clock_events(minutes: pd.DataFrame, signal: pd.DataFrame, hourly: pd.DataFrame, *, symbol: str,
                       period_label: str, split: str, evaluation_start: date, evaluation_end: date) -> list[dict[str, Any]]:
    records = []
    for event_time, event in release_events(signal).iterrows():
        if not (evaluation_start <= event_time.date() <= evaluation_end): continue
        base = {"symbol": symbol, "period_label": period_label, "split": split, "event_time": event_time,
            "event_id": f"{symbol}:{event['event_id']}", "side": int(event["side"]),
            "squeeze_bars": int(event["squeeze_bars"]), "volume_ratio": float(event["volume_ratio"]),
            "momentum_norm": float(event["momentum_norm"]), "rsi": float(event["rsi"]),
            "outside_band": bool(event["outside_band"]), "source_score": float(event["source_score"]),
            "release_open": float(event["open"]), "release_high": float(event["high"]),
            "release_low": float(event["low"]), "release_close": float(event["close"])}
        for clock in CLOCKS:
            atr = _completed_atr(hourly, event_time, clock)
            if atr is None: continue
            path = _path_metrics(minutes, side=int(event["side"]), entry_time=_entry_timestamp(event_time, clock), atr=atr, clock=clock)
            if path is not None: records.append({**base, "clock": clock, **path})
    return records


def _profit_factor(values: np.ndarray) -> float | None:
    gains = float(values[values > 0].sum()); losses = float(-values[values < 0].sum())
    return None if losses <= 0 else gains / losses


def summarize(frame: pd.DataFrame, column: str) -> dict[str, Any]:
    clean = pd.to_numeric(frame[column], errors="coerce").dropna().to_numpy(float)
    if clean.size == 0: return {"count": 0}
    return {"count": int(clean.size), "mean": float(clean.mean()), "median": float(np.median(clean)),
        "win_rate": float(np.mean(clean > 0)), "profit_factor": _profit_factor(clean),
        "gross_profit": float(clean[clean > 0].sum()), "gross_loss": float(-clean[clean < 0].sum()),
        "q10": float(np.quantile(clean, .1)), "q90": float(np.quantile(clean, .9))}


def _greedy_fixed_slot(frame: pd.DataFrame, minutes: int) -> pd.DataFrame:
    selected = []; occupied_until = None; seen = set()
    ordered = frame.sort_values(["entry_time", "source_score", "symbol"], ascending=[True, False, True], kind="stable")
    for idx, row in ordered.iterrows():
        if row["event_id"] in seen: continue
        entry = pd.Timestamp(row["entry_time"])
        if occupied_until is not None and entry < occupied_until: continue
        selected.append(idx); seen.add(row["event_id"]); occupied_until = entry + pd.Timedelta(minutes=minutes)
    return frame.loc[selected].copy()


def run_one(args: argparse.Namespace) -> None:
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    warm_start, forward_end = start - timedelta(days=args.warmup_days), end + timedelta(days=args.forward_days)
    output = Path(args.output); output.mkdir(parents=True, exist_ok=True)
    minutes, evidence = load_minutes(symbol=args.symbol, start=warm_start, end=forward_end,
        cache=Path(args.cache), candidate05_path=Path(args.candidate05_path), candidate51_path=Path(args.candidate51_path))
    records = build_clock_events(minutes, indicators(resample_ohlcv(minutes, "4h")), indicators(resample_ohlcv(minutes, "1h")),
        symbol=args.symbol, period_label=args.period_label, split=args.split, evaluation_start=start, evaluation_end=end)
    result = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "source": SOURCE,
        "symbol": args.symbol, "period_label": args.period_label, "split": args.split,
        "start": args.start, "end": args.end, "warm_start": warm_start.isoformat(), "forward_end": forward_end.isoformat(),
        "round_trip_cost_bps": ROUND_TRIP_COST_BPS, "raw_evidence": evidence, "events": records}
    (output / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True, default=_json_default) + "\n")
    print(json.dumps({"symbol": args.symbol, "period": args.period_label, "events": len(records)}, indent=2))


def aggregate(args: argparse.Namespace) -> None:
    paths = sorted(Path(args.results_root).rglob("result.json"))
    if not paths: raise RuntimeError("no result files")
    payloads = [json.loads(path.read_text()) for path in paths]
    events = pd.concat([pd.DataFrame(payload["events"]) for payload in payloads], ignore_index=True)
    if events.empty: raise RuntimeError("no squeeze release events")
    for column in ("event_time", "entry_time", "exit_time_720m", "exit_time_1440m", "exit_time_7d"):
        if column in events: events[column] = pd.to_datetime(events[column], utc=True, errors="coerce")
    metrics = [*(f"net19_{h}m" for h in HORIZONS_MIN), "net19_720m", "net19_1440m", "net19_7d", "r_720m", "r_1440m", "r_7d"]
    summary: dict[str, Any] = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "source": SOURCE, "source_runs": len(payloads), "symbols": sorted(events.symbol.unique()),
        "periods": sorted(events.period_label.unique()), "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
        "clock_contract": {"source_label_left": "full 4h and 1h bars exposed at left labels",
                           "causal_completed": "bars visible after completion; next 1m open entry"},
        "predictions": ["source clock materially exceeds causal clock if public edge is leakage",
                        "causal clock retains first-leg expectancy if compression-release is genuine",
                        "12h/24h preserve most expectancy if suitable for day trading"], "groups": {}}
    rows = []; groups = [("all", events)]
    groups += [(f"split:{k}", v) for k, v in events.groupby("split", sort=True)]
    groups += [(f"asset:{k}", v) for k, v in events.groupby("symbol", sort=True)]
    groups += [(f"period:{k}", v) for k, v in events.groupby("period_label", sort=True)]
    for name, group in groups:
        payload = {}
        for clock, frame in group.groupby("clock", sort=True):
            cp = {metric: summarize(frame, metric) for metric in metrics if metric in frame}; cp["event_count"] = int(frame.event_id.nunique()); payload[str(clock)] = cp
            for metric in metrics:
                if metric in frame: rows.append({"group": name, "clock": clock, "metric": metric, **summarize(frame, metric)})
        summary["groups"][name] = payload
    slots = {}
    for clock in CLOCKS:
        frame = events[events.clock.eq(clock)].copy()
        for limit in DAYTRADE_LIMITS_MIN:
            selected = _greedy_fixed_slot(frame, limit); days = int(events.event_time.dt.date.nunique())
            slots[f"{clock}:{limit}m"] = {"calendar_days": days, "events": int(len(selected)),
                "events_per_day": len(selected) / max(days, 1), "net": summarize(selected, f"net19_{limit}m"),
                "r": summarize(selected, f"r_{limit}m"), "by_asset": selected.symbol.value_counts().sort_index().to_dict()}
    summary["global_fixed_slots"] = slots
    pivot = events.pivot_table(index=["event_id", "symbol", "period_label", "split"], columns="clock",
        values=["net19_720m", "net19_1440m", "net19_7d"], aggfunc="first")
    paired = {}
    for metric in ("net19_720m", "net19_1440m", "net19_7d"):
        sc, cc = (metric, "source_label_left"), (metric, "causal_completed")
        if sc not in pivot or cc not in pivot: continue
        pair = pivot[[sc, cc]].dropna(); spread = pair[sc] - pair[cc]
        paired[metric] = {"count": len(pair), "source": summarize(pair.rename(columns={sc: "value"}), "value"),
            "causal": summarize(pair.rename(columns={cc: "value"}), "value"),
            "source_minus_causal": {"mean": float(spread.mean()), "median": float(spread.median()),
                                    "source_better_share": float((spread > 0).mean())}}
    summary["paired_clock_effect"] = paired
    output = Path(args.output); output.mkdir(parents=True, exist_ok=True)
    (output / "ANATOMY.json").write_text(json.dumps(summary, indent=2, sort_keys=True, default=_json_default) + "\n")
    pd.DataFrame(rows).to_csv(output / "SUMMARY.csv", index=False); events.to_csv(output / "EVENTS.csv", index=False)
    md = ["# Recovered squeeze source: causal clock audit", "", f"- source runs: {len(payloads)}",
          f"- assets: {', '.join(summary['symbols'])}", f"- periods: {len(summary['periods'])}",
          f"- unique causal releases: {events.event_id.nunique()}", f"- cost screen: {ROUND_TRIP_COST_BPS:.0f} bp round trip",
          "- one release bar creates at most one episode", "- path diagnostic, not NautilusTrader NAV", "",
          "## Paired source clock versus causal clock", "",
          "| lifecycle | paired n | source net mean bp | causal net mean bp | source-causal bp | source better % |",
          "|---|---:|---:|---:|---:|---:|"]
    for metric, item in paired.items():
        effect = item["source_minus_causal"]
        md.append(f"| {metric.replace('net19_', '')} | {item['count']} | {10000*item['source']['mean']:.2f} | {10000*item['causal']['mean']:.2f} | {10000*effect['mean']:.2f} | {100*effect['source_better_share']:.1f} |")
    md += ["", "## One global fixed slot", "", "| clock | lifecycle | trades | trades/day | net mean bp | win % | PF | mean R |",
           "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for key, item in slots.items():
        clock, life = key.split(":"); net, r = item["net"], item["r"]
        pf = "na" if net.get("profit_factor") is None else f"{net['profit_factor']:.2f}"
        md.append(f"| {clock} | {life} | {item['events']} | {item['events_per_day']:.3f} | {10000*net.get('mean', 0):.2f} | {100*net.get('win_rate', 0):.1f} | {pf} | {r.get('mean', 0):.3f} |")
    md += ["", "## Interpretation contract", "", "The source and causal rows use the same completed 4h release events. Their only intended difference is when that completed bar and the 1h ATR become observable. A large source-clock advantage is implementation leakage, not market edge.", "", "A causal positive result is still insufficient for deployment. It must be decomposed by period, asset, outside-band versus momentum-fallback entry, and intraday versus seven-day lifecycle. Only a stable first-leg state is eligible for derivatives-state refinement and later NautilusTrader execution.", ""]
    (output / "ANATOMY.md").write_text("\n".join(md))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(); sub = root.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    for name in ("symbol", "start", "end", "period_label", "split", "output"): run.add_argument(f"--{name.replace('_','-')}", dest=name, required=True)
    run.add_argument("--cache", default=".cache/candidate-51-squeeze-clock-v56")
    run.add_argument("--candidate05-path", default="research/candidate-05"); run.add_argument("--candidate51-path", default="research/candidate-51")
    run.add_argument("--warmup-days", type=int, default=10); run.add_argument("--forward-days", type=int, default=8); run.set_defaults(func=run_one)
    agg = sub.add_parser("aggregate"); agg.add_argument("--results-root", required=True); agg.add_argument("--output", required=True); agg.set_defaults(func=aggregate)
    return root


def main() -> None:
    args = parser().parse_args(); args.func(args)

if __name__ == "__main__": main()
