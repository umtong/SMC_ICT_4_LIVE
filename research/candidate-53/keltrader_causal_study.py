#!/usr/bin/env python3
"""Causal repair/replication of the public Keltrader squeeze system.

External source mined from jicheolha/crypto-trading-bot Git history:
- signal_generator.py before deletion: 99cfa582...
- run_backtest.py before deletion: 00b1ac989...
- technical.py remains public.

The published backtest has two validity defects which this study deliberately
repairs instead of copying:
1) pandas resample labels 4h/1h bars at interval *start*, while the external
   signal code accepts any indexed bar <= current minute.  A historical full
   08:00-12:00 bar can therefore be consumed at 08:01.  We instead stamp each
   aggregate at its actual completion and only trade strictly afterwards.
2) after a trade enters, the external generator deletes its active setup and
   can rediscover the same latest 4h release every following minute.  We make
   each 4h release a final causal event and allow at most one entry from it.

Everything else which matters to the trade geometry is copied from the recovered
configuration: signal=4h, ATR=1h, BB(19,2.47), KC(17,2.38), momentum=15,
RSI=21, long RSI<=68, short RSI>=25, squeeze>=2, volume ratio>=1.02,
ATR period=16, stop=3.45 ATR, target=4.0 ATR, max hold=7 days.

This is a mechanism/trade-geometry study only.  It does not create an account,
portfolio, leverage or risk sizing.  One-minute stop-before-target path ordering
and Candidate53's 21 bp round-trip hurdle are used to decide whether the repaired
external family deserves promotion into NautilusTrader.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import date
import json
import math
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from v9_liquidation_event_study import Archive, StudyError, download_verified, read_kline

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
COST_RATE = 0.0021
SIGNAL_HOURS = 4
ATR_HOURS = 1
BB_PERIOD = 19
BB_STD = 2.47
KC_PERIOD = 17
KC_ATR_MULT = 2.38
MOMENTUM_PERIOD = 15
RSI_PERIOD = 21
RSI_OVERBOUGHT = 68.0
RSI_OVERSOLD = 25.0
MIN_SQUEEZE_BARS = 2
VOLUME_PERIOD = 45
MIN_VOLUME_RATIO = 1.02
ATR_PERIOD = 16
ATR_STOP_MULT = 3.45
ATR_TARGET_MULT = 4.0
MAX_HOLD_MINUTES = 7 * 24 * 60
GLOBAL_CLUSTER_MINUTES = 3


@dataclass(frozen=True, slots=True)
class Setup:
    symbol: str
    event_ts: pd.Timestamp
    entry_ts: pd.Timestamp
    direction: int
    entry: float
    stop: float
    target: float
    atr_1h: float
    squeeze_bars: int
    volume_ratio: float
    momentum_norm: float
    rsi: float
    score: float
    planned_loss_rate: float
    target_net_r: float


@dataclass(frozen=True, slots=True)
class Scored:
    setup: Setup
    exit_ts: pd.Timestamp
    exit_price: float
    exit_reason: str
    gross_return: float
    net_return: float
    net_r: float
    mfe: float
    mae: float


def month_labels(year: int) -> list[str]:
    return [f"{year}-{month:02d}" for month in range(1, 13)]


def load_symbol(symbol: str, year: int, cache: Path) -> pd.DataFrame:
    # Include Dec of prior year for higher-timeframe warmup.
    labels = [f"{year - 1}-12", *month_labels(year)]
    keyed: dict[str, pd.DataFrame] = {}
    def fetch(label: str):
        path = download_verified(Archive("um", "monthly", "klines", symbol, label, "1m"), cache / symbol)
        frame = read_kline(path, prefix="perp")
        return label, frame
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(fetch, label) for label in labels]
        for future in as_completed(futures):
            label, frame = future.result()
            keyed[label] = frame
    panel = pd.concat([keyed[label] for label in labels], ignore_index=True)
    panel["minute"] = pd.to_datetime(panel["minute"], utc=True, errors="raise")
    panel = panel.sort_values("minute", kind="stable").drop_duplicates("minute", keep="last")
    if panel["minute"].duplicated().any() or not panel["minute"].is_monotonic_increasing:
        raise StudyError(f"invalid minute clock: {symbol}")
    return panel.set_index("minute", drop=False)


def aggregate(panel: pd.DataFrame, hours: int) -> pd.DataFrame:
    # Input minute index denotes the minute open.  label=left creates intervals
    # [t,t+hours); we explicitly move the observation index to the *last minute*
    # of the fully completed interval so no future bar can be consumed early.
    grouped = panel.resample(f"{hours}h", label="left", closed="left")
    out = pd.DataFrame({
        "open": grouped["perp_open"].first(),
        "high": grouped["perp_high"].max(),
        "low": grouped["perp_low"].min(),
        "close": grouped["perp_close"].last(),
        "volume": grouped["perp_quote_volume"].sum(),
        "count": grouped["perp_close"].count(),
    })
    expected = hours * 60
    out = out[out["count"].eq(expected)].copy()
    out.index = out.index + pd.Timedelta(minutes=expected - 1)
    out["completed_ts"] = out.index
    return out


def indicators(frame: pd.DataFrame) -> pd.DataFrame:
    df = frame.copy()
    df["bb_mid"] = df["close"].rolling(BB_PERIOD).mean()
    df["bb_std"] = df["close"].rolling(BB_PERIOD).std()
    df["bb_upper"] = df["bb_mid"] + BB_STD * df["bb_std"]
    df["bb_lower"] = df["bb_mid"] - BB_STD * df["bb_std"]
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.rolling(ATR_PERIOD).mean()
    df["kc_mid"] = df["close"].ewm(span=KC_PERIOD, adjust=False).mean()
    df["kc_upper"] = df["kc_mid"] + KC_ATR_MULT * df["atr"]
    df["kc_lower"] = df["kc_mid"] - KC_ATR_MULT * df["atr"]
    df["squeeze"] = df["bb_lower"].gt(df["kc_lower"]) & df["bb_upper"].lt(df["kc_upper"])
    df["momentum"] = df["close"] - df["bb_mid"].shift(MOMENTUM_PERIOD)
    df["momentum_norm"] = df["momentum"] / df["atr"].replace(0.0, np.nan)
    delta = df["close"].diff()
    gain = delta.where(delta > 0.0, 0.0).rolling(RSI_PERIOD).mean()
    loss = (-delta.where(delta < 0.0, 0.0)).rolling(RSI_PERIOD).mean()
    rs = gain / loss.replace(0.0, np.nan)
    df["rsi"] = 100.0 - 100.0 / (1.0 + rs)
    df["volume_ma"] = df["volume"].rolling(VOLUME_PERIOD).mean()
    df["volume_ratio"] = df["volume"] / df["volume_ma"].replace(0.0, np.nan)
    blocks = (~df["squeeze"]).cumsum()
    df["squeeze_duration"] = df["squeeze"].groupby(blocks).cumsum().astype(int)
    return df


def score_value(squeeze_bars: int, volume_ratio: float, momentum_norm: float) -> float:
    value = 0.5
    value += min(squeeze_bars / 20.0, 0.2)
    value += min(volume_ratio / 3.0, 0.15)
    value += min(abs(momentum_norm) / 2.0, 0.15)
    return min(value, 1.0)


def detect(symbol: str, panel: pd.DataFrame, year: int) -> list[Setup]:
    sig = indicators(aggregate(panel, SIGNAL_HOURS))
    atr = indicators(aggregate(panel, ATR_HOURS))
    core_start = pd.Timestamp(f"{year}-01-01", tz="UTC")
    core_end = pd.Timestamp(f"{year + 1}-01-01", tz="UTC")
    setups: list[Setup] = []

    for i in range(1, len(sig)):
        current = sig.iloc[i]
        previous = sig.iloc[i - 1]
        event_ts = pd.Timestamp(current["completed_ts"])
        if event_ts < core_start or event_ts >= core_end:
            continue
        if not bool(previous["squeeze"]) or bool(current["squeeze"]):
            continue
        squeeze_bars = int(previous["squeeze_duration"])
        volume_ratio = float(current["volume_ratio"])
        momentum = float(current["momentum"])
        momentum_norm = float(current["momentum_norm"])
        rsi = float(current["rsi"])
        close = float(current["close"])
        if squeeze_bars < MIN_SQUEEZE_BARS:
            continue
        if not all(math.isfinite(v) for v in (volume_ratio, momentum, momentum_norm, rsi, close)):
            continue
        if volume_ratio < MIN_VOLUME_RATIO:
            continue
        if close > float(current["bb_upper"]):
            side = 1
        elif close < float(current["bb_lower"]):
            side = -1
        elif momentum > 0.0:
            side = 1
        elif momentum < 0.0:
            side = -1
        else:
            continue
        if side > 0 and rsi > RSI_OVERBOUGHT:
            continue
        if side < 0 and rsi < RSI_OVERSOLD:
            continue

        # Strictly next minute after a completed 4h observation.  The original
        # code enters at whatever 1m close happens to inspect the already-visible
        # 4h setup; causal repair uses exactly one entry opportunity per event.
        entry_minute = event_ts + pd.Timedelta(minutes=1)
        if entry_minute not in panel.index:
            continue
        entry_row = panel.loc[entry_minute]
        entry = float(entry_row["perp_open"])
        # ATR must also be a completed 1h value known by event time.
        known_atr = atr[atr.index <= event_ts]
        if known_atr.empty:
            continue
        atr_value = float(known_atr.iloc[-1]["atr"])
        if not (math.isfinite(entry) and entry > 0.0 and math.isfinite(atr_value) and atr_value > 0.0):
            continue
        stop = entry - side * ATR_STOP_MULT * atr_value
        target = entry + side * ATR_TARGET_MULT * atr_value
        if not (stop > 0.0 and target > 0.0):
            continue
        risk = abs(entry - stop) / entry
        reward = abs(target - entry) / entry
        planned_loss = risk + COST_RATE
        target_net_r = (reward - COST_RATE) / planned_loss
        setups.append(Setup(
            symbol=symbol,
            event_ts=event_ts,
            entry_ts=entry_minute,
            direction=side,
            entry=entry,
            stop=stop,
            target=target,
            atr_1h=atr_value,
            squeeze_bars=squeeze_bars,
            volume_ratio=volume_ratio,
            momentum_norm=momentum_norm,
            rsi=rsi,
            score=score_value(squeeze_bars, volume_ratio, momentum_norm),
            planned_loss_rate=planned_loss,
            target_net_r=target_net_r,
        ))
    return setups


def score(setup: Setup, panel: pd.DataFrame) -> Scored:
    start_i = int(panel.index.get_indexer([setup.entry_ts])[0])
    last_i = min(start_i + MAX_HOLD_MINUTES, len(panel) - 1)
    exit_ts = setup.entry_ts
    exit_price = setup.entry
    reason = "TIME"
    mfe = 0.0
    mae = 0.0
    for i in range(start_i, last_i + 1):
        row = panel.iloc[i]
        high = float(row["perp_high"])
        low = float(row["perp_low"])
        close = float(row["perp_close"])
        if setup.direction > 0:
            mfe = max(mfe, high / setup.entry - 1.0)
            mae = min(mae, low / setup.entry - 1.0)
            if low <= setup.stop:
                exit_ts, exit_price, reason = pd.Timestamp(row["minute"]), setup.stop, "STOP"
                break
            if high >= setup.target:
                exit_ts, exit_price, reason = pd.Timestamp(row["minute"]), setup.target, "TARGET"
                break
        else:
            mfe = max(mfe, setup.entry / low - 1.0)
            mae = min(mae, setup.entry / high - 1.0)
            if high >= setup.stop:
                exit_ts, exit_price, reason = pd.Timestamp(row["minute"]), setup.stop, "STOP"
                break
            if low <= setup.target:
                exit_ts, exit_price, reason = pd.Timestamp(row["minute"]), setup.target, "TARGET"
                break
        exit_ts, exit_price = pd.Timestamp(row["minute"]), close
    gross = setup.direction * (exit_price / setup.entry - 1.0)
    net = gross - COST_RATE
    return Scored(setup, exit_ts, exit_price, reason, gross, net, net / setup.planned_loss_rate, mfe, mae)


def global_single_position(scored: Iterable[Scored]) -> list[Scored]:
    # For the mechanism study, choose the highest external score among entries
    # which arrive in the same small causal cluster, then block until that trade
    # actually exits.  This mirrors the project's one-position universe without
    # inventing another account engine.
    ordered = sorted(scored, key=lambda x: (x.setup.entry_ts, -x.setup.score, x.setup.symbol))
    selected: list[Scored] = []
    free_at: pd.Timestamp | None = None
    i = 0
    while i < len(ordered):
        candidate = ordered[i]
        if free_at is not None and candidate.setup.entry_ts <= free_at:
            i += 1
            continue
        cluster = [candidate]
        j = i + 1
        while j < len(ordered) and ordered[j].setup.entry_ts - candidate.setup.entry_ts <= pd.Timedelta(minutes=GLOBAL_CLUSTER_MINUTES):
            cluster.append(ordered[j]); j += 1
        chosen = max(cluster, key=lambda x: (x.setup.score, -x.setup.entry_ts.value))
        selected.append(chosen)
        free_at = chosen.exit_ts
        i = j
    return selected


def stats(scored: list[Scored], calendar_days: int) -> dict[str, object]:
    if not scored:
        return {"trades": 0, "wins": 0, "win_rate": 0.0, "mean_net_r": 0.0, "median_net_r": 0.0,
                "profit_factor_r": 0.0, "mean_net_return": 0.0, "target_rate": 0.0, "stop_rate": 0.0,
                "trades_per_calendar_day": 0.0}
    rs = np.asarray([item.net_r for item in scored], dtype=float)
    gains = rs[rs > 0].sum(); losses = -rs[rs < 0].sum()
    return {
        "trades": len(scored),
        "wins": int((rs > 0).sum()),
        "win_rate": float((rs > 0).mean()),
        "mean_net_r": float(rs.mean()),
        "median_net_r": float(np.median(rs)),
        "profit_factor_r": float(gains / losses) if losses > 0 else 999999.0,
        "mean_net_return": float(np.mean([item.net_return for item in scored])),
        "target_rate": sum(item.exit_reason == "TARGET" for item in scored) / len(scored),
        "stop_rate": sum(item.exit_reason == "STOP" for item in scored) / len(scored),
        "trades_per_calendar_day": len(scored) / calendar_days,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    panels: dict[str, pd.DataFrame] = {}
    all_scored: list[Scored] = []
    per_symbol: dict[str, object] = {}
    for symbol in SYMBOLS:
        panel = load_symbol(symbol, args.year, args.cache)
        panels[symbol] = panel
        setups = detect(symbol, panel, args.year)
        scored = [score(item, panel) for item in setups]
        all_scored.extend(scored)
        per_symbol[symbol] = stats(scored, 365 + int(pd.Timestamp(f"{args.year}-12-31").is_leap_year))
    global_scored = global_single_position(all_scored)
    calendar_days = 366 if pd.Timestamp(f"{args.year}-01-01").is_leap_year else 365
    result = {
        "external_source": "jicheolha/crypto-trading-bot pre-redaction Git history",
        "validity_repairs": ["completed_4h_and_1h_timestamps", "one_entry_per_4h_release_event"],
        "year": args.year,
        "round_trip_cost_rate": COST_RATE,
        "fixed_params": {
            "signal_timeframe": "4h", "atr_timeframe": "1h", "bb_period": BB_PERIOD, "bb_std": BB_STD,
            "kc_period": KC_PERIOD, "kc_atr_mult": KC_ATR_MULT, "momentum_period": MOMENTUM_PERIOD,
            "rsi_period": RSI_PERIOD, "rsi_overbought": RSI_OVERBOUGHT, "rsi_oversold": RSI_OVERSOLD,
            "min_squeeze_bars": MIN_SQUEEZE_BARS, "volume_period": VOLUME_PERIOD,
            "min_volume_ratio": MIN_VOLUME_RATIO, "atr_period": ATR_PERIOD,
            "atr_stop_mult": ATR_STOP_MULT, "atr_target_mult": ATR_TARGET_MULT,
        },
        "per_symbol": per_symbol,
        "global_single_position": stats(global_scored, calendar_days),
    }
    rows = []
    for item in global_scored:
        record = asdict(item.setup)
        record.update({
            "exit_ts": item.exit_ts, "exit_price": item.exit_price, "exit_reason": item.exit_reason,
            "gross_return": item.gross_return, "net_return": item.net_return, "net_r": item.net_r,
            "mfe": item.mfe, "mae": item.mae,
        })
        rows.append(record)
    pd.DataFrame(rows).to_csv(args.output / "global_trades.csv", index=False)
    (args.output / "summary.json").write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
