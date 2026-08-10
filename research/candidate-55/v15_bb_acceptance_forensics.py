"""Result-blind state-transition audit for V15 lower-band short interactions.

Development episode analysis showed that V15's high-capacity Bollinger short
engine often identifies the correct liquidity interaction but enters before the
auction demonstrates acceptance below the interaction price.  This script tests
one predeclared explanation rather than a delay grid:

* a lower-band close arms a short episode;
* exactly three subsequent completed five-minute bars form the observation
  window (fifteen minutes, one source-state transition);
* the episode is accepted only when the final close remains below the signal
  close, the source 1.5% invalidation was not touched, and the source 1.07%
  trailing-activation move was not already consumed before entry;
* only after that decision is frozen is the next-minute entry path exposed.

The 1.5% and 1.07% boundaries are not fitted here.  They are inherited from the
source V15 stop and trailing geometry.  The diagnostic records every accepted,
reclaimed, exhausted and invalidated episode plus same-time cross-asset
arbitration.  It does not simulate an account and cannot establish performance.
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
HORIZONS_MINUTES = (15, 30, 60, 120)
COST_FLOOR_BPS = 20.0
SOURCE_STOP_FRACTION = 0.015
SOURCE_TRAIL_ACTIVATION_FRACTION = 0.0107
CONFIRMATION_BARS = 3
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


def future_path(
    minute_bars: list[BarObservation],
    minute_index_by_time: dict[int, int],
    decision_time_ns: int,
) -> dict[str, float | None]:
    decision_index = minute_index_by_time.get(int(decision_time_ns))
    result: dict[str, float | None] = {}
    if decision_index is None or decision_index + 1 >= len(minute_bars):
        for horizon in HORIZONS_MINUTES:
            result[f"mfe_{horizon}m_net_bps"] = None
            result[f"mae_{horizon}m_with_cost_bps"] = None
            result[f"close_{horizon}m_net_bps"] = None
        return result
    entry_index = decision_index + 1
    entry = float(minute_bars[entry_index].open)
    result["entry_next_minute_open"] = entry
    for horizon in HORIZONS_MINUTES:
        window = minute_bars[entry_index : entry_index + horizon]
        if len(window) != horizon or entry <= 0.0:
            result[f"mfe_{horizon}m_net_bps"] = None
            result[f"mae_{horizon}m_with_cost_bps"] = None
            result[f"close_{horizon}m_net_bps"] = None
            continue
        low = min(float(item.low) for item in window)
        high = max(float(item.high) for item in window)
        close = float(window[-1].close)
        result[f"mfe_{horizon}m_net_bps"] = (
            (entry - low) / entry * 10_000.0 - COST_FLOOR_BPS
        )
        result[f"mae_{horizon}m_with_cost_bps"] = (
            (high - entry) / entry * 10_000.0 + COST_FLOOR_BPS
        )
        result[f"close_{horizon}m_net_bps"] = (
            (entry - close) / entry * 10_000.0 - COST_FLOOR_BPS
        )
    return result


def classify_transition(
    signal: BarObservation,
    confirmation: list[BarObservation],
) -> tuple[str, dict[str, Any]]:
    signal_close = float(signal.close)
    closes = [float(item.close) for item in confirmation]
    highest = max(float(item.high) for item in confirmation)
    lowest = min(float(item.low) for item in confirmation)
    invalidation = signal_close * (1.0 + SOURCE_STOP_FRACTION)
    activation = signal_close * (1.0 - SOURCE_TRAIL_ACTIVATION_FRACTION)
    below_count = sum(close < signal_close for close in closes)
    diagnostics = {
        "confirmation_bars": len(confirmation),
        "confirmation_close": closes[-1],
        "confirmation_return_bps": (signal_close - closes[-1]) / signal_close * 10_000.0,
        "confirmation_below_signal_count": below_count,
        "confirmation_high": highest,
        "confirmation_low": lowest,
        "source_invalidation_price": invalidation,
        "source_activation_price": activation,
        "pre_entry_mfe_bps": (signal_close - lowest) / signal_close * 10_000.0,
        "pre_entry_mae_bps": (highest - signal_close) / signal_close * 10_000.0,
    }
    if highest >= invalidation:
        return "INVALIDATED_BEFORE_CONFIRMATION", diagnostics
    if lowest <= activation:
        return "EXHAUSTED_BEFORE_CONFIRMATION", diagnostics
    if closes[-1] < signal_close:
        return "ACCEPTED_BELOW_INTERACTION", diagnostics
    return "RECLAIMED_INTERACTION", diagnostics


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
    for index in range(minimum, len(candles) - CONFIRMATION_BARS):
        signal = candles[index]
        previous = candles[index - 1]
        signal_time = int(signal.ts_event)
        if not (start_ns <= signal_time <= end_ns):
            continue
        required = (
            lower[index], lower[index - 1], upper[index], upper[index - 1],
            dx[index], adx[index], plus_di[index], minus_di[index], atr[index],
            obv[index], obv[index - 1], mfi[index],
        )
        if not all(finite(value) for value in required):
            continue
        short_bb = (
            float(previous.close) >= float(lower[index - 1])
            and float(signal.close) < float(lower[index])
        )
        if not short_bb:
            continue

        confirmation = candles[index + 1 : index + 1 + CONFIRMATION_BARS]
        state, transition = classify_transition(signal, confirmation)
        decision_time = int(confirmation[-1].ts_event)
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
            "decision_time_ns": decision_time,
            "decision_time": pd.Timestamp(decision_time, unit="ns", tz="UTC").isoformat(),
            "episode_key": f"{symbol}:BB_SHORT_ACCEPTANCE:{signal_time}",
            "transition_state": state,
            "accepted": int(state == "ACCEPTED_BELOW_INTERACTION"),
            "source_score": float(score),
            "signal_close": signal_close,
            "signal_lower_band": float(lower[index]),
            "signal_breakout_bps": float(breakout_bps),
            "signal_directional_margin": float(directional_margin),
            "signal_mfi_margin": float(mfi_margin),
        }
        item.update(transition)
        item.update(future_path(minute_bars, minute_index, decision_time))
        rows.append(item)
    return rows


def mark_arbitration(events: pd.DataFrame) -> pd.DataFrame:
    events = events.copy()
    for column in (
        "source_rank", "selected_source", "accepted_rank", "selected_accepted",
        "same_time_candidates", "same_time_accepted_candidates",
    ):
        events[column] = 0
    for _, group in events.groupby("decision_time_ns", sort=True):
        source_order = sorted(
            group.index,
            key=lambda index: (
                -float(events.at[index, "source_score"]),
                SYMBOL_PRIORITY.get(str(events.at[index, "symbol"]), 99),
                int(events.at[index, "signal_time_ns"]),
            ),
        )
        for rank, index in enumerate(source_order, start=1):
            events.at[index, "source_rank"] = rank
            events.at[index, "same_time_candidates"] = len(source_order)
        if source_order:
            events.at[source_order[0], "selected_source"] = 1
        accepted_order = [
            index for index in source_order if int(events.at[index, "accepted"]) == 1
        ]
        for rank, index in enumerate(accepted_order, start=1):
            events.at[index, "accepted_rank"] = rank
            events.at[index, "same_time_accepted_candidates"] = len(accepted_order)
        if accepted_order:
            events.at[accepted_order[0], "selected_accepted"] = 1
    return events


def summarize_group(group: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {
        "episodes": int(len(group)),
        "symbols": group["symbol"].value_counts().to_dict(),
        "selected_source": int(group["selected_source"].sum()),
        "selected_accepted": int(group["selected_accepted"].sum()),
    }
    for horizon in HORIZONS_MINUTES:
        mfe = pd.to_numeric(group[f"mfe_{horizon}m_net_bps"], errors="coerce")
        mae = pd.to_numeric(group[f"mae_{horizon}m_with_cost_bps"], errors="coerce")
        close = pd.to_numeric(group[f"close_{horizon}m_net_bps"], errors="coerce")
        result.update(
            {
                f"mfe_{horizon}m_mean_net_bps": number_or_none(mfe.mean()),
                f"mfe_{horizon}m_median_net_bps": number_or_none(mfe.median()),
                f"mfe_{horizon}m_cost_cover_fraction": float((mfe > 0.0).mean()),
                f"mae_{horizon}m_mean_with_cost_bps": number_or_none(mae.mean()),
                f"close_{horizon}m_mean_net_bps": number_or_none(close.mean()),
                f"close_{horizon}m_positive_fraction": float((close > 0.0).mean()),
            }
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
        raise RuntimeError("no V15 Bollinger short interaction episodes were recorded")
    events = mark_arbitration(events)
    events.to_csv(output / "episodes.csv", index=False)

    by_state = {
        str(state): summarize_group(group)
        for state, group in events.groupby("transition_state", sort=True)
    }
    accepted = events[events["accepted"] == 1]
    rejected = events[events["accepted"] == 0]
    accepted_summary = summarize_group(accepted) if len(accepted) else {"episodes": 0}
    rejected_summary = summarize_group(rejected) if len(rejected) else {"episodes": 0}
    source_selected = events[events["selected_source"] == 1]
    accepted_selected = events[events["selected_accepted"] == 1]
    source_summary = summarize_group(source_selected) if len(source_selected) else {"episodes": 0}
    accepted_selected_summary = summarize_group(accepted_selected) if len(accepted_selected) else {"episodes": 0}

    accepted_cover = float(accepted_summary.get("mfe_60m_cost_cover_fraction") or 0.0)
    rejected_cover = float(rejected_summary.get("mfe_60m_cost_cover_fraction") or 0.0)
    accepted_close = float(accepted_summary.get("close_60m_mean_net_bps") or 0.0)
    rejected_close = float(rejected_summary.get("close_60m_mean_net_bps") or 0.0)
    prediction_checks = {
        "at_least_five_accepted_episodes": int(accepted_summary.get("episodes", 0)) >= 5,
        "accepted_cost_cover_advantage_at_least_15pct": accepted_cover - rejected_cover >= 0.15,
        "accepted_close_60m_advantage_at_least_15bps": accepted_close - rejected_close >= 15.0,
        "accepted_close_60m_positive": accepted_close > 0.0,
        "accepted_median_mfe_60m_positive": float(accepted_summary.get("mfe_60m_median_net_bps") or -math.inf) > 0.0,
    }
    days = (end - start).days + 1
    result = {
        "candidate": "candidate-55",
        "family": "V15_BB_SHORT_ACCEPTANCE_TRANSITION_FORENSICS",
        "interval": [start.isoformat(), end.isoformat()],
        "calendar_days": days,
        "result_blind_decision_then_outcome": True,
        "execution_backtest": False,
        "one_bollinger_cross_one_episode": True,
        "all_symbols_arbitrated_before_outcome": True,
        "cost_floor_bps": COST_FLOOR_BPS,
        "confirmation_bars": CONFIRMATION_BARS,
        "confirmation_minutes": CONFIRMATION_BARS * 5,
        "source_stop_fraction": SOURCE_STOP_FRACTION,
        "source_trail_activation_fraction": SOURCE_TRAIL_ACTIVATION_FRACTION,
        "hypothesis": (
            "The lower-band cross is an interaction, not an entry. A short should be owned only after the auction "
            "remains accepted below the signal close for one three-bar transition without invalidating or consuming the source move."
        ),
        "falsification": (
            "The explanation fails if accepted episodes do not improve both cost-covering MFE and close follow-through, "
            "or if the apparent benefit comes only from deleting exhausted winners rather than separating failed auctions."
        ),
        "episodes": int(len(events)),
        "episodes_per_day": float(len(events) / days),
        "accepted_episodes_per_day": float(len(accepted) / days),
        "same_timestamp_conflict_episodes": int((events["same_time_candidates"] > 1).groupby(events["decision_time_ns"]).max().sum()),
        "by_transition_state": by_state,
        "accepted": accepted_summary,
        "rejected": rejected_summary,
        "source_arbitration": source_summary,
        "accepted_arbitration": accepted_selected_summary,
        "prediction_checks": prediction_checks,
        "prediction_supported_in_this_window": all(prediction_checks.values()),
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
