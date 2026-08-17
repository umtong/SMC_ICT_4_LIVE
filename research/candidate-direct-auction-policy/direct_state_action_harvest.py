"""Causal direct state-action research for liquid crypto day trading.

This module deliberately does not start from legacy chart-pattern plans.  At each
completed five-minute decision clock it describes the auction with only information
available at that time, enumerates natural long/short stop/target actions, enters at
the next one-minute open, and labels the immutable plan by conservative first passage.
The same normalized state and action logic is used for every instrument.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable
import json
import math

import numpy as np
import pandas as pd


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
TICK_SIZE = {
    "BTCUSDT": 0.1,
    "ETHUSDT": 0.01,
    "SOLUSDT": 0.001,
    "XRPUSDT": 0.0001,
}
TAKER_FEE = 0.0005
MAKER_FEE = 0.0002
ENTRY_SLIPPAGE_TICKS = 2
STOP_SLIPPAGE_TICKS = 2
MAX_HOLD_MINUTES = 720
EPS = 1e-12


@dataclass(frozen=True)
class HarvestConfig:
    start: date
    end: date
    load_start: date
    symbols: tuple[str, ...]
    cache: Path
    output: Path
    decision_minutes: int = 5


def _safe_div(num: pd.Series, den: pd.Series) -> pd.Series:
    return num / den.replace(0.0, np.nan)


def _rolling_mad(series: pd.Series, window: int) -> pd.Series:
    median = series.rolling(window, min_periods=max(8, window // 3)).median()
    return (series - median).abs().rolling(window, min_periods=max(8, window // 3)).median()


def _rolling_efficiency(ret: pd.Series, window: int) -> pd.Series:
    return _safe_div(ret.rolling(window, min_periods=window).sum().abs(), ret.abs().rolling(window, min_periods=window).sum())


def _rolling_turn_rate(ret: pd.Series, window: int) -> pd.Series:
    signs = np.sign(ret)
    turns = signs.ne(signs.shift(1)).astype(float)
    return turns.rolling(window, min_periods=window).mean()


def _confirmed_pivot_state(
    bars: pd.DataFrame,
    span: int,
    prefix: str,
) -> pd.DataFrame:
    """Return last pivot high/low known at each completed bar.

    A center pivot at j becomes observable only when bar j+span is complete.
    Equality ties are resolved by requiring the center to be the first occurrence,
    avoiding duplicate level identities without future information at observation.
    """
    high = bars["high"].to_numpy(float)
    low = bars["low"].to_numpy(float)
    n = len(bars)
    hi_updates = np.full(n, np.nan)
    lo_updates = np.full(n, np.nan)
    hi_center = np.full(n, np.nan)
    lo_center = np.full(n, np.nan)
    for j in range(span, n - span):
        hs = high[j - span : j + span + 1]
        ls = low[j - span : j + span + 1]
        if np.isfinite(high[j]) and high[j] == np.nanmax(hs) and int(np.nanargmax(hs)) == span:
            k = j + span
            hi_updates[k] = high[j]
            hi_center[k] = j
        if np.isfinite(low[j]) and low[j] == np.nanmin(ls) and int(np.nanargmin(ls)) == span:
            k = j + span
            lo_updates[k] = low[j]
            lo_center[k] = j
    out = pd.DataFrame(index=bars.index)
    out[f"{prefix}_pivot_high"] = pd.Series(hi_updates, index=bars.index).ffill()
    out[f"{prefix}_pivot_low"] = pd.Series(lo_updates, index=bars.index).ffill()
    hi_center_s = pd.Series(hi_center, index=bars.index).ffill()
    lo_center_s = pd.Series(lo_center, index=bars.index).ffill()
    positions = pd.Series(np.arange(n, dtype=float), index=bars.index)
    out[f"{prefix}_pivot_high_age_bars"] = positions - hi_center_s
    out[f"{prefix}_pivot_low_age_bars"] = positions - lo_center_s
    return out


def _resample_ohlc(frame: pd.DataFrame, minutes: int) -> pd.DataFrame:
    rule = f"{minutes}min"
    return frame[["open", "high", "low", "close", "quote_volume", "count", "taker_buy_quote_volume"]].resample(
        rule, label="right", closed="left"
    ).agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "quote_volume": "sum",
            "count": "sum",
            "taker_buy_quote_volume": "sum",
        }
    ).dropna(subset=["open", "high", "low", "close"])


def _prepare_symbol(symbol: str, raw: pd.DataFrame) -> pd.DataFrame:
    frame = raw.copy()
    frame.index = pd.DatetimeIndex(frame.pop("open_time_dt")) + pd.Timedelta(minutes=1)
    frame = frame.sort_index()
    for c in ("open", "high", "low", "close", "volume", "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume"):
        frame[c] = pd.to_numeric(frame[c], errors="coerce")

    close = frame["close"]
    log_close = np.log(close)
    ret1 = log_close.diff()
    frame["ret_1m"] = ret1
    for h in (2, 3, 5, 10, 15, 30, 60, 120, 240):
        frame[f"ret_{h}m"] = log_close - log_close.shift(h)

    prev_close = close.shift(1)
    tr = pd.concat(
        [(frame["high"] - frame["low"]), (frame["high"] - prev_close).abs(), (frame["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    frame["tr_bps"] = _safe_div(tr, close) * 1e4
    frame["body_bps"] = _safe_div((frame["close"] - frame["open"]), close) * 1e4
    candle_range = (frame["high"] - frame["low"]).replace(0.0, np.nan)
    frame["body_fraction"] = (frame["close"] - frame["open"]).abs() / candle_range
    frame["upper_wick_fraction"] = (frame["high"] - frame[["open", "close"]].max(axis=1)) / candle_range
    frame["lower_wick_fraction"] = (frame[["open", "close"]].min(axis=1) - frame["low"]) / candle_range
    frame["close_location"] = (frame["close"] - frame["low"]) / candle_range

    delta_quote = 2.0 * frame["taker_buy_quote_volume"] - frame["quote_volume"]
    frame["delta_share_1m"] = _safe_div(delta_quote, frame["quote_volume"])
    frame["average_trade_quote"] = _safe_div(frame["quote_volume"], frame["count"])
    vwap = _safe_div(frame["quote_volume"], frame["volume"])
    frame["close_vwap_bps"] = _safe_div(frame["close"] - vwap, frame["close"]) * 1e4

    for w in (5, 15, 30, 60, 120, 240):
        minp = max(5, w // 2)
        abs_sum = ret1.abs().rolling(w, min_periods=minp).sum()
        frame[f"rv_{w}m"] = np.sqrt((ret1.pow(2)).rolling(w, min_periods=minp).sum()) * 1e4
        frame[f"path_efficiency_{w}m"] = _rolling_efficiency(ret1, w)
        frame[f"sign_persistence_{w}m"] = np.sign(ret1).rolling(w, min_periods=minp).mean()
        frame[f"turn_rate_{w}m"] = _rolling_turn_rate(ret1, w)
        hi = frame["high"].rolling(w, min_periods=minp).max()
        lo = frame["low"].rolling(w, min_periods=minp).min()
        frame[f"range_{w}m_bps"] = _safe_div(hi - lo, close) * 1e4
        frame[f"range_position_{w}m"] = _safe_div(close - lo, hi - lo)
        frame[f"distance_high_{w}m_bps"] = _safe_div(hi - close, close) * 1e4
        frame[f"distance_low_{w}m_bps"] = _safe_div(close - lo, close) * 1e4
        qsum = frame["quote_volume"].rolling(w, min_periods=minp).sum()
        dsum = delta_quote.rolling(w, min_periods=minp).sum()
        frame[f"delta_share_{w}m"] = _safe_div(dsum, qsum)
        frame[f"flow_price_alignment_{w}m"] = np.sign(frame[f"ret_{w}m"]) * frame[f"delta_share_{w}m"]
        frame[f"flow_impact_{w}m"] = _safe_div(frame[f"ret_{w}m"].abs() * 1e4, frame[f"delta_share_{w}m"].abs() + 0.02)

    for base_col, name in ((frame["quote_volume"], "activity"), (frame["count"], "trade_count"), (frame["average_trade_quote"], "trade_size"), (frame["tr_bps"], "true_range")):
        med60 = base_col.rolling(60, min_periods=30).median().shift(1)
        med240 = base_col.rolling(240, min_periods=120).median().shift(1)
        mad240 = _rolling_mad(base_col, 240).shift(1)
        frame[f"{name}_ratio_60"] = _safe_div(base_col, med60)
        frame[f"{name}_ratio_240"] = _safe_div(base_col, med240)
        frame[f"{name}_robust_z_240"] = _safe_div(base_col - med240, 1.4826 * mad240 + EPS)

    frame["volatility_compression_15v120"] = _safe_div(frame["rv_15m"], frame["rv_120m"] / math.sqrt(8.0))
    frame["range_compression_15v120"] = _safe_div(frame["range_15m_bps"], frame["range_120m_bps"])
    frame["activity_acceleration_5v60"] = _safe_div(
        frame["quote_volume"].rolling(5, min_periods=5).mean(),
        frame["quote_volume"].rolling(60, min_periods=30).mean().shift(1),
    )
    frame["delta_acceleration_5v60"] = frame["delta_share_5m"] - frame["delta_share_60m"]

    p1 = _confirmed_pivot_state(frame, span=2, prefix="p1")
    frame = frame.join(p1)
    for tf, span in ((5, 2), (15, 2), (60, 2)):
        agg = _resample_ohlc(frame, tf)
        piv = _confirmed_pivot_state(agg, span=span, prefix=f"p{tf}")
        frame = pd.merge_asof(
            frame.sort_index(),
            piv.sort_index(),
            left_index=True,
            right_index=True,
            direction="backward",
        )

    # merge_asof preserves the close-time index, but refresh the view explicitly so
    # every post-merge feature is aligned to the final frame rather than relying on
    # an implementation detail of pandas index retention.
    close = frame["close"]
    for tf in (1, 5, 15, 60):
        frame[f"distance_p{tf}_high_bps"] = _safe_div(frame[f"p{tf}_pivot_high"] - close, close) * 1e4
        frame[f"distance_p{tf}_low_bps"] = _safe_div(close - frame[f"p{tf}_pivot_low"], close) * 1e4

    frame["atr_price_60"] = tr.rolling(60, min_periods=30).median()
    frame["rolling_high_60"] = frame["high"].rolling(60, min_periods=30).max()
    frame["rolling_low_60"] = frame["low"].rolling(60, min_periods=30).min()
    frame["rolling_high_240"] = frame["high"].rolling(240, min_periods=120).max()
    frame["rolling_low_240"] = frame["low"].rolling(240, min_periods=120).min()
    frame["next_open"] = frame["open"].shift(-1)
    return frame


def _add_common_state(prepared: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    common_index = None
    for frame in prepared.values():
        common_index = frame.index if common_index is None else common_index.intersection(frame.index)
    if common_index is None or len(common_index) == 0:
        raise RuntimeError("no synchronized common index")
    symbols = tuple(prepared)
    aligned = {s: prepared[s].reindex(common_index).copy() for s in symbols}

    for h in (1, 5, 15, 30, 60, 120):
        matrix = pd.concat({s: aligned[s][f"ret_{h}m"] for s in symbols}, axis=1)
        common = matrix.median(axis=1)
        dispersion = matrix.std(axis=1, ddof=0)
        breadth = np.sign(matrix).mean(axis=1)
        for s in symbols:
            aligned[s][f"common_ret_{h}m"] = common
            aligned[s][f"residual_ret_{h}m"] = matrix[s] - common
            aligned[s][f"cross_dispersion_{h}m"] = dispersion
            aligned[s][f"cross_breadth_{h}m"] = breadth
            aligned[s][f"return_rank_{h}m"] = matrix.rank(axis=1, pct=True)[s]

    delta_matrix = pd.concat({s: aligned[s]["delta_share_5m"] for s in symbols}, axis=1)
    activity_matrix = pd.concat({s: aligned[s]["activity_ratio_60"] for s in symbols}, axis=1)
    common_delta = delta_matrix.median(axis=1)
    for s in symbols:
        aligned[s]["common_delta_share_5m"] = common_delta
        aligned[s]["residual_delta_share_5m"] = delta_matrix[s] - common_delta
        aligned[s]["delta_rank_5m"] = delta_matrix.rank(axis=1, pct=True)[s]
        aligned[s]["activity_rank_1m"] = activity_matrix.rank(axis=1, pct=True)[s]

    # The most liquid instrument is treated as a dynamically reusable market leader.
    rolling_activity = pd.concat(
        {s: aligned[s]["quote_volume"].rolling(60, min_periods=30).median() for s in symbols}, axis=1
    )
    # Warmup rows can be all-NaN across every symbol.  ``idxmax`` raises on
    # that state, and choosing an arbitrary symbol would contaminate the leader
    # features.  Select on a filled view, then explicitly restore NaN wherever
    # no causal rolling-liquidity observation exists yet.
    all_missing_leader = rolling_activity.isna().all(axis=1)
    leader_name = rolling_activity.fillna(-float("inf")).idxmax(axis=1)
    leader_name = leader_name.mask(all_missing_leader)
    for h in (1, 2, 5, 10, 15):
        ret_matrix = pd.concat({s: aligned[s][f"ret_{h}m"] for s in symbols}, axis=1)
        leader_ret = pd.Series(index=common_index, dtype=float)
        for s in symbols:
            mask = leader_name.eq(s)
            leader_ret.loc[mask] = ret_matrix.loc[mask, s]
        for s in symbols:
            aligned[s][f"leader_ret_{h}m"] = leader_ret
            aligned[s][f"leader_lag_residual_{h}m"] = aligned[s][f"ret_{h}m"] - leader_ret
    return aligned


def _clock_features(index: pd.DatetimeIndex) -> pd.DataFrame:
    minute_of_day = index.hour * 60 + index.minute
    out = pd.DataFrame(index=index)
    out["clock_day_sin"] = np.sin(2.0 * np.pi * minute_of_day / 1440.0)
    out["clock_day_cos"] = np.cos(2.0 * np.pi * minute_of_day / 1440.0)
    out["clock_hour_sin"] = np.sin(2.0 * np.pi * index.minute / 60.0)
    out["clock_hour_cos"] = np.cos(2.0 * np.pi * index.minute / 60.0)
    out["clock_quarter_sin"] = np.sin(2.0 * np.pi * (index.minute % 15) / 15.0)
    out["clock_quarter_cos"] = np.cos(2.0 * np.pi * (index.minute % 15) / 15.0)
    return out


def _dedupe_levels(levels: Iterable[tuple[str, float]], tick: float) -> list[tuple[str, float]]:
    result: list[tuple[str, float]] = []
    for name, value in levels:
        if not np.isfinite(value):
            continue
        if any(abs(value - existing) <= tick for _, existing in result):
            continue
        result.append((name, float(value)))
    return result


def _first_passage(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    start_index: int,
    side: str,
    stop: float,
    target: float,
    entry_fill: float,
    tick: float,
) -> tuple[str, int, float]:
    """Conservative first passage evaluated in vectorized C-level operations."""
    end = min(len(close) - 1, start_index + MAX_HOLD_MINUTES - 1)
    if end < start_index:
        raise ValueError("empty first-passage horizon")
    hi = high[start_index : end + 1]
    lo = low[start_index : end + 1]
    if side == "LONG":
        stop_hits = lo <= stop
        target_hits = hi >= target
    else:
        stop_hits = hi >= stop
        target_hits = lo <= target
    any_hit = stop_hits | target_hits
    hit_positions = np.flatnonzero(any_hit)
    if hit_positions.size:
        offset = int(hit_positions[0])
        j = start_index + offset
        if bool(stop_hits[offset]) and bool(target_hits[offset]):
            return "AMBIGUOUS_SAME_MINUTE", j, stop
        if bool(stop_hits[offset]):
            return "STOP_FIRST", j, stop
        return "TARGET_FIRST", j, target
    # Stale daytrade thesis: close after 12 hours rather than silently discarding it.
    exit_raw = close[end]
    exit_fill = exit_raw - ENTRY_SLIPPAGE_TICKS * tick if side == "LONG" else exit_raw + ENTRY_SLIPPAGE_TICKS * tick
    return "TIME_EXIT", end, exit_fill


def _action_rows_for_decision(
    symbol: str,
    frame: pd.DataFrame,
    index_position: int,
    state_id: str,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    time_ns: np.ndarray,
) -> list[dict[str, object]]:
    if index_position + 1 >= len(frame):
        return []
    row = frame.iloc[index_position]
    next_open = float(row["next_open"])
    if not np.isfinite(next_open) or next_open <= 0:
        return []
    tick = TICK_SIZE[symbol]
    timestamp_ns = int(time_ns[index_position])
    entry_index = index_position + 1
    actions: list[dict[str, object]] = []

    for side in ("LONG", "SHORT"):
        entry_fill = next_open + ENTRY_SLIPPAGE_TICKS * tick if side == "LONG" else next_open - ENTRY_SLIPPAGE_TICKS * tick
        if entry_fill <= 0:
            continue
        if side == "LONG":
            stop_levels = [
                ("MICRO_SWING", row.get("p1_pivot_low", np.nan)),
                ("LOCAL_SWING", row.get("p5_pivot_low", np.nan)),
                ("VOLATILITY", entry_fill - 1.25 * row.get("atr_price_60", np.nan)),
            ]
        else:
            stop_levels = [
                ("MICRO_SWING", row.get("p1_pivot_high", np.nan)),
                ("LOCAL_SWING", row.get("p5_pivot_high", np.nan)),
                ("VOLATILITY", entry_fill + 1.25 * row.get("atr_price_60", np.nan)),
            ]
        stop_levels = _dedupe_levels(stop_levels, tick)

        for stop_kind, stop in stop_levels:
            if side == "LONG" and stop >= entry_fill - tick:
                continue
            if side == "SHORT" and stop <= entry_fill + tick:
                continue
            risk = abs(entry_fill - stop)
            risk_bps = risk / entry_fill * 1e4
            if risk_bps < 1.0 or risk_bps > 500.0:
                continue

            targets: list[tuple[str, float]] = []
            for rr in (1.0, 1.5, 2.0):
                target = entry_fill + rr * risk if side == "LONG" else entry_fill - rr * risk
                targets.append((f"RR_{rr:.1f}", target))
            if side == "LONG":
                targets.extend(
                    [
                        ("PIVOT_5M", row.get("p5_pivot_high", np.nan)),
                        ("PIVOT_15M", row.get("p15_pivot_high", np.nan)),
                        ("RANGE_60M", row.get("rolling_high_60", np.nan)),
                        ("RANGE_240M", row.get("rolling_high_240", np.nan)),
                    ]
                )
            else:
                targets.extend(
                    [
                        ("PIVOT_5M", row.get("p5_pivot_low", np.nan)),
                        ("PIVOT_15M", row.get("p15_pivot_low", np.nan)),
                        ("RANGE_60M", row.get("rolling_low_60", np.nan)),
                        ("RANGE_240M", row.get("rolling_low_240", np.nan)),
                    ]
                )
            targets = _dedupe_levels(targets, tick)

            for target_kind, target in targets:
                if side == "LONG" and target <= entry_fill + tick:
                    continue
                if side == "SHORT" and target >= entry_fill - tick:
                    continue
                target_distance = abs(target - entry_fill)
                gross_rr = target_distance / risk
                if gross_rr < 0.50 or gross_rr > 8.0:
                    continue

                outcome, resolution_index, exit_level = _first_passage(
                    high, low, close, entry_index, side, stop, target, entry_fill, tick
                )
                stop_fill = stop - STOP_SLIPPAGE_TICKS * tick if side == "LONG" else stop + STOP_SLIPPAGE_TICKS * tick
                gross_stop_pct = abs(entry_fill - stop_fill) / entry_fill
                stop_net_r = -(gross_stop_pct + TAKER_FEE + TAKER_FEE) / (risk / entry_fill)
                gross_target_pct = target_distance / entry_fill
                target_net_r = (gross_target_pct - TAKER_FEE - MAKER_FEE) / (risk / entry_fill)
                if outcome == "TARGET_FIRST":
                    net_r = target_net_r
                elif outcome in ("STOP_FIRST", "AMBIGUOUS_SAME_MINUTE"):
                    net_r = stop_net_r
                else:
                    if side == "LONG":
                        gross = (exit_level - entry_fill) / entry_fill
                    else:
                        gross = (entry_fill - exit_level) / entry_fill
                    net_r = (gross - TAKER_FEE - TAKER_FEE) / (risk / entry_fill)

                action_id = f"{state_id}|{side}|{stop_kind}|{target_kind}"
                actions.append(
                    {
                        "state_id": state_id,
                        "action_id": action_id,
                        "symbol": symbol,
                        "decision_time_ns": timestamp_ns,
                        "entry_time_ns": int(time_ns[entry_index]),
                        "side": side,
                        "stop_kind": stop_kind,
                        "target_kind": target_kind,
                        "entry": entry_fill,
                        "stop": stop,
                        "target": target,
                        "risk_bps": risk_bps,
                        "target_bps": target_distance / entry_fill * 1e4,
                        "gross_rr": gross_rr,
                        "target_net_r": target_net_r,
                        "stop_net_r": stop_net_r,
                        "post_cost_break_even_probability": (-stop_net_r) / (target_net_r - stop_net_r),
                        "outcome": outcome,
                        "resolution_time_ns": int(time_ns[resolution_index]),
                        "holding_minutes": int(resolution_index - entry_index + 1),
                        "net_r": net_r,
                    }
                )
    return actions


def harvest(config: HarvestConfig) -> dict[str, object]:
    from data_re1_flow import load_range_flow

    config.output.mkdir(parents=True, exist_ok=True)
    prepared: dict[str, pd.DataFrame] = {}
    for symbol in config.symbols:
        raw = load_range_flow(symbol, config.load_start, config.end + timedelta(days=1), config.cache)
        prepared[symbol] = _prepare_symbol(symbol, raw)
    prepared = _add_common_state(prepared)

    start_ts = pd.Timestamp(config.start, tz="UTC")
    end_ts = pd.Timestamp(config.end + timedelta(days=1), tz="UTC")
    state_rows: list[pd.DataFrame] = []
    action_rows: list[dict[str, object]] = []

    internal_cols = {
        "open", "high", "low", "close", "volume", "quote_volume", "count", "taker_buy_volume",
        "taker_buy_quote_volume", "average_trade_quote", "atr_price_60", "rolling_high_60", "rolling_low_60",
        "rolling_high_240", "rolling_low_240", "next_open", "p1_pivot_high", "p1_pivot_low", "p5_pivot_high",
        "p5_pivot_low", "p15_pivot_high", "p15_pivot_low", "p60_pivot_high", "p60_pivot_low",
    }

    for symbol, frame in prepared.items():
        frame = frame.join(_clock_features(frame.index))
        decision_mask = (
            (frame.index >= start_ts)
            & (frame.index < end_ts)
            & (frame.index.minute % config.decision_minutes == 0)
        )
        positions = np.flatnonzero(decision_mask)
        feature_cols = [
            c for c in frame.columns
            if c not in internal_cols and not c.endswith("_pivot_high") and not c.endswith("_pivot_low")
        ]
        states = frame.iloc[positions][feature_cols].copy()
        decision_time_ns = states.index.as_unit("ns").asi8
        states.insert(0, "decision_time_ns", decision_time_ns)
        states.insert(0, "symbol", symbol)
        states.insert(0, "state_id", [f"{int(ts_ns)}|{symbol}" for ts_ns in decision_time_ns])
        states.reset_index(drop=True, inplace=True)
        state_rows.append(states)
        high = frame["high"].to_numpy(dtype=float, copy=False)
        low = frame["low"].to_numpy(dtype=float, copy=False)
        close = frame["close"].to_numpy(dtype=float, copy=False)
        # Binance Vision moved some archives from millisecond to microsecond
        # epoch timestamps.  Pandas preserves that resolution, while
        # ``Timestamp.value`` is always nanoseconds.  Normalize once so state
        # identities and action timestamps cannot silently diverge by 1,000x.
        time_ns = frame.index.as_unit("ns").asi8
        for pos in positions:
            state_id = f"{int(time_ns[pos])}|{symbol}"
            action_rows.extend(
                _action_rows_for_decision(
                    symbol, frame, int(pos), state_id, high, low, close, time_ns
                )
            )

    state_frame = pd.concat(state_rows, ignore_index=True)
    action_frame = pd.DataFrame(action_rows)
    if state_frame.empty or action_frame.empty:
        raise RuntimeError("direct state-action harvest produced no rows")
    if state_frame["state_id"].duplicated().any():
        raise RuntimeError("duplicate state identity")
    if action_frame["action_id"].duplicated().any():
        raise RuntimeError("duplicate action identity")
    if not set(action_frame["state_id"]).issubset(set(state_frame["state_id"])):
        raise RuntimeError("action without causal state")
    if not (action_frame["entry_time_ns"] > action_frame["decision_time_ns"]).all():
        raise RuntimeError("entry is not strictly later than decision")

    state_path = config.output / "states.csv.gz"
    action_path = config.output / "actions.csv.gz"
    state_frame.to_csv(state_path, index=False, compression="gzip")
    action_frame.to_csv(action_path, index=False, compression="gzip")

    resolved = action_frame["outcome"].isin(["TARGET_FIRST", "STOP_FIRST", "AMBIGUOUS_SAME_MINUTE", "TIME_EXIT"])
    summary = {
        "start": config.start.isoformat(),
        "end": config.end.isoformat(),
        "symbols": list(config.symbols),
        "states": int(len(state_frame)),
        "actions": int(len(action_frame)),
        "state_features": int(len(state_frame.columns) - 3),
        "target_first": int(action_frame["outcome"].eq("TARGET_FIRST").sum()),
        "stop_first_or_ambiguous": int(action_frame["outcome"].isin(["STOP_FIRST", "AMBIGUOUS_SAME_MINUTE"]).sum()),
        "time_exit": int(action_frame["outcome"].eq("TIME_EXIT").sum()),
        "mean_net_r": float(action_frame.loc[resolved, "net_r"].mean()),
        "causal_policy": "COMPLETED_1M_STATE_TO_NEXT_1M_OPEN_IMMUTABLE_ACTION",
        "same_minute_policy": "STOP_FIRST",
        "cost_policy": "TAKER_ENTRY_MAKER_TARGET_TAKER_STOP_WITH_TWO_TICK_ENTRY_AND_STOP_SLIPPAGE",
    }
    (config.output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return summary
