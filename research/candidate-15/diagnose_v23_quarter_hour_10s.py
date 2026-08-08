#!/usr/bin/env python3
"""Candidate 15 V23 exact ten-second quarter-hour order-imbalance screen.

The signal is built from Binance USD-M one-second klines, not one-minute
proxies.  Only seconds 0-9 of each quarter-hour opening are used for the
boundary imbalance.  Public context ends before the boundary and confirmation
uses a separate five-minute interval after the event minute.  This is an
economic mechanism screen; a passing route still requires NautilusTrader.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from hashlib import sha256
from io import BytesIO
import json
import math
from pathlib import Path
import time
from typing import Any, Iterable
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zipfile import ZipFile

import numpy as np
import pandas as pd

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
KLINE_COLUMNS = (
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trades", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
)
ROUTE_PERSISTENCE = "QH_BOUNDARY_FLOW_PERSISTENCE_4H"
ROUTE_PUBLIC = "QH_PUBLIC_STATE_DELIVERY_8H"


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
        raise TypeError(f"{path} must contain a JSON object")
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


def daterange(start: date, end_exclusive: date) -> Iterable[date]:
    cursor = start
    while cursor < end_exclusive:
        yield cursor
        cursor += timedelta(days=1)


def download(url: str, destination: Path, retries: int = 5) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and destination.stat().st_size > 100:
        try:
            with ZipFile(destination) as archive:
                if archive.testzip() is None:
                    return {
                        "url": url,
                        "path": str(destination),
                        "bytes": destination.stat().st_size,
                        "sha256": sha256(destination.read_bytes()).hexdigest(),
                        "cached": True,
                    }
        except Exception:
            destination.unlink(missing_ok=True)
    request = Request(url, headers={"User-Agent": "SMC-ICT-4-candidate-15-v23"})
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with urlopen(request, timeout=120) as response:  # noqa: S310 fixed host
                payload = response.read()
            if len(payload) < 100:
                raise RuntimeError(f"small response from {url}")
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            temporary.write_bytes(payload)
            with ZipFile(temporary) as archive:
                bad = archive.testzip()
                if bad is not None:
                    raise RuntimeError(f"corrupt ZIP member {bad}")
            temporary.replace(destination)
            return {
                "url": url,
                "path": str(destination),
                "bytes": destination.stat().st_size,
                "sha256": sha256(destination.read_bytes()).hexdigest(),
                "cached": False,
            }
        except Exception as exc:
            last = exc
            if attempt + 1 < retries:
                time.sleep(2**attempt)
    raise RuntimeError(f"download failed {url}: {last}")


def read_kline_archive(path: Path) -> pd.DataFrame:
    with ZipFile(path) as archive:
        members = [name for name in archive.namelist() if not name.endswith("/")]
        if len(members) != 1:
            raise RuntimeError(f"unexpected archive members in {path}: {members}")
        payload = archive.read(members[0])
    frame = pd.read_csv(BytesIO(payload))
    if not set(KLINE_COLUMNS).issubset(frame.columns):
        frame = pd.read_csv(BytesIO(payload), header=None, names=KLINE_COLUMNS)
    else:
        frame = frame.loc[:, KLINE_COLUMNS]
    return frame[pd.to_numeric(frame["open_time"], errors="coerce").notna()].copy()


def timestamp_unit(values: pd.Series) -> str:
    first = int(pd.to_numeric(values, errors="raise").iloc[0])
    if 1_000_000_000_000 <= first < 10_000_000_000_000:
        return "ms"
    if 1_000_000_000_000_000 <= first < 10_000_000_000_000_000:
        return "us"
    raise RuntimeError(f"unsupported timestamp magnitude {first}")


def normalize_kline(frame: pd.DataFrame, interval_seconds: int) -> pd.DataFrame:
    frame = frame.drop_duplicates(subset=["open_time"], keep="last")
    frame = frame.sort_values("open_time", kind="stable")
    open_numeric = pd.to_numeric(frame["open_time"], errors="raise").astype("int64")
    open_index = pd.to_datetime(open_numeric, unit=timestamp_unit(open_numeric), utc=True)
    output = pd.DataFrame(index=open_index + pd.Timedelta(seconds=interval_seconds))
    output["open_time"] = open_index.to_numpy()
    for name in (
        "open", "high", "low", "close", "volume", "quote_volume", "trades",
        "taker_buy_volume", "taker_buy_quote_volume",
    ):
        output[name] = pd.to_numeric(frame[name], errors="raise").to_numpy(copy=True)
    return output[~output.index.duplicated(keep="last")].sort_index()


def selected_event_dates(protocol: dict[str, Any]) -> list[date]:
    data = protocol["data"]
    evaluation = protocol["evaluation"]
    selected: set[date] = set()
    for key, day_numbers in (
        ("development", data["development_sample_day_numbers"]),
        ("stability", data["stability_sample_day_numbers"]),
    ):
        start = date.fromisoformat(evaluation[f"{key}_start"])
        end = date.fromisoformat(evaluation[f"{key}_end_exclusive"])
        for month in month_starts(start, end):
            next_month = date(
                month.year + int(month.month == 12),
                1 if month.month == 12 else month.month + 1,
                1,
            )
            for day_number in day_numbers:
                try:
                    candidate = date(month.year, month.month, int(day_number))
                except ValueError:
                    continue
                if month <= candidate < min(next_month, end) and candidate >= start:
                    selected.add(candidate)
    selected.update(
        daterange(
            date.fromisoformat(evaluation["july_confirmation_start"]),
            date.fromisoformat(evaluation["july_confirmation_end_exclusive"]),
        )
    )
    selected.update(date.fromisoformat(value) for value in data["latest_august_dates"])
    return sorted(selected)


def load_monthly_one_minute(
    symbol: str,
    protocol: dict[str, Any],
    data_dir: Path,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    data = protocol["data"]
    start = date.fromisoformat(data["context_start"])
    monthly_end = date.fromisoformat(data["monthly_end_exclusive"])
    frames: list[pd.DataFrame] = []
    manifest: list[dict[str, Any]] = []
    for month in month_starts(start, monthly_end):
        token = f"{month.year:04d}-{month.month:02d}"
        filename = f"{symbol}-1m-{token}.zip"
        url = (
            "https://data.binance.vision/data/futures/um/monthly/klines/"
            f"{symbol}/1m/{filename}"
        )
        path = data_dir / "one_minute" / symbol / filename
        record = download(url, path)
        raw = read_kline_archive(path)
        record.update({"symbol": symbol, "interval": "1m", "token": token, "rows": len(raw.index)})
        frames.append(raw)
        manifest.append(record)
    daily_start = monthly_end
    daily_end = date.fromisoformat(data["latest_daily_end_exclusive"])
    for day in daterange(daily_start, daily_end):
        filename = f"{symbol}-1m-{day.isoformat()}.zip"
        url = (
            "https://data.binance.vision/data/futures/um/daily/klines/"
            f"{symbol}/1m/{filename}"
        )
        path = data_dir / "one_minute" / symbol / filename
        record = download(url, path)
        raw = read_kline_archive(path)
        record.update({"symbol": symbol, "interval": "1m", "token": day.isoformat(), "rows": len(raw.index)})
        frames.append(raw)
        manifest.append(record)
    output = normalize_kline(pd.concat(frames, ignore_index=True), 60)
    lower = pd.Timestamp(start, tz="UTC")
    upper = pd.Timestamp(daily_end, tz="UTC")
    output = output[(output.index > lower) & (output.index <= upper)]
    expected = int((upper - lower).total_seconds() // 60)
    coverage = len(output.index) / max(expected, 1)
    if coverage < 0.995:
        raise RuntimeError(f"insufficient {symbol} one-minute coverage {coverage:.6f}")
    return output, manifest


def load_daily_one_second(
    symbol: str,
    event_dates: list[date],
    data_dir: Path,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    frames: list[pd.DataFrame] = []
    manifest: list[dict[str, Any]] = []
    for day in event_dates:
        filename = f"{symbol}-1s-{day.isoformat()}.zip"
        url = (
            "https://data.binance.vision/data/futures/um/daily/klines/"
            f"{symbol}/1s/{filename}"
        )
        path = data_dir / "one_second" / symbol / filename
        record = download(url, path)
        raw = read_kline_archive(path)
        record.update({"symbol": symbol, "interval": "1s", "token": day.isoformat(), "rows": len(raw.index)})
        frames.append(raw)
        manifest.append(record)
    if not frames:
        return pd.DataFrame(), manifest
    return normalize_kline(pd.concat(frames, ignore_index=True), 1), manifest


def safe_div(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator / denominator.replace(0.0, np.nan)


def boundary_events(
    symbol: str,
    one_second: pd.DataFrame,
    rules: dict[str, Any],
) -> pd.DataFrame:
    if one_second.empty:
        return pd.DataFrame()
    open_stamp = pd.DatetimeIndex(one_second["open_time"])
    seconds = open_stamp.second
    minutes = open_stamp.minute
    mask = (
        minutes.isin((0, 15, 30, 45))
        & (seconds >= int(rules["first_second_inclusive"]) if "first_second_inclusive" in rules else seconds >= 0)
    )
    # The protocol keeps the exact second range under data, while this routine
    # is passed a merged rules object containing those values.
    lower_second = int(rules.get("first_second_inclusive", 0))
    upper_second = int(rules.get("last_second_inclusive", 9))
    mask = minutes.isin((0, 15, 30, 45)) & (seconds >= lower_second) & (seconds <= upper_second)
    selected = one_second.loc[mask].copy()
    timestamps = pd.DatetimeIndex(selected["open_time"])
    selected["event_ts"] = timestamps.floor("15min").to_numpy()
    grouped = selected.groupby("event_ts", sort=True)
    output = grouped.agg(
        ten_second_quote_volume=("quote_volume", "sum"),
        ten_second_taker_buy_quote=("taker_buy_quote_volume", "sum"),
        ten_second_trade_count=("trades", "sum"),
        ten_second_open=("open", "first"),
        ten_second_close=("close", "last"),
        observed_seconds=("open_time", "count"),
    )
    output["symbol"] = symbol
    output["ten_second_imbalance"] = (
        2.0
        * safe_div(
            output["ten_second_taker_buy_quote"],
            output["ten_second_quote_volume"],
        )
        - 1.0
    ).clip(-1.0, 1.0)
    output = output[
        (output["observed_seconds"] == upper_second - lower_second + 1)
        & (
            output["ten_second_quote_volume"]
            >= float(rules["minimum_ten_second_quote_volume"])
        )
    ]
    return output.replace([np.inf, -np.inf], np.nan).dropna(subset=["ten_second_imbalance"])


def one_minute_features(frame: pd.DataFrame) -> pd.DataFrame:
    close = frame["close"].astype(float)
    volume = frame["quote_volume"].astype(float)
    taker = frame["taker_buy_quote_volume"].astype(float)
    output = frame.loc[:, ["open", "high", "low", "close", "quote_volume"]].copy()
    output["signed_taker_pressure"] = (2.0 * safe_div(taker, volume) - 1.0).clip(-1.0, 1.0)
    for minutes in (60, 180, 360, 720):
        output[f"ret_{minutes}m"] = close.shift(1) / close.shift(minutes + 1) - 1.0
    short = volume.shift(1).rolling(60, min_periods=60).sum()
    long = volume.shift(1).rolling(480, min_periods=480).sum() / 8.0
    output["public_volume_ratio"] = safe_div(short, long)
    output["target_240m"] = close.shift(-240) / close - 1.0
    output["target_480m"] = close.shift(-480) / close - 1.0
    return output.replace([np.inf, -np.inf], np.nan)


def add_event_context(
    events: pd.DataFrame,
    minute_features: pd.DataFrame,
    rules: dict[str, Any],
) -> pd.DataFrame:
    if events.empty:
        return events.copy()
    event_index = pd.DatetimeIndex(events.index)
    context_index = event_index
    rows = minute_features.reindex(context_index, method="ffill").copy()
    rows.index = event_index
    output = events.join(rows, how="left")
    confirmation_start = event_index + pd.Timedelta(
        minutes=int(rules["confirmation_start_minute_after_boundary"])
    )
    confirmation_end = confirmation_start + pd.Timedelta(
        minutes=int(rules["confirmation_window_minutes"])
    )
    base = minute_features
    confirmations: list[dict[str, Any]] = []
    for start, end in zip(confirmation_start, confirmation_end, strict=True):
        window = base[(base.index > start) & (base.index <= end)]
        if len(window.index) != int(rules["confirmation_window_minutes"]):
            confirmations.append({})
            continue
        first = float(window["open"].iloc[0])
        last = float(window["close"].iloc[-1])
        high = float(window["high"].max())
        low = float(window["low"].min())
        quote = float(window["quote_volume"].sum())
        pressure = float(
            np.average(
                window["signed_taker_pressure"].astype(float),
                weights=np.maximum(window["quote_volume"].astype(float), 1.0),
            )
        )
        confirmations.append(
            {
                "confirmation_start": start,
                "entry_ts": end,
                "entry_price": last,
                "confirmation_return": last / first - 1.0,
                "confirmation_body_fraction": abs(last - first) / max(high - low, 1e-12),
                "confirmation_pressure": pressure,
            }
        )
    confirmation_frame = pd.DataFrame(confirmations, index=event_index)
    return output.join(confirmation_frame, how="left")


def prior_quantile(series: pd.Series, window: int, minimum: int, quantile: float) -> pd.Series:
    return series.shift(1).rolling(window, min_periods=minimum).quantile(quantile)


def add_prior_boundary_state(frame: pd.DataFrame, rules: dict[str, Any]) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    output = frame.sort_index(kind="stable").copy()
    abs_imbalance = output["ten_second_imbalance"].abs()
    output["imbalance_threshold"] = prior_quantile(
        abs_imbalance,
        int(rules["absolute_imbalance_prior_lookback_events"]),
        int(rules["absolute_imbalance_prior_minimum_events"]),
        float(rules["absolute_imbalance_prior_quantile"]),
    )
    phase = pd.Series(pd.DatetimeIndex(output.index).minute, index=output.index)
    sign = np.sign(output["ten_second_imbalance"]).replace(0.0, np.nan)
    lag_signs: list[pd.Series] = []
    for lag in range(1, int(rules["same_phase_lag_events"]) + 1):
        lag_signs.append(sign.shift(lag))
    lag_frame = pd.concat(lag_signs, axis=1)
    current_sign = sign
    output["same_phase_direction_agreement"] = lag_frame.eq(current_sign, axis=0).mean(axis=1)
    fresh_lags = lag_frame.iloc[:, :4]
    output["fresh_phase_direction_agreement"] = fresh_lags.eq(current_sign, axis=0).mean(axis=1)
    output["phase_minute"] = phase
    return output


def public_state_direction(frame: pd.DataFrame, rules: dict[str, Any]) -> pd.Series:
    returns = frame[[f"ret_{minutes}m" for minutes in (60, 180, 360, 720)]]
    positive = (returns > 0.0).mean(axis=1)
    negative = (returns < 0.0).mean(axis=1)
    minimum = float(rules["public_minimum_directional_return_agreement"])
    direction = pd.Series(0.0, index=frame.index)
    direction[positive >= minimum] = 1.0
    direction[negative >= minimum] = -1.0
    direction[frame["public_volume_ratio"] < float(rules["public_volume_ratio_minimum"])] = 0.0
    return direction


def candidate_rows(symbol: str, frame: pd.DataFrame, rules: dict[str, Any]) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    direction = np.sign(frame["ten_second_imbalance"])
    public_direction = public_state_direction(frame, rules)
    large = frame["ten_second_imbalance"].abs() >= frame["imbalance_threshold"]
    confirmation_aligned = (
        direction * frame["confirmation_return"]
        > float(rules["confirmation_minimum_directional_close_return"])
    ) & (
        frame["confirmation_body_fraction"]
        >= float(rules["confirmation_minimum_directional_body_fraction"])
    ) & (
        direction * frame["confirmation_pressure"]
        > float(rules["confirmation_minimum_directional_taker_pressure"])
    )
    persistence = (
        large
        & confirmation_aligned
        & (
            frame["same_phase_direction_agreement"]
            >= float(rules["same_phase_minimum_direction_agreement"])
        )
        & (
            frame["fresh_phase_direction_agreement"]
            >= float(rules["same_phase_minimum_fresh_sign_agreement"])
        )
    )
    public = large & confirmation_aligned & (public_direction == direction)
    rows: list[dict[str, Any]] = []
    for route, mask, target, horizon, quality in (
        (
            ROUTE_PERSISTENCE,
            persistence,
            "target_240m",
            int(rules["boundary_persistence_horizon_minutes"]),
            frame["same_phase_direction_agreement"],
        ),
        (
            ROUTE_PUBLIC,
            public,
            "target_480m",
            int(rules["public_delivery_horizon_minutes"]),
            frame[["ret_60m", "ret_180m", "ret_360m", "ret_720m"]].abs().mean(axis=1),
        ),
    ):
        eligible = frame[mask & frame[target].notna() & frame["entry_ts"].notna()]
        for timestamp, item in eligible.iterrows():
            sign_value = float(np.sign(item["ten_second_imbalance"]))
            gross = sign_value * float(item[target])
            normalized = abs(float(item["ten_second_imbalance"])) / max(
                float(item["imbalance_threshold"]), 1e-12
            )
            rank_value = normalized * (1.0 + float(quality.loc[timestamp]))
            rows.append(
                {
                    "event_ts": pd.Timestamp(timestamp),
                    "entry_ts": pd.Timestamp(item["entry_ts"]),
                    "exit_ts": pd.Timestamp(item["entry_ts"]) + pd.Timedelta(minutes=horizon),
                    "symbol": symbol,
                    "route": route,
                    "direction": "LONG" if sign_value > 0 else "SHORT",
                    "horizon_minutes": horizon,
                    "ten_second_imbalance": float(item["ten_second_imbalance"]),
                    "imbalance_threshold": float(item["imbalance_threshold"]),
                    "same_phase_direction_agreement": float(item["same_phase_direction_agreement"]),
                    "fresh_phase_direction_agreement": float(item["fresh_phase_direction_agreement"]),
                    "public_state_direction": float(public_direction.loc[timestamp]),
                    "confirmation_return": float(item["confirmation_return"]),
                    "confirmation_body_fraction": float(item["confirmation_body_fraction"]),
                    "confirmation_pressure": float(item["confirmation_pressure"]),
                    "entry_price": float(item["entry_price"]),
                    "rank_value": rank_value,
                    "gross_return": gross,
                }
            )
    return pd.DataFrame(rows)


def arbitrate(candidates: pd.DataFrame) -> tuple[pd.DataFrame, Counter[str]]:
    if candidates.empty:
        return candidates.copy(), Counter()
    candidates = candidates.sort_values(
        ["entry_ts", "rank_value", "symbol", "route"],
        ascending=[True, False, True, True],
        kind="stable",
    )
    selected: list[pd.Series] = []
    skips: Counter[str] = Counter()
    free_at = pd.Timestamp.min.tz_localize("UTC")
    same_symbol_free: dict[str, pd.Timestamp] = {}
    for timestamp, episode in candidates.groupby("entry_ts", sort=True):
        winner = episode.iloc[0]
        skips["SAME_ENTRY_TIME_LOSER"] += max(0, len(episode.index) - 1)
        symbol = str(winner["symbol"])
        if timestamp < free_at:
            skips["GLOBAL_POSITION_OCCUPIED"] += 1
            continue
        if timestamp < same_symbol_free.get(symbol, pd.Timestamp.min.tz_localize("UTC")):
            skips["SAME_SYMBOL_CAUSAL_COOLDOWN"] += 1
            continue
        selected.append(winner)
        free_at = pd.Timestamp(winner["exit_ts"])
        same_symbol_free[symbol] = timestamp + pd.Timedelta(
            minutes=int(winner["horizon_minutes"])
        )
    if not selected:
        return candidates.iloc[0:0].copy(), skips
    return pd.DataFrame(selected).reset_index(drop=True), skips


def t_stat(values: pd.Series) -> float | None:
    count = len(values.index)
    if count < 2:
        return None
    standard = float(values.std(ddof=1))
    if not math.isfinite(standard) or standard <= 0.0:
        return None
    return float(values.mean() / (standard / math.sqrt(count)))


def payoff(values: pd.Series) -> float | None:
    wins = values[values > 0.0]
    losses = values[values < 0.0]
    if wins.empty or losses.empty:
        return None
    return float(wins.mean() / abs(losses.mean()))


def split_summary(frame: pd.DataFrame, start: str, end_exclusive: str, sampled_days: int) -> dict[str, Any]:
    lower = pd.Timestamp(start, tz="UTC")
    upper = pd.Timestamp(end_exclusive, tz="UTC")
    section = frame[(frame["entry_ts"] >= lower) & (frame["entry_ts"] < upper)].copy()
    net = section["net_return"] if not section.empty else pd.Series(dtype=float)
    month = section["entry_ts"].dt.to_period("M").astype(str) if not section.empty else pd.Series(dtype=str)
    monthly = section.assign(month=month).groupby("month")["net_return"].sum() if not section.empty else pd.Series(dtype=float)
    route_stats: dict[str, Any] = {}
    for route, group in section.groupby("route"):
        route_stats[str(route)] = {
            "trades": len(group.index),
            "mean_net_bps": float(group["net_return"].mean() * 10_000.0),
            "win_rate": float((group["net_return"] > 0.0).mean()),
            "net_t_stat": t_stat(group["net_return"]),
        }
    return {
        "start": start,
        "end_exclusive": end_exclusive,
        "sampled_calendar_days": sampled_days,
        "trades": len(section.index),
        "trades_per_sampled_day": len(section.index) / max(sampled_days, 1),
        "mean_gross_bps": None if section.empty else float(section["gross_return"].mean() * 10_000.0),
        "mean_net_bps": None if section.empty else float(net.mean() * 10_000.0),
        "win_rate": None if section.empty else float((net > 0.0).mean()),
        "payoff_ratio": payoff(net),
        "net_t_stat": t_stat(net),
        "positive_months": int((monthly > 0.0).sum()),
        "active_months": len(monthly.index),
        "positive_month_share": float((monthly > 0.0).mean()) if len(monthly.index) else 0.0,
        "route_stats": route_stats,
        "route_counts": dict(Counter(section["route"])) if not section.empty else {},
        "symbol_counts": dict(Counter(section["symbol"])) if not section.empty else {},
    }


def selected_day_counts(event_dates: list[date], protocol: dict[str, Any]) -> dict[str, int]:
    evaluation = protocol["evaluation"]
    result: dict[str, int] = {}
    for name in ("development", "stability", "july_confirmation", "latest_pulse"):
        start = date.fromisoformat(evaluation[f"{name}_start"])
        end = date.fromisoformat(evaluation[f"{name}_end_exclusive"])
        result[name] = sum(start <= value < end for value in event_dates)
    return result


def route_cross_split(summary_by_split: dict[str, dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for route in (ROUTE_PERSISTENCE, ROUTE_PUBLIC):
        splits: dict[str, Any] = {}
        positive = True
        for split, summary in summary_by_split.items():
            record = summary["route_stats"].get(route)
            splits[split] = record
            if record is None or float(record["mean_net_bps"]) <= 0.0:
                positive = False
        output[route] = {"positive_across_all_declared_splits": positive, "splits": splits}
    return output


def execute(protocol_path: Path, output: Path) -> dict[str, Any]:
    protocol = load_object(protocol_path)
    data_config = protocol["data"]
    rules = {**protocol["fixed_rules"], **data_config}
    event_dates = selected_event_dates(protocol)
    output.mkdir(parents=True, exist_ok=True)
    data_dir = output / "data"
    frames_1m: dict[str, pd.DataFrame] = {}
    frames_1s: dict[str, pd.DataFrame] = {}
    manifest: list[dict[str, Any]] = []

    def load_symbol(symbol: str) -> tuple[str, pd.DataFrame, pd.DataFrame, list[dict[str, Any]]]:
        one_minute, m1 = load_monthly_one_minute(symbol, protocol, data_dir)
        one_second, s1 = load_daily_one_second(symbol, event_dates, data_dir)
        return symbol, one_minute, one_second, m1 + s1

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(load_symbol, symbol) for symbol in SYMBOLS]
        for future in as_completed(futures):
            symbol, one_minute, one_second, records = future.result()
            frames_1m[symbol] = one_minute
            frames_1s[symbol] = one_second
            manifest.extend(records)
    write_json(
        output / "data_manifest.json",
        {
            "schema": "candidate-15-v23-data-manifest-v1",
            "protocol": protocol["schema"],
            "selected_event_dates": [value.isoformat() for value in event_dates],
            "files": sorted(manifest, key=lambda item: (item["symbol"], item["interval"], item["token"])),
        },
    )

    candidates: list[pd.DataFrame] = []
    event_count = 0
    featured_count = 0
    for symbol in SYMBOLS:
        events = boundary_events(symbol, frames_1s[symbol], rules)
        event_count += len(events.index)
        features = one_minute_features(frames_1m[symbol])
        joined = add_event_context(events, features, rules)
        joined = add_prior_boundary_state(joined, rules)
        required = [
            "imbalance_threshold", "same_phase_direction_agreement",
            "fresh_phase_direction_agreement", "entry_ts", "entry_price",
            "confirmation_return", "confirmation_body_fraction",
            "confirmation_pressure", "public_volume_ratio", "target_240m",
            "target_480m", "ret_60m", "ret_180m", "ret_360m", "ret_720m",
        ]
        joined = joined.replace([np.inf, -np.inf], np.nan).dropna(subset=required)
        featured_count += len(joined.index)
        rows = candidate_rows(symbol, joined, rules)
        if not rows.empty:
            candidates.append(rows)
    candidate_frame = pd.concat(candidates, ignore_index=True) if candidates else pd.DataFrame()
    selected, skips = arbitrate(candidate_frame)
    cost = (
        float(rules["execution_round_trip_cost_bps"])
        + float(rules["funding_and_unmodeled_impact_reserve_bps"])
    ) / 10_000.0
    if selected.empty:
        selected = pd.DataFrame(
            columns=[
                "event_ts", "entry_ts", "exit_ts", "symbol", "route",
                "direction", "gross_return", "net_return",
            ]
        )
    else:
        selected["net_return"] = selected["gross_return"] - cost
        selected = selected.sort_values("entry_ts", kind="stable").reset_index(drop=True)
    candidate_frame.to_csv(output / "all_routed_candidates.csv", index=False)
    selected.to_csv(output / "selected_events.csv", index=False)

    counts = selected_day_counts(event_dates, protocol)
    evaluation = protocol["evaluation"]
    summaries = {
        name: split_summary(
            selected,
            evaluation[f"{name}_start"],
            evaluation[f"{name}_end_exclusive"],
            counts[name],
        )
        for name in ("development", "stability", "july_confirmation", "latest_pulse")
    }
    development = summaries["development"]
    stability = summaries["stability"]
    july = summaries["july_confirmation"]
    pulse = summaries["latest_pulse"]
    gate = protocol["advance_gate"]
    cross_split_routes = route_cross_split(summaries)
    survivors = [route for route, item in cross_split_routes.items() if item["positive_across_all_declared_splits"]]
    stability_symbol_counts = stability["symbol_counts"]
    max_symbol_share = (
        max(stability_symbol_counts.values()) / max(stability["trades"], 1)
        if stability_symbol_counts else 1.0
    )
    checks = {
        "positive_development_mean_net": development["mean_net_bps"] is not None and development["mean_net_bps"] > 0.0,
        "positive_stability_mean_net": stability["mean_net_bps"] is not None and stability["mean_net_bps"] >= float(gate["minimum_stability_mean_net_bps"]),
        "stability_net_t_stat": stability["net_t_stat"] is not None and stability["net_t_stat"] >= float(gate["minimum_stability_net_t_stat"]),
        "stability_positive_month_share": stability["positive_month_share"] >= float(gate["minimum_stability_positive_month_share"]),
        "family_stability_frequency": stability["trades_per_sampled_day"] >= float(gate["minimum_family_stability_trades_per_sampled_day"]),
        "positive_july_confirmation_mean_net": july["mean_net_bps"] is not None and july["mean_net_bps"] > 0.0,
        "july_confirmation_trade_count": july["trades"] >= int(gate["minimum_july_confirmation_trades"]),
        "positive_latest_pulse_mean_net": pulse["mean_net_bps"] is not None and pulse["mean_net_bps"] >= float(gate["minimum_latest_pulse_mean_net_bps"]),
        "latest_pulse_trade_count": pulse["trades"] >= int(gate["minimum_latest_pulse_trades"]),
        "symbol_concentration": max_symbol_share <= float(gate["maximum_single_symbol_share"]),
        "cross_split_positive_route_exists": bool(survivors),
    }
    advance = all(checks.values())
    classification = (
        "V23_QUARTER_HOUR_10S_ROUTE_ADVANCE_TO_NAUTILUS"
        if advance
        else "V23_QUARTER_HOUR_10S_ROUTER_REJECTED_OR_UNDERPOWERED"
    )
    decision = (
        f"Fixed cross-split-positive routes: {survivors}. "
        + (
            "Freeze those routes and implement NautilusTrader bracket execution."
            if advance
            else "Do not tune the ten-second imbalance family; preserve only any "
            "cross-split-positive route and move to another independent mechanism."
        )
    )
    summary = {
        "schema": "candidate-15-v23-summary-v1",
        "classification": classification,
        "advance_to_nautilus": advance,
        "raw_ten_second_events": event_count,
        "causally_featured_events": featured_count,
        "routed_candidates": len(candidate_frame.index),
        "selected_events": len(selected.index),
        "arbitration_skips": dict(skips),
        "development": development,
        "stability": stability,
        "july_confirmation": july,
        "latest_pulse": pulse,
        "advance_checks": checks,
        "cross_split_routes": cross_split_routes,
        "surviving_routes": survivors,
        "maximum_stability_symbol_share": max_symbol_share,
        "decision": decision,
    }
    write_json(output / "summary.json", summary)

    lines = [
        "# Candidate 15 V23 — Exact ten-second quarter-hour order-imbalance diagnostic",
        "",
        f"**{classification}**",
        "",
        "Boundary imbalance is reconstructed from Binance USD-M one-second bars "
        "covering seconds 0-9. Public state ends before the boundary and a "
        "separate completed five-minute interval confirms the tradable leg.",
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
                f"- sampled days: `{item['sampled_calendar_days']}`",
                f"- selected trades / sampled day: `{item['trades']} / "
                f"{item['trades_per_sampled_day']}`",
                f"- gross / net mean: `{item['mean_gross_bps']} / "
                f"{item['mean_net_bps']}` bp",
                f"- win rate / payoff: `{item['win_rate']} / "
                f"{item['payoff_ratio']}`",
                f"- net t-stat: `{item['net_t_stat']}`",
                f"- positive months: `{item['positive_months']} / "
                f"{item['active_months']}`",
                f"- route stats: `{item['route_stats']}`",
                f"- symbol counts: `{item['symbol_counts']}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Advance checks",
            *[f"- {key}: `{value}`" for key, value in checks.items()],
            "",
            "## Cross-split route evidence",
            f"`{cross_split_routes}`",
            "",
            "## Decision",
            decision,
            "",
            "This is a mechanism screen, not synthetic NAV. A survivor still "
            "requires frozen NautilusTrader orders, same-leg invalidation, "
            "current-NAV 3% risk sizing, actual costs, one global slot and final "
            "continuous-account validation.",
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
