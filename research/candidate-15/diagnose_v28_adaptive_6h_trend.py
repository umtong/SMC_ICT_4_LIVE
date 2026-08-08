#!/usr/bin/env python3
"""Candidate 15 V28 six-hour adaptive trend mechanism screen.

This module evaluates a complete medium-frequency scenario: completed six-hour
momentum context, separate fifteen-minute confirmation, entry at that completed
confirmation close, volatility-floor invalidation, and a causal five-minute
trailing stop.  It is not an account engine; a surviving fixed policy is
promoted to NautilusTrader.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from hashlib import sha256
from io import BytesIO
import json
import math
from pathlib import Path
import time
from typing import Any, Iterable
from urllib.request import Request, urlopen
from zipfile import ZipFile

import numpy as np
import pandas as pd

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
COLUMNS = (
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trades", "taker_buy_volume", "taker_buy_quote_volume",
    "ignore",
)
BREADTH = "BREADTH_ALIGNED_6H_TREND"
LEADER = "RELATIVE_6H_LEADER"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain an object")
    return value


def month_starts(start: date, end_exclusive: date) -> Iterable[date]:
    cursor = date(start.year, start.month, 1)
    while cursor < end_exclusive:
        yield cursor
        cursor = date(
            cursor.year + int(cursor.month == 12),
            1 if cursor.month == 12 else cursor.month + 1,
            1,
        )


def archive_url(symbol: str, interval: str, token: str, monthly: bool) -> str:
    cadence = "monthly" if monthly else "daily"
    return (
        "https://data.binance.vision/data/futures/um/"
        f"{cadence}/klines/{symbol}/{interval}/{symbol}-{interval}-{token}.zip"
    )


def download_one(
    task: tuple[str, str, str, bool, Path],
) -> dict[str, Any]:
    symbol, interval, token, monthly, destination = task
    url = archive_url(symbol, interval, token, monthly)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or destination.stat().st_size < 100:
        request = Request(url, headers={"User-Agent": "SMC-ICT-4-candidate-15-v28"})
        last: Exception | None = None
        for attempt in range(5):
            try:
                with urlopen(request, timeout=120) as response:  # noqa: S310 fixed host
                    payload = response.read()
                if len(payload) < 100:
                    raise RuntimeError(f"unexpectedly small response from {url}")
                temporary = destination.with_suffix(".zip.tmp")
                temporary.write_bytes(payload)
                with ZipFile(temporary) as archive:
                    bad = archive.testzip()
                    if bad is not None:
                        raise RuntimeError(f"corrupt member {bad}")
                temporary.replace(destination)
                break
            except Exception as exc:
                last = exc
                if attempt == 4:
                    raise RuntimeError(f"download failed {url}: {last}") from exc
                time.sleep(2**attempt)
    payload = destination.read_bytes()
    return {
        "symbol": symbol,
        "interval": interval,
        "token": token,
        "cadence": "monthly" if monthly else "daily",
        "url": url,
        "path": str(destination),
        "bytes": len(payload),
        "sha256": sha256(payload).hexdigest(),
    }


def read_zip(path: Path) -> pd.DataFrame:
    with ZipFile(path) as archive:
        members = [name for name in archive.namelist() if not name.endswith("/")]
        if len(members) != 1:
            raise RuntimeError(f"unexpected archive {path}: {members}")
        payload = archive.read(members[0])
    frame = pd.read_csv(BytesIO(payload))
    if not set(COLUMNS).issubset(frame.columns):
        frame = pd.read_csv(BytesIO(payload), names=COLUMNS, header=None)
    else:
        frame = frame.loc[:, COLUMNS]
    return frame[pd.to_numeric(frame["open_time"], errors="coerce").notna()].copy()


def download_data(
    protocol: dict[str, Any],
    output: Path,
) -> tuple[dict[str, list[Path]], list[dict[str, Any]]]:
    data = protocol["data"]
    start = date.fromisoformat(data["start"])
    monthly_end = date.fromisoformat(data["monthly_end_exclusive"])
    tasks: list[tuple[str, str, str, bool, Path]] = []
    paths: dict[str, list[Path]] = {symbol: [] for symbol in SYMBOLS}
    for symbol in SYMBOLS:
        for month in month_starts(start, monthly_end):
            token = f"{month.year:04d}-{month.month:02d}"
            destination = (
                output / "data" / symbol / f"{symbol}-5m-{token}.zip"
            )
            tasks.append((symbol, "5m", token, True, destination))
            paths[symbol].append(destination)
        for token in data["latest_daily_dates"]:
            destination = (
                output / "data" / symbol / f"{symbol}-5m-{token}.zip"
            )
            tasks.append((symbol, "5m", str(token), False, destination))
            paths[symbol].append(destination)
    manifest: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(download_one, task): task for task in tasks}
        for future in as_completed(futures):
            manifest.append(future.result())
    return paths, sorted(
        manifest,
        key=lambda item: (item["symbol"], item["token"], item["cadence"]),
    )


def load_symbol(
    paths: list[Path],
    start: date,
    end_exclusive: date,
) -> pd.DataFrame:
    raw = pd.concat([read_zip(path) for path in paths], ignore_index=True)
    raw = raw.drop_duplicates("open_time", keep="last").sort_values("open_time")
    timestamps = pd.to_numeric(raw["open_time"], errors="raise").astype("int64")
    first = int(timestamps.iloc[0])
    if 1_000_000_000_000 <= first < 10_000_000_000_000:
        unit = "ms"
    elif 1_000_000_000_000_000 <= first < 10_000_000_000_000_000:
        unit = "us"
    else:
        raise RuntimeError(f"unsupported timestamp magnitude {first}")
    index = pd.to_datetime(timestamps, unit=unit, utc=True) + pd.Timedelta(minutes=5)
    frame = pd.DataFrame(index=index)
    for name in (
        "open", "high", "low", "close", "quote_volume",
        "taker_buy_quote_volume",
    ):
        frame[name] = pd.to_numeric(raw[name], errors="raise").to_numpy()
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    lower = pd.Timestamp(start, tz="UTC")
    upper = pd.Timestamp(end_exclusive, tz="UTC")
    frame = frame[(frame.index > lower) & (frame.index <= upper)]
    expected = int((upper - lower).total_seconds() // 300)
    coverage = len(frame.index) / max(expected, 1)
    if coverage < 0.995:
        raise RuntimeError(
            f"insufficient five-minute coverage {len(frame.index)}/{expected} "
            f"({coverage:.6f})"
        )
    return frame


def resample_six_hour(frame: pd.DataFrame) -> pd.DataFrame:
    grouped = frame.resample(
        "6h",
        closed="right",
        label="right",
        origin="start_day",
    )
    output = pd.DataFrame(index=grouped.size().index)
    output["count"] = grouped.size()
    output["open"] = grouped["open"].first()
    output["high"] = grouped["high"].max()
    output["low"] = grouped["low"].min()
    output["close"] = grouped["close"].last()
    output["quote_volume"] = grouped["quote_volume"].sum()
    output["taker_buy_quote_volume"] = grouped[
        "taker_buy_quote_volume"
    ].sum()
    output = output[output["count"] == 72].copy()
    return output


def build_signal_frame(
    symbol: str,
    five: pd.DataFrame,
    rules: dict[str, Any],
) -> pd.DataFrame:
    six = resample_six_hour(five)
    close = six["close"].astype(float)
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            six["high"] - six["low"],
            (six["high"] - previous_close).abs(),
            (six["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = true_range.rolling(
        int(rules["atr_lookback_bars"]),
        min_periods=int(rules["atr_lookback_bars"]),
    ).mean()
    momentum = (
        close / close.shift(int(rules["momentum_lookback_bars"])) - 1.0
    )
    atr_fraction = atr / close.replace(0.0, np.nan)
    momentum_atr = momentum / atr_fraction.replace(0.0, np.nan)
    six_return = close.pct_change()
    quality_window = int(rules["trend_quality_lookback_bars"])
    quality_minimum = int(rules["minimum_prior_bars"])
    prior_mean = six_return.shift(1).rolling(
        quality_window,
        min_periods=quality_minimum,
    ).mean()
    prior_std = six_return.shift(1).rolling(
        quality_window,
        min_periods=quality_minimum,
    ).std(ddof=0).replace(0.0, np.nan)
    direction = np.sign(momentum)
    trend_quality = direction * prior_mean / prior_std
    volume_median = six["quote_volume"].shift(1).rolling(
        int(rules["volume_median_lookback_bars"]),
        min_periods=int(rules["volume_median_lookback_bars"]),
    ).median()
    volume_ratio = six["quote_volume"] / volume_median.replace(0.0, np.nan)
    pressure = (
        2.0
        * six["taker_buy_quote_volume"]
        / six["quote_volume"].replace(0.0, np.nan)
        - 1.0
    ).clip(-1.0, 1.0)
    output = six.copy()
    output["symbol"] = symbol
    output["atr"] = atr
    output["momentum"] = momentum
    output["momentum_atr"] = momentum_atr
    output["direction"] = direction
    output["trend_quality"] = trend_quality
    output["volume_ratio"] = volume_ratio
    output["six_hour_taker_pressure"] = pressure
    return output.replace([np.inf, -np.inf], np.nan)


def add_cross_market_state(
    frames: dict[str, pd.DataFrame],
    rules: dict[str, Any],
) -> dict[str, pd.DataFrame]:
    momentum = pd.DataFrame(
        {symbol: frame["momentum_atr"] for symbol, frame in frames.items()}
    )
    directions = np.sign(momentum)
    median = momentum.median(axis=1)
    residual = momentum.sub(median, axis=0)
    absolute_residual = residual.abs()
    top = absolute_residual.max(axis=1)
    second = absolute_residual.apply(
        lambda row: row.nlargest(2).iloc[-1] if row.notna().sum() >= 2 else np.nan,
        axis=1,
    )
    positive_breadth = (directions > 0.0).mean(axis=1)
    negative_breadth = (directions < 0.0).mean(axis=1)
    output: dict[str, pd.DataFrame] = {}
    for symbol, frame in frames.items():
        enriched = frame.copy()
        enriched["market_median_momentum_atr"] = median
        enriched["residual_momentum_atr"] = residual[symbol]
        enriched["leader_gap_atr"] = top - second
        direction = enriched["direction"]
        enriched["directional_breadth"] = np.where(
            direction > 0.0,
            positive_breadth,
            negative_breadth,
        )
        enriched["is_absolute_residual_leader"] = (
            absolute_residual[symbol] >= top - 1e-12
        )
        output[symbol] = enriched
    return output


def confirmation_record(
    signal_ts: pd.Timestamp,
    direction: float,
    five: pd.DataFrame,
    rules: dict[str, Any],
) -> dict[str, Any] | None:
    confirmation_end = signal_ts + pd.Timedelta(
        minutes=int(rules["confirmation_minutes"])
    )
    window = five[(five.index > signal_ts) & (five.index <= confirmation_end)]
    expected = int(rules["confirmation_minutes"]) // 5
    if len(window.index) != expected:
        return None
    opening = float(window.iloc[0]["open"])
    closing = float(window.iloc[-1]["close"])
    high = float(window["high"].max())
    low = float(window["low"].min())
    body_fraction = abs(closing - opening) / max(high - low, 1e-12)
    pressure = (
        2.0
        * float(window["taker_buy_quote_volume"].sum())
        / max(float(window["quote_volume"].sum()), 1e-12)
        - 1.0
    )
    confirmation_return = closing / opening - 1.0
    if direction * confirmation_return <= 0.0:
        return None
    if body_fraction < float(rules["minimum_confirmation_body_fraction"]):
        return None
    if (
        direction * pressure
        < float(rules["minimum_directional_taker_pressure"])
    ):
        return None
    return {
        "entry_ts": confirmation_end,
        "entry_price": closing,
        "confirmation_open": opening,
        "confirmation_high": high,
        "confirmation_low": low,
        "confirmation_return": confirmation_return,
        "confirmation_body_fraction": body_fraction,
        "confirmation_taker_pressure": pressure,
    }


def candidate_signals(
    frames: dict[str, pd.DataFrame],
    five_frames: dict[str, pd.DataFrame],
    rules: dict[str, Any],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    long_min = float(rules["long_momentum_atr_min"])
    short_min = float(rules["short_momentum_atr_min"])
    for symbol, frame in frames.items():
        five = five_frames[symbol]
        for signal_ts, item in frame.iterrows():
            required = (
                "atr", "momentum_atr", "direction", "trend_quality",
                "volume_ratio", "directional_breadth",
                "residual_momentum_atr", "leader_gap_atr",
            )
            if any(pd.isna(item[name]) for name in required):
                continue
            direction = float(item["direction"])
            if direction == 0.0:
                continue
            threshold = long_min if direction > 0.0 else short_min
            if abs(float(item["momentum_atr"])) < threshold:
                continue
            if (
                float(item["volume_ratio"])
                < float(rules["minimum_six_hour_volume_ratio"])
            ):
                continue
            if (
                float(item["trend_quality"])
                < float(rules["minimum_trend_quality"])
            ):
                continue
            breadth_route = (
                float(item["directional_breadth"])
                >= float(rules["breadth_fraction_min"])
            )
            leader_route = (
                not breadth_route
                and bool(item["is_absolute_residual_leader"])
                and abs(float(item["residual_momentum_atr"]))
                >= float(rules["relative_leader_atr_min"])
                and float(item["leader_gap_atr"])
                >= float(rules["relative_leader_gap_atr_min"])
                and np.sign(float(item["residual_momentum_atr"])) == direction
            )
            if not breadth_route and not leader_route:
                continue
            confirmation = confirmation_record(
                pd.Timestamp(signal_ts),
                direction,
                five,
                rules,
            )
            if confirmation is None:
                continue
            entry = float(confirmation["entry_price"])
            atr = float(item["atr"])
            if direction > 0.0:
                stop = min(
                    float(confirmation["confirmation_low"]),
                    entry - float(rules["initial_stop_atr"]) * atr,
                )
            else:
                stop = max(
                    float(confirmation["confirmation_high"]),
                    entry + float(rules["initial_stop_atr"]) * atr,
                )
            if direction * (entry - stop) <= 0.0:
                continue
            route = BREADTH if breadth_route else LEADER
            quality = (
                abs(float(item["momentum_atr"]))
                * max(float(item["volume_ratio"]), 0.0)
                * max(float(item["trend_quality"]), 0.0)
            )
            if route == BREADTH:
                quality *= 1.0 + float(item["directional_breadth"])
            else:
                quality *= 1.0 + abs(float(item["residual_momentum_atr"]))
            rows.append(
                {
                    "signal_ts": signal_ts,
                    "entry_ts": confirmation["entry_ts"],
                    "symbol": symbol,
                    "route": route,
                    "route_priority": 2 if route == BREADTH else 1,
                    "direction": "LONG" if direction > 0.0 else "SHORT",
                    "direction_value": direction,
                    "entry_price": entry,
                    "initial_stop": stop,
                    "atr_at_signal": atr,
                    "momentum": float(item["momentum"]),
                    "momentum_atr": float(item["momentum_atr"]),
                    "volume_ratio": float(item["volume_ratio"]),
                    "trend_quality": float(item["trend_quality"]),
                    "directional_breadth": float(item["directional_breadth"]),
                    "residual_momentum_atr": float(
                        item["residual_momentum_atr"]
                    ),
                    "leader_gap_atr": float(item["leader_gap_atr"]),
                    "confirmation_return": float(
                        confirmation["confirmation_return"]
                    ),
                    "confirmation_body_fraction": float(
                        confirmation["confirmation_body_fraction"]
                    ),
                    "confirmation_taker_pressure": float(
                        confirmation["confirmation_taker_pressure"]
                    ),
                    "rank_score": quality,
                }
            )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(
        ["entry_ts", "route_priority", "rank_score", "symbol"],
        ascending=[True, False, False, True],
        kind="stable",
    )


def simulate_trade(
    row: pd.Series,
    five: pd.DataFrame,
    six: pd.DataFrame,
    rules: dict[str, Any],
) -> dict[str, Any] | None:
    entry_ts = pd.Timestamp(row["entry_ts"])
    direction = float(row["direction_value"])
    entry = float(row["entry_price"])
    stop = float(row["initial_stop"])
    best_close = entry
    cap = entry_ts + pd.Timedelta(
        minutes=int(rules["maximum_hold_minutes"])
    )
    bars = five[(five.index > entry_ts) & (five.index <= cap)]
    if bars.empty:
        return None
    exit_ts: pd.Timestamp | None = None
    exit_price: float | None = None
    reason = ""
    trailing_updates = 0
    for timestamp, bar in bars.iterrows():
        if direction > 0.0 and float(bar["low"]) <= stop:
            exit_ts = timestamp
            exit_price = stop
            reason = "TRAILING_STOP"
            break
        if direction < 0.0 and float(bar["high"]) >= stop:
            exit_ts = timestamp
            exit_price = stop
            reason = "TRAILING_STOP"
            break
        close = float(bar["close"])
        best_close = max(best_close, close) if direction > 0.0 else min(
            best_close, close
        )
        available = six.loc[six.index <= timestamp, "atr"].dropna()
        current_atr = (
            float(available.iloc[-1])
            if len(available.index)
            else float(row["atr_at_signal"])
        )
        if direction > 0.0:
            new_stop = max(
                stop,
                best_close - float(rules["trailing_stop_atr"]) * current_atr,
            )
        else:
            new_stop = min(
                stop,
                best_close + float(rules["trailing_stop_atr"]) * current_atr,
            )
        if abs(new_stop - stop) > 1e-12:
            trailing_updates += 1
            stop = new_stop
    if exit_ts is None:
        final = bars.iloc[-1]
        exit_ts = pd.Timestamp(bars.index[-1])
        exit_price = float(final["close"])
        reason = "TWENTY_FOUR_HOUR_CAP"
    gross = direction * (float(exit_price) / entry - 1.0)
    cost = (
        float(rules["execution_round_trip_cost_bps"])
        + float(rules["funding_and_unmodeled_impact_reserve_bps"])
    ) / 10_000.0
    output = row.to_dict()
    output.update(
        {
            "exit_ts": exit_ts,
            "exit_price": float(exit_price),
            "exit_reason": reason,
            "gross_return": gross,
            "net_return": gross - cost,
            "holding_minutes": (
                exit_ts - entry_ts
            ).total_seconds() / 60.0,
            "final_stop": stop,
            "trailing_updates": trailing_updates,
        }
    )
    return output


def arbitrate_and_simulate(
    candidates: pd.DataFrame,
    five_frames: dict[str, pd.DataFrame],
    six_frames: dict[str, pd.DataFrame],
    rules: dict[str, Any],
) -> tuple[pd.DataFrame, Counter[str]]:
    if candidates.empty:
        return candidates.copy(), Counter()
    chosen: list[dict[str, Any]] = []
    skips: Counter[str] = Counter()
    free_at = pd.Timestamp.min.tz_localize("UTC")
    for entry_ts, group in candidates.groupby("entry_ts", sort=True):
        winner = group.iloc[0]
        skips["SAME_EVENT_LOSER"] += max(0, len(group.index) - 1)
        if pd.Timestamp(entry_ts) < free_at:
            skips["GLOBAL_POSITION_OCCUPIED"] += 1
            continue
        result = simulate_trade(
            winner,
            five_frames[str(winner["symbol"])],
            six_frames[str(winner["symbol"])],
            rules,
        )
        if result is None:
            skips["MISSING_TRADE_PATH"] += 1
            continue
        chosen.append(result)
        free_at = pd.Timestamp(result["exit_ts"])
    if not chosen:
        return candidates.iloc[0:0].copy(), skips
    return pd.DataFrame(chosen).sort_values(
        "entry_ts", kind="stable"
    ).reset_index(drop=True), skips


def t_stat(values: pd.Series) -> float | None:
    if len(values.index) < 2:
        return None
    standard = float(values.std(ddof=1))
    if not math.isfinite(standard) or standard <= 0.0:
        return None
    return float(values.mean() / (standard / math.sqrt(len(values.index))))


def payoff(values: pd.Series) -> float | None:
    wins = values[values > 0.0]
    losses = values[values < 0.0]
    if wins.empty or losses.empty:
        return None
    return float(wins.mean() / abs(losses.mean()))


def summarize(
    frame: pd.DataFrame,
    start: str,
    end_exclusive: str,
) -> dict[str, Any]:
    lower = pd.Timestamp(start, tz="UTC")
    upper = pd.Timestamp(end_exclusive, tz="UTC")
    sample = frame[
        (frame["entry_ts"] >= lower) & (frame["entry_ts"] < upper)
    ].copy()
    days = int((upper - lower).total_seconds() // 86_400)
    if sample.empty:
        return {
            "start": start,
            "end_exclusive": end_exclusive,
            "calendar_days": days,
            "trades": 0,
            "trades_per_day": 0.0,
            "mean_gross_bps": None,
            "mean_net_bps": None,
            "win_rate": None,
            "payoff_ratio": None,
            "net_t_stat": None,
            "positive_months": 0,
            "active_months": 0,
            "positive_month_share": 0.0,
            "mean_holding_minutes": None,
            "route_stats": {},
            "symbol_counts": {},
            "exit_reasons": {},
        }
    sample["month"] = sample["entry_ts"].dt.to_period("M").astype(str)
    monthly = sample.groupby("month")["net_return"].mean()
    routes: dict[str, Any] = {}
    for route, current in sample.groupby("route", sort=True):
        routes[str(route)] = {
            "trades": len(current.index),
            "mean_net_bps": float(current["net_return"].mean() * 10_000.0),
            "win_rate": float((current["net_return"] > 0.0).mean()),
            "payoff_ratio": payoff(current["net_return"]),
            "net_t_stat": t_stat(current["net_return"]),
            "mean_holding_minutes": float(current["holding_minutes"].mean()),
        }
    return {
        "start": start,
        "end_exclusive": end_exclusive,
        "calendar_days": days,
        "trades": len(sample.index),
        "trades_per_day": len(sample.index) / max(days, 1),
        "mean_gross_bps": float(sample["gross_return"].mean() * 10_000.0),
        "mean_net_bps": float(sample["net_return"].mean() * 10_000.0),
        "win_rate": float((sample["net_return"] > 0.0).mean()),
        "payoff_ratio": payoff(sample["net_return"]),
        "net_t_stat": t_stat(sample["net_return"]),
        "positive_months": int((monthly > 0.0).sum()),
        "active_months": len(monthly.index),
        "positive_month_share": float((monthly > 0.0).mean()),
        "mean_holding_minutes": float(sample["holding_minutes"].mean()),
        "route_stats": routes,
        "symbol_counts": {
            str(key): int(value)
            for key, value in sample["symbol"].value_counts().items()
        },
        "exit_reasons": {
            str(key): int(value)
            for key, value in sample["exit_reason"].value_counts().items()
        },
    }


def execute(protocol_path: Path, output: Path) -> dict[str, Any]:
    protocol = load_object(protocol_path)
    if protocol["schema"] != "candidate-15-v28-adaptive-6h-trend-v1":
        raise RuntimeError("unexpected V28 protocol")
    output.mkdir(parents=True, exist_ok=True)
    paths, manifest = download_data(protocol, output)
    write_json(
        output / "data_manifest.json",
        {
            "schema": "candidate-15-v28-data-manifest-v1",
            "files": manifest,
        },
    )
    start = date.fromisoformat(protocol["data"]["start"])
    end = date.fromisoformat(protocol["data"]["end_exclusive"])
    five_frames = {
        symbol: load_symbol(paths[symbol], start, end)
        for symbol in SYMBOLS
    }
    signal_frames = {
        symbol: build_signal_frame(
            symbol,
            frame,
            protocol["fixed_rules"],
        )
        for symbol, frame in five_frames.items()
    }
    signal_frames = add_cross_market_state(
        signal_frames,
        protocol["fixed_rules"],
    )
    candidates = candidate_signals(
        signal_frames,
        five_frames,
        protocol["fixed_rules"],
    )
    selected, skips = arbitrate_and_simulate(
        candidates,
        five_frames,
        signal_frames,
        protocol["fixed_rules"],
    )
    candidates.to_csv(output / "all_executable_candidates.csv", index=False)
    selected.to_csv(output / "selected_trades.csv", index=False)

    evaluation = protocol["evaluation"]
    development = summarize(
        selected,
        evaluation["development_start"],
        evaluation["development_end_exclusive"],
    )
    stability = summarize(
        selected,
        evaluation["stability_start"],
        evaluation["stability_end_exclusive"],
    )
    july = summarize(
        selected,
        evaluation["july_confirmation_start"],
        evaluation["july_confirmation_end_exclusive"],
    )
    pulse = summarize(
        selected,
        evaluation["latest_pulse_start"],
        evaluation["latest_pulse_end_exclusive"],
    )
    gate = protocol["advance_gate"]
    total_stability = sum(stability["symbol_counts"].values())
    max_symbol_share = (
        max(stability["symbol_counts"].values(), default=0)
        / max(total_stability, 1)
    )
    checks = {
        "positive_development_mean_net": (
            development["mean_net_bps"] is not None
            and development["mean_net_bps"] > 0.0
        ),
        "positive_stability_mean_net": (
            stability["mean_net_bps"] is not None
            and stability["mean_net_bps"]
            >= float(gate["minimum_stability_mean_net_bps"])
        ),
        "stability_net_t_stat": (
            stability["net_t_stat"] is not None
            and stability["net_t_stat"]
            >= float(gate["minimum_stability_net_t_stat"])
        ),
        "stability_positive_month_share": (
            stability["positive_month_share"]
            >= float(gate["minimum_stability_positive_month_share"])
        ),
        "stability_frequency": (
            stability["trades_per_day"]
            >= float(gate["minimum_stability_trades_per_calendar_day"])
        ),
        "positive_july_confirmation_mean_net": (
            july["mean_net_bps"] is not None
            and july["mean_net_bps"] > 0.0
        ),
        "july_confirmation_trade_count": (
            july["trades"] >= int(gate["minimum_july_confirmation_trades"])
        ),
        "positive_latest_pulse_mean_net": (
            pulse["mean_net_bps"] is not None
            and pulse["mean_net_bps"]
            >= float(gate["minimum_latest_pulse_mean_net_bps"])
        ),
        "latest_pulse_trade_count": (
            pulse["trades"] >= int(gate["minimum_latest_pulse_trades"])
        ),
        "symbol_concentration": (
            max_symbol_share <= float(gate["maximum_single_symbol_share"])
        ),
    }
    cross_split: dict[str, Any] = {}
    for route in (BREADTH, LEADER):
        splits: dict[str, Any] = {}
        positive = True
        for name, summary in (
            ("development", development),
            ("stability", stability),
            ("july_confirmation", july),
            ("latest_pulse", pulse),
        ):
            current = summary["route_stats"].get(route)
            splits[name] = current
            if current is None or current["mean_net_bps"] <= 0.0:
                positive = False
        cross_split[route] = {
            "positive_across_all_declared_splits": positive,
            "splits": splits,
        }
    survivors = [
        route
        for route, record in cross_split.items()
        if record["positive_across_all_declared_splits"]
    ]
    advance = all(checks.values()) and bool(survivors)
    classification = (
        "V28_ADAPTIVE_6H_TREND_ADVANCE_TO_NAUTILUS"
        if advance
        else "V28_ADAPTIVE_6H_TREND_REJECTED_OR_UNDERPOWERED"
    )
    decision = (
        f"Cross-split-positive fixed routes: {survivors}. "
        + (
            "Freeze and implement the surviving route in NautilusTrader."
            if advance
            else "Do not tune the six-hour trend family; preserve only any "
            "cross-split-positive route and move to another independent mechanism."
        )
    )
    summary = {
        "schema": "candidate-15-v28-summary-v1",
        "classification": classification,
        "advance_to_nautilus": advance,
        "executable_candidates": len(candidates.index),
        "selected_trades": len(selected.index),
        "arbitration_skips": dict(skips),
        "development": development,
        "stability": stability,
        "july_confirmation": july,
        "latest_pulse": pulse,
        "advance_checks": checks,
        "cross_split_routes": cross_split,
        "surviving_routes": survivors,
        "maximum_stability_symbol_share": max_symbol_share,
        "decision": decision,
    }
    write_json(output / "summary.json", summary)

    lines = [
        "# Candidate 15 V28 — Adaptive six-hour trend diagnostic",
        "",
        f"**{classification}**",
        "",
        "The policy uses completed six-hour momentum, a wholly subsequent "
        "fifteen-minute confirmation, entry at that confirmation close, and a "
        "five-minute causal ATR trailing stop.",
        "",
    ]
    for title, item in (
        ("Development", development),
        ("Year-long stability", stability),
        ("July 2026 confirmation", july),
        ("Latest August 1-7 pulse", pulse),
    ):
        lines.extend(
            [
                f"## {title}",
                f"- interval: `{item['start']} -> {item['end_exclusive']}`",
                f"- trades / day: `{item['trades']} / {item['trades_per_day']}`",
                f"- gross / net mean: `{item['mean_gross_bps']} / "
                f"{item['mean_net_bps']}` bp",
                f"- win rate / payoff: `{item['win_rate']} / "
                f"{item['payoff_ratio']}`",
                f"- net t-stat: `{item['net_t_stat']}`",
                f"- positive months: `{item['positive_months']} / "
                f"{item['active_months']}`",
                f"- mean holding minutes: `{item['mean_holding_minutes']}`",
                f"- route stats: `{item['route_stats']}`",
                f"- symbol counts: `{item['symbol_counts']}`",
                f"- exit reasons: `{item['exit_reasons']}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Advance checks",
            *[f"- {key}: `{value}`" for key, value in checks.items()],
            "",
            "## Cross-split route evidence",
            f"`{cross_split}`",
            "",
            "## Decision",
            decision,
            "",
            "This is a mechanism screen, not synthetic account NAV. A pass still "
            "requires frozen NautilusTrader execution, current-NAV 3% sizing, "
            "fees/slippage/impact/funding, one global slot and continuous-account "
            "validation.",
        ]
    )
    (output / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    execute(args.protocol.resolve(), args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
