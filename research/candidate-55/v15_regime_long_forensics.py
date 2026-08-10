"""Result-blind clean-up audit for the complementary V15 Bollinger long family.

The short clean-auction study tests whether a known high-capacity loss engine can
be repaired.  This diagnostic asks the orthogonal coverage question required by
a regime-robust integrated system: does the same source Bollinger interaction
contain a clean-up continuation engine when the higher-timeframe auction is
``trending_up_clean``?

No account is simulated and no threshold is searched.  Every long Bollinger
edge is frozen from completed five-minute bars, assigned the latest completed
30-minute state, arbitrated across the four symbols, and only then exposed to
future 15/30/60/120-minute paths.  A Nautilus implementation is warranted only
if the predeclared clean-up group repeatedly dominates the other states while
retaining target-relevant opportunity density.
"""
from __future__ import annotations

from bisect import bisect_right
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
from v15_regime_state import (
    RegimeSnapshot,
    RegimeThresholds,
    TRENDING_UP_CLEAN,
    regime_series,
)


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
SYMBOL_PRIORITY = {symbol: index for index, symbol in enumerate(SYMBOLS)}
HORIZONS_MINUTES = (15, 30, 60, 120)
COST_FLOOR_BPS = 20.0
WARMUP_DAYS = 2


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


def empty_snapshot() -> RegimeSnapshot:
    return RegimeSnapshot(
        0, False, "ranging_quiet",
        math.nan, math.nan, math.nan, math.nan,
        math.nan, math.nan, math.nan, math.nan,
    )


def regime_for_time(
    snapshots: list[RegimeSnapshot],
    times: list[int],
    ts_event: int,
) -> RegimeSnapshot:
    index = bisect_right(times, int(ts_event)) - 1
    return snapshots[index] if index >= 0 else empty_snapshot()


def future_path(
    minute_bars: list[BarObservation],
    minute_index_by_time: dict[int, int],
    signal_time_ns: int,
) -> dict[str, float]:
    signal_index = minute_index_by_time.get(int(signal_time_ns))
    result: dict[str, float] = {}
    if signal_index is None or signal_index + 1 >= len(minute_bars):
        for horizon in HORIZONS_MINUTES:
            result[f"mfe_{horizon}m_net_bps"] = math.nan
            result[f"mae_{horizon}m_with_cost_bps"] = math.nan
            result[f"close_{horizon}m_net_bps"] = math.nan
        return result
    entry_index = signal_index + 1
    entry = float(minute_bars[entry_index].open)
    result["entry_next_minute_open"] = entry
    for horizon in HORIZONS_MINUTES:
        window = minute_bars[entry_index : entry_index + horizon]
        if len(window) != horizon or entry <= 0.0:
            result[f"mfe_{horizon}m_net_bps"] = math.nan
            result[f"mae_{horizon}m_with_cost_bps"] = math.nan
            result[f"close_{horizon}m_net_bps"] = math.nan
            continue
        high = max(float(item.high) for item in window)
        low = min(float(item.low) for item in window)
        close = float(window[-1].close)
        result[f"mfe_{horizon}m_net_bps"] = (
            (high - entry) / entry * 10_000.0 - COST_FLOOR_BPS
        )
        result[f"mae_{horizon}m_with_cost_bps"] = (
            (entry - low) / entry * 10_000.0 + COST_FLOOR_BPS
        )
        result[f"close_{horizon}m_net_bps"] = (
            (close - entry) / entry * 10_000.0 - COST_FLOOR_BPS
        )
    return result


