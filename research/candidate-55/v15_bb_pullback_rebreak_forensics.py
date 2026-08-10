"""Result-blind pullback/re-break geometry audit for V15 lower-band shorts.

The prior fixed-delay acceptance hypothesis improved favorable excursion but did
not create positive close follow-through.  That falsifies "remain below the
interaction" as a complete entry state.  The next uncertainty is entry geometry,
not another threshold:

    lower-band interaction -> counter-trend pullback -> failure to reclaim ->
    re-break of the pullback low.

This is adapted from an external four-phase volatility-expansion state machine,
but stripped to the causal core required by V15.  One signal episode owns the
symbol until it expires, so repeated edges cannot inflate evidence.  The first
bullish five-minute candle within three source bars defines the pullback high
(structural invalidation) and low (re-break trigger).  A short stop-entry is
considered triggered only if a later three-bar window trades below that low.
Minute bars resolve the trigger; a trigger minute that also touches the stop is
classified ambiguous and never credited.

No stop distance, target multiple or waiting window is optimized.  The two
three-bar windows are one fixed source-state transition each.  Outcomes are
reported in R units using the structural pullback high so the audit answers
whether the geometry can support a real strategy before a Nautilus account is
implemented.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
import argparse
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from kline_only_inputs import load_range
from router import (
    BarObservation,
    _adx_dx,
    _aggregate_complete,
    _atr,
    _bollinger,
    _directional_indicators,
    _mfi,
    _obv,
)


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
SYMBOL_PRIORITY = {symbol: index for index, symbol in enumerate(SYMBOLS)}
PULLBACK_WINDOW_BARS = 3
REBREAK_WINDOW_BARS = 3
OUTCOME_MINUTES = 120
WARMUP_DAYS = 1


def bars_from_frame(frame: pd.DataFrame) -> list[BarObservation]:
    return [
        BarObservation(
            int(pd.Timestamp(row.close_time_dt).value),
            float(row.open),
            float(row.high),
            float(row.low),
            float(row.close),
            float(row.volume),
        )
        for row in frame.itertuples(index=False)
    ]


def finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def number_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def first_trigger_minute(
    minute_bars: list[BarObservation],
    minute_index_by_time: dict[int, int],
    breakout_candle: BarObservation,
    trigger_price: float,
) -> tuple[int | None, BarObservation | None]:
    close_index = minute_index_by_time.get(int(breakout_candle.ts_event))
    if close_index is None:
        return None, None
    start = max(0, close_index - 4)
    for index in range(start, close_index + 1):
        bar = minute_bars[index]
        if float(bar.low) <= trigger_price:
            return index, bar
    return None, None


def r_path(
    minute_bars: list[BarObservation],
    trigger_index: int,
    entry: float,
    stop: float,
) -> dict[str, Any]:
    risk = stop - entry
    if not (math.isfinite(risk) and risk > 0.0 and entry > 0.0):
        return {"geometry_valid": False}
    window = minute_bars[trigger_index + 1 : trigger_index + 1 + OUTCOME_MINUTES]
    if not window:
        return {"geometry_valid": False}

    max_r = -math.inf
    min_r = math.inf
    stop_minute = None
    hit_minutes = {1: None, 2: None, 3: None}
    close_r = None
    for offset, bar in enumerate(window, start=1):
        # Conservative same-minute ordering: invalidation is evaluated before
        # favorable objectives whenever both occur inside one minute.
        if float(bar.high) >= stop:
            stop_minute = offset
            close_r = -1.0
            break
        favorable_r = (entry - float(bar.low)) / risk
        adverse_r = (entry - float(bar.high)) / risk
        max_r = max(max_r, favorable_r)
        min_r = min(min_r, adverse_r)
        for multiple in hit_minutes:
            if hit_minutes[multiple] is None and favorable_r >= float(multiple):
                hit_minutes[multiple] = offset
        close_r = (entry - float(bar.close)) / risk
    if max_r == -math.inf:
        max_r = -1.0 if stop_minute is not None else 0.0
    if min_r == math.inf:
        min_r = -1.0 if stop_minute is not None else 0.0
    result: dict[str, Any] = {
        "geometry_valid": True,
        "entry_price": entry,
        "stop_price": stop,
        "risk_fraction": risk / entry,
        "max_favorable_r_120m": float(max_r),
        "max_adverse_r_120m": float(min_r),
        "close_r_120m": number_or_none(close_r),
        "stop_minute": stop_minute,
        "stop_before_1r": int(stop_minute is not None and hit_minutes[1] is None),
    }
    for multiple, minute in hit_minutes.items():
        result[f"hit_{multiple}r_before_stop"] = int(minute is not None)
        result[f"minute_to_{multiple}r"] = minute
    return result


def signal_rows_for_symbol(
    *,
    symbol: str,
    frame: pd.DataFrame,
    evaluation_start: date,
    evaluation_end: date,
) -> list[dict[str, Any]]:
    minute_bars = bars_from_frame(frame)
    minute_index = {int(item.ts_event): index for index, item in enumerate(minute_bars)}
    candles = _aggregate_complete(minute_bars, 5)
    period = 14
    lower, _, upper = _bollinger(candles, 20)
    plus_di, minus_di = _directional_indicators(candles, period)
    dx, adx = _adx_dx(plus_di, minus_di, period)
    atr = _atr(candles, period)
    obv = _obv(candles)
    mfi = _mfi(candles, period)

    start_ns = int(pd.Timestamp(evaluation_start, tz="UTC").value)
    end_ns = int((pd.Timestamp(evaluation_end + timedelta(days=1), tz="UTC") - pd.Timedelta(nanoseconds=1)).value)
    rows: list[dict[str, Any]] = []
    minimum = max(2 * period + 3, 23)
    index = minimum
    last_possible = len(candles) - PULLBACK_WINDOW_BARS - REBREAK_WINDOW_BARS - 1
    while index < last_possible:
        signal = candles[index]
        previous = candles[index - 1]
        signal_time = int(signal.ts_event)
        required = (
            lower[index], lower[index - 1], upper[index], upper[index - 1],
            dx[index], adx[index], plus_di[index], minus_di[index], atr[index],
            obv[index], obv[index - 1], mfi[index],
        )
        short_bb = (
            start_ns <= signal_time <= end_ns
            and all(finite(value) for value in required)
            and float(previous.close) >= float(lower[index - 1])
            and float(signal.close) < float(lower[index])
        )
        if not short_bb:
            index += 1
            continue

        directional_margin = min(
            float(dx[index]) - float(plus_di[index]),
            float(adx[index]) - float(plus_di[index]),
            float(minus_di[index]) - float(plus_di[index]),
        )
        signal_close = float(signal.close)
        breakout_bps = (float(lower[index]) - signal_close) / signal_close * 10_000.0
        mfi_margin = abs(float(mfi[index]) - 50.0)
        score = (
            1.0
            + 2.5
            + min(4.0, max(0.0, directional_margin) / 3.0)
            + min(3.0, max(0.0, breakout_bps) / 12.0)
            + min(2.0, mfi_margin / 15.0)
        )
        item: dict[str, Any] = {
            "symbol": symbol,
            "signal_time_ns": signal_time,
            "signal_time": pd.Timestamp(signal_time, unit="ns", tz="UTC").isoformat(),
            "episode_key": f"{symbol}:BB_SHORT_PULLBACK_REBREAK:{signal_time}",
            "source_score": float(score),
            "signal_close": signal_close,
            "signal_lower_band": float(lower[index]),
            "signal_breakout_bps": float(breakout_bps),
            "signal_directional_margin": float(directional_margin),
            "signal_mfi_margin": float(mfi_margin),
            "pullback_window_bars": PULLBACK_WINDOW_BARS,
            "rebreak_window_bars": REBREAK_WINDOW_BARS,
        }

        pullback_index = None
        for candidate in range(index + 1, index + 1 + PULLBACK_WINDOW_BARS):
            bar = candles[candidate]
            if float(bar.close) > float(bar.open):
                pullback_index = candidate
                break
        if pullback_index is None:
            terminal_index = index + PULLBACK_WINDOW_BARS
            item.update(
                {
                    "setup_state": "NO_COUNTERTREND_PULLBACK",
                    "actionable": 0,
                    "decision_time_ns": int(candles[terminal_index].ts_event),
                    "decision_time": pd.Timestamp(candles[terminal_index].ts_event, unit="ns", tz="UTC").isoformat(),
                }
            )
            rows.append(item)
            index = terminal_index + 1
            continue

        pullback = candles[pullback_index]
        trigger_price = float(pullback.low)
        stop_price = float(pullback.high)
        item.update(
            {
                "pullback_time_ns": int(pullback.ts_event),
                "pullback_time": pd.Timestamp(pullback.ts_event, unit="ns", tz="UTC").isoformat(),
                "pullback_open": float(pullback.open),
                "pullback_high": stop_price,
                "pullback_low": trigger_price,
                "pullback_close": float(pullback.close),
            }
        )
        breakout_index = None
        for candidate in range(pullback_index + 1, pullback_index + 1 + REBREAK_WINDOW_BARS):
            if float(candles[candidate].low) <= trigger_price:
                breakout_index = candidate
                break
        if breakout_index is None:
            terminal_index = pullback_index + REBREAK_WINDOW_BARS
            item.update(
                {
                    "setup_state": "PULLBACK_WITHOUT_REBREAK",
                    "actionable": 0,
                    "decision_time_ns": int(candles[terminal_index].ts_event),
                    "decision_time": pd.Timestamp(candles[terminal_index].ts_event, unit="ns", tz="UTC").isoformat(),
                }
            )
            rows.append(item)
            index = terminal_index + 1
            continue

        breakout = candles[breakout_index]
        trigger_index, trigger_minute = first_trigger_minute(
            minute_bars, minute_index, breakout, trigger_price
        )
        if trigger_index is None or trigger_minute is None:
            item.update(
                {
                    "setup_state": "TRIGGER_CLOCK_MISSING",
                    "actionable": 0,
                    "decision_time_ns": int(breakout.ts_event),
                    "decision_time": pd.Timestamp(breakout.ts_event, unit="ns", tz="UTC").isoformat(),
                }
            )
        elif float(trigger_minute.high) >= stop_price:
            item.update(
                {
                    "setup_state": "AMBIGUOUS_TRIGGER_AND_STOP_MINUTE",
                    "actionable": 0,
                    "decision_time_ns": int(trigger_minute.ts_event),
                    "decision_time": pd.Timestamp(trigger_minute.ts_event, unit="ns", tz="UTC").isoformat(),
                    "trigger_minute_high": float(trigger_minute.high),
                    "trigger_minute_low": float(trigger_minute.low),
                }
            )
        else:
            geometry = r_path(minute_bars, trigger_index, trigger_price, stop_price)
            item.update(
                {
                    "setup_state": "ACTIONABLE_PULLBACK_REBREAK",
                    "actionable": int(bool(geometry.get("geometry_valid"))),
                    "decision_time_ns": int(trigger_minute.ts_event),
                    "decision_time": pd.Timestamp(trigger_minute.ts_event, unit="ns", tz="UTC").isoformat(),
                    "trigger_minute_high": float(trigger_minute.high),
                    "trigger_minute_low": float(trigger_minute.low),
                    **geometry,
                }
            )
        rows.append(item)
        index = breakout_index + 1
    return rows


def mark_arbitration(events: pd.DataFrame) -> pd.DataFrame:
    events = events.copy()
    events["actionable_rank"] = 0
    events["selected_actionable"] = 0
    events["same_time_actionable_candidates"] = 0
    actionable = events[events["actionable"] == 1]
    for _, group in actionable.groupby("decision_time_ns", sort=True):
        order = sorted(
            group.index,
            key=lambda index: (
                -float(events.at[index, "source_score"]),
                SYMBOL_PRIORITY.get(str(events.at[index, "symbol"]), 99),
                int(events.at[index, "signal_time_ns"]),
            ),
        )
        for rank, index in enumerate(order, start=1):
            events.at[index, "actionable_rank"] = rank
            events.at[index, "same_time_actionable_candidates"] = len(order)
        if order:
            events.at[order[0], "selected_actionable"] = 1
    return events


def summarize_actionable(group: pd.DataFrame) -> dict[str, Any]:
    if group.empty:
        return {"episodes": 0}
    result = {
        "episodes": int(len(group)),
        "symbols": group["symbol"].value_counts().to_dict(),
        "selected": int(group["selected_actionable"].sum()),
        "median_risk_fraction": number_or_none(group["risk_fraction"].median()),
        "mean_risk_fraction": number_or_none(group["risk_fraction"].mean()),
        "median_max_favorable_r_120m": number_or_none(group["max_favorable_r_120m"].median()),
        "mean_max_favorable_r_120m": number_or_none(group["max_favorable_r_120m"].mean()),
        "median_close_r_120m": number_or_none(group["close_r_120m"].median()),
        "mean_close_r_120m": number_or_none(group["close_r_120m"].mean()),
        "hit_1r_before_stop_fraction": float(group["hit_1r_before_stop"].mean()),
        "hit_2r_before_stop_fraction": float(group["hit_2r_before_stop"].mean()),
        "hit_3r_before_stop_fraction": float(group["hit_3r_before_stop"].mean()),
        "stop_before_1r_fraction": float(group["stop_before_1r"].mean()),
    }
    # A fixed 2R bracket is reported only as a geometry diagnostic. It is not
    # installed as management unless a later account experiment is warranted.
    result["diagnostic_2r_bracket_expectancy_r"] = (
        2.0 * result["hit_2r_before_stop_fraction"]
        - 1.0 * result["stop_before_1r_fraction"]
    )
    return result


def run(*, start: date, end: date, cache: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    load_start = start - timedelta(days=WARMUP_DAYS)
    rows: list[dict[str, Any]] = []
    manifest: dict[str, Any] = {}
    for symbol in SYMBOLS:
        frame, _, files, evidence = load_range(
            symbol=symbol,
            start=load_start,
            end=end,
            cache=cache,
            output=output / "source" / symbol,
        )
        rows.extend(
            signal_rows_for_symbol(
                symbol=symbol,
                frame=frame,
                evaluation_start=start,
                evaluation_end=end,
            )
        )
        manifest[symbol] = {
            "load_start": load_start.isoformat(),
            "evaluation_start": start.isoformat(),
            "evaluation_end": end.isoformat(),
            "minute_rows": int(len(frame)),
            "files": [str(path) for path in files],
            "evidence": [asdict(item) for item in evidence],
        }
    events = pd.DataFrame.from_records(rows)
    if events.empty:
        raise RuntimeError("no V15 lower-band episodes were recorded")
    events = mark_arbitration(events)
    events.to_csv(output / "episodes.csv", index=False)
    actionable = events[events["actionable"] == 1]
    selected = events[events["selected_actionable"] == 1]
    by_state = events["setup_state"].value_counts().to_dict()
    actionable_summary = summarize_actionable(actionable)
    selected_summary = summarize_actionable(selected)
    days = (end - start).days + 1
    checks = {
        "at_least_five_selected_episodes": int(selected_summary.get("episodes", 0)) >= 5,
        "selected_density_at_least_half_per_day": int(selected_summary.get("episodes", 0)) / days >= 0.5,
        "one_r_before_stop_at_least_55pct": float(selected_summary.get("hit_1r_before_stop_fraction") or 0.0) >= 0.55,
        "two_r_before_stop_at_least_35pct": float(selected_summary.get("hit_2r_before_stop_fraction") or 0.0) >= 0.35,
        "median_max_favorable_r_at_least_one": float(selected_summary.get("median_max_favorable_r_120m") or -math.inf) >= 1.0,
        "diagnostic_2r_expectancy_positive": float(selected_summary.get("diagnostic_2r_bracket_expectancy_r") or -math.inf) > 0.0,
    }
    result = {
        "candidate": "candidate-55",
        "family": "V15_BB_SHORT_PULLBACK_REBREAK_GEOMETRY",
        "interval": [start.isoformat(), end.isoformat()],
        "calendar_days": days,
        "result_blind_decision_then_outcome": True,
        "execution_backtest": False,
        "one_active_setup_per_symbol": True,
        "same_minute_entry_and_stop_never_credited": True,
        "pullback_window_bars": PULLBACK_WINDOW_BARS,
        "rebreak_window_bars": REBREAK_WINDOW_BARS,
        "outcome_minutes": OUTCOME_MINUTES,
        "hypothesis": (
            "V15 lower-band interactions become tradeable only after a counter-trend pullback fails and price re-breaks "
            "the pullback low, allowing invalidation above the pullback high in the same auction leg."
        ),
        "falsification": (
            "The geometry is rejected if re-break episodes cannot reach one and two structural R before invalidation "
            "at sufficient independent density, regardless of any raw-price MFE improvement."
        ),
        "source_episodes": int(len(events)),
        "source_episodes_per_day": float(len(events) / days),
        "setup_state_counts": {str(key): int(value) for key, value in by_state.items()},
        "actionable": actionable_summary,
        "selected_actionable": selected_summary,
        "prediction_checks": checks,
        "prediction_supported_in_this_window": all(checks.values()),
        "data_manifest": manifest,
    }
    (output / "SUMMARY.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        cache=args.cache,
        output=args.output,
    )
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
