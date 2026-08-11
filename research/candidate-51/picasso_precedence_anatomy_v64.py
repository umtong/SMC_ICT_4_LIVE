#!/usr/bin/env python3
"""Causal anatomy of the high-frequency Picasso RSI/BB/MACD claim.

External clue
-------------
The public ``RSI_BB_MACD_Nov_2023_1h_2_Dec`` source and log report two
2021-2023 futures backtests.  With 129 simultaneous slots the report contains
330,583 trades (321.89/day), 78.1% wins and 5.42% arithmetic daily profit.
With four simultaneous slots it contains 24,973 trades (24.32/day), 76.8%
wins, PF 1.57 and 9.26% arithmetic daily profit.  The source is unusually
valuable because it contains a concrete operator-precedence ambiguity:

    low_adx | high_adx & direction & volume

Python evaluates ``&`` before ``|``.  Therefore the first ADX interval enters
without RSI, Bollinger, MACD, candle-body or volume confirmation.  This module
directly compares that executed expression with the likely intended expression:

    (low_adx | high_adx) & direction & volume

The report also uses five-times leverage, hourly candles and Freqtrade trailing
logic.  Freqtrade updates a trailing stop from the favorable hourly bound and
then tests the adverse bound from the same candle.  That can assume a favorable
intrabar ordering which the OHLC candle does not prove.  The experiment thus
compares:

* ``freqtrade_bound_first``: the public backtest ordering;
* ``conservative_adverse_first``: an already-active stop may execute first and
  a newly activated trail cannot be hit until a later candle.

No threshold is searched.  Public indicators, ADX intervals, ROI ladder,
leverage conversion, stop, trail and exits are frozen.  Two opportunity clocks
are reported: source-style re-entry whenever a slot becomes free, and one entry
per continuous signal episode.  All four assets are arbitrated into one global
held position using only pre-entry branch specificity, normalized body/MACD/
volume strength and stable symbol priority.  This is a causal path diagnostic,
not a matching/account/NAV engine and not final evidence.
"""
from __future__ import annotations

import argparse
import calendar
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
SYMBOL_PRIORITY = {symbol: index for index, symbol in enumerate(SYMBOLS)}
BASE_DAILY = "https://data.binance.vision/data/futures/um/daily/klines"
BASE_MONTHLY = "https://data.binance.vision/data/futures/um/monthly/klines"
KLINE_COLUMNS = (
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "count", "taker_buy_volume",
    "taker_buy_quote_volume", "ignore",
)

LEVERAGE = 5.0
ROUND_TRIP_COST = 0.0019
HARD_STOP_PROFIT = -0.317
TRAIL_POSITIVE_PROFIT = 0.01
TRAIL_OFFSET_PROFIT = 0.022
ROI_PROFIT = ((0, 0.184), (416, 0.14), (933, 0.073), (1982, 0.0))
MAX_HOLD_HOURS = 14 * 24

ADX_LONG_LOW = (5.7, 6.5)
ADX_LONG_HIGH = (20.9, 50.7)
ADX_SHORT_LOW = (9.9, 21.4)
ADX_SHORT_HIGH = (30.3, 50.8)
RSI_LONG = 22
RSI_SHORT = 17
BB_LONG = 16
BB_SHORT = 20
VOLUME_LONG = 38
VOLUME_SHORT = 20
ATR_PERIOD = 20
EMA_LONG_EXIT = 91
EMA_SHORT_EXIT = 147
VOLUME_LONG_EXIT = 19
VOLUME_SHORT_EXIT = 41
ATR_LONG_EXIT = 3.8
ATR_SHORT_EXIT = 5.0
POLICY_ORDER = tuple(
    f"{variant}:{episode}:{path}"
    for variant in ("executed_precedence", "intended_parentheses")
    for episode in ("source_reentry", "continuous_signal_episode")
    for path in ("freqtrade_bound_first", "conservative_adverse_first")
)


