#!/usr/bin/env python3
"""Causal anatomy of the high-frequency public ``ichiV2`` performance claim.

External clue
-------------
A public Freqtrade report named ``ichiV2`` claims 1,056 spot trades in roughly
93 days, 76.6% wins, 1.03% average profit and PF 6.51 across 18 assets.  Its
822 ROI exits averaged +1.52% with 94.3% wins, while 234 ``exit_signal`` exits
averaged -0.67% with only 14.5% wins.  The report is a clue, not evidence.

The widely copied public ``ichiV2.py`` has a 30% ROI target and therefore does
not explain those reported 1.52% ROI exits.  A sibling public implementation,
``ichiV2_1.py``, has the same strategy family and a 5%/3%/1%/0% ROI ladder at
0/10/41/114 minutes, a -5% stop, a multi-EMA Heikin-Ashi fan entry, and an
EMA5/EMA120 cross exit.  That implementation is the only discovered public
variant whose lifecycle is mechanically compatible with the report.  This
module therefore calls it the *report-compatible public variant*; it does not
claim that the gist author used byte-identical code.

Research question
-----------------
Does the report-compatible opportunity engine create frequent, independent,
short-horizon favorable excursions on BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT
across different regimes, and is its exit-signal branch a concentrated loss
engine as the report suggests?

No threshold or parameter is searched.  The public rules are frozen exactly:

* five-minute bars, next-bar-open entry;
* TA-Lib-compatible EMA periods 5/15/30/60/120/240/360/480 bars;
* all close EMAs above their Heikin-Ashi-open EMAs;
* EMA60/EMA480 fan > 1, fan gain >= 1.002, current fan above each of the
  previous three values;
* gross ROI ladder 5% / 3% / 1% / 0% at 0 / 10 / 41 / 114 minutes;
* gross stop -5%; EMA5 crossing below EMA120 exits at the next open;
* stop-first intrabar ambiguity and 19bp round-trip cost.

One trade is allowed per continuous signal episode.  All four assets are then
arbitrated into one global position slot using only fan gain, fan magnitude and
stable symbol priority.  Raw signal bars, independent episodes, favorable and
adverse excursions, exit reasons, rejected collisions and every trade are
retained.  This is a mechanism diagnostic, not a custom account/matching engine
and not final continuous-NAV evidence.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import importlib.util
import json
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
SYMBOL_PRIORITY = {symbol: index for index, symbol in enumerate(SYMBOLS)}
EMA_PERIODS = (5, 15, 30, 60, 120, 240, 360, 480)
FAN_GAIN_MIN = 1.002
FAN_SHIFT_COUNT = 3
STOP_GROSS = -0.05
ROUND_TRIP_COST = 0.0019
PLANNED_LOSS_FRACTION = abs(STOP_GROSS) + ROUND_TRIP_COST
MAX_HOLD_MINUTES = 24 * 60
HERE = Path(__file__).resolve().parent


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


def _talib_ema(series: pd.Series, period: int) -> pd.Series:
    """EMA with the SMA seed used by TA-Lib's standard EMA implementation."""
    values = pd.to_numeric(series, errors="coerce").to_numpy(float)
    output = np.full(values.shape, np.nan, dtype=float)
    if len(values) < period:
        return pd.Series(output, index=series.index)
    seed = values[:period]
    if not np.all(np.isfinite(seed)):
        return pd.Series(output, index=series.index)
    output[period - 1] = float(seed.mean())
    alpha = 2.0 / (period + 1.0)
    for index in range(period, len(values)):
        value = values[index]
        previous = output[index - 1]
        if not math.isfinite(value) or not math.isfinite(previous):
            output[index] = np.nan
        else:
            output[index] = previous + alpha * (value - previous)
    return pd.Series(output, index=series.index)


