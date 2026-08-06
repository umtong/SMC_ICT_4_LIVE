"""Causal 5-minute completed-range/FVG context plus 1-minute MSS trigger probe.

This is a path diagnostic, not an exchange simulator. The completed-range detector is unchanged.
After consequent-encroachment is traded, a later one-minute displacement must break the preceding
three-bar micro structure with aligned taker imbalance and activity. Cost-after external-target
geometry and conservative first-touch paths are then measured before NautilusTrader promotion.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import timedelta
import json
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
from range_fvg_logic import Direction, RangeFVGConfig, RangeFVGSignal, build_range_fvg_signals  # noqa: E402
from run import _build_instrument, _ns, _parse_utc  # noqa: E402


FEE_RATE = 0.0006
TICK = 0.1


def _features(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    for column in ("open", "high", "low", "close", "volume", "trade_count", "taker_buy_volume"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=["open", "high", "low", "close", "volume", "trade_count", "taker_buy_volume"])
    previous = data["close"].shift(1)
    true_range = pd.concat(
        [
            data["high"] - data["low"],
            (data["high"] - previous).abs(),
            (data["low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    data["atr1m"] = true_range.rolling(20, min_periods=20).mean()
    data["volume_ratio1m"] = data["volume"] / data["volume"].shift(1).rolling(60, min_periods=30).median().replace(0, np.nan)
    data["trade_ratio1m"] = data["trade_count"] / data["trade_count"].shift(1).rolling(60, min_periods=30).median().replace(0, np.nan)
    data["imbalance1m"] = (2.0 * data["taker_buy_volume"] - data["volume"]) / data["volume"].replace(0, np.nan)
    spread = data["high"] - data["low"]
    data["close_location1m"] = (data["close"] - data["low"]) / spread.replace(0, np.nan)
    return data


def _direction(signal: RangeFVGSignal) -> int:
    return 1 if signal.direction is Direction.LONG else -1


def _target_hit(signal: RangeFVGSignal, row: pd.Series) -> bool:
    return float(row["high"]) >= signal.external_target if _direction(signal) > 0 else float(row["low"]) <= signal.external_target


def _invalidated(signal: RangeFVGSignal, row: pd.Series) -> bool:
    return float(row["low"]) <= signal.invalidation_before_fill if _direction(signal) > 0 else float(row["high"]) >= signal.invalidation_before_fill


def _touches_ce(signal: RangeFVGSignal, row: pd.Series) -> bool:
    return float(row["low"]) <= signal.limit_entry if _direction(signal) > 0 else float(row["high"]) >= signal.limit_entry


def _micro_trigger(data: pd.DataFrame, position: int, direction: int) -> bool:
    if position < 3:
        return False
    row = data.iloc[position]
    prior = data.iloc[position - 3 : position]
    atr = float(row["atr1m"])
    values = (
        atr,
        float(row["imbalance1m"]),
        float(row["volume_ratio1m"]),
        float(row["trade_ratio1m"]),
        float(row["close_location1m"]),
    )
    if not all(np.isfinite(value) for value in values) or atr <= 0:
        return False
    directional_body = direction * float(row["close"] - row["open"])
    directional_flow = direction * float(row["imbalance1m"])
    if direction > 0:
        structure_broken = float(row["close"]) > float(prior["high"].max())
        located = float(row["close_location1m"]) >= 0.65
    else:
        structure_broken = float(row["close"]) < float(prior["low"].min())
        located = float(row["close_location1m"]) <= 0.35
    return (
        structure_broken
        and located
        and directional_body >= 0.25 * atr
        and directional_flow >= 0.08
        and float(row["volume_ratio1m"]) >= 1.10
        and float(row["trade_ratio1m"]) >= 1.05
    )


def _net_per_unit(direction: int, entry: float, exit_price: float) -> float:
    return direction * (exit_price - entry) - FEE_RATE * (entry + exit_price) - 2.0 * TICK


def _first_touch(
    frame: pd.DataFrame,
    *,
    direction: int,
    stop: float,
    target: float,
) -> tuple[str, str | None]:
    for timestamp, row in frame.iterrows():
        stop_hit = float(row["low"]) <= stop if direction > 0 else float(row["high"]) >= stop
        target_hit = float(row["high"]) >= target if direction > 0 else float(row["low"]) <= target
        if stop_hit and target_hit:
            return "STOP_AMBIGUOUS_FIRST", timestamp.isoformat()
        if stop_hit:
            return "STOP", timestamp.isoformat()
        if target_hit:
            return "TARGET", timestamp.isoformat()
    return "TIMEOUT", None


def _evaluate_signal(
    signal: RangeFVGSignal,
    data: pd.DataFrame,
    *,
    maximum_touch_minutes: int = 30,
    maximum_trigger_minutes: int = 15,
) -> tuple[dict[str, Any] | None, str]:
    direction = _direction(signal)
    signal_time = pd.to_datetime(signal.signal_time_ns, unit="ns", utc=True)
    after_signal = data.loc[
        (data.index > signal_time)
        & (data.index <= signal_time + timedelta(minutes=maximum_touch_minutes))
    ]
    touch_position: int | None = None
    touch_time: pd.Timestamp | None = None
    for timestamp, row in after_signal.iterrows():
        if _target_hit(signal, row):
            return None, "TARGET_BEFORE_CE_TOUCH"
        if _invalidated(signal, row):
            return None, "INVALIDATED_BEFORE_CE_TOUCH"
        if _touches_ce(signal, row):
            touch_position = int(data.index.get_loc(timestamp))
            touch_time = timestamp
            break
    if touch_position is None or touch_time is None:
        return None, "NO_CE_TOUCH"

    trigger_end = min(len(data), touch_position + 1 + maximum_trigger_minutes)
    touch_extreme = (
        float(data.iloc[touch_position]["low"])
        if direction > 0
        else float(data.iloc[touch_position]["high"])
    )
    trigger_position: int | None = None
    terminal_reason: str | None = None
    for position in range(touch_position + 1, trigger_end):
        row = data.iloc[position]
        touch_extreme = (
            min(touch_extreme, float(row["low"]))
            if direction > 0
            else max(touch_extreme, float(row["high"]))
        )
        if _target_hit(signal, row):
            terminal_reason = "TARGET_BEFORE_MICRO_TRIGGER"
            break
        if _invalidated(signal, row):
            terminal_reason = "INVALIDATED_BEFORE_MICRO_TRIGGER"
            break
        if _micro_trigger(data, position, direction):
            trigger_position = position
            break
    if trigger_position is None:
        return None, terminal_reason or "NO_MICRO_TRIGGER"

    trigger = data.iloc[trigger_position]
    trigger_time = data.index[trigger_position]
    entry = float(trigger["close"]) + direction * TICK
    buffer = 0.05 * signal.atr
    minimum_distance = 0.15 * signal.atr
    if direction > 0:
        structural = touch_extreme - buffer
        stop = min(structural, entry - minimum_distance)
        valid = stop < entry < signal.external_target
    else:
        structural = touch_extreme + buffer
        stop = max(structural, entry + minimum_distance)
        valid = signal.external_target < entry < stop
    if not valid:
        return None, "INVALID_TRIGGER_GEOMETRY"

    expected_loss = -_net_per_unit(direction, entry, stop)
    expected_gain = _net_per_unit(direction, entry, signal.external_target)
    net_rr = expected_gain / expected_loss if expected_loss > 0 else float("-inf")
    future = data.iloc[trigger_position + 1 : trigger_position + 1 + 240]
    outcome, outcome_time = _first_touch(
        future,
        direction=direction,
        stop=stop,
        target=signal.external_target,
    )
    if outcome == "TARGET":
        net_r_proxy = net_rr
    elif outcome in ("STOP", "STOP_AMBIGUOUS_FIRST"):
        net_r_proxy = -1.0
    elif future.empty:
        net_r_proxy = 0.0
    else:
        net_r_proxy = _net_per_unit(direction, entry, float(future.iloc[-1]["close"])) / expected_loss
    favorable = (
        float(future["high"].max()) if direction > 0 else float(future["low"].min())
    ) if not future.empty else entry
    adverse = (
        float(future["low"].min()) if direction > 0 else float(future["high"].max())
    ) if not future.empty else entry
    return {
        "scenario_id": signal.scenario_id,
        "family": signal.family.value,
        "direction": signal.direction.value,
        "base_signal_time": signal_time.isoformat(),
        "ce_touch_time": touch_time.isoformat(),
        "trigger_time": trigger_time.isoformat(),
        "minutes_signal_to_touch": (touch_time - signal_time).total_seconds() / 60.0,
        "minutes_touch_to_trigger": (trigger_time - touch_time).total_seconds() / 60.0,
        "boundary_source": signal.boundary_source.value,
        "boundary_level": signal.boundary_level,
        "target_source": signal.external_target_source.value,
        "entry": entry,
        "stop": stop,
        "target": signal.external_target,
        "expected_loss_per_unit": expected_loss,
        "expected_gain_per_unit": expected_gain,
        "net_reward_risk": net_rr,
        "cost_after_geometry_passed": net_rr >= 1.20 and expected_gain > 0,
        "outcome_240m": outcome,
        "outcome_time": outcome_time,
        "net_r_proxy_240m": net_r_proxy,
        "net_mfe_240m_r": _net_per_unit(direction, entry, favorable) / expected_loss,
        "net_mae_240m_r": -_net_per_unit(direction, entry, adverse) / expected_loss,
        "trigger_imbalance": float(trigger["imbalance1m"]),
        "trigger_volume_ratio": float(trigger["volume_ratio1m"]),
        "trigger_trade_ratio": float(trigger["trade_ratio1m"]),
        "trigger_body_atr1m": abs(float(trigger["close"] - trigger["open"])) / float(trigger["atr1m"]),
    }, "TRIGGERED"


def run(config_path: Path, output: Path, data_cache: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    window = config["suites"]["first"][0]
    start = _parse_utc(str(window["start"]))
    end = _parse_utc(str(window["end"]))
    instrument = _build_instrument(config)
    bar_type = BarType.from_str(str(config["bar_type"]))
    loaded = load_official_binance_bars(
        symbol="BTCUSDT",
        interval="1m",
        load_start=start - timedelta(days=10),
        load_end=end + timedelta(hours=4, minutes=10),
        bar_type=bar_type,
        instrument=instrument,
        cache_dir=data_cache,
    )
    data = _features(loaded.frame)
    bundle = build_range_fvg_signals(
        loaded.frame,
        RangeFVGConfig.from_mapping(dict(config["pattern"])),
    )
    signals = [
        signal
        for timestamp, items in bundle.signals_by_time_ns.items()
        if _ns(start) <= timestamp < _ns(end)
        for signal in items
    ]
    diagnostics: Counter[str] = Counter()
    records: list[dict[str, Any]] = []
    for signal in signals:
        record, reason = _evaluate_signal(signal, data)
        diagnostics[reason] += 1
        if record is not None:
            records.append(record)
    eligible = [record for record in records if record["cost_after_geometry_passed"]]
    outcomes = Counter(record["outcome_240m"] for record in eligible)
    proxies = np.asarray([record["net_r_proxy_240m"] for record in eligible], dtype=float)
    result = {
        "candidate": "candidate-08-range-fvg-ltf-probe",
        "purpose": "causal path diagnostic only; not NautilusTrader execution evidence",
        "window": window,
        "base_signals": len(signals),
        "diagnostic_counts": dict(sorted(diagnostics.items())),
        "micro_triggers": len(records),
        "cost_after_eligible": len(eligible),
        "eligible_outcomes_240m": dict(sorted(outcomes.items())),
        "eligible_total_net_r_proxy": float(proxies.sum()) if proxies.size else 0.0,
        "eligible_mean_net_r_proxy": float(proxies.mean()) if proxies.size else 0.0,
        "eligible_positive_share": float((proxies > 0).mean()) if proxies.size else 0.0,
        "eligible_median_net_rr": float(np.median([record["net_reward_risk"] for record in eligible])) if eligible else 0.0,
        "records": records,
        "data_quality": loaded.quality,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config_range_fvg.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-cache", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.config.resolve(), args.output.resolve(), args.data_cache.resolve())
    print(json.dumps({key: result[key] for key in (
        "base_signals",
        "diagnostic_counts",
        "micro_triggers",
        "cost_after_eligible",
        "eligible_outcomes_240m",
        "eligible_total_net_r_proxy",
        "eligible_mean_net_r_proxy",
        "eligible_positive_share",
        "eligible_median_net_rr",
    )}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