@dataclass(frozen=True, slots=True)
class RawEvidence:
    symbol: str
    interval: str
    source_mode: str
    stamp: str
    archive: str
    checksum: str
    size_bytes: int
    sha256: str


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def _download(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    request = Request(url, headers={"User-Agent": "candidate-51-research/1.0"})
    with urlopen(request, timeout=60) as response:
        path.write_bytes(response.read())


def _checked_archive(
    *,
    symbol: str,
    interval: str,
    source_mode: str,
    stamp: str,
    cache: Path,
) -> tuple[Path, RawEvidence]:
    if source_mode == "monthly":
        filename = f"{symbol}-{interval}-{stamp}.zip"
        url = f"{BASE_MONTHLY}/{symbol}/{interval}/{filename}"
    elif source_mode == "daily":
        filename = f"{symbol}-{interval}-{stamp}.zip"
        url = f"{BASE_DAILY}/{symbol}/{interval}/{filename}"
    else:
        raise ValueError(source_mode)
    directory = cache / source_mode / symbol / interval
    archive = directory / filename
    checksum = directory / f"{filename}.CHECKSUM"
    if not archive.exists():
        _download(url, archive)
    if not checksum.exists():
        _download(url + ".CHECKSUM", checksum)
    expected = checksum.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = _sha256_file(archive)
    if actual != expected:
        raise RuntimeError(f"checksum mismatch for {archive}: {actual} != {expected}")
    return archive, RawEvidence(
        symbol=symbol,
        interval=interval,
        source_mode=source_mode,
        stamp=stamp,
        archive=str(archive),
        checksum=str(checksum),
        size_bytes=archive.stat().st_size,
        sha256=actual,
    )


def _timestamp_unit(series: pd.Series) -> str:
    first = int(pd.to_numeric(series, errors="raise").iloc[0])
    return "us" if abs(first) > 10**14 else "ms"


def _read_kline(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path, compression="zip", header=None)
    if raw.shape[1] == len(KLINE_COLUMNS):
        raw.columns = KLINE_COLUMNS
        first = str(raw.iloc[0]["open_time"]).strip()
        if not first.lstrip("-").isdigit():
            raw = raw.iloc[1:].copy()
    else:
        with_header = pd.read_csv(path, compression="zip")
        if not set(KLINE_COLUMNS).issubset(with_header.columns):
            raise RuntimeError(f"unexpected kline schema in {path}")
        raw = with_header[list(KLINE_COLUMNS)].copy()
    for column in (
        "open_time", "close_time", "open", "high", "low", "close",
        "volume", "quote_volume",
    ):
        raw[column] = pd.to_numeric(raw[column], errors="raise")
    raw["open_time_dt"] = pd.to_datetime(
        raw["open_time"].astype("int64"),
        unit=_timestamp_unit(raw["open_time"]),
        utc=True,
    )
    raw["close_time_dt"] = pd.to_datetime(
        raw["close_time"].astype("int64"),
        unit=_timestamp_unit(raw["close_time"]),
        utc=True,
    )
    return raw[
        ["open_time_dt", "close_time_dt", "open", "high", "low", "close", "volume", "quote_volume"]
    ].sort_values("open_time_dt")


def _month_starts(start: date, end: date) -> list[date]:
    cursor = date(start.year, start.month, 1)
    last = date(end.year, end.month, 1)
    result: list[date] = []
    while cursor <= last:
        result.append(cursor)
        cursor = date(cursor.year + (cursor.month == 12), 1 if cursor.month == 12 else cursor.month + 1, 1)
    return result


def _load_hourly(
    *,
    symbol: str,
    start: date,
    end: date,
    cache: Path,
) -> tuple[pd.DataFrame, list[RawEvidence]]:
    frames: list[pd.DataFrame] = []
    evidence: list[RawEvidence] = []
    for month in _month_starts(start, end):
        month_end = date(month.year, month.month, calendar.monthrange(month.year, month.month)[1])
        overlap_start = max(start, month)
        overlap_end = min(end, month_end)
        stamp = f"{month.year:04d}-{month.month:02d}"
        try:
            archive, item = _checked_archive(
                symbol=symbol,
                interval="1h",
                source_mode="monthly",
                stamp=stamp,
                cache=cache,
            )
            frames.append(_read_kline(archive))
            evidence.append(item)
            continue
        except HTTPError as error:
            if error.code != 404:
                raise
        day = overlap_start
        while day <= overlap_end:
            archive, item = _checked_archive(
                symbol=symbol,
                interval="1h",
                source_mode="daily",
                stamp=day.isoformat(),
                cache=cache,
            )
            frames.append(_read_kline(archive))
            evidence.append(item)
            day += timedelta(days=1)
    frame = pd.concat(frames, ignore_index=True)
    frame = frame.drop_duplicates("open_time_dt", keep="last").sort_values("open_time_dt")
    lower = pd.Timestamp(start, tz="UTC")
    upper = pd.Timestamp(end + timedelta(days=1), tz="UTC")
    frame = frame[(frame["open_time_dt"] >= lower) & (frame["open_time_dt"] < upper)].reset_index(drop=True)
    expected = (end - start).days * 24 + 24
    if len(frame) != expected:
        raise RuntimeError(f"incomplete hourly data for {symbol}: {len(frame)} != {expected}")
    times = pd.DatetimeIndex(frame["open_time_dt"])
    if not bool(((times[1:] - times[:-1]) == pd.Timedelta(hours=1)).all()):
        raise RuntimeError(f"non-contiguous hourly data for {symbol}")
    return frame, evidence


def _ema(series: pd.Series, period: int) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce").to_numpy(float)
    output = np.full(len(values), np.nan, dtype=float)
    finite = np.isfinite(values)
    start: int | None = None
    for index in range(0, len(values) - period + 1):
        if bool(finite[index:index + period].all()):
            start = index
            break
    if start is None:
        return pd.Series(output, index=series.index)
    seed_index = start + period - 1
    output[seed_index] = float(values[start:seed_index + 1].mean())
    alpha = 2.0 / (period + 1.0)
    for index in range(seed_index + 1, len(values)):
        if math.isfinite(values[index]) and math.isfinite(output[index - 1]):
            output[index] = output[index - 1] + alpha * (values[index] - output[index - 1])
    return pd.Series(output, index=series.index)


def _wilder(series: pd.Series, period: int) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce").to_numpy(float)
    output = np.full(len(values), np.nan, dtype=float)
    if len(values) <= period:
        return pd.Series(output, index=series.index)
    seed = values[1:period + 1]
    if not np.all(np.isfinite(seed)):
        return pd.Series(output, index=series.index)
    output[period] = float(seed.mean())
    for index in range(period + 1, len(values)):
        if math.isfinite(values[index]) and math.isfinite(output[index - 1]):
            output[index] = ((period - 1) * output[index - 1] + values[index]) / period
    return pd.Series(output, index=series.index)


def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = pd.to_numeric(close, errors="coerce").diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = _wilder(gain.fillna(0.0), period)
    avg_loss = _wilder(loss.fillna(0.0), period)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    result = 100.0 - 100.0 / (1.0 + rs)
    result = result.where(avg_loss > 0.0, 100.0)
    return result


def _true_range(frame: pd.DataFrame) -> pd.Series:
    prior = frame["close"].shift(1)
    return pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - prior).abs(),
            (frame["low"] - prior).abs(),
        ],
        axis=1,
    ).max(axis=1)