def signal_rows_for_symbol(
    *,
    symbol: str,
    frame: pd.DataFrame,
    evaluation_start: date,
    evaluation_end: date,
    regime_bucket_minutes: int,
    regime_period: int,
) -> list[dict[str, Any]]:
    minute_bars = bars_from_frame(frame)
    minute_index = {int(item.ts_event): index for index, item in enumerate(minute_bars)}
    candles_5m = _aggregate_complete(minute_bars, 5)
    regime_candles = _aggregate_complete(minute_bars, int(regime_bucket_minutes))
    snapshots = regime_series(
        regime_candles,
        period=int(regime_period),
        thresholds=RegimeThresholds(),
    )
    regime_times = [int(item.observed_time_ns) for item in snapshots]

    period = 14
    lower, _, upper = _bollinger(candles_5m, 20)
    plus_di, minus_di = _directional_indicators(candles_5m, period)
    dx, adx = _adx_dx(plus_di, minus_di, period)
    atr = _atr(candles_5m, period)
    obv = _obv(candles_5m)
    mfi = _mfi(candles_5m, period)

    start_ns = int(pd.Timestamp(evaluation_start, tz="UTC").value)
    end_ns = int((pd.Timestamp(evaluation_end + timedelta(days=1), tz="UTC") - pd.Timedelta(nanoseconds=1)).value)
    rows: list[dict[str, Any]] = []
    minimum = max(2 * period + 3, 23)
    for index in range(minimum, len(candles_5m)):
        current = candles_5m[index]
        previous = candles_5m[index - 1]
        ts_event = int(current.ts_event)
        if not (start_ns <= ts_event <= end_ns):
            continue
        required = (
            lower[index], lower[index - 1], upper[index], upper[index - 1],
            dx[index], adx[index], plus_di[index], minus_di[index], atr[index],
            obv[index], obv[index - 1], mfi[index],
        )
        if not all(finite(value) for value in required):
            continue
        long_bb = (
            float(previous.close) <= float(upper[index - 1])
            and float(current.close) > float(upper[index])
        )
        if not long_bb:
            continue

        entry = float(current.close)
        directional_margin = min(
            float(dx[index]) - float(minus_di[index]),
            float(adx[index]) - float(minus_di[index]),
            float(plus_di[index]) - float(minus_di[index]),
        )
        breakout_bps = (entry - float(upper[index])) / entry * 10_000.0
        mfi_margin = abs(float(mfi[index]) - 50.0)
        score = (
            1.0
            + 2.5
            + min(4.0, max(0.0, directional_margin) / 3.0)
            + min(3.0, max(0.0, breakout_bps) / 12.0)
            + min(2.0, mfi_margin / 15.0)
        )
        snapshot = regime_for_time(snapshots, regime_times, ts_event)
        item: dict[str, Any] = {
            "symbol": symbol,
            "decision_time_ns": ts_event,
            "decision_time": pd.Timestamp(ts_event, unit="ns", tz="UTC").isoformat(),
            "episode_key": f"{symbol}:BB_LONG:{ts_event}",
            "source_component": "BB",
            "source_side": 1,
            "source_score": float(score),
            "source_breakout_bps": float(breakout_bps),
            "source_directional_margin": float(directional_margin),
            "source_mfi_margin": float(mfi_margin),
            "regime_ready": int(snapshot.ready),
            "regime_label": snapshot.label,
            "clean_up_eligible": int(snapshot.ready and snapshot.label == TRENDING_UP_CLEAN),
            "regime_observed_time_ns": int(snapshot.observed_time_ns),
            "regime_return_eff": snapshot.return_eff,
            "regime_range_eff": snapshot.range_eff,
            "regime_efficiency": snapshot.efficiency,
            "regime_adx": snapshot.adx,
            "regime_plus_di": snapshot.plus_di,
            "regime_minus_di": snapshot.minus_di,
            "regime_atr_fraction": snapshot.atr_fraction,
            "regime_window_net_fraction": snapshot.window_net_fraction,
        }
        item.update(future_path(minute_bars, minute_index, ts_event))
        rows.append(item)
    return rows


def mark_arbitration(events: pd.DataFrame) -> pd.DataFrame:
    events = events.copy()
    for column in (
        "source_rank", "selected_source", "clean_rank", "selected_clean",
        "same_time_candidates", "same_time_clean_candidates",
    ):
        events[column] = 0
    for _, group in events.groupby("decision_time_ns", sort=True):
        source_order = sorted(
            group.index,
            key=lambda index: (
                -float(events.at[index, "source_score"]),
                SYMBOL_PRIORITY.get(str(events.at[index, "symbol"]), 99),
            ),
        )
        for rank, index in enumerate(source_order, start=1):
            events.at[index, "source_rank"] = rank
            events.at[index, "same_time_candidates"] = len(source_order)
        if source_order:
            events.at[source_order[0], "selected_source"] = 1
        clean_order = [
            index for index in source_order
            if int(events.at[index, "clean_up_eligible"]) == 1
        ]
        for rank, index in enumerate(clean_order, start=1):
            events.at[index, "clean_rank"] = rank
            events.at[index, "same_time_clean_candidates"] = len(clean_order)
        if clean_order:
            events.at[clean_order[0], "selected_clean"] = 1
    return events


