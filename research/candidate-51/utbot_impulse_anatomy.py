"""Causal anatomy of a high-ranked public UTBot/impulse futures claim.

This is deliberately a signal-mechanism diagnostic, not an account backtest.
It compares the public vectorized implementation (whose effective signal is a
large ATR impulse) with the intended recursive UTBot trailing stop and causal
ablations.  Entry is the next one-minute open after the completed one-hour bar.
No NAV, leverage, position reuse, or matching claims are made here.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import json
import math
from pathlib import Path
from statistics import median
from typing import Iterable

import numpy as np
import pandas as pd

from kline_only_inputs import load_range
import router_picasso as ta

SYMBOL_PRIORITY = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}
COST_SCREENS_BPS = (10.0, 15.0, 20.0)
HORIZONS_HOURS = (1, 2, 4, 8, 12, 24)


@dataclass(frozen=True)
class PublicParams:
    adx_long_min: float = 14.0
    adx_long_max: float = 48.0
    adx_short_min: float = 8.0
    adx_short_max: float = 50.0
    ema_long: int = 63
    ema_short: int = 53
    atr_long: int = 8
    atr_short: int = 8
    key_long: float = 2.0
    key_short: float = 2.0
    volume_long: int = 40
    volume_short: int = 37


VARIANTS = (
    "public_vectorized_full",
    "public_vectorized_no_adx",
    "public_vectorized_no_volume",
    "public_vectorized_no_ema",
    "recursive_utbot_full",
    "recursive_utbot_no_adx",
    "impulse_only_2atr",
)


def _safe_float(value: object, default: float = math.nan) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default



def _json_safe(value: object) -> object:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def _hourly(klines: pd.DataFrame) -> pd.DataFrame:
    frame = klines.copy()
    close_time = pd.DatetimeIndex(pd.to_datetime(frame["close_time_dt"], utc=True))
    # Binance minute archives are stamped at xx:xx:59.999. Floor by close clock
    # so every completed UTC hour contains exactly 60 consecutive observations.
    frame["bucket"] = close_time.floor("h")
    grouped = frame.groupby("bucket", sort=True, observed=True)
    out = grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        quote_volume=("quote_volume", "sum"),
        minute_count=("close", "size"),
        close_time=("close_time_dt", "last"),
    ).reset_index(drop=False)
    out = out[out["minute_count"] == 60].copy()
    out = out.sort_values("close_time").reset_index(drop=True)
    if out.empty or not pd.DatetimeIndex(out["close_time"]).is_monotonic_increasing:
        raise RuntimeError("hour aggregation failed")
    return out


def _recursive_stop(close: np.ndarray, nloss: np.ndarray) -> np.ndarray:
    stop = np.full(len(close), np.nan, dtype=np.float64)
    first = next((i for i in range(len(close)) if math.isfinite(close[i]) and math.isfinite(nloss[i])), None)
    if first is None:
        return stop
    stop[first] = close[first] - nloss[first]
    for i in range(first + 1, len(close)):
        c = close[i]
        p = close[i - 1]
        prev = stop[i - 1]
        loss = nloss[i]
        if not all(math.isfinite(x) for x in (c, p, prev, loss)):
            stop[i] = prev
        elif c > prev and p > prev:
            stop[i] = max(prev, c - loss)
        elif c < prev and p < prev:
            stop[i] = min(prev, c + loss)
        elif c > prev:
            stop[i] = c - loss
        else:
            stop[i] = c + loss
    return stop


def _public_vectorized_stop(close: np.ndarray, nloss: np.ndarray) -> np.ndarray:
    # Preserve source execution order exactly.  The masks are calculated from
    # the initial mostly-zero array; the final mask makes the practical stop
    # close - nLoss for positive-priced crypto candles.
    stop = np.zeros(len(close), dtype=np.float64)
    if len(close):
        stop[0] = close[0] - nloss[0]
    rolled_stop = np.roll(stop, 1)
    rolled_close = np.roll(close, 1)
    mask1 = (close > rolled_stop) & (rolled_close > rolled_stop)
    mask2 = (close < rolled_stop) & (rolled_close < rolled_stop)
    mask3 = close > rolled_stop
    stop = np.where(mask1, np.maximum(np.roll(stop, 1), close - nloss), stop)
    stop = np.where(mask2, np.minimum(np.roll(stop, 1), close + nloss), stop)
    stop = np.where(mask3, close - nloss, stop)
    return stop


def _signals(frame: pd.DataFrame, params: PublicParams) -> pd.DataFrame:
    close = frame["close"].to_numpy(dtype=np.float64)
    high = frame["high"].to_numpy(dtype=np.float64)
    low = frame["low"].to_numpy(dtype=np.float64)
    volume = frame["volume"].to_numpy(dtype=np.float64)
    bars = [
        ta.BarObservation(
            ts_event=int(pd.Timestamp(ts).value),
            open=float(o), high=float(h), low=float(l), close=float(c), volume=float(v),
        )
        for ts, o, h, l, c, v in zip(frame["close_time"], frame["open"], high, low, close, volume, strict=True)
    ]
    adx = np.asarray(ta._adx(bars, 14), dtype=np.float64)
    atr_l = np.asarray(ta._atr(bars, params.atr_long), dtype=np.float64)
    atr_s = np.asarray(ta._atr(bars, params.atr_short), dtype=np.float64)
    ema_l = np.asarray(ta._ema(close.tolist(), params.ema_long), dtype=np.float64)
    ema_s = np.asarray(ta._ema(close.tolist(), params.ema_short), dtype=np.float64)
    vol_l = np.asarray(ta._rolling_mean_shifted(volume.tolist(), params.volume_long), dtype=np.float64)
    vol_s = np.asarray(ta._rolling_mean_shifted(volume.tolist(), params.volume_short), dtype=np.float64)

    source_l = _public_vectorized_stop(close, params.key_long * atr_l)
    source_s = _public_vectorized_stop(close, params.key_short * atr_s)
    recursive_l = _recursive_stop(close, params.key_long * atr_l)
    recursive_s = _recursive_stop(close, params.key_short * atr_s)
    prev_close = np.roll(close, 1)

    def crossing(stop: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        prev_stop = np.roll(stop, 1)
        buy = (prev_close < stop) & (close > prev_stop)
        sell = (prev_close > stop) & (close < prev_stop)
        buy[0] = False
        sell[0] = False
        return buy, sell

    source_buy, _ = crossing(source_l)
    _, source_sell = crossing(source_s)
    rec_buy, _ = crossing(recursive_l)
    _, rec_sell = crossing(recursive_s)

    long_adx = (adx > params.adx_long_min) & (adx < params.adx_long_max)
    short_adx = (adx > params.adx_short_min) & (adx < params.adx_short_max)
    long_ema = (source_l > ema_l) & (close > ema_l)
    short_ema = (source_s < ema_s) & (close < ema_s)
    rec_long_ema = (recursive_l > ema_l) & (close > ema_l)
    rec_short_ema = (recursive_s < ema_s) & (close < ema_s)
    long_volume = volume > vol_l
    short_volume = volume > vol_s
    prior_atr = np.roll(atr_l, 1)
    impulse_up = (close - prev_close) > 2.0 * prior_atr
    impulse_down = (prev_close - close) > 2.0 * prior_atr
    impulse_up[0] = impulse_down[0] = False

    data: dict[str, object] = {
        "close_time": pd.to_datetime(frame["close_time"], utc=True),
        "close": close,
        "atr": atr_l,
        "adx": adx,
        "volume_ratio_long": np.divide(volume, vol_l, out=np.full(len(close), np.nan), where=vol_l > 0),
        "volume_ratio_short": np.divide(volume, vol_s, out=np.full(len(close), np.nan), where=vol_s > 0),
        "source_stop_long": source_l,
        "source_stop_short": source_s,
        "recursive_stop_long": recursive_l,
        "recursive_stop_short": recursive_s,
    }
    variant_conditions = {
        "public_vectorized_full": (source_buy & long_adx & long_ema & long_volume, source_sell & short_adx & short_ema & short_volume),
        "public_vectorized_no_adx": (source_buy & long_ema & long_volume, source_sell & short_ema & short_volume),
        "public_vectorized_no_volume": (source_buy & long_adx & long_ema, source_sell & short_adx & short_ema),
        "public_vectorized_no_ema": (source_buy & long_adx & long_volume, source_sell & short_adx & short_volume),
        "recursive_utbot_full": (rec_buy & long_adx & rec_long_ema & long_volume, rec_sell & short_adx & rec_short_ema & short_volume),
        "recursive_utbot_no_adx": (rec_buy & rec_long_ema & long_volume, rec_sell & rec_short_ema & short_volume),
        "impulse_only_2atr": (impulse_up, impulse_down),
    }
    for name, (long_condition, short_condition) in variant_conditions.items():
        side = np.where(long_condition & ~short_condition, 1, np.where(short_condition & ~long_condition, -1, 0))
        data[f"side__{name}"] = side.astype(np.int8)
    return pd.DataFrame(data)


def _path_metrics(
    minute: pd.DataFrame,
    signal_time: pd.Timestamp,
    side: int,
    horizon_hours: int,
) -> dict[str, float | str] | None:
    times = pd.DatetimeIndex(pd.to_datetime(minute["close_time_dt"], utc=True))
    start = int(times.searchsorted(signal_time, side="right"))
    if start >= len(minute):
        return None
    end_time = signal_time + pd.Timedelta(hours=horizon_hours)
    end = int(times.searchsorted(end_time, side="right"))
    if end <= start:
        return None
    path = minute.iloc[start:end]
    if len(path) < horizon_hours * 60 - 1:
        return None
    entry = float(path.iloc[0]["open"])
    final = float(path.iloc[-1]["close"])
    high = float(path["high"].max())
    low = float(path["low"].min())
    if entry <= 0.0:
        return None
    gross = side * (final / entry - 1.0) * 10_000.0
    mfe = ((high / entry - 1.0) if side > 0 else (1.0 - low / entry)) * 10_000.0
    mae = ((low / entry - 1.0) if side > 0 else (1.0 - high / entry)) * 10_000.0
    return {
        "entry_time": pd.Timestamp(path.iloc[0]["close_time_dt"]).isoformat(),
        "entry_price": entry,
        "final_price": final,
        "gross_bps": gross,
        "mfe_bps": mfe,
        "mae_bps": mae,
    }


def run_one(args: argparse.Namespace) -> None:
    symbol = args.symbol.upper()
    evaluation_start = date.fromisoformat(args.start)
    evaluation_end = date.fromisoformat(args.end)
    warmup_start = evaluation_start - timedelta(days=int(args.warmup_days))
    forward_end = evaluation_end + timedelta(days=2)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    klines, _, _, evidence = load_range(
        symbol=symbol,
        start=warmup_start,
        end=forward_end,
        cache=Path(args.cache),
        output=output / "inputs",
    )
    hourly = _hourly(klines)
    signals = _signals(hourly, PublicParams())
    eval_start_ts = pd.Timestamp(evaluation_start, tz="UTC")
    eval_end_exclusive = pd.Timestamp(evaluation_end + timedelta(days=1), tz="UTC")
    rows: list[dict[str, object]] = []
    for variant in VARIANTS:
        selected = signals[
            (signals["close_time"] >= eval_start_ts)
            & (signals["close_time"] < eval_end_exclusive)
            & (signals[f"side__{variant}"] != 0)
        ]
        for index, row in selected.iterrows():
            side = int(row[f"side__{variant}"])
            signal_time = pd.Timestamp(row["close_time"])
            atr = _safe_float(row["atr"])
            close = _safe_float(row["close"])
            impulse_atr = math.nan
            if index > 0 and math.isfinite(atr) and atr > 0.0:
                previous = _safe_float(signals.iloc[index - 1]["close"])
                impulse_atr = side * (close - previous) / atr
            base = {
                "symbol": symbol,
                "period": args.period_label,
                "split": args.split,
                "variant": variant,
                "signal_time": signal_time.isoformat(),
                "side": side,
                "signal_close": close,
                "atr": atr,
                "adx": _safe_float(row["adx"]),
                "impulse_atr": impulse_atr,
                "volume_ratio": _safe_float(row["volume_ratio_long"] if side > 0 else row["volume_ratio_short"]),
            }
            for horizon in HORIZONS_HOURS:
                metrics = _path_metrics(klines, signal_time, side, horizon)
                if metrics is None:
                    continue
                record = dict(base)
                record.update(metrics)
                record["horizon_hours"] = horizon
                for cost in COST_SCREENS_BPS:
                    record[f"net_{int(cost)}bps"] = float(metrics["gross_bps"]) - cost
                rows.append(record)
    payload = {
        "schema_version": 1,
        "kind": "causal_signal_anatomy_not_account_backtest",
        "source_family": "UTBotAlert_Donchain_MultTrend_TradingStratAug2023_OnlyUT_1h",
        "source_semantics": {
            "public_vectorized": "preserved source np.roll implementation",
            "recursive_utbot": "intended stateful ATR trailing stop",
            "entry_clock": "next one-minute open after completed one-hour signal",
            "lookahead": "none; state uses completed candles only",
        },
        "symbol": symbol,
        "period": args.period_label,
        "split": args.split,
        "evaluation_start": evaluation_start.isoformat(),
        "evaluation_end": evaluation_end.isoformat(),
        "warmup_start": warmup_start.isoformat(),
        "forward_end": forward_end.isoformat(),
        "raw_files_verified": len(evidence),
        "params": asdict(PublicParams()),
        "events": rows,
    }
    (output / "result.json").write_text(json.dumps(_json_safe(payload), indent=2, sort_keys=True, allow_nan=False) + "\n")


def _stats(values: Iterable[float]) -> dict[str, float | int | None]:
    arr = np.asarray([float(v) for v in values if math.isfinite(float(v))], dtype=np.float64)
    if arr.size == 0:
        return {"n": 0, "mean": None, "median": None, "win_rate": None, "t_stat_naive": None, "profit_factor": None}
    std = float(arr.std(ddof=1)) if arr.size > 1 else math.nan
    t_stat = float(arr.mean() / (std / math.sqrt(arr.size))) if arr.size > 1 and std > 0 else None
    positive = float(arr[arr > 0].sum())
    negative = float(-arr[arr < 0].sum())
    return {
        "n": int(arr.size),
        "mean": float(arr.mean()),
        "median": float(np.median(arr)),
        "win_rate": float(np.mean(arr > 0)),
        "t_stat_naive": t_stat,
        "profit_factor": (positive / negative if negative > 0 else None),
    }


def aggregate(args: argparse.Namespace) -> None:
    root = Path(args.results_root)
    results = sorted(root.rglob("result.json"))
    if not results:
        raise RuntimeError(f"no results below {root}")
    events: list[dict[str, object]] = []
    sources: list[dict[str, object]] = []
    for path in results:
        payload = json.loads(path.read_text())
        sources.append({k: payload[k] for k in ("symbol", "period", "split", "evaluation_start", "evaluation_end")})
        events.extend(payload.get("events", []))
    frame = pd.DataFrame(events)
    if frame.empty:
        raise RuntimeError("diagnostic emitted no events")
    frame["signal_time"] = pd.to_datetime(frame["signal_time"], utc=True)
    frame = frame.sort_values(["variant", "horizon_hours", "signal_time", "symbol"]).reset_index(drop=True)

    # One global opportunity slot: choose the strongest simultaneous asset,
    # then greedily suppress overlap for each variant/horizon. This is only an
    # independence screen; it is not account accounting.
    independent_parts: list[pd.DataFrame] = []
    for (variant, horizon), subset in frame.groupby(["variant", "horizon_hours"], sort=True):
        candidates = subset.copy()
        candidates["symbol_priority"] = candidates["symbol"].map(SYMBOL_PRIORITY).fillna(99)
        candidates["selection_strength"] = (
            candidates["impulse_atr"].abs().fillna(0.0) * 10.0
            + candidates["volume_ratio"].fillna(0.0)
            + candidates["adx"].fillna(0.0) / 100.0
        )
        candidates = candidates.sort_values(
            ["signal_time", "selection_strength", "symbol_priority"],
            ascending=[True, False, True],
        ).drop_duplicates("signal_time", keep="first")
        selected = []
        free_at = pd.Timestamp.min.tz_localize("UTC")
        duration = pd.Timedelta(hours=int(horizon))
        for _, row in candidates.sort_values("signal_time").iterrows():
            ts = pd.Timestamp(row["signal_time"])
            if ts < free_at:
                continue
            selected.append(row)
            free_at = ts + duration
        if selected:
            independent_parts.append(pd.DataFrame(selected))
    independent = pd.concat(independent_parts, ignore_index=True) if independent_parts else pd.DataFrame()

    summary_rows: list[dict[str, object]] = []
    for label, source_frame in (("all_symbol_events", frame), ("global_nonoverlap", independent)):
        for keys, subset in source_frame.groupby(["split", "variant", "horizon_hours"], sort=True):
            split, variant, horizon = keys
            row: dict[str, object] = {
                "population": label,
                "split": split,
                "variant": variant,
                "horizon_hours": int(horizon),
                "signals": int(len(subset)),
                "mean_mfe_bps": float(subset["mfe_bps"].mean()),
                "mean_mae_bps": float(subset["mae_bps"].mean()),
                "median_impulse_atr": float(subset["impulse_atr"].median()),
                "median_volume_ratio": float(subset["volume_ratio"].median()),
            }
            gross = _stats(subset["gross_bps"])
            for key, value in gross.items():
                row[f"gross_{key}"] = value
            for cost in COST_SCREENS_BPS:
                net = _stats(subset[f"net_{int(cost)}bps"])
                for key, value in net.items():
                    row[f"net{int(cost)}_{key}"] = value
            summary_rows.append(row)
    summary = pd.DataFrame(summary_rows).sort_values(
        ["population", "split", "horizon_hours", "net15_mean"],
        ascending=[True, True, True, False],
    )

    # Development-only ranking, then report the frozen ranking on stress/forward.
    dev = summary[(summary["population"] == "global_nonoverlap") & (summary["split"] == "development")].copy()
    dev["rank_score"] = (
        dev["net15_mean"].fillna(-1e9)
        + 20.0 * dev["net15_win_rate"].fillna(0.0)
        + 3.0 * dev["net15_t_stat_naive"].fillna(0.0)
        + np.minimum(dev["signals"].astype(float), 100.0) / 20.0
    )
    dev = dev.sort_values("rank_score", ascending=False)
    top = dev.head(12)[["variant", "horizon_hours", "rank_score", "signals", "net15_mean", "net15_win_rate", "net15_profit_factor"]].to_dict("records")
    frozen_keys = {(str(r["variant"]), int(r["horizon_hours"])) for r in top[:6]}
    frozen = summary[
        (summary["population"] == "global_nonoverlap")
        & summary.apply(lambda r: (str(r["variant"]), int(r["horizon_hours"])) in frozen_keys, axis=1)
    ].copy()

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out / "EVENTS.csv.gz", index=False, compression="gzip")
    independent.to_csv(out / "INDEPENDENT_EVENTS.csv.gz", index=False, compression="gzip")
    summary.to_csv(out / "SUMMARY.csv", index=False)
    anatomy = {
        "schema_version": 1,
        "kind": "causal_signal_anatomy_not_account_backtest",
        "source_results": sources,
        "result_files": len(results),
        "events": len(frame),
        "independent_events": len(independent),
        "development_top": top,
        "frozen_key_results": _json_safe(frozen.to_dict("records")),
        "interpretation_rules": [
            "Public vectorized and intended recursive implementations are evaluated separately.",
            "No NAV or leverage result is inferred from forward-return anatomy.",
            "Only completed-hour information is used; entry is next-minute open.",
            "Global non-overlap is a causal-episode independence screen, not a portfolio simulator.",
        ],
    }
    (out / "ANATOMY.json").write_text(json.dumps(_json_safe(anatomy), indent=2, sort_keys=True, allow_nan=False) + "\n")
    lines = [
        "# Public UTBot / ATR-impulse causal anatomy",
        "",
        "This is a signal-mechanism diagnostic, not a NAV backtest.",
        "",
        f"- Source result files: {len(results)}",
        f"- Raw horizon observations: {len(frame)}",
        f"- Global non-overlap observations: {len(independent)}",
        "",
        "## Development ranking (15 bps round-trip screen)",
        "",
    ]
    for rank, row in enumerate(top, start=1):
        lines.append(
            f"{rank}. `{row['variant']}` h={int(row['horizon_hours'])}: "
            f"n={int(row['signals'])}, mean={row['net15_mean']:.2f} bps, "
            f"win={row['net15_win_rate']:.1%}, PF={row['net15_profit_factor']}"
        )
    lines += ["", "## Frozen development winners on other splits", ""]
    if frozen.empty:
        lines.append("No frozen rows.")
    else:
        for _, row in frozen.sort_values(["variant", "horizon_hours", "split"]).iterrows():
            lines.append(
                f"- {row['split']} `{row['variant']}` h={int(row['horizon_hours'])}: "
                f"n={int(row['signals'])}, mean={row['net15_mean']:.2f} bps, "
                f"win={row['net15_win_rate']:.1%}, PF={row['net15_profit_factor']}"
            )
    (out / "ANATOMY.md").write_text("\n".join(lines) + "\n")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--symbol", required=True)
    run.add_argument("--start", required=True)
    run.add_argument("--end", required=True)
    run.add_argument("--period-label", required=True)
    run.add_argument("--split", required=True, choices=("development", "stress", "forward"))
    run.add_argument("--warmup-days", type=int, default=12)
    run.add_argument("--cache", required=True)
    run.add_argument("--output", required=True)
    run.set_defaults(func=run_one)
    agg = sub.add_parser("aggregate")
    agg.add_argument("--results-root", required=True)
    agg.add_argument("--output", required=True)
    agg.set_defaults(func=aggregate)
    return p


def main() -> None:
    args = parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