def _atr(frame: pd.DataFrame, period: int) -> pd.Series:
    return _wilder(_true_range(frame), period)


def _adx(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    high = pd.to_numeric(frame["high"], errors="coerce")
    low = pd.to_numeric(frame["low"], errors="coerce")
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0.0), up, 0.0), index=frame.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0.0), down, 0.0), index=frame.index)
    atr = _atr(frame, period)
    plus_di = 100.0 * _wilder(plus_dm, period) / atr
    minus_di = 100.0 * _wilder(minus_dm, period) / atr
    denominator = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / denominator
    return _wilder(dx.fillna(0.0), period)


def _bollinger(close: pd.Series, period: int) -> tuple[pd.Series, pd.Series, pd.Series]:
    middle = close.rolling(period, min_periods=period).mean()
    deviation = close.rolling(period, min_periods=period).std(ddof=0)
    return middle + 2.0 * deviation, middle, middle - 2.0 * deviation


def _macd(close: pd.Series) -> tuple[pd.Series, pd.Series]:
    macd = _ema(close, 12) - _ema(close, 26)
    signal = _ema(macd, 9)
    return macd, signal


def _features(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["adx"] = _adx(out)
    out["atr"] = _atr(out, ATR_PERIOD)
    out["ema_long_exit"] = _ema(out["close"], EMA_LONG_EXIT)
    out["ema_short_exit"] = _ema(out["close"], EMA_SHORT_EXIT)
    out["volume_mean_long"] = out["volume"].rolling(VOLUME_LONG).mean().shift(1)
    out["volume_mean_short"] = out["volume"].rolling(VOLUME_SHORT).mean().shift(1)
    out["volume_mean_long_exit"] = out["volume"].rolling(VOLUME_LONG_EXIT).mean().shift(1)
    out["volume_mean_short_exit"] = out["volume"].rolling(VOLUME_SHORT_EXIT).mean().shift(1)

    out["rsi_long"] = _rsi(out["close"], RSI_LONG)
    out["upper_long"], out["middle_long"], out["lower_long"] = _bollinger(out["close"], BB_LONG)
    out["macd_long"], out["macd_signal_long"] = _macd(out["close"])
    out["rsi_short"] = _rsi(out["close"], RSI_SHORT)
    out["upper_short"], out["middle_short"], out["lower_short"] = _bollinger(out["close"], BB_SHORT)
    out["macd_short"], out["macd_signal_short"] = _macd(out["close"])

    out["trend_long"] = (
        (out["rsi_long"] > 50.0)
        & (out["close"] > out["middle_long"])
        & (out["close"] < out["upper_long"])
        & (out["macd_long"] > out["macd_signal_long"])
        & ((out["high"] - out["close"]) < (out["close"] - out["open"]))
        & (out["close"] > out["open"])
    )
    out["trend_short"] = (
        (out["rsi_short"] < 50.0)
        & (out["close"] < out["middle_short"])
        & (out["close"] > out["lower_short"])
        & (out["macd_short"] < out["macd_signal_short"])
        & ((out["close"] - out["low"]) < (out["open"] - out["close"]))
        & (out["close"] < out["open"])
    )
    low_long = out["adx"].between(*ADX_LONG_LOW, inclusive="neither")
    high_long = out["adx"].between(*ADX_LONG_HIGH, inclusive="neither")
    low_short = out["adx"].between(*ADX_SHORT_LOW, inclusive="neither")
    high_short = out["adx"].between(*ADX_SHORT_HIGH, inclusive="neither")
    volume_long = out["volume"] > out["volume_mean_long"]
    volume_short = out["volume"] > out["volume_mean_short"]

    out["executed_long"] = low_long | (high_long & out["trend_long"] & volume_long)
    out["executed_short"] = low_short | (high_short & out["trend_short"] & volume_short)
    out["intended_long"] = (low_long | high_long) & out["trend_long"] & volume_long
    out["intended_short"] = (low_short | high_short) & out["trend_short"] & volume_short
    out["executed_long_branch"] = np.where(low_long, "low_adx_only", "high_adx_full")
    out["executed_short_branch"] = np.where(low_short, "low_adx_only", "high_adx_full")
    out["intended_long_branch"] = "fully_confirmed"
    out["intended_short_branch"] = "fully_confirmed"

    out["source_exit_long"] = (
        (out["close"] < out["ema_long_exit"] - ATR_LONG_EXIT * out["atr"])
        & (out["volume"] > out["volume_mean_long_exit"])
    )
    out["source_exit_short"] = (
        (out["close"] > out["ema_short_exit"] + ATR_SHORT_EXIT * out["atr"])
        & (out["volume"] > out["volume_mean_short_exit"])
    )
    out["body_atr"] = (out["close"] - out["open"]).abs() / out["atr"].replace(0.0, np.nan)
    out["macd_atr"] = (out["macd_long"] - out["macd_signal_long"]).abs() / out["atr"].replace(0.0, np.nan)
    out["volume_ratio_long"] = out["volume"] / out["volume_mean_long"].replace(0.0, np.nan)
    out["volume_ratio_short"] = out["volume"] / out["volume_mean_short"].replace(0.0, np.nan)
    return out


def _roi_price_threshold(elapsed_minutes: float) -> float:
    selected = ROI_PROFIT[0][1]
    for minute, value in ROI_PROFIT:
        if elapsed_minutes >= minute:
            selected = value
    return selected / LEVERAGE


def _side_return(side: int, price: float, entry: float) -> float:
    return side * (price / entry - 1.0)


def _stop_price(entry: float, side: int) -> float:
    distance = abs(HARD_STOP_PROFIT) / LEVERAGE
    return entry * (1.0 - side * distance)


def _trailing_price(bound: float, side: int) -> float:
    distance = TRAIL_POSITIVE_PROFIT / LEVERAGE
    return bound * (1.0 - side * distance)


def _exit_signal_column(variant: str, side: int) -> tuple[str, str]:
    prefix = "executed" if variant == "executed_precedence" else "intended"
    entry = f"{prefix}_{'long' if side > 0 else 'short'}"
    exit_column = "source_exit_long" if side > 0 else "source_exit_short"
    return entry, exit_column


def _simulate(
    frame: pd.DataFrame,
    signal_index: int,
    *,
    variant: str,
    side: int,
    path_mode: str,
) -> dict[str, Any] | None:
    entry_index = signal_index + 1
    if entry_index >= len(frame):
        return None
    entry_row = frame.iloc[entry_index]
    entry_time = pd.Timestamp(entry_row["open_time_dt"])
    entry_price = float(entry_row["open"])
    hard_stop = _stop_price(entry_price, side)
    active_trail: float | None = None
    maximum_index = min(len(frame) - 1, entry_index + MAX_HOLD_HOURS)
    entry_column, exit_column = _exit_signal_column(variant, side)
    same_bar_trail_activation = False
    maximum_favorable = -math.inf
    maximum_adverse = math.inf

    for index in range(entry_index, maximum_index + 1):
        bar = frame.iloc[index]
        open_time = pd.Timestamp(bar["open_time_dt"])
        close_time = pd.Timestamp(bar["close_time_dt"])
        bar_open = float(bar["open"])
        favorable = float(bar["high"] if side > 0 else bar["low"])
        adverse = float(bar["low"] if side > 0 else bar["high"])
        maximum_favorable = max(maximum_favorable, _side_return(side, favorable, entry_price))
        maximum_adverse = min(maximum_adverse, _side_return(side, adverse, entry_price))

        # Freqtrade exit signals are shifted to the next candle and ignored
        # while the same-side entry signal is still active.
        if index > entry_index:
            previous = frame.iloc[index - 1]
            if bool(previous[exit_column]) and not bool(previous[entry_column]):
                gross = _side_return(side, bar_open, entry_price)
                return {
                    "entry_time": entry_time,
                    "entry_price": entry_price,
                    "exit_time": open_time,
                    "exit_price": bar_open,
                    "exit_reason": "exit_signal",
                    "hold_minutes": (open_time - entry_time).total_seconds() / 60.0,
                    "gross_return": gross,
                    "net_return": gross - ROUND_TRIP_COST,
                    "planned_loss_fraction": abs(HARD_STOP_PROFIT) / LEVERAGE + ROUND_TRIP_COST,
                    "r_multiple": (gross - ROUND_TRIP_COST) / (abs(HARD_STOP_PROFIT) / LEVERAGE + ROUND_TRIP_COST),
                    "mfe_gross": maximum_favorable,
                    "mae_gross": maximum_adverse,
                    "same_bar_trail_activation": same_bar_trail_activation,
                }

        # Gaps through an already active stop are filled at the open.
        stop_now = active_trail if active_trail is not None else hard_stop
        gap_hit = bar_open <= stop_now if side > 0 else bar_open >= stop_now
        if gap_hit:
            gross = _side_return(side, bar_open, entry_price)
            reason = "trailing_gap" if active_trail is not None else "stop_gap"
            return {
                "entry_time": entry_time,
                "entry_price": entry_price,
                "exit_time": open_time,
                "exit_price": bar_open,
                "exit_reason": reason,
                "hold_minutes": (open_time - entry_time).total_seconds() / 60.0,
                "gross_return": gross,
                "net_return": gross - ROUND_TRIP_COST,
                "planned_loss_fraction": abs(HARD_STOP_PROFIT) / LEVERAGE + ROUND_TRIP_COST,
                "r_multiple": (gross - ROUND_TRIP_COST) / (abs(HARD_STOP_PROFIT) / LEVERAGE + ROUND_TRIP_COST),
                "mfe_gross": maximum_favorable,
                "mae_gross": maximum_adverse,
                "same_bar_trail_activation": same_bar_trail_activation,
            }

        if path_mode == "conservative_adverse_first":
            existing_stop_hit = adverse <= stop_now if side > 0 else adverse >= stop_now
            if existing_stop_hit:
                gross = _side_return(side, stop_now, entry_price)
                reason = "trailing_stop" if active_trail is not None else "stop_loss"
                return {
                    "entry_time": entry_time,
                    "entry_price": entry_price,
                    "exit_time": close_time,
                    "exit_price": stop_now,
                    "exit_reason": reason,
                    "hold_minutes": (close_time - entry_time).total_seconds() / 60.0,
                    "gross_return": gross,
                    "net_return": gross - ROUND_TRIP_COST,
                    "planned_loss_fraction": abs(HARD_STOP_PROFIT) / LEVERAGE + ROUND_TRIP_COST,
                    "r_multiple": (gross - ROUND_TRIP_COST) / (abs(HARD_STOP_PROFIT) / LEVERAGE + ROUND_TRIP_COST),
                    "mfe_gross": maximum_favorable,
                    "mae_gross": maximum_adverse,
                    "same_bar_trail_activation": same_bar_trail_activation,
                }

        elapsed = (open_time - entry_time).total_seconds() / 60.0
        roi = _roi_price_threshold(elapsed)
        roi_price = entry_price * (1.0 + side * roi)
        roi_hit = favorable >= roi_price if side > 0 else favorable <= roi_price

        best_profit_leveraged = LEVERAGE * _side_return(side, favorable, entry_price)
        newly_active = (
            best_profit_leveraged > TRAIL_OFFSET_PROFIT
            and active_trail is None
        )
        if best_profit_leveraged > TRAIL_OFFSET_PROFIT:
            proposed = _trailing_price(favorable, side)
            active_trail = (
                proposed
                if active_trail is None
                else max(active_trail, proposed)
                if side > 0
                else min(active_trail, proposed)
            )

        if path_mode == "freqtrade_bound_first":
            stop_after_update = active_trail if active_trail is not None else hard_stop
            stop_hit = adverse <= stop_after_update if side > 0 else adverse >= stop_after_update
            if stop_hit and active_trail is None:
                gross = _side_return(side, hard_stop, entry_price)
                return {
                    "entry_time": entry_time,
                    "entry_price": entry_price,
                    "exit_time": close_time,
                    "exit_price": hard_stop,
                    "exit_reason": "stop_loss",
                    "hold_minutes": (close_time - entry_time).total_seconds() / 60.0,
                    "gross_return": gross,
                    "net_return": gross - ROUND_TRIP_COST,
                    "planned_loss_fraction": abs(HARD_STOP_PROFIT) / LEVERAGE + ROUND_TRIP_COST,
                    "r_multiple": (gross - ROUND_TRIP_COST) / (abs(HARD_STOP_PROFIT) / LEVERAGE + ROUND_TRIP_COST),
                    "mfe_gross": maximum_favorable,
                    "mae_gross": maximum_adverse,
                    "same_bar_trail_activation": same_bar_trail_activation,
                }
            # Official exit order is hard stop, ROI, then trailing stop.
            if roi_hit:
                gross = _side_return(side, roi_price, entry_price)
                return {
                    "entry_time": entry_time,
                    "entry_price": entry_price,
                    "exit_time": close_time,
                    "exit_price": roi_price,
                    "exit_reason": "roi",
                    "hold_minutes": (close_time - entry_time).total_seconds() / 60.0,
                    "gross_return": gross,
                    "net_return": gross - ROUND_TRIP_COST,
                    "planned_loss_fraction": abs(HARD_STOP_PROFIT) / LEVERAGE + ROUND_TRIP_COST,
                    "r_multiple": (gross - ROUND_TRIP_COST) / (abs(HARD_STOP_PROFIT) / LEVERAGE + ROUND_TRIP_COST),
                    "mfe_gross": maximum_favorable,
                    "mae_gross": maximum_adverse,
                    "same_bar_trail_activation": same_bar_trail_activation,
                }
            if active_trail is not None and stop_hit:
                if newly_active:
                    same_bar_trail_activation = True
                gross = _side_return(side, active_trail, entry_price)
                return {
                    "entry_time": entry_time,
                    "entry_price": entry_price,
                    "exit_time": close_time,
                    "exit_price": active_trail,
                    "exit_reason": "trailing_stop",
                    "hold_minutes": (close_time - entry_time).total_seconds() / 60.0,
                    "gross_return": gross,
                    "net_return": gross - ROUND_TRIP_COST,
                    "planned_loss_fraction": abs(HARD_STOP_PROFIT) / LEVERAGE + ROUND_TRIP_COST,
                    "r_multiple": (gross - ROUND_TRIP_COST) / (abs(HARD_STOP_PROFIT) / LEVERAGE + ROUND_TRIP_COST),
                    "mfe_gross": maximum_favorable,
                    "mae_gross": maximum_adverse,
                    "same_bar_trail_activation": same_bar_trail_activation,
                }
        else:
            # After the adverse-first check, ROI may still execute from the
            # favorable bound. A newly activated trail becomes eligible only
            # on a later candle.
            if roi_hit:
                gross = _side_return(side, roi_price, entry_price)
                return {
                    "entry_time": entry_time,
                    "entry_price": entry_price,
                    "exit_time": close_time,
                    "exit_price": roi_price,
                    "exit_reason": "roi",
                    "hold_minutes": (close_time - entry_time).total_seconds() / 60.0,
                    "gross_return": gross,
                    "net_return": gross - ROUND_TRIP_COST,
                    "planned_loss_fraction": abs(HARD_STOP_PROFIT) / LEVERAGE + ROUND_TRIP_COST,
                    "r_multiple": (gross - ROUND_TRIP_COST) / (abs(HARD_STOP_PROFIT) / LEVERAGE + ROUND_TRIP_COST),
                    "mfe_gross": maximum_favorable,
                    "mae_gross": maximum_adverse,
                    "same_bar_trail_activation": False,
                }

    last = frame.iloc[maximum_index]
    exit_price = float(last["close"])
    exit_time = pd.Timestamp(last["close_time_dt"])
    gross = _side_return(side, exit_price, entry_price)
    return {
        "entry_time": entry_time,
        "entry_price": entry_price,
        "exit_time": exit_time,
        "exit_price": exit_price,
        "exit_reason": "max_14d",
        "hold_minutes": (exit_time - entry_time).total_seconds() / 60.0,
        "gross_return": gross,
        "net_return": gross - ROUND_TRIP_COST,
        "planned_loss_fraction": abs(HARD_STOP_PROFIT) / LEVERAGE + ROUND_TRIP_COST,
        "r_multiple": (gross - ROUND_TRIP_COST) / (abs(HARD_STOP_PROFIT) / LEVERAGE + ROUND_TRIP_COST),
        "mfe_gross": maximum_favorable,
        "mae_gross": maximum_adverse,
        "same_bar_trail_activation": same_bar_trail_activation,
    }


def _signals(
    *,
    symbol: str,
    frame: pd.DataFrame,
    start: date,
    end: date,
    period_label: str,
    split: str,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for variant, prefix in (
        ("executed_precedence", "executed"),
        ("intended_parentheses", "intended"),
    ):
        for side, side_name in ((1, "long"), (-1, "short")):
            signal_column = f"{prefix}_{side_name}"
            branch_column = f"{prefix}_{side_name}_branch"
            signal = frame[signal_column].fillna(False).astype(bool)
            episode_start = signal & ~signal.shift(1).fillna(False)
            episode_number = episode_start.cumsum()
            for index in frame.index[signal]:
                signal_time = pd.Timestamp(frame.iloc[index]["close_time_dt"])
                if not (start <= signal_time.date() <= end):
                    continue
                row = frame.iloc[index]
                branch = str(row[branch_column])
                confirmed = bool(row["trend_long" if side > 0 else "trend_short"])
                volume_ratio = float(row["volume_ratio_long" if side > 0 else "volume_ratio_short"])
                strength = (
                    (1.0 if branch != "low_adx_only" else 0.0)
                    + float(row["body_atr"] if math.isfinite(float(row["body_atr"])) else 0.0)
                    + float(row["macd_atr"] if math.isfinite(float(row["macd_atr"])) else 0.0)
                    + max(0.0, volume_ratio - 1.0) if math.isfinite(volume_ratio) else 0.0
                )
                for path_mode in ("freqtrade_bound_first", "conservative_adverse_first"):
                    path = _simulate(
                        frame,
                        int(index),
                        variant=variant,
                        side=side,
                        path_mode=path_mode,
                    )
                    if path is None:
                        continue
                    records.append(
                        {
                            "event_id": f"{variant}:{symbol}:{side:+d}:{signal_time.isoformat()}",
                            "symbol": symbol,
                            "period_label": period_label,
                            "split": split,
                            "variant": variant,
                            "path_mode": path_mode,
                            "side": side,
                            "signal_time": signal_time,
                            "signal_episode_number": int(episode_number.iloc[index]),
                            "signal_episode_start": bool(episode_start.iloc[index]),
                            "entry_branch": branch,
                            "direction_confirmation": confirmed,
                            "adx": float(row["adx"]),
                            "trend_long": bool(row["trend_long"]),
                            "trend_short": bool(row["trend_short"]),
                            "volume_ratio": volume_ratio,
                            "body_atr": float(row["body_atr"]),
                            "macd_atr": float(row["macd_atr"]),
                            "pre_entry_strength": strength,
                            **path,
                        }
                    )
    return records


def _same_clock(group: pd.DataFrame) -> pd.Series:
    work = group.copy()
    work["branch_priority"] = (~work["entry_branch"].eq("low_adx_only")).astype(int)
    work["symbol_priority"] = work["symbol"].map(SYMBOL_PRIORITY).fillna(99).astype(int)
    return work.sort_values(
        ["branch_priority", "pre_entry_strength", "symbol_priority", "side", "event_id"],
        ascending=[False, False, True, False, True],
        kind="stable",
    ).iloc[0]


def _one_slot(frame: pd.DataFrame, episode_mode: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    work = frame.copy()
    if episode_mode == "continuous_signal_episode":
        work = work[work["signal_episode_start"].eq(True)].copy()
    elif episode_mode != "source_reentry":
        raise ValueError(episode_mode)
    if work.empty:
        return work.copy(), work.copy()
    same_clock = pd.DataFrame(
        [_same_clock(group) for _, group in work.groupby("entry_time", sort=True)]
    ).sort_values(["entry_time", "event_id"], kind="stable")
    selected: list[int] = []
    rejected: list[dict[str, Any]] = []
    occupied_until: pd.Timestamp | None = None
    for index, row in same_clock.iterrows():
        entry = pd.Timestamp(row["entry_time"])
        if occupied_until is not None and entry < occupied_until:
            rejected.append({**row.to_dict(), "rejection_reason": "GLOBAL_SLOT_OCCUPIED"})
            continue
        selected.append(index)
        occupied_until = pd.Timestamp(row["exit_time"])
    return same_clock.loc[selected].copy(), pd.DataFrame(rejected)


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
    return {str(key): _summary(group["r_multiple"]) for key, group in frame.groupby(column, dropna=False, sort=True)}


def _nav(frame: pd.DataFrame, calendar_days: int) -> dict[str, Any]:
    multiples = pd.to_numeric(frame["r_multiple"], errors="coerce").dropna().to_numpy(float)
    if multiples.size == 0:
        return {"trades": 0}
    returns = 0.03 * multiples
    if np.any(1.0 + returns <= 0.0):
        return {"trades": int(len(returns)), "account_destroyed": True}
    nav = np.concatenate(([1.0], np.cumprod(1.0 + returns)))
    peak = np.maximum.accumulate(nav)
    drawdown = nav / peak - 1.0
    return {
        "trades": int(len(returns)),
        "final_nav_multiple": float(nav[-1]),
        "daily_geometric_growth_over_sampled_days": float(nav[-1] ** (1.0 / max(calendar_days, 1)) - 1.0),
        "max_drawdown": float(drawdown.min()),
        "note": "Path-level 3% planned-loss compounding, not a continuous NautilusTrader account.",
    }


def run_one(args: argparse.Namespace) -> None:
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    warm_start = start - timedelta(days=14)
    forward_end = end + timedelta(days=15)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    evidence: dict[str, Any] = {}
    diagnostics: dict[str, Any] = {}
    for symbol in SYMBOLS:
        hourly, raw = _load_hourly(symbol=symbol, start=warm_start, end=forward_end, cache=Path(args.cache))
        features = _features(hourly)
        item = _signals(symbol=symbol, frame=features, start=start, end=end, period_label=args.period_label, split=args.split)
        records.extend(item)
        evidence[symbol] = [asdict(value) for value in raw]
        diagnostics[symbol] = {
            "hourly_rows": int(len(features)),
            "executed_long_signal_bars": int(features["executed_long"].sum()),
            "executed_short_signal_bars": int(features["executed_short"].sum()),
            "intended_long_signal_bars": int(features["intended_long"].sum()),
            "intended_short_signal_bars": int(features["intended_short"].sum()),
        }
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "period_label": args.period_label,
        "split": args.split,
        "start": args.start,
        "end": args.end,
        "calendar_days": (end - start).days + 1,
        "source_provenance": {
            "strategy": "syuraj/freq-test RSI_BB_MACD_Nov_2023_1h_2_Dec.py blob b4f205bf221c45f7524a350c14d49d1dfa8d10ce",
            "report": "same repository .log; 2021-01-02..2023-10-27",
            "executed_precedence": "low_adx | (high_adx & direction & volume)",
            "intended_parentheses": "(low_adx | high_adx) & direction & volume",
        },
        "frozen_contract": {
            "timeframe": "1h",
            "leverage_for_source_price_threshold_conversion": LEVERAGE,
            "round_trip_cost": ROUND_TRIP_COST,
            "hard_stop_profit": HARD_STOP_PROFIT,
            "trailing_positive_profit": TRAIL_POSITIVE_PROFIT,
            "trailing_offset_profit": TRAIL_OFFSET_PROFIT,
            "roi_profit": list(ROI_PROFIT),
            "one_global_slot": True,
            "threshold_search": "none",
        },
        "raw_evidence": evidence,
        "signal_diagnostics": diagnostics,
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
    for column in ("signal_time", "entry_time", "exit_time"):
        if column in records.columns:
            records[column] = pd.to_datetime(records[column], utc=True, errors="coerce")
    calendar_days = sum(int(payload["calendar_days"]) for payload in payloads)
    policies: dict[str, Any] = {}
    selected_outputs: list[pd.DataFrame] = []
    rejected_outputs: list[pd.DataFrame] = []
    policy_frames: dict[str, pd.DataFrame] = {}
    for name in POLICY_ORDER:
        variant, episode, path_mode = name.split(":")
        source = records[(records["variant"].eq(variant)) & (records["path_mode"].eq(path_mode))].copy()
        selected, rejected = _one_slot(source, episode)
        selected["policy"] = name
        if not rejected.empty:
            rejected["policy"] = name
        policy_frames[name] = selected
        selected_outputs.append(selected)
        rejected_outputs.append(rejected)
        policies[name] = {
            "raw_signal_candidates": int(len(source)),
            "selected_trades": int(len(selected)),
            "trades_per_day": float(len(selected) / max(calendar_days, 1)),
            "r_multiple": _summary(selected["r_multiple"]),
            "net_return": _summary(selected["net_return"]),
            "diagnostic_nav": _nav(selected, calendar_days),
            "by_split_r": _groups(selected, "split"),
            "by_period_r": _groups(selected, "period_label"),
            "by_symbol_r": _groups(selected, "symbol"),
            "by_side_r": _groups(selected, "side"),
            "by_branch_r": _groups(selected, "entry_branch"),
            "by_exit_reason_r": _groups(selected, "exit_reason"),
            "same_bar_trail_activation_count": int(selected["same_bar_trail_activation"].sum()),
            "rejected_global_slot": int(len(rejected)),
        }

    bug_source = policies["executed_precedence:source_reentry:freqtrade_bound_first"]
    bug_conservative = policies["executed_precedence:source_reentry:conservative_adverse_first"]
    bug_independent = policies["executed_precedence:continuous_signal_episode:conservative_adverse_first"]
    intended_independent = policies["intended_parentheses:continuous_signal_episode:conservative_adverse_first"]
    assessment = {
        "operator_precedence_materially_increases_candidates": (
            bug_source["raw_signal_candidates"]
            > 2 * policies["intended_parentheses:source_reentry:freqtrade_bound_first"]["raw_signal_candidates"]
        ),
        "freqtrade_favorable_bound_order_materially_inflates_expectancy": (
            bug_source["r_multiple"].get("mean", -math.inf)
            > bug_conservative["r_multiple"].get("mean", -math.inf) + 0.05
            and bug_source["same_bar_trail_activation_count"] > 0
        ),
        "executed_precedence_survives_conservative_independent_accounting": (
            bug_independent["r_multiple"].get("mean_without_best", -math.inf) > 0.0
            and bug_independent["r_multiple"].get("profit_factor", 0.0) > 1.0
        ),
        "intended_logic_survives_conservative_independent_accounting": (
            intended_independent["r_multiple"].get("mean_without_best", -math.inf) > 0.0
            and intended_independent["r_multiple"].get("profit_factor", 0.0) > 1.0
        ),
        "independent_opportunity_density_reaches_one_per_day": (
            max(bug_independent["trades_per_day"], intended_independent["trades_per_day"]) >= 1.0
        ),
    }
    result = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_runs": len(payloads),
        "calendar_days": calendar_days,
        "records": int(len(records)),
        "policies": policies,
        "predeclared_assessment": assessment,
        "diagnostic_conclusion": (
            "one_or_more_causal_picasso_components_survive"
            if assessment["executed_precedence_survives_conservative_independent_accounting"]
            or assessment["intended_logic_survives_conservative_independent_accounting"]
            else "public_picasso_edge_does_not_survive_causal_accounting"
        ),
        "next_inference": (
            "Preserve only the variant and exit branches which remain positive under conservative intrabar ordering, one continuous signal episode and one global slot. Diagnose its remaining stop/exit loss states before any NautilusTrader promotion; do not tune the public ADX/RSI/BB/MACD parameters on these periods."
        ),
        "truth_boundary": (
            "This is signal/path anatomy. It does not validate the public leverage report and is not a continuous NautilusTrader account."
        ),
    }
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "ANATOMY.json").write_text(json.dumps(result, indent=2, sort_keys=True, default=_json_default) + "\n")
    records.to_csv(output / "CANDIDATES.csv", index=False)
    pd.concat(selected_outputs, ignore_index=True).to_csv(output / "SELECTED.csv", index=False)
    nonempty = [value for value in rejected_outputs if not value.empty]
    (pd.concat(nonempty, ignore_index=True) if nonempty else pd.DataFrame()).to_csv(output / "REJECTED.csv", index=False)

    lines = [
        "# Picasso RSI/BB/MACD precedence anatomy v64",
        "",
        f"- source periods: {len(payloads)}",
        f"- sampled calendar days: {calendar_days}",
        f"- raw candidate/path records: {len(records)}",
        f"- conclusion: **{result['diagnostic_conclusion']}**",
        "",
        "| policy | raw candidates | trades | trades/day | mean R | median R | PF | ex-best R | daily diagnostic geom | max DD | same-bar trail |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in POLICY_ORDER:
        value = policies[name]
        r = value["r_multiple"]
        nav = value["diagnostic_nav"]
        pf = r.get("profit_factor")
        lines.append(
            f"| {name} | {value['raw_signal_candidates']} | {value['selected_trades']} | {value['trades_per_day']:.3f} | "
            f"{r.get('mean', 0.0):.3f} | {r.get('median', 0.0):.3f} | {'na' if pf is None else f'{pf:.2f}'} | "
            f"{r.get('mean_without_best', 0.0):.3f} | {100 * nav.get('daily_geometric_growth_over_sampled_days', 0.0):.3f}% | "
            f"{100 * nav.get('max_drawdown', 0.0):.2f}% | {value['same_bar_trail_activation_count']} |"
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
    run.add_argument("--cache", default=".cache/candidate-51-picasso-v64")
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