def _heikin_ashi_open(frame: pd.DataFrame) -> pd.Series:
    close = (
        frame["open"] + frame["high"] + frame["low"] + frame["close"]
    ) / 4.0
    source_open = pd.to_numeric(frame["open"], errors="coerce").to_numpy(float)
    source_close = pd.to_numeric(frame["close"], errors="coerce").to_numpy(float)
    ha_close = close.to_numpy(float)
    output = np.full(len(frame), np.nan, dtype=float)
    if len(frame) == 0:
        return pd.Series(output, index=frame.index)
    output[0] = (source_open[0] + source_close[0]) / 2.0
    for index in range(1, len(frame)):
        output[index] = (output[index - 1] + ha_close[index - 1]) / 2.0
    return pd.Series(output, index=frame.index)


def _complete_five_minute_bars(minute: pd.DataFrame) -> pd.DataFrame:
    work = minute.copy()
    work["close_time_dt"] = pd.to_datetime(
        work["close_time_dt"], utc=True, errors="coerce"
    )
    work = work.sort_values("close_time_dt").reset_index(drop=True)
    # Binance closes at xx:xx:59.999.  Reconstruct the minute open clock and
    # group by the causal five-minute interval start.
    work["minute_open_time"] = (
        work["close_time_dt"]
        + pd.Timedelta(milliseconds=1)
        - pd.Timedelta(minutes=1)
    )
    work["bucket"] = work["minute_open_time"].dt.floor("5min")
    rows: list[dict[str, Any]] = []
    for bucket, group in work.groupby("bucket", sort=True):
        group = group.sort_values("close_time_dt")
        if len(group) != 5:
            continue
        times = pd.DatetimeIndex(group["close_time_dt"])
        if not bool(
            ((times[1:] - times[:-1]) == pd.Timedelta(minutes=1)).all()
        ):
            continue
        expected_last = (
            pd.Timestamp(bucket)
            + pd.Timedelta(minutes=5)
            - pd.Timedelta(milliseconds=1)
        )
        if times[-1] != expected_last:
            continue
        rows.append(
            {
                "open_time": pd.Timestamp(bucket),
                "close_time": times[-1],
                "open": float(group.iloc[0]["open"]),
                "high": float(group["high"].max()),
                "low": float(group["low"].min()),
                "close": float(group.iloc[-1]["close"]),
                "volume": float(group["volume"].sum()),
                "quote_volume": float(group["quote_volume"].sum()),
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame = frame.sort_values("close_time").reset_index(drop=True)
    return frame


def _features(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["ha_open"] = _heikin_ashi_open(out)
    for period in EMA_PERIODS:
        out[f"close_ema_{period}"] = _talib_ema(out["close"], period)
        out[f"ha_open_ema_{period}"] = _talib_ema(out["ha_open"], period)
    out["fan_magnitude"] = out["close_ema_60"] / out["close_ema_480"]
    out["fan_magnitude_gain"] = (
        out["fan_magnitude"] / out["fan_magnitude"].shift(1)
    )
    signal = pd.Series(True, index=out.index, dtype=bool)
    for period in EMA_PERIODS:
        signal &= out[f"close_ema_{period}"] > out[f"ha_open_ema_{period}"]
    signal &= out["fan_magnitude"] > 1.0
    signal &= out["fan_magnitude_gain"] >= FAN_GAIN_MIN
    for shift in range(1, FAN_SHIFT_COUNT + 1):
        signal &= out["fan_magnitude"] > out["fan_magnitude"].shift(shift)
    out["entry_signal"] = signal.fillna(False)
    out["signal_episode_start"] = out["entry_signal"] & ~out[
        "entry_signal"
    ].shift(1).fillna(False)
    out["exit_signal"] = (
        (out["close_ema_5"] < out["close_ema_120"])
        & (out["close_ema_5"].shift(1) >= out["close_ema_120"].shift(1))
    ).fillna(False)
    out["signal_episode_number"] = out["signal_episode_start"].cumsum()
    return out


def _roi_threshold(elapsed_minutes: float) -> float:
    if elapsed_minutes < 10.0:
        return 0.05
    if elapsed_minutes < 41.0:
        return 0.03
    if elapsed_minutes < 114.0:
        return 0.01
    return 0.0


def _forward_excursion(
    frame: pd.DataFrame,
    entry_index: int,
    entry_price: float,
    minutes: int,
) -> dict[str, Any]:
    bars = max(1, int(math.ceil(minutes / 5.0)))
    path = frame.iloc[entry_index : min(len(frame), entry_index + bars)]
    if path.empty:
        return {"mfe_net": None, "mae_net": None}
    maximum = float(path["high"].max())
    minimum = float(path["low"].min())
    return {
        "mfe_net": maximum / entry_price - 1.0 - ROUND_TRIP_COST,
        "mae_net": minimum / entry_price - 1.0 - ROUND_TRIP_COST,
    }


def _simulate_source_path(
    frame: pd.DataFrame,
    signal_index: int,
) -> dict[str, Any] | None:
    entry_index = signal_index + 1
    if entry_index >= len(frame):
        return None
    entry = frame.iloc[entry_index]
    entry_time = pd.Timestamp(entry["open_time"])
    entry_price = float(entry["open"])
    stop_price = entry_price * (1.0 + STOP_GROSS)
    last_index = min(
        len(frame) - 1,
        entry_index + int(MAX_HOLD_MINUTES / 5),
    )
    exit_index: int | None = None
    exit_time: pd.Timestamp | None = None
    exit_price: float | None = None
    exit_reason: str | None = None

    for index in range(entry_index, last_index + 1):
        bar = frame.iloc[index]
        bar_open_time = pd.Timestamp(bar["open_time"])
        bar_open = float(bar["open"])
        bar_low = float(bar["low"])
        bar_high = float(bar["high"])

        # Gap through the hard stop is filled at the observed open.
        if bar_open <= stop_price:
            exit_index = index
            exit_time = bar_open_time
            exit_price = bar_open
            exit_reason = "stop_gap"
            break

        # Exit signal is known only after the preceding completed bar.
        if index > entry_index and bool(frame.iloc[index - 1]["exit_signal"]):
            exit_index = index
            exit_time = bar_open_time
            exit_price = bar_open
            exit_reason = "exit_signal"
            break

        # Conservative same-bar ambiguity: stop before ROI.
        if bar_low <= stop_price:
            exit_index = index
            exit_time = pd.Timestamp(bar["close_time"])
            exit_price = stop_price
            exit_reason = "stoploss"
            break

        elapsed = (bar_open_time - entry_time).total_seconds() / 60.0
        roi = _roi_threshold(elapsed)
        target = entry_price * (1.0 + roi)
        if roi == 0.0:
            if bar_open >= entry_price:
                exit_price = bar_open
                exit_time = bar_open_time
                exit_index = index
                exit_reason = "roi_0"
                break
            if bar_high >= entry_price:
                exit_price = entry_price
                exit_time = pd.Timestamp(bar["close_time"])
                exit_index = index
                exit_reason = "roi_0"
                break
        else:
            if bar_open >= target:
                exit_price = bar_open
                exit_time = bar_open_time
                exit_index = index
                exit_reason = f"roi_{roi:.2f}"
                break
            if bar_high >= target:
                exit_price = target
                exit_time = pd.Timestamp(bar["close_time"])
                exit_index = index
                exit_reason = f"roi_{roi:.2f}"
                break

    if exit_index is None or exit_price is None or exit_time is None:
        exit_index = last_index
        last = frame.iloc[last_index]
        exit_time = pd.Timestamp(last["close_time"])
        exit_price = float(last["close"])
        exit_reason = "max_24h"

    gross_return = exit_price / entry_price - 1.0
    net_return = gross_return - ROUND_TRIP_COST
    result: dict[str, Any] = {
        "entry_index": entry_index,
        "entry_time": entry_time,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "exit_index": exit_index,
        "exit_time": exit_time,
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "hold_minutes": (exit_time - entry_time).total_seconds() / 60.0,
        "gross_return": gross_return,
        "net_return": net_return,
        "planned_loss_fraction": PLANNED_LOSS_FRACTION,
        "r_multiple": net_return / PLANNED_LOSS_FRACTION,
    }
    for minutes in (30, 60, 120):
        excursion = _forward_excursion(frame, entry_index, entry_price, minutes)
        result[f"mfe_{minutes}m_net"] = excursion["mfe_net"]
        result[f"mae_{minutes}m_net"] = excursion["mae_net"]
    return result


def _event_records(
    *,
    symbol: str,
    frame: pd.DataFrame,
    start: date,
    end: date,
    period_label: str,
    split: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records: list[dict[str, Any]] = []
    eligible = frame[
        frame["signal_episode_start"]
        & frame["close_time"].dt.date.map(lambda value: start <= value <= end)
    ]
    for index, signal in eligible.iterrows():
        path = _simulate_source_path(frame, int(index))
        if path is None:
            continue
        episode = int(signal["signal_episode_number"])
        records.append(
            {
                "event_id": f"{symbol}:{pd.Timestamp(signal['close_time']).isoformat()}:{episode}",
                "symbol": symbol,
                "period_label": period_label,
                "split": split,
                "signal_time": pd.Timestamp(signal["close_time"]),
                "signal_episode_number": episode,
                "fan_magnitude": float(signal["fan_magnitude"]),
                "fan_magnitude_gain": float(signal["fan_magnitude_gain"]),
                "close_ema_5": float(signal["close_ema_5"]),
                "close_ema_60": float(signal["close_ema_60"]),
                "close_ema_120": float(signal["close_ema_120"]),
                "close_ema_480": float(signal["close_ema_480"]),
                **path,
            }
        )
    diagnostics = {
        "five_minute_bars": int(len(frame)),
        "signal_bars": int(frame["entry_signal"].sum()),
        "independent_signal_episodes": int(
            frame["signal_episode_start"].sum()
        ),
        "period_signal_episodes": int(len(eligible)),
    }
    return records, diagnostics


def _same_time_arbitration(group: pd.DataFrame) -> pd.Series:
    work = group.copy()
    work["symbol_priority"] = (
        work["symbol"].map(SYMBOL_PRIORITY).fillna(99).astype(int)
    )
    return work.sort_values(
        ["fan_magnitude_gain", "fan_magnitude", "symbol_priority", "event_id"],
        ascending=[False, False, True, True],
        kind="stable",
    ).iloc[0]


def _global_one_slot(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if frame.empty:
        return frame.copy(), frame.copy()
    candidates = pd.DataFrame(
        [
            _same_time_arbitration(group)
            for _, group in frame.groupby("entry_time", sort=True)
        ]
    ).sort_values(["entry_time", "event_id"], kind="stable")
    selected: list[int] = []
    rejected: list[dict[str, Any]] = []
    occupied_until: pd.Timestamp | None = None
    for index, row in candidates.iterrows():
        entry_time = pd.Timestamp(row["entry_time"])
        if occupied_until is not None and entry_time < occupied_until:
            rejected.append(
                {**row.to_dict(), "rejection_reason": "GLOBAL_SLOT_OCCUPIED"}
            )
            continue
        selected.append(index)
        occupied_until = pd.Timestamp(row["exit_time"])
    selected_frame = candidates.loc[selected].copy()
    rejected_frame = pd.DataFrame(rejected)
    return selected_frame, rejected_frame


def _summary(series: pd.Series) -> dict[str, Any]:
    values = (
        pd.to_numeric(series, errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .dropna()
        .to_numpy(float)
    )
    if values.size == 0:
        return {"count": 0}
    gains = float(values[values > 0.0].sum())
    losses = float(-values[values < 0.0].sum())
    without_best = (
        np.delete(values, int(np.argmax(values)))
        if values.size > 1
        else np.array([], dtype=float)
    )
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
        "mean_without_best": (
            None if without_best.size == 0 else float(without_best.mean())
        ),
    }


def _groups(frame: pd.DataFrame, column: str, value: str) -> dict[str, Any]:
    if frame.empty or column not in frame.columns:
        return {}
    return {
        str(key): _summary(group[value])
        for key, group in frame.groupby(column, dropna=False, sort=True)
    }


def _opportunity(frame: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for minutes in (30, 60, 120):
        column = f"mfe_{minutes}m_net"
        values = (
            pd.to_numeric(frame[column], errors="coerce")
            .replace([np.inf, -np.inf], np.nan)
            .dropna()
        )
        result[str(minutes)] = {
            **_summary(values),
            "hit_positive_after_cost": float((values > 0.0).mean()) if len(values) else None,
            "hit_0_5pct_after_cost": float((values >= 0.005).mean()) if len(values) else None,
            "hit_1pct_after_cost": float((values >= 0.01).mean()) if len(values) else None,
            "hit_3pct_after_cost": float((values >= 0.03).mean()) if len(values) else None,
        }
    return result


def _nav(frame: pd.DataFrame, calendar_days: int) -> dict[str, Any]:
    multiples = (
        pd.to_numeric(frame["r_multiple"], errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .dropna()
        .to_numpy(float)
    )
    if multiples.size == 0:
        return {"trades": 0}
    returns = 0.03 * multiples
    if np.any(1.0 + returns <= 0.0):
        raise RuntimeError("diagnostic NAV became non-positive")
    nav = np.concatenate(([1.0], np.cumprod(1.0 + returns)))
    peak = np.maximum.accumulate(nav)
    drawdown = nav / peak - 1.0
    return {
        "trades": int(len(multiples)),
        "final_nav_multiple": float(nav[-1]),
        "daily_geometric_growth_over_sampled_days": float(
            nav[-1] ** (1.0 / max(calendar_days, 1)) - 1.0
        ),
        "max_drawdown": float(drawdown.min()),
        "note": (
            "Path-level compounding at NAV x 3% planned loss over sampled "
            "periods; not a continuous NautilusTrader account."
        ),
    }


def run_one(args: argparse.Namespace) -> None:
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    warm_start = start - timedelta(days=4)
    forward_end = end + timedelta(days=2)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    v59 = _load(
        HERE / "forced_unwind_geometry_v59_fixed.py",
        f"candidate51_v63_v59_{args.period_label}",
    )
    loader = v59._load_target()
    loader._contiguous = v59._contiguous

    records: list[dict[str, Any]] = []
    source: dict[str, Any] = {}
    diagnostics: dict[str, Any] = {}
    for symbol in SYMBOLS:
        minute, evidence, missing = loader._load_observed_minutes(
            symbol=symbol,
            start=warm_start,
            end=forward_end,
            cache=Path(args.cache) / symbol,
            candidate05=Path(args.candidate05_path),
            candidate51=Path(args.candidate51_path),
        )
        five = _features(_complete_five_minute_bars(minute))
        events, item = _event_records(
            symbol=symbol,
            frame=five,
            start=start,
            end=end,
            period_label=args.period_label,
            split=args.split,
        )
        records.extend(events)
        diagnostics[symbol] = item
        source[symbol] = {
            "evidence": evidence,
            "missing_minute_close_times": [
                value.isoformat() for value in missing
            ],
            "five_minute_rows": int(len(five)),
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
            "claim_gist": "vjaykrsna/3aa41ada83ea890721e27ccda02c1d64",
            "claim_summary": {
                "trades": 1056,
                "trades_per_day": 11.48,
                "win_rate": 0.766,
                "average_profit": 0.0103,
                "profit_factor": 6.51,
                "roi_exits": 822,
                "roi_average_profit": 0.0152,
                "exit_signal_exits": 234,
                "exit_signal_average_profit": -0.0067,
                "assets": 18,
                "max_open_trades": 3,
            },
            "report_compatible_public_source": (
                "remiotore/ccxt-freqtrade strategies/ichiV2_1.py "
                "blob 391b88169f6efc4fd4573161bfe8daeec7a84bc6"
            ),
            "source_ambiguity": (
                "The gist does not publish its strategy file. The public "
                "ichiV2_1 lifecycle is tested because its ROI ladder is "
                "compatible with the reported exit distribution; this is an "
                "inference, not an identity claim."
            ),
        },
        "frozen_contract": {
            "timeframe": "5m",
            "entry": "next five-minute open after a completed signal bar",
            "ema_periods_in_five_minute_bars": list(EMA_PERIODS),
            "fan_gain_min": FAN_GAIN_MIN,
            "fan_shift_count": FAN_SHIFT_COUNT,
            "roi_ladder": {"0": 0.05, "10": 0.03, "41": 0.01, "114": 0.0},
            "stop_gross": STOP_GROSS,
            "exit_signal": "EMA5 crosses below EMA120; exit next open",
            "cost": ROUND_TRIP_COST,
            "same_bar_ambiguity": "stop before ROI",
            "episode_independence": "one entry per continuous signal run",
            "global_slot": "one actual held position across four assets",
            "same_clock_arbitration": (
                "highest fan gain, then fan magnitude, then stable symbol priority"
            ),
            "threshold_search": "none",
        },
        "source": source,
        "signal_diagnostics": diagnostics,
        "records": records,
    }
    (output / "result.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=_json_default)
        + "\n"
    )


def aggregate(args: argparse.Namespace) -> None:
    paths = sorted(Path(args.results_root).rglob("result.json"))
    if not paths:
        raise RuntimeError("no result files")
    payloads = [json.loads(path.read_text()) for path in paths]
    frames = [
        pd.DataFrame(payload["records"])
        for payload in payloads
        if payload.get("records")
    ]
    events = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    for column in ("signal_time", "entry_time", "exit_time"):
        if column in events.columns:
            events[column] = pd.to_datetime(
                events[column], utc=True, errors="coerce"
            )
    selected, rejected = _global_one_slot(events)
    calendar_days = sum(int(payload["calendar_days"]) for payload in payloads)

    result = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_runs": len(payloads),
        "calendar_days": calendar_days,
        "raw_independent_signal_episodes": int(len(events)),
        "global_selected_trades": int(len(selected)),
        "global_selected_trades_per_day": float(
            len(selected) / max(calendar_days, 1)
        ),
        "global_collision_rejections": int(len(rejected)),
        "selected_net_return": _summary(selected["net_return"]),
        "selected_r_multiple": _summary(selected["r_multiple"]),
        "diagnostic_nav": _nav(selected, calendar_days),
        "selected_by_exit_reason": _groups(
            selected, "exit_reason", "net_return"
        ),
        "selected_by_split": _groups(selected, "split", "net_return"),
        "selected_by_period": _groups(
            selected, "period_label", "net_return"
        ),
        "selected_by_symbol": _groups(selected, "symbol", "net_return"),
        "all_episode_opportunity": _opportunity(events),
        "selected_opportunity": _opportunity(selected),
        "source_report_comparison": {
            "claim_trades_per_day_with_18_assets_and_3_slots": 11.48,
            "our_four_asset_one_slot_independent_trades_per_day": float(
                len(selected) / max(calendar_days, 1)
            ),
            "claim_roi_exit_average": 0.0152,
            "our_roi_exit_average": _summary(
                selected.loc[
                    selected["exit_reason"].astype(str).str.startswith("roi"),
                    "net_return",
                ]
            ).get("mean"),
            "claim_exit_signal_average": -0.0067,
            "our_exit_signal_average": _summary(
                selected.loc[
                    selected["exit_reason"].eq("exit_signal"),
                    "net_return",
                ]
            ).get("mean"),
        },
    }

    roi = result["selected_by_exit_reason"]
    roi_groups = [
        value for key, value in roi.items() if str(key).startswith("roi")
    ]
    roi_count = sum(int(value.get("count", 0)) for value in roi_groups)
    roi_profit = sum(float(value.get("gross_profit", 0.0)) for value in roi_groups)
    roi_loss = sum(float(value.get("gross_loss", 0.0)) for value in roi_groups)
    exit_signal = roi.get("exit_signal", {"count": 0})
    result["predeclared_anatomy_assessment"] = {
        "material_independent_opportunity_density": (
            result["global_selected_trades_per_day"] >= 0.25
        ),
        "sixty_minute_favorable_excursion_is_positive_after_cost": (
            result["selected_opportunity"].get("60", {}).get("median", -math.inf)
            > 0.0
        ),
        "roi_branch_has_positive_aggregate_expectancy": (
            roi_count > 0 and roi_profit > roi_loss
        ),
        "exit_signal_branch_is_a_concentrated_loss_engine": (
            exit_signal.get("count", 0) > 0
            and exit_signal.get("mean", 0.0) < 0.0
            and exit_signal.get("profit_factor", 0.0) < 1.0
        ),
        "overall_ex_best_expectancy_is_positive": (
            result["selected_net_return"].get("mean_without_best", -math.inf)
            > 0.0
        ),
    }
    result["diagnostic_conclusion"] = (
        "opportunity_engine_and_exit_loss_split_reproduced"
        if all(result["predeclared_anatomy_assessment"].values())
        else "public_claim_mechanism_not_fully_reproduced"
    )
    result["next_inference"] = (
        "If the opportunity and exit-loss split repeats, preserve the fan "
        "expansion opportunity engine and design a causal failure-management "
        "repair before any NautilusTrader promotion. If it does not repeat, "
        "do not tune the public thresholds on these periods."
    )
    result["truth_boundary"] = (
        "The public claim is not validated by this diagnostic. Signal-path "
        "results are not a continuous NautilusTrader account and do not prove "
        "the final growth, frequency or production-readiness target."
    )

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "ANATOMY.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, default=_json_default)
        + "\n"
    )
    events.to_csv(output / "EPISODES.csv", index=False)
    selected.to_csv(output / "SELECTED.csv", index=False)
    rejected.to_csv(output / "REJECTED.csv", index=False)

    overall = result["selected_net_return"]
    r = result["selected_r_multiple"]
    nav = result["diagnostic_nav"]
    pf = overall.get("profit_factor")
    lines = [
        "# Public ichiV2 claim anatomy v63",
        "",
        f"- source periods: {len(payloads)}",
        f"- sampled calendar days: {calendar_days}",
        f"- raw independent signal episodes: {len(events)}",
        f"- global one-slot selected trades: {len(selected)} ({result['global_selected_trades_per_day']:.3f}/day)",
        f"- conclusion: **{result['diagnostic_conclusion']}**",
        "",
        "| metric | value |",
        "|---|---:|",
        f"| mean net return | {100 * overall.get('mean', 0.0):.3f}% |",
        f"| median net return | {100 * overall.get('median', 0.0):.3f}% |",
        f"| win rate | {100 * overall.get('win_rate', 0.0):.2f}% |",
        f"| profit factor | {'na' if pf is None else f'{pf:.2f}'} |",
        f"| mean R | {r.get('mean', 0.0):.3f} |",
        f"| ex-best mean net | {100 * overall.get('mean_without_best', 0.0):.3f}% |",
        f"| diagnostic final NAV | {nav.get('final_nav_multiple', 0.0):.3f} |",
        f"| diagnostic daily geom | {100 * nav.get('daily_geometric_growth_over_sampled_days', 0.0):.3f}% |",
        f"| diagnostic max DD | {100 * nav.get('max_drawdown', 0.0):.2f}% |",
        "",
        "## Exit anatomy",
        "",
        "| exit reason | trades | mean net | win rate | PF |",
        "|---|---:|---:|---:|---:|",
    ]
    for reason, value in result["selected_by_exit_reason"].items():
        reason_pf = value.get("profit_factor")
        lines.append(
            f"| {reason} | {value.get('count', 0)} | "
            f"{100 * value.get('mean', 0.0):.3f}% | "
            f"{100 * value.get('win_rate', 0.0):.2f}% | "
            f"{'na' if reason_pf is None else f'{reason_pf:.2f}'} |"
        )
    lines += ["", "## Predeclared anatomy assessment", ""]
    for key, value in result["predeclared_anatomy_assessment"].items():
        lines.append(f"- {key}: `{value}`")
    lines += [
        "",
        "## Next inference",
        "",
        result["next_inference"],
        "",
        "## Truth boundary",
        "",
        result["truth_boundary"],
        "",
    ]
    (output / "ANATOMY.md").write_text("\n".join(lines))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    for name in ("start", "end", "period_label", "split", "output"):
        run.add_argument(
            f"--{name.replace('_', '-')}", dest=name, required=True
        )
    run.add_argument("--cache", default=".cache/candidate-51-ichiv2-v63")
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