def number_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def summarize_group(group: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {
        "episodes": int(len(group)),
        "symbols": group["symbol"].value_counts().to_dict(),
        "selected_source": int(group["selected_source"].sum()),
        "selected_clean": int(group["selected_clean"].sum()),
    }
    for horizon in HORIZONS_MINUTES:
        mfe = group[f"mfe_{horizon}m_net_bps"]
        close = group[f"close_{horizon}m_net_bps"]
        mae = group[f"mae_{horizon}m_with_cost_bps"]
        result.update(
            {
                f"mfe_{horizon}m_mean_net_bps": number_or_none(mfe.mean()),
                f"mfe_{horizon}m_median_net_bps": number_or_none(mfe.median()),
                f"mfe_{horizon}m_cost_cover_fraction": float((mfe > 0.0).mean()),
                f"close_{horizon}m_mean_net_bps": number_or_none(close.mean()),
                f"close_{horizon}m_positive_fraction": float((close > 0.0).mean()),
                f"mae_{horizon}m_mean_with_cost_bps": number_or_none(mae.mean()),
            }
        )
    return result


def run(
    *,
    start: date,
    end: date,
    cache: Path,
    output: Path,
    regime_bucket_minutes: int = 30,
    regime_period: int = 21,
) -> dict[str, Any]:
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
                regime_bucket_minutes=regime_bucket_minutes,
                regime_period=regime_period,
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
        raise RuntimeError("no V15 Bollinger long episodes were recorded")
    events = mark_arbitration(events)
    events.to_csv(output / "episodes.csv", index=False)

    by_label = {
        str(label): summarize_group(group)
        for label, group in events.groupby("regime_label", sort=True)
    }
    clean = events[events["regime_label"] == TRENDING_UP_CLEAN]
    other = events[events["regime_label"] != TRENDING_UP_CLEAN]
    selected_source = events[events["selected_source"] == 1]
    selected_clean = events[events["selected_clean"] == 1]
    clean_summary = summarize_group(clean) if len(clean) else {"episodes": 0}
    other_summary = summarize_group(other) if len(other) else {"episodes": 0}
    source_summary = summarize_group(selected_source) if len(selected_source) else {"episodes": 0}
    selected_clean_summary = summarize_group(selected_clean) if len(selected_clean) else {"episodes": 0}

    clean_cost_cover = float(clean_summary.get("mfe_60m_cost_cover_fraction") or 0.0)
    other_cost_cover = float(other_summary.get("mfe_60m_cost_cover_fraction") or 0.0)
    clean_close = float(clean_summary.get("close_60m_mean_net_bps") or 0.0)
    other_close = float(other_summary.get("close_60m_mean_net_bps") or 0.0)
    prediction_checks = {
        "at_least_five_clean_episodes": int(clean_summary.get("episodes", 0)) >= 5,
        "clean_cost_cover_advantage_at_least_10pct": clean_cost_cover - other_cost_cover >= 0.10,
        "clean_close_60m_advantage_at_least_10bps": clean_close - other_close >= 10.0,
        "clean_median_mfe_60m_positive": float(clean_summary.get("mfe_60m_median_net_bps") or -math.inf) > 0.0,
    }
    result = {
        "candidate": "candidate-55",
        "family": "V15_BB_LONG_TREND_QUALITY_FORENSICS",
        "interval": [start.isoformat(), end.isoformat()],
        "result_blind_decision_then_outcome": True,
        "execution_backtest": False,
        "one_bollinger_cross_one_episode": True,
        "all_symbols_arbitrated_before_outcome": True,
        "cost_floor_bps": COST_FLOOR_BPS,
        "regime_bucket_minutes": int(regime_bucket_minutes),
        "regime_period": int(regime_period),
        "fixed_thresholds": asdict(RegimeThresholds()),
        "hypothesis": (
            "A complementary V15 Bollinger long opportunity engine should concentrate in clean upside price discovery."
        ),
        "falsification": (
            "The family is not implemented if clean-up episodes fail to materially improve cost-covering MFE and "
            "60-minute close follow-through, or if opportunity density is structurally negligible."
        ),
        "episodes": int(len(events)),
        "same_timestamp_conflict_episodes": int((events["same_time_candidates"] > 1).groupby(events["decision_time_ns"]).max().sum()),
        "by_regime_label": by_label,
        "clean_up": clean_summary,
        "all_other_states": other_summary,
        "source_arbitration": source_summary,
        "clean_arbitration": selected_clean_summary,
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
    parser.add_argument("--regime-bucket-minutes", type=int, default=30)
    parser.add_argument("--regime-period", type=int, default=21)
    args = parser.parse_args()
    result = run(
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        cache=args.cache,
        output=args.output,
        regime_bucket_minutes=args.regime_bucket_minutes,
        regime_period=args.regime_period,
    )
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
