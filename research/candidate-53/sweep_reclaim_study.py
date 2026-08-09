#!/usr/bin/env python3
"""Candidate 53 failed-auction sweep/reclaim mechanism study.

This is a cheap causal screen, not a backtest engine. It reuses the project's
checksum-verified Binance Vision downloader and one-minute USD-M klines. A
candidate is a complete failed auction rather than a wick pattern:

past 60m/240m external extreme -> meaningful breach on exceptional volume with
aggressor flow in the breach direction -> price fails to hold the new auction
and re-enters the old range -> strictly later reversal bar confirms rejection ->
entry at the confirmation close -> invalidation beyond the sweep extreme ->
objective at the pre-event range midpoint (balance/value).

All thresholds are trailing and shifted.  Outcome paths are descriptive only,
with conservative stop-before-target ordering and Candidate 53's current 21 bp
round-trip fee+slippage budget.  No fills, account, portfolio or NAV are made;
NautilusTrader remains the only execution/accounting engine if this mechanism
passes and is promoted.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date, timedelta
import json
import math
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from v9_liquidation_event_study import Archive, StudyError, download_verified, read_kline

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
COST_RATE = 0.0021
ATR_PERIOD = 30
VOLUME_WINDOW = 240
VOLUME_QUANTILE = 0.90
MIN_HISTORY = 240
RECLAIM_BARS = 2
CONFIRM_BARS = 3
MAX_HOLD_MINUTES = 180
MIN_OBJECTIVE_NET_R = 1.50
GLOBAL_CLUSTER_MINUTES = 3
SYMBOL_DECLUSTER_MINUTES = 45


@dataclass(frozen=True, slots=True)
class Candidate:
    symbol: str
    variant: str
    side: int
    range_minutes: int
    sweep_ts: pd.Timestamp
    reclaim_ts: pd.Timestamp
    entry_ts: pd.Timestamp
    entry: float
    stop: float
    target: float
    prior_high: float
    prior_low: float
    prior_mid: float
    atr: float
    sweep_breach_atr: float
    sweep_volume_quantile_threshold: float
    sweep_volume_ratio_to_threshold: float
    sweep_flow: float
    sweep_close_location: float
    planned_loss_rate: float
    objective_net_r: float
    score: float


@dataclass(frozen=True, slots=True)
class Scored:
    candidate: Candidate
    exit_ts: pd.Timestamp
    exit_reason: str
    exit_price: float
    gross_return: float
    net_return: float
    net_r: float
    mfe: float
    mae: float


def date_labels(start: date, end: date) -> list[str]:
    return [d.strftime("%Y-%m-%d") for d in pd.date_range(start, end, freq="D")]


def load_symbol(symbol: str, start: date, end: date, cache: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for label in date_labels(start, end):
        path = download_verified(
            Archive("um", "daily", "klines", symbol, label, "1m"),
            cache / symbol,
        )
        frames.append(read_kline(path, prefix="perp"))
    panel = pd.concat(frames, ignore_index=True)
    panel["minute"] = pd.to_datetime(panel["minute"], utc=True, errors="raise")
    panel = panel.sort_values("minute", kind="stable").drop_duplicates("minute", keep="last")
    if panel["minute"].duplicated().any() or not panel["minute"].is_monotonic_increasing:
        raise StudyError(f"invalid minute clock for {symbol}")
    panel = panel.set_index("minute", drop=False)
    previous_close = panel["perp_close"].shift(1)
    tr = pd.concat(
        [
            panel["perp_high"] - panel["perp_low"],
            (panel["perp_high"] - previous_close).abs(),
            (panel["perp_low"] - previous_close).abs(),
        ], axis=1,
    ).max(axis=1)
    panel["atr"] = tr.rolling(ATR_PERIOD, min_periods=ATR_PERIOD).mean().shift(1)
    panel["volume_threshold"] = (
        panel["perp_quote_volume"]
        .rolling(VOLUME_WINDOW, min_periods=VOLUME_WINDOW)
        .quantile(VOLUME_QUANTILE)
        .shift(1)
    )
    panel["flow"] = (
        2.0 * panel["perp_taker_buy_quote"]
        / panel["perp_quote_volume"].replace(0.0, np.nan) - 1.0
    )
    for minutes in (60, 240):
        panel[f"prior_high_{minutes}"] = panel["perp_high"].rolling(minutes, min_periods=minutes).max().shift(1)
        panel[f"prior_low_{minutes}"] = panel["perp_low"].rolling(minutes, min_periods=minutes).min().shift(1)
    return panel


def _objective_net_r(side: int, entry: float, stop: float, target: float) -> tuple[float, float]:
    risk = side * (entry - stop) / entry
    reward = side * (target - entry) / entry
    if not (risk > 0.0 and reward > 0.0):
        return math.nan, math.nan
    net_reward = reward - COST_RATE
    planned_loss = risk + COST_RATE
    return planned_loss, net_reward / planned_loss


def _close_location(row: pd.Series) -> float:
    high, low, close = map(float, (row["perp_high"], row["perp_low"], row["perp_close"]))
    return (close - low) / max(high - low, 1e-12)


def _candidate_for_event(
    symbol: str,
    panel: pd.DataFrame,
    i: int,
    range_minutes: int,
    side: int,
) -> Candidate | None:
    sweep = panel.iloc[i]
    prior_high = float(sweep[f"prior_high_{range_minutes}"])
    prior_low = float(sweep[f"prior_low_{range_minutes}"])
    atr = float(sweep["atr"])
    volume_threshold = float(sweep["volume_threshold"])
    volume = float(sweep["perp_quote_volume"])
    flow = float(sweep["flow"])
    if not all(math.isfinite(x) for x in (prior_high, prior_low, atr, volume_threshold, volume, flow)):
        return None
    if not (prior_low > 0.0 and prior_high > prior_low and atr > 0.0 and volume_threshold > 0.0):
        return None
    if volume < volume_threshold or side * flow <= 0.0:
        return None

    high = float(sweep["perp_high"])
    low = float(sweep["perp_low"])
    if side < 0:
        breach = high - prior_high
        if breach <= 0.5 * atr:
            return None
        swept_level = prior_high
    else:
        breach = prior_low - low
        if breach <= 0.5 * atr:
            return None
        swept_level = prior_low

    # Reclaim may be on the sweep bar or one of the next two completed bars.
    reclaim_i: int | None = None
    for j in range(i, min(i + RECLAIM_BARS + 1, len(panel))):
        row = panel.iloc[j]
        close = float(row["perp_close"])
        if (side < 0 and close < prior_high) or (side > 0 and close > prior_low):
            reclaim_i = j
            break
    if reclaim_i is None:
        return None

    reclaim = panel.iloc[reclaim_i]
    # Strictly later confirmation: reversal body plus break of reclaim bar's
    # opposite extreme. This prevents the reclaim observation from confirming itself.
    confirm_i: int | None = None
    for j in range(reclaim_i + 1, min(reclaim_i + 1 + CONFIRM_BARS, len(panel))):
        row = panel.iloc[j]
        open_ = float(row["perp_open"])
        close = float(row["perp_close"])
        if side < 0:
            confirmed = close < open_ and close < float(reclaim["perp_low"])
        else:
            confirmed = close > open_ and close > float(reclaim["perp_high"])
        if confirmed:
            confirm_i = j
            break
    if confirm_i is None:
        return None

    confirm = panel.iloc[confirm_i]
    entry = float(confirm["perp_close"])
    stop = high if side < 0 else low
    target = (prior_high + prior_low) / 2.0
    planned_loss, objective_net_r = _objective_net_r(side, entry, stop, target)
    if not math.isfinite(objective_net_r) or objective_net_r < MIN_OBJECTIVE_NET_R:
        return None
    close_location = _close_location(sweep)
    # A rejection is stronger when the sweep closes away from the swept edge.
    rejection = (1.0 - close_location) if side < 0 else close_location
    score = (
        breach / atr
        * (volume / volume_threshold)
        * (1.0 + abs(flow))
        * (1.0 + rejection)
        * objective_net_r
    )
    variant = "60M_SWEEP_RECLAIM" if range_minutes == 60 else "240M_SWEEP_RECLAIM"
    return Candidate(
        symbol=symbol,
        variant=variant,
        side=side,
        range_minutes=range_minutes,
        sweep_ts=pd.Timestamp(sweep["minute"]),
        reclaim_ts=pd.Timestamp(reclaim["minute"]),
        entry_ts=pd.Timestamp(confirm["minute"]),
        entry=entry,
        stop=stop,
        target=target,
        prior_high=prior_high,
        prior_low=prior_low,
        prior_mid=target,
        atr=atr,
        sweep_breach_atr=breach / atr,
        sweep_volume_quantile_threshold=volume_threshold,
        sweep_volume_ratio_to_threshold=volume / volume_threshold,
        sweep_flow=flow,
        sweep_close_location=close_location,
        planned_loss_rate=planned_loss,
        objective_net_r=objective_net_r,
        score=score,
    )


def detect(symbol: str, panel: pd.DataFrame) -> list[Candidate]:
    result: list[Candidate] = []
    last_event_by_side: dict[int, pd.Timestamp] = {}
    for i in range(MIN_HISTORY + 5, len(panel) - CONFIRM_BARS - 1):
        sweep = panel.iloc[i]
        for minutes in (60, 240):
            ph = float(sweep[f"prior_high_{minutes}"])
            pl = float(sweep[f"prior_low_{minutes}"])
            high = float(sweep["perp_high"])
            low = float(sweep["perp_low"])
            directions = []
            if math.isfinite(ph) and high > ph:
                directions.append(-1)
            if math.isfinite(pl) and low < pl:
                directions.append(1)
            for side in directions:
                candidate = _candidate_for_event(symbol, panel, i, minutes, side)
                if candidate is None:
                    continue
                previous = last_event_by_side.get(side)
                if previous is not None and candidate.sweep_ts - previous < pd.Timedelta(minutes=SYMBOL_DECLUSTER_MINUTES):
                    continue
                last_event_by_side[side] = candidate.sweep_ts
                result.append(candidate)
    return result


def global_arbitrate(candidates: Iterable[Candidate]) -> list[Candidate]:
    ordered = sorted(candidates, key=lambda c: (c.entry_ts, -c.score, c.symbol, c.variant))
    result: list[Candidate] = []
    bucket: list[Candidate] = []
    anchor: pd.Timestamp | None = None
    for candidate in ordered:
        if anchor is None or candidate.entry_ts - anchor <= pd.Timedelta(minutes=GLOBAL_CLUSTER_MINUTES):
            if anchor is None:
                anchor = candidate.entry_ts
            bucket.append(candidate)
            continue
        result.append(max(bucket, key=lambda c: c.score))
        bucket = [candidate]
        anchor = candidate.entry_ts
    if bucket:
        result.append(max(bucket, key=lambda c: c.score))
    return result


def score(candidate: Candidate, panel: pd.DataFrame) -> Scored:
    start_i = int(panel.index.get_indexer([candidate.entry_ts])[0]) + 1
    last_i = min(start_i + MAX_HOLD_MINUTES, len(panel))
    exit_ts = candidate.entry_ts
    exit_price = candidate.entry
    exit_reason = "TIME"
    mfe = 0.0
    mae = 0.0
    for i in range(start_i, last_i):
        row = panel.iloc[i]
        high, low, close = map(float, (row["perp_high"], row["perp_low"], row["perp_close"]))
        if candidate.side > 0:
            mfe = max(mfe, high / candidate.entry - 1.0)
            mae = min(mae, low / candidate.entry - 1.0)
            if low <= candidate.stop:
                exit_ts, exit_price, exit_reason = pd.Timestamp(row["minute"]), candidate.stop, "STOP"
                break
            if high >= candidate.target:
                exit_ts, exit_price, exit_reason = pd.Timestamp(row["minute"]), candidate.target, "TARGET"
                break
        else:
            mfe = max(mfe, candidate.entry / low - 1.0)
            mae = min(mae, candidate.entry / high - 1.0)
            if high >= candidate.stop:
                exit_ts, exit_price, exit_reason = pd.Timestamp(row["minute"]), candidate.stop, "STOP"
                break
            if low <= candidate.target:
                exit_ts, exit_price, exit_reason = pd.Timestamp(row["minute"]), candidate.target, "TARGET"
                break
        exit_ts, exit_price = pd.Timestamp(row["minute"]), close
    gross_return = candidate.side * (exit_price / candidate.entry - 1.0)
    net_return = gross_return - COST_RATE
    net_r = net_return / candidate.planned_loss_rate
    return Scored(candidate, exit_ts, exit_reason, exit_price, gross_return, net_return, net_r, mfe, mae)


def summarize(scored: list[Scored]) -> dict[str, object]:
    if not scored:
        return {"trades": 0, "wins": 0, "win_rate": 0.0, "mean_net_r": 0.0, "median_net_r": 0.0,
                "profit_factor_r": 0.0, "target_rate": 0.0, "stop_rate": 0.0, "trades_per_day": 0.0}
    rs = np.asarray([s.net_r for s in scored], dtype=float)
    gains = rs[rs > 0.0].sum()
    losses = -rs[rs < 0.0].sum()
    days = max(1, len({s.candidate.entry_ts.date() for s in scored}))
    return {
        "trades": len(scored),
        "wins": int((rs > 0.0).sum()),
        "win_rate": float((rs > 0.0).mean()),
        "mean_net_r": float(rs.mean()),
        "median_net_r": float(np.median(rs)),
        "profit_factor_r": float(gains / losses) if losses > 0.0 else math.inf,
        "target_rate": sum(s.exit_reason == "TARGET" for s in scored) / len(scored),
        "stop_rate": sum(s.exit_reason == "STOP" for s in scored) / len(scored),
        "trades_per_day": len(scored) / days,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    warm_start = start - timedelta(days=2)
    panels = {symbol: load_symbol(symbol, warm_start, end, args.cache) for symbol in SYMBOLS}
    all_candidates: list[Candidate] = []
    for symbol, panel in panels.items():
        all_candidates.extend(c for c in detect(symbol, panel) if c.entry_ts.date() >= start)
    selected = global_arbitrate(all_candidates)

    # One global active slot: skip any later candidate whose entry occurs before
    # the selected previous diagnostic path has exited.
    accepted: list[Scored] = []
    occupied_until: pd.Timestamp | None = None
    for candidate in selected:
        if occupied_until is not None and candidate.entry_ts <= occupied_until:
            continue
        scored = score(candidate, panels[candidate.symbol])
        accepted.append(scored)
        occupied_until = scored.exit_ts

    args.output.mkdir(parents=True, exist_ok=True)
    payload = [
        {**asdict(item.candidate), **{k: v for k, v in asdict(item).items() if k != "candidate"}}
        for item in accepted
    ]
    def safe(value):
        if isinstance(value, pd.Timestamp): return value.isoformat()
        if isinstance(value, (np.integer,)): return int(value)
        if isinstance(value, (np.floating,)): return float(value)
        raise TypeError(type(value).__name__)
    summary = summarize(accepted)
    by_variant = {}
    for variant in ("60M_SWEEP_RECLAIM", "240M_SWEEP_RECLAIM"):
        by_variant[variant] = summarize([s for s in accepted if s.candidate.variant == variant])
    result = {
        "study": "candidate-53-failed-auction-sweep-reclaim",
        "start": args.start,
        "end": args.end,
        "cost_rate": COST_RATE,
        "all_detected_candidates": len(all_candidates),
        "global_arbitrated_candidates": len(selected),
        "single_slot_scored": len(accepted),
        "summary": summary,
        "by_variant": by_variant,
    }
    (args.output / "summary.json").write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    (args.output / "trades.json").write_text(json.dumps(payload, indent=2, sort_keys=True, default=safe, allow_nan=False) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
