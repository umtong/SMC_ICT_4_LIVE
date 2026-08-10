#!/usr/bin/env python3
"""Route causal intraday jumps by derivatives sponsorship and acceptance state.

The existing academic jump family blindly reverses every large completed return.
The recovered v57 evidence shows that one superficially identical subgroup -- a
material OI unwind with aligned taker flow and last-15-minute price acceptance --
continues for 4--8 hours instead.  This experiment combines the existing causal
jump detector with the frozen v57 state model:

* accepted forced unwind -> continuation;
* rejected forced unwind -> delayed reversal;
* +15m persistent price/flow -> delayed continuation;
* +15m rejected price/flow -> delayed reversal;
* all other states -> unresolved/no trade.

The 1h, 2h and 4h timeframes, 2-sigma jump threshold and 36-prior-return
volatility window are inherited unchanged from the prior jump experiment.  No
state threshold is optimized.  This is a causal mechanism and routing audit,
not a replacement backtest engine or NAV claim.
"""
from __future__ import annotations

import argparse
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
TIMEFRAMES_MIN = (60, 120, 240)
VOL_WINDOW = 36
Z_THRESHOLD = 2.0
HORIZONS_MIN = (60, 120, 240, 480)
EVALUATION_DAYS = 140


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


def _aggregate(minute: pd.DataFrame, timeframe: int) -> pd.DataFrame:
    frame = minute.copy()
    close_time = pd.DatetimeIndex(pd.to_datetime(frame["close_time_dt"], utc=True))
    frame["bucket"] = close_time.floor(f"{timeframe}min")
    out = frame.groupby("bucket", sort=True, observed=True).agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), minute_count=("close", "size"),
        close_time=("close_time_dt", "last"),
    ).reset_index(drop=True)
    out = out[out["minute_count"].eq(timeframe)].sort_values("close_time").reset_index(drop=True)
    if out.empty:
        raise RuntimeError(f"no complete {timeframe}m bars")
    return out


def _jump_events(minute: pd.DataFrame, start: date, end: date) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for timeframe in TIMEFRAMES_MIN:
        candles = _aggregate(minute, timeframe)
        close = candles["close"].astype(float).to_numpy()
        returns = np.full(len(candles), np.nan)
        returns[1:] = np.log(close[1:] / close[:-1])
        for index in range(VOL_WINDOW + 1, len(candles)):
            current = float(returns[index])
            sample = returns[index - VOL_WINDOW:index]
            if not math.isfinite(current) or not np.isfinite(sample).all():
                continue
            mean = float(sample.mean())
            sigma = float(sample.std(ddof=1))
            if sigma <= 1e-12:
                continue
            zscore = (current - mean) / sigma
            if abs(zscore) < Z_THRESHOLD:
                continue
            event_time = pd.Timestamp(candles.iloc[index]["close_time"])
            if not (start <= event_time.date() <= end):
                continue
            result.append({
                "event_time": event_time,
                "timeframe_min": timeframe,
                "jump_log_return": current,
                "jump_return": float(math.expm1(current)),
                "jump_zscore": float(zscore),
                "jump_direction": 1 if current > 0.0 else -1,
                "jump_open": float(candles.iloc[index]["open"]),
                "jump_high": float(candles.iloc[index]["high"]),
                "jump_low": float(candles.iloc[index]["low"]),
                "jump_close": float(candles.iloc[index]["close"]),
            })
    return result


