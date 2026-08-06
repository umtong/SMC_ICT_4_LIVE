"""Causal diagnostic for quarter-hour order-flow and external-liquidity hypotheses.

This is not a backtest engine. It measures predeclared signal/outcome relationships on
official Binance inputs before the hypothesis is translated into NautilusTrader orders.
All trade execution validation remains in NautilusTrader.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import timedelta
import json
from math import isfinite
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd
from nautilus_trader.model.data import BarType

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from data import load_official_binance_bars  # noqa: E402
from run import _build_instrument, _parse_utc  # noqa: E402


@dataclass(frozen=True, slots=True)
class ProbeSpec:
    name: str
    require_lag_alignment: bool = False
    top_of_hour_only: bool = False
    require_session_break: bool = False
    require_session_sweep: bool = False


SPECS = (
    ProbeSpec("QH_FLOW_PRICE_ALIGNMENT"),
    ProbeSpec("QH_FLOW_LAG_ALIGNMENT", require_lag_alignment=True),
    ProbeSpec("TOP_HOUR_FLOW_LAG", require_lag_alignment=True, top_of_hour_only=True),
    ProbeSpec("SESSION_BREAK_ACCEPTANCE", require_lag_alignment=True, require_session_break=True),
    ProbeSpec("SESSION_SWEEP_REJECTION", require_session_sweep=True),
)


def _numeric_frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in (
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "trade_count",
        "taker_buy_volume",
        "taker_buy_quote_volume",
    ):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.dropna(
        subset=["open", "high", "low", "close", "volume", "trade_count", "taker_buy_volume"]
    )
    return result


def _features(frame: pd.DataFrame) -> pd.DataFrame:
    f = _numeric_frame(frame)
    previous_close = f["close"].shift(1)
    true_range = pd.concat(
        [
            f["high"] - f["low"],
            (f["high"] - previous_close).abs(),
            (f["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    f["atr20"] = true_range.rolling(20, min_periods=20).mean()
    f["volume_median60"] = f["volume"].shift(1).rolling(60, min_periods=30).median()
    f["trades_median60"] = f["trade_count"].shift(1).rolling(60, min_periods=30).median()
    f["volume_ratio"] = f["volume"] / f["volume_median60"].replace(0, np.nan)
    f["trade_ratio"] = f["trade_count"] / f["trades_median60"].replace(0, np.nan)
    signed = 2.0 * f["taker_buy_volume"] - f["volume"]
    f["imbalance"] = signed / f["volume"].replace(0, np.nan)
    f["opening_return"] = (f["close"] - f["open"]) / f["open"]
    f["body_atr"] = (f["close"] - f["open"]).abs() / f["atr20"].replace(0, np.nan)
    f["close_location"] = (f["close"] - f["low"]) / (f["high"] - f["low"]).replace(0, np.nan)

    session = f.index.floor("4h")
    grouped = f.groupby(session, sort=True)
    session_table = grouped.agg(
        session_open=("open", "first"),
        session_high=("high", "max"),
        session_low=("low", "min"),
        session_close=("close", "last"),
    )
    session_table["previous_session_open"] = session_table["session_open"].shift(1)
    session_table["previous_session_high"] = session_table["session_high"].shift(1)
    session_table["previous_session_low"] = session_table["session_low"].shift(1)
    session_table["previous_session_close"] = session_table["session_close"].shift(1)
    session_table["previous_session_direction"] = np.sign(
        session_table["previous_session_close"] - session_table["previous_session_open"]
    )
    for column in (
        "previous_session_open",
        "previous_session_high",
        "previous_session_low",
        "previous_session_close",
        "previous_session_direction",
    ):
        f[column] = session.map(session_table[column])

    qh = f.index.minute.isin((0, 15, 30, 45))
    f["quarter_hour"] = qh
    boundary_imbalance = f["imbalance"].where(qh)
    f["boundary_lag_mean4"] = boundary_imbalance.shift(1).rolling(61, min_periods=2).apply(
        lambda values: float(pd.Series(values).dropna().tail(4).mean())
        if pd.Series(values).dropna().size
        else np.nan,
        raw=False,
    )
    return f


def _side(row: pd.Series, spec: ProbeSpec) -> int:
    if not bool(row["quarter_hour"]):
        return 0
    if spec.top_of_hour_only and int(row.name.minute) != 0:
        return 0
    atr = float(row["atr20"])
    if not isfinite(atr) or atr <= 0:
        return 0
    imbalance = float(row["imbalance"])
    opening_move = float(row["close"] - row["open"])
    volume_ratio = float(row["volume_ratio"])
    trade_ratio = float(row["trade_ratio"])
    body_atr = abs(opening_move) / atr
    if not all(isfinite(value) for value in (imbalance, opening_move, volume_ratio, trade_ratio, body_atr)):
        return 0
    if abs(imbalance) < 0.08 or volume_ratio < 1.10 or trade_ratio < 1.05 or body_atr < 0.12:
        return 0
    direction = 1 if imbalance > 0 else -1
    if opening_move * direction <= 0:
        return 0
    if direction > 0 and float(row["close_location"]) < 0.62:
        return 0
    if direction < 0 and float(row["close_location"]) > 0.38:
        return 0

    lag = float(row["boundary_lag_mean4"])
    if spec.require_lag_alignment and (not isfinite(lag) or lag * direction <= 0):
        return 0

    previous_high = float(row["previous_session_high"])
    previous_low = float(row["previous_session_low"])
    if not isfinite(previous_high) or not isfinite(previous_low):
        return 0
    closes_outside = (
        float(row["close"]) > previous_high + 0.02 * atr
        if direction > 0
        else float(row["close"]) < previous_low - 0.02 * atr
    )
    if spec.require_session_break and not closes_outside:
        return 0
    if spec.require_session_sweep:
        if direction < 0:
            valid = float(row["high"]) > previous_high + 0.02 * atr and float(row["close"]) < previous_high
        else:
            valid = float(row["low"]) < previous_low - 0.02 * atr and float(row["close"]) > previous_low
        if not valid:
            return 0
    return direction


def _first_touch_outcome(
    future: pd.DataFrame,
    *,
    direction: int,
    entry: float,
    stop: float,
    target: float,
) -> str:
    for _, bar in future.iterrows():
        if direction > 0:
            stop_hit = float(bar["low"]) <= stop
            target_hit = float(bar["high"]) >= target
        else:
            stop_hit = float(bar["high"]) >= stop
            target_hit = float(bar["low"]) <= target
        if stop_hit and target_hit:
            return "STOP_AMBIGUOUS_FIRST"
        if stop_hit:
            return "STOP"
        if target_hit:
            return "TARGET"
    return "TIMEOUT"


def _event_record(frame: pd.DataFrame, position: int, direction: int, spec: ProbeSpec) -> dict[str, Any]:
    row = frame.iloc[position]
    atr = float(row["atr20"])
    entry = float(row["close"])
    minimum_stop = 0.35 * atr
    if direction > 0:
        stop = min(float(row["low"]) - 0.05 * atr, entry - minimum_stop)
    else:
        stop = max(float(row["high"]) + 0.05 * atr, entry + minimum_stop)
    risk = abs(entry - stop)
    future = frame.iloc[position + 1 : position + 241]
    record: dict[str, Any] = {
        "timestamp": row.name.isoformat(),
        "spec": spec.name,
        "direction": "LONG" if direction > 0 else "SHORT",
        "entry": entry,
        "stop": stop,
        "risk": risk,
        "imbalance": float(row["imbalance"]),
        "lag_mean4": float(row["boundary_lag_mean4"]),
        "volume_ratio": float(row["volume_ratio"]),
        "trade_ratio": float(row["trade_ratio"]),
        "body_atr": float(row["body_atr"]),
        "previous_session_high": float(row["previous_session_high"]),
        "previous_session_low": float(row["previous_session_low"]),
        "previous_session_direction": float(row["previous_session_direction"]),
    }
    for horizon in (15, 30, 60, 120, 240):
        segment = frame.iloc[position + 1 : position + 1 + horizon]
        if segment.empty:
            continue
        final = float(segment.iloc[-1]["close"])
        signed_return = direction * (final - entry) / entry
        if direction > 0:
            mfe = (float(segment["high"].max()) - entry) / risk
            mae = (entry - float(segment["low"].min())) / risk
        else:
            mfe = (entry - float(segment["low"].min())) / risk
            mae = (float(segment["high"].max()) - entry) / risk
        record[f"return_{horizon}m"] = signed_return
        record[f"mfe_{horizon}m_r"] = mfe
        record[f"mae_{horizon}m_r"] = mae
    for multiple in (1.5, 2.0, 3.0):
        target = entry + direction * multiple * risk
        record[f"outcome_{multiple:.1f}r_240m"] = _first_touch_outcome(
            future,
            direction=direction,
            entry=entry,
            stop=stop,
            target=target,
        )
    return record


def _summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    if not events:
        return {"events": 0}
    result: dict[str, Any] = {"events": len(events)}
    for horizon in (15, 30, 60, 120, 240):
        returns = [float(item[f"return_{horizon}m"]) for item in events if f"return_{horizon}m" in item]
        mfes = [float(item[f"mfe_{horizon}m_r"]) for item in events if f"mfe_{horizon}m_r" in item]
        maes = [float(item[f"mae_{horizon}m_r"]) for item in events if f"mae_{horizon}m_r" in item]
        if returns:
            result[f"horizon_{horizon}m"] = {
                "mean_signed_return": float(np.mean(returns)),
                "median_signed_return": float(np.median(returns)),
                "positive_share": float(np.mean(np.asarray(returns) > 0)),
                "median_mfe_r": float(np.median(mfes)),
                "median_mae_r": float(np.median(maes)),
            }
    for multiple in (1.5, 2.0, 3.0):
        key = f"outcome_{multiple:.1f}r_240m"
        outcomes = [item[key] for item in events]
        result[f"{multiple:.1f}r_240m"] = {
            value: outcomes.count(value) for value in sorted(set(outcomes))
        }
    return result


def run(config_path: Path, output: Path, data_cache: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    instrument = _build_instrument(config)
    bar_type = BarType.from_str(str(config["bar_type"]))
    output.mkdir(parents=True, exist_ok=True)
    all_windows: dict[str, Any] = {}
    for window in config["suites"]["screen"]:
        start = _parse_utc(window["start"])
        end = _parse_utc(window["end"])
        loaded = load_official_binance_bars(
            symbol="BTCUSDT",
            interval="1m",
            load_start=start - timedelta(days=7),
            load_end=end + timedelta(hours=4),
            bar_type=bar_type,
            instrument=instrument,
            cache_dir=data_cache,
        )
        frame = _features(loaded.frame)
        evaluation = frame.loc[(frame.index >= start) & (frame.index < end)]
        records_by_spec: dict[str, list[dict[str, Any]]] = {spec.name: [] for spec in SPECS}
        positions = {timestamp: int(frame.index.get_loc(timestamp)) for timestamp in evaluation.index}
        for timestamp, row in evaluation.iterrows():
            position = positions[timestamp]
            for spec in SPECS:
                direction = _side(row, spec)
                if direction:
                    records_by_spec[spec.name].append(_event_record(frame, position, direction, spec))
        window_result = {
            "window": window,
            "data_quality": loaded.quality,
            "specs": {
                spec.name: {
                    "definition": asdict(spec),
                    "summary": _summarize(records_by_spec[spec.name]),
                    "events": records_by_spec[spec.name],
                }
                for spec in SPECS
            },
        }
        all_windows[window["name"]] = window_result
        (output / f"{window['name']}.json").write_text(
            json.dumps(window_result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    combined: dict[str, Any] = {}
    for spec in SPECS:
        events = [
            event
            for window in all_windows.values()
            for event in window["specs"][spec.name]["events"]
        ]
        combined[spec.name] = _summarize(events)
    result = {
        "candidate": "candidate-08-quarter-hour-probe",
        "purpose": "causal feature/outcome diagnostic; not execution evidence",
        "fixed_specs": [asdict(spec) for spec in SPECS],
        "combined": combined,
        "windows": {
            name: {
                "window": value["window"],
                "spec_summaries": {
                    spec: payload["summary"] for spec, payload in value["specs"].items()
                },
            }
            for name, value in all_windows.items()
        },
    }
    (output / "summary.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-cache", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.config.resolve(), args.output.resolve(), args.data_cache.resolve())
    print(json.dumps(result["combined"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
