"""Result-blind source-signal and false-negative audit for public ichiV1.

Every source entry episode and every one-component near miss is frozen from
completed 5-minute information.  Only then are future 15/30/60/120-minute paths
opened.  This diagnostic does not simulate an account and cannot establish
performance; it identifies whether the public selector contains a gross
opportunity engine and whether its no-trade decisions omit many cost-covering
moves.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date
import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from kline_only_inputs import load_range
from router import BarObservation, RouteConfig, _aggregate_complete, _state

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
PERIODS = (1, 3, 6, 12, 24, 48)
HORIZONS = (15, 30, 60, 120)
COST_BPS = 20.0


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


def component_mask(state: dict[str, list[float]], index: int, config: RouteConfig) -> tuple[bool, bool, bool, bool]:
    close = float(state["close"][index])
    span_a = float(state["span_a"][index])
    span_b = float(state["span_b"][index])
    fan = float(state["fan"][index])
    gain = float(state["fan_gain"][index])
    values = (close, span_a, span_b, fan, gain)
    if not all(math.isfinite(value) for value in values):
        return False, False, False, False
    cloud = close > span_a and close > span_b
    trend = all(
        math.isfinite(float(state[f"close_{period}"][index]))
        and math.isfinite(float(state[f"open_{period}"][index]))
        and float(state[f"close_{period}"][index]) > float(state[f"open_{period}"][index])
        for period in PERIODS
    )
    fan_gate = fan > 1.0 and gain >= config.ichi_min_fan_gain
    acceleration = all(
        math.isfinite(float(state["fan"][index - shift]))
        and fan > float(state["fan"][index - shift])
        for shift in range(1, config.ichi_fan_shift + 1)
    )
    return cloud, trend, fan_gate, acceleration


def future_path(candles: list[BarObservation], index: int) -> dict[str, float]:
    entry_index = index + 1
    entry = float(candles[entry_index].open)
    result: dict[str, float] = {"entry_next_open": entry}
    for horizon in HORIZONS:
        count = horizon // 5
        window = candles[entry_index : entry_index + count]
        if len(window) != count:
            result[f"mfe_{horizon}m_net_bps"] = math.nan
            result[f"mae_{horizon}m_with_cost_bps"] = math.nan
            result[f"close_{horizon}m_net_bps"] = math.nan
            continue
        high = max(float(item.high) for item in window)
        low = min(float(item.low) for item in window)
        close = float(window[-1].close)
        result[f"mfe_{horizon}m_net_bps"] = (high / entry - 1.0) * 10_000.0 - COST_BPS
        result[f"mae_{horizon}m_with_cost_bps"] = (1.0 - low / entry) * 10_000.0 + COST_BPS
        result[f"close_{horizon}m_net_bps"] = (close / entry - 1.0) * 10_000.0 - COST_BPS
    return result


def summarize(events: pd.DataFrame) -> dict[str, Any]:
    output: dict[str, Any] = {"episodes": int(len(events)), "by_decision": {}, "by_symbol": {}}
    for key, group in events.groupby("decision"):
        output["by_decision"][key] = {
            "episodes": int(len(group)),
            "symbols": group["symbol"].value_counts().to_dict(),
            "mfe_60m_mean_net_bps": float(group["mfe_60m_net_bps"].mean()),
            "mfe_60m_median_net_bps": float(group["mfe_60m_net_bps"].median()),
            "mfe_60m_cost_cover_fraction": float((group["mfe_60m_net_bps"] > 0.0).mean()),
            "close_60m_mean_net_bps": float(group["close_60m_net_bps"].mean()),
            "close_60m_positive_fraction": float((group["close_60m_net_bps"] > 0.0).mean()),
            "mfe_120m_mean_net_bps": float(group["mfe_120m_net_bps"].mean()),
            "close_120m_mean_net_bps": float(group["close_120m_net_bps"].mean()),
        }
    for symbol, group in events.groupby("symbol"):
        source = group[group["decision"] == "SOURCE_ENTRY"]
        output["by_symbol"][symbol] = {
            "all_diagnostic_episodes": int(len(group)),
            "source_episodes": int(len(source)),
            "source_mfe_60m_mean_net_bps": float(source["mfe_60m_net_bps"].mean()) if len(source) else None,
            "source_close_60m_mean_net_bps": float(source["close_60m_net_bps"].mean()) if len(source) else None,
        }
    return output


def run(start: date, end: date, cache: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    config = RouteConfig(
        bucket_minutes=5,
        ichi_conversion_period=20,
        ichi_base_period=60,
        ichi_span_period=120,
        ichi_displacement=30,
        ichi_above_cloud_level=1,
        ichi_bullish_level=6,
        ichi_fan_shift=3,
        ichi_min_fan_gain=1.002,
        ichi_exit_ema_period=24,
        ichi_allow_short=False,
        ichi_use_source_emergency_stop=True,
        ichi_source_stoploss=0.275,
    )
    records: list[dict[str, Any]] = []
    manifest: dict[str, Any] = {}
    for symbol in SYMBOLS:
        frame, _, files, evidence = load_range(
            symbol=symbol,
            start=start,
            end=end,
            cache=cache,
            output=output / "source" / symbol,
        )
        candles = _aggregate_complete(bars_from_frame(frame), 5)
        state = _state(candles, config)
        manifest[symbol] = {
            "minute_rows": len(frame),
            "complete_5m_candles": len(candles),
            "files": [str(path) for path in files],
            "evidence": [asdict(item) for item in evidence],
        }
        previous_label = ""
        for index in range(153, len(candles) - 25):
            mask = component_mask(state, index, config)
            count = sum(mask)
            if count == 4:
                decision = "SOURCE_ENTRY"
            elif count == 3:
                missing = ("CLOUD", "TREND", "FAN_GAIN", "ACCELERATION")[mask.index(False)]
                decision = f"NEAR_MISS_MISSING_{missing}"
            else:
                previous_label = ""
                continue
            # A continuous source/near-miss condition is one causal episode.
            if decision == previous_label:
                continue
            previous_label = decision
            item: dict[str, Any] = {
                "symbol": symbol,
                "decision_time": pd.Timestamp(candles[index].ts_event, unit="ns", tz="UTC").isoformat(),
                "decision": decision,
                "cloud_ok": int(mask[0]),
                "trend_ok": int(mask[1]),
                "fan_gate_ok": int(mask[2]),
                "acceleration_ok": int(mask[3]),
                "close": float(state["close"][index]),
                "span_a": float(state["span_a"][index]),
                "span_b": float(state["span_b"][index]),
                "fan_magnitude": float(state["fan"][index]),
                "fan_magnitude_gain": float(state["fan_gain"][index]),
            }
            item.update(future_path(candles, index))
            records.append(item)
    events = pd.DataFrame.from_records(records)
    if events.empty:
        raise RuntimeError("no source or near-miss episodes were recorded")
    events.to_csv(output / "episodes.csv", index=False)
    result = {
        "candidate": "candidate-55",
        "family": "PUBLIC_ICHI_V1_SELECTOR",
        "interval": [start.isoformat(), end.isoformat()],
        "result_blind_decision_then_outcome": True,
        "execution_backtest": False,
        "source_thresholds_changed": False,
        "one_continuous_condition_one_episode": True,
        "cost_floor_bps": COST_BPS,
        "summary": summarize(events),
        "data_manifest": manifest,
        "next_decision": (
            "Use account replay to test management only if source episodes show a repeated gross opportunity engine; "
            "use near-miss groups only to diagnose false negatives, never as a post-hoc entry rule."
        ),
    }
    (output / "SUMMARY.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2025-01-08")
    parser.add_argument("--end", default="2025-01-14")
    parser.add_argument("--cache", type=Path, default=Path(".cache/candidate-55-ichi-episodes"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/candidate-55/ichi-episode-forensics"))
    args = parser.parse_args()
    result = run(date.fromisoformat(args.start), date.fromisoformat(args.end), args.cache, args.output)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