def run_one(args: argparse.Namespace) -> None:
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    warm_start = start - timedelta(days=8)
    forward_end = end + timedelta(days=1)
    candidate = Path(args.candidate51_path)
    repair = _load(candidate / "derivatives_impulse_v57_repair.py", "jump_state_repair")
    target = repair._TARGET
    kline_module = target._load_module(candidate / "kline_only_inputs.py", "jump_state_kline")
    utbot = target._load_module(candidate / "utbot_impulse_anatomy.py", "jump_state_utbot")

    minute_by_symbol: dict[str, pd.DataFrame] = {}
    hourly_by_symbol: dict[str, pd.DataFrame] = {}
    derivatives_by_symbol: dict[str, pd.DataFrame] = {}
    source: dict[str, Any] = {}
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    for symbol in SYMBOLS:
        minute, _, _, evidence = kline_module.load_range(
            symbol=symbol, start=warm_start, end=forward_end,
            cache=Path(args.cache) / "klines" / symbol,
            output=output / "source" / symbol,
        )
        hourly = utbot._signals(utbot._hourly(minute), utbot.PublicParams())
        metrics, premium, derivative_evidence = target._load_derivatives(
            symbol, warm_start, forward_end, Path(args.cache) / "derivatives"
        )
        minute_by_symbol[symbol] = minute
        hourly_by_symbol[symbol] = hourly
        derivatives_by_symbol[symbol] = target._state_series(metrics, premium)
        source[symbol] = {
            "klines": [getattr(item, "__dict__", str(item)) for item in evidence],
            "derivatives": derivative_evidence,
        }

    records: list[dict[str, Any]] = []
    for symbol in SYMBOLS:
        minute = minute_by_symbol[symbol]
        for jump in _jump_events(minute, start, end):
            event_time = pd.Timestamp(jump["event_time"])
            direction = int(jump["jump_direction"])
            state = target._event_state(
                symbol=symbol, event_time=event_time, side=direction,
                hourly_by_symbol=hourly_by_symbol, minute=minute,
                derivatives=derivatives_by_symbol[symbol],
            )
            direct = target._path_returns(minute, event_time, direction)
            delayed = target._path_returns(minute, event_time + pd.Timedelta(minutes=15), direction)
            if state is None or direct is None or delayed is None:
                continue
            record: dict[str, Any] = {
                "symbol": symbol, "period_label": args.period_label, "split": args.split,
                "event_id": f"{symbol}:{event_time.isoformat()}:{jump['timeframe_min']}m",
                **jump, **state,
                "entry_time": direct["entry_time"],
                "entry_price": direct["entry_price"],
                "delayed_entry_time": delayed["entry_time"],
                "delayed_entry_price": delayed["entry_price"],
            }
            for horizon in HORIZONS_MIN:
                record[f"direct_cont_{horizon}m"] = direct.get(f"cont_{horizon}m")
                record[f"direct_rev_{horizon}m"] = direct.get(f"rev_{horizon}m")
                record[f"delayed_cont_{horizon}m"] = delayed.get(f"cont_{horizon}m")
                record[f"delayed_rev_{horizon}m"] = delayed.get(f"rev_{horizon}m")
            records.append(record)

    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "period_label": args.period_label, "split": args.split,
        "start": args.start, "end": args.end,
        "warm_start": warm_start.isoformat(), "forward_end": forward_end.isoformat(),
        "fixed_jump_contract": {
            "timeframes_min": list(TIMEFRAMES_MIN),
            "prior_return_window": VOL_WINDOW,
            "absolute_z_threshold": Z_THRESHOLD,
        },
        "state_contract": "frozen v57 derivatives sponsorship and +15m transition",
        "source": source,
        "records": records,
    }
    (output / "result.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"period": args.period_label, "jump_records": len(records)}, indent=2))


def _deduplicate(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["episode_id"] = result["symbol"].astype(str) + ":" + result["event_time"].astype(str)
    result["abs_z"] = result["jump_zscore"].abs()
    return result.sort_values(
        ["episode_id", "abs_z", "timeframe_min"],
        ascending=[True, False, True], kind="stable",
    ).drop_duplicates("episode_id", keep="first")


def _one_slot(frame: pd.DataFrame, horizon: int, entry_column: str) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    ordered = frame.sort_values(
        [entry_column, "abs_z", "impulse_atr", "breadth", "symbol"],
        ascending=[True, False, False, False, True], kind="stable",
    )
    selected: list[int] = []
    occupied_until: pd.Timestamp | None = None
    for idx, row in ordered.iterrows():
        entry = pd.Timestamp(row[entry_column])
        if occupied_until is not None and entry < occupied_until:
            continue
        selected.append(idx)
        occupied_until = entry + pd.Timedelta(minutes=horizon)
    return frame.loc[selected].copy()


def _route_stats(frame: pd.DataFrame, column: str, entry: str, horizon: int) -> dict[str, Any]:
    selected = _one_slot(frame, horizon, entry)
    overall = _summary(selected[column])
    splits = {
        str(split): _summary(group[column])
        for split, group in selected.groupby("split", sort=True)
    }
    periods = {
        str(period): _summary(group[column])
        for period, group in selected.groupby("period_label", sort=True)
    }
    positive_periods = sum(1 for item in periods.values() if item.get("mean", 0.0) > 0.0)
    reasons: list[str] = []
    if overall.get("mean", 0.0) <= 0.0 or (overall.get("profit_factor") or 0.0) <= 1.0:
        reasons.append("one-slot after-cost expectancy is non-positive")
    if overall.get("mean_without_best") is not None and overall["mean_without_best"] <= 0.0:
        reasons.append("best-event removal eliminates expectancy")
    post = splits.get("post_publication", {})
    if post.get("count", 0) and post.get("mean", 0.0) <= 0.0:
        reasons.append("post-publication expectancy is non-positive")
    if periods and positive_periods / len(periods) < 0.60:
        reasons.append(f"fewer than 60% of periods are positive ({positive_periods}/{len(periods)})")
    return {
        "events_before_slot": int(len(frame)),
        "trades": int(len(selected)),
        "trades_per_day": float(len(selected) / EVALUATION_DAYS),
        "performance": overall,
        "by_split": splits,
        "by_period": periods,
        "by_asset": selected["symbol"].value_counts().sort_index().to_dict(),
        "status": "route_survives_initial_falsification" if not reasons else "route_rejected",
        "reasons": reasons,
    }


def aggregate(args: argparse.Namespace) -> None:
    paths = sorted(Path(args.results_root).rglob("result.json"))
    if not paths:
        raise RuntimeError("no result files")
    payloads = [json.loads(path.read_text()) for path in paths]
    raw = pd.concat([pd.DataFrame(payload["records"]) for payload in payloads], ignore_index=True)
    if raw.empty:
        raise RuntimeError("no jump-state records")
    for column in ("event_time", "entry_time", "delayed_entry_time"):
        raw[column] = pd.to_datetime(raw[column], utc=True, format="mixed")
    events = _deduplicate(raw)

    route_specs = {
        "blind_direct_reversal": {
            "mask": pd.Series(True, index=events.index), "entry": "entry_time",
            "column": "direct_rev_{h}m",
        },
        "accepted_unwind_direct_continuation": {
            "mask": events["state"].eq("FORCED_UNWIND_ACCEPTED"), "entry": "entry_time",
            "column": "direct_cont_{h}m",
        },
        "accepted_unwind_delayed_continuation": {
            "mask": events["state"].eq("FORCED_UNWIND_ACCEPTED"), "entry": "delayed_entry_time",
            "column": "delayed_cont_{h}m",
        },
        "rejected_unwind_delayed_reversal": {
            "mask": events["state"].eq("FORCED_UNWIND_REJECTED"), "entry": "delayed_entry_time",
            "column": "delayed_rev_{h}m",
        },
        "persistent15_delayed_continuation": {
            "mask": events["transition15"].eq("PERSISTENT_15"), "entry": "delayed_entry_time",
            "column": "delayed_cont_{h}m",
        },
        "rejected15_delayed_reversal": {
            "mask": events["transition15"].eq("REJECTED_15"), "entry": "delayed_entry_time",
            "column": "delayed_rev_{h}m",
        },
    }
    routes: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    for route_name, spec in route_specs.items():
        subset = events[spec["mask"]].copy()
        routes[route_name] = {}
        for horizon in HORIZONS_MIN:
            column = spec["column"].format(h=horizon)
            item = _route_stats(subset, column, spec["entry"], horizon)
            routes[route_name][str(horizon)] = item
            perf = item["performance"]
            rows.append({
                "route": route_name, "horizon_min": horizon,
                "events_before_slot": item["events_before_slot"], "trades": item["trades"],
                "trades_per_day": item["trades_per_day"], "mean": perf.get("mean"),
                "median": perf.get("median"), "win_rate": perf.get("win_rate"),
                "profit_factor": perf.get("profit_factor"),
                "mean_without_best": perf.get("mean_without_best"),
                "status": item["status"], "reasons": " | ".join(item["reasons"]),
            })

    # Composite policy uses a mutually exclusive priority order fixed before
    # observing results.  State acceptance overrides the later transition; a
    # rejected unwind is reversal; otherwise persistent/rejected transitions
    # route continuation/reversal, and mixed states do not trade.
    composite_records: list[dict[str, Any]] = []
    for _, row in events.iterrows():
        policy: str | None = None
        entry = "delayed_entry_time"
        template: str | None = None
        if row["state"] == "FORCED_UNWIND_ACCEPTED":
            policy = "ACCEPTED_UNWIND_CONTINUATION"; entry = "entry_time"; template = "direct_cont_{h}m"
        elif row["state"] == "FORCED_UNWIND_REJECTED":
            policy = "REJECTED_UNWIND_REVERSAL"; template = "delayed_rev_{h}m"
        elif row["transition15"] == "PERSISTENT_15":
            policy = "PERSISTENT_TRANSITION_CONTINUATION"; template = "delayed_cont_{h}m"
        elif row["transition15"] == "REJECTED_15":
            policy = "REJECTED_TRANSITION_REVERSAL"; template = "delayed_rev_{h}m"
        if policy is not None and template is not None:
            item = row.to_dict(); item["composite_policy"] = policy
            item["composite_entry_column"] = entry; item["composite_template"] = template
            composite_records.append(item)
    composite = pd.DataFrame(composite_records)
    composite_results: dict[str, Any] = {}
    if not composite.empty:
        for horizon in HORIZONS_MIN:
            composite[f"composite_{horizon}m"] = [
                row[row["composite_template"].format(h=horizon)]
                for _, row in composite.iterrows()
            ]
            # All delayed entries are event+15m; accepted direct entries are
            # earlier.  Use the actual timestamp stored in the chosen column.
            composite[f"chosen_entry_{horizon}m"] = [
                row[row["composite_entry_column"]] for _, row in composite.iterrows()
            ]
            selected = _one_slot(
                composite.rename(columns={f"chosen_entry_{horizon}m": "chosen_entry"}),
                horizon, "chosen_entry",
            )
            column = f"composite_{horizon}m"
            overall = _summary(selected[column])
            split = {str(k): _summary(v[column]) for k, v in selected.groupby("split", sort=True)}
            periods = {str(k): _summary(v[column]) for k, v in selected.groupby("period_label", sort=True)}
            reasons: list[str] = []
            if overall.get("mean", 0.0) <= 0.0 or (overall.get("profit_factor") or 0.0) <= 1.0:
                reasons.append("composite one-slot expectancy is non-positive")
            if overall.get("mean_without_best") is not None and overall["mean_without_best"] <= 0.0:
                reasons.append("best event removal eliminates composite expectancy")
            post = split.get("post_publication", {})
            if post.get("count", 0) and post.get("mean", 0.0) <= 0.0:
                reasons.append("post-publication composite expectancy is non-positive")
            composite_results[str(horizon)] = {
                "trades": int(len(selected)), "trades_per_day": float(len(selected) / EVALUATION_DAYS),
                "performance": overall, "by_split": split, "by_period": periods,
                "policy_counts": selected["composite_policy"].value_counts().sort_index().to_dict(),
                "status": "composite_survives_initial_falsification" if not reasons else "composite_rejected",
                "reasons": reasons,
            }

    result = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_runs": len(payloads), "evaluation_days": EVALUATION_DAYS,
        "raw_jump_records": int(len(raw)), "unique_jump_episodes": int(len(events)),
        "fixed_jump_contract": payloads[0]["fixed_jump_contract"],
        "state_contract": payloads[0]["state_contract"],
        "routes": routes, "composite": composite_results,
    }
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "ANATOMY.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )
    table = pd.DataFrame(rows).sort_values(["status", "mean"], ascending=[True, False])
    table.to_csv(output / "ROUTES.csv", index=False)
    events.to_csv(output / "EVENTS.csv", index=False)
    if not composite.empty:
        composite.to_csv(output / "COMPOSITE_EVENTS.csv", index=False)
    md = [
        "# Jump derivatives-state router anatomy", "",
        f"- source periods: {len(payloads)}", f"- unique jump episodes: {len(events)}",
        f"- timeframes: {', '.join(str(x) + 'm' for x in TIMEFRAMES_MIN)}",
        f"- fixed jump threshold: {Z_THRESHOLD:.1f} prior sigma", "- cost screen: inherited v57 19 bp round trip",
        "- no fitted state or jump thresholds", "", "## Route results", "",
        "| route | horizon | trades | trades/day | mean bp | median bp | win % | PF | ex-best bp | status |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in table.to_dict(orient="records"):
        pf = row.get("profit_factor")
        md.append(
            f"| {row['route']} | {row['horizon_min']}m | {int(row['trades'])} | {row['trades_per_day']:.3f} | "
            f"{10000*(row.get('mean') or 0):.2f} | {10000*(row.get('median') or 0):.2f} | "
            f"{100*(row.get('win_rate') or 0):.1f} | {'na' if pd.isna(pf) else f'{pf:.2f}'} | "
            f"{10000*(row.get('mean_without_best') or 0):.2f} | {row['status']} |"
        )
    md += ["", "## Composite state router", "",
           "| horizon | trades | trades/day | mean bp | median bp | PF | status |",
           "|---:|---:|---:|---:|---:|---:|---|"]
    for horizon, item in composite_results.items():
        perf = item["performance"]; pf = perf.get("profit_factor")
        md.append(
            f"| {horizon}m | {item['trades']} | {item['trades_per_day']:.3f} | "
            f"{10000*perf.get('mean', 0):.2f} | {10000*perf.get('median', 0):.2f} | "
            f"{'na' if pf is None else f'{pf:.2f}'} | {item['status']} |"
        )
    md += ["", "Blind reversal is the reused baseline, not a straw man. The router advances only if pre-existing derivatives states reverse the baseline loss mechanism, survive chronological and post-publication partitions, remain after the best event is removed, and preserve enough global one-slot opportunity. A surviving route still requires complete structural geometry and NautilusTrader validation.", ""]
    (output / "ANATOMY.md").write_text("\n".join(md), encoding="utf-8")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    for name in ("start", "end", "period_label", "split", "output"):
        run.add_argument(f"--{name.replace('_', '-')}", dest=name, required=True)
    run.add_argument("--cache", default=".cache/candidate-51-jump-state-v60")
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
