"""Causal top-hour burst -> contracted retrace -> reacceleration probe.

This diagnostic does not simulate an exchange and is not performance evidence. It tests whether
the quarter-hour directional effect can be converted into economically coherent entry geometry
before the exact sequence is implemented as NautilusTrader orders.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
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
from qh_probe import ProbeSpec, _features, _side  # noqa: E402
from run import _build_instrument, _parse_utc  # noqa: E402


@dataclass(frozen=True, slots=True)
class SequenceSpec:
    name: str
    intense_burst: bool = False
    require_session_break: bool = False
    require_previous_session_direction: bool = False
    maximum_retrace_bars: int = 6
    maximum_reacceleration_bars: int = 4


SPECS = (
    SequenceSpec("TOP_HOUR_CONTRACTED_RETRACE"),
    SequenceSpec("TOP_HOUR_INTENSE_CONTRACTED_RETRACE", intense_burst=True),
    SequenceSpec("TOP_HOUR_SESSION_BREAK_RETEST", require_session_break=True),
    SequenceSpec(
        "TOP_HOUR_SESSION_ALIGNED_RETRACE",
        intense_burst=True,
        require_previous_session_direction=True,
    ),
)

BASE_BURST_SPEC = ProbeSpec(
    name="TOP_HOUR_FLOW_LAG",
    require_lag_alignment=True,
    top_of_hour_only=True,
)
FEE_RATE_PER_FILL = 0.0006
TICK_SIZE = 0.1


def _burst_side(row: pd.Series, spec: SequenceSpec) -> int:
    direction = _side(row, BASE_BURST_SPEC)
    if direction == 0:
        return 0
    atr = float(row["atr20"])
    if spec.intense_burst and not (
        abs(float(row["imbalance"])) >= 0.18
        and float(row["volume_ratio"]) >= 1.50
        and float(row["trade_ratio"]) >= 1.25
        and float(row["body_atr"]) >= 0.50
    ):
        return 0
    previous_high = float(row["previous_session_high"])
    previous_low = float(row["previous_session_low"])
    if spec.require_session_break:
        closes_outside = (
            float(row["close"]) > previous_high + 0.02 * atr
            if direction > 0
            else float(row["close"]) < previous_low - 0.02 * atr
        )
        if not closes_outside:
            return 0
    if spec.require_previous_session_direction:
        previous_direction = int(np.sign(float(row["previous_session_direction"])))
        if previous_direction != direction:
            return 0
    return direction


def _contracted_retrace(
    frame: pd.DataFrame,
    burst_position: int,
    direction: int,
    maximum_bars: int,
) -> int | None:
    burst = frame.iloc[burst_position]
    atr = float(burst["atr20"])
    body = abs(float(burst["close"] - burst["open"]))
    burst_volume = float(burst["volume"])
    burst_trades = float(burst["trade_count"])
    burst_imbalance = abs(float(burst["imbalance"]))
    if body <= 0 or atr <= 0:
        return None

    for position in range(burst_position + 1, min(len(frame), burst_position + 1 + maximum_bars)):
        bar = frame.iloc[position]
        if direction > 0:
            reached_value = float(bar["low"]) <= float(burst["close"]) - 0.25 * body
            origin_held = float(bar["close"]) >= float(burst["open"]) - 0.10 * atr
            excessive = float(bar["low"]) < float(burst["open"]) - 0.35 * atr
        else:
            reached_value = float(bar["high"]) >= float(burst["close"]) + 0.25 * body
            origin_held = float(bar["close"]) <= float(burst["open"]) + 0.10 * atr
            excessive = float(bar["high"]) > float(burst["open"]) + 0.35 * atr
        contracted_activity = (
            float(bar["volume"]) <= 0.80 * burst_volume
            and float(bar["trade_count"]) <= 0.90 * burst_trades
        )
        controlled_flow = abs(float(bar["imbalance"])) <= 0.75 * burst_imbalance + 0.03
        if reached_value and origin_held and not excessive and contracted_activity and controlled_flow:
            return position
    return None


def _reacceleration(
    frame: pd.DataFrame,
    retrace_position: int,
    direction: int,
    maximum_bars: int,
) -> int | None:
    retrace = frame.iloc[retrace_position]
    atr = float(retrace["atr20"])
    for position in range(
        retrace_position + 1,
        min(len(frame), retrace_position + 1 + maximum_bars),
    ):
        bar = frame.iloc[position]
        directional_body = direction * float(bar["close"] - bar["open"])
        directional_flow = direction * float(bar["imbalance"])
        if direction > 0:
            displaced = float(bar["close"]) >= float(retrace["high"]) + 0.05 * atr
            located = float(bar["close_location"]) >= 0.62
        else:
            displaced = float(bar["close"]) <= float(retrace["low"]) - 0.05 * atr
            located = float(bar["close_location"]) <= 0.38
        activity_reexpanded = (
            float(bar["volume"]) >= 1.05 * float(retrace["volume"])
            and float(bar["trade_count"]) >= float(retrace["trade_count"])
        )
        if (
            displaced
            and located
            and directional_body >= 0.15 * atr
            and directional_flow >= 0.05
            and activity_reexpanded
        ):
            return position
    return None


def _first_touch(
    future: pd.DataFrame,
    *,
    direction: int,
    stop: float,
    target: float,
) -> str:
    for _, bar in future.iterrows():
        stop_hit = float(bar["low"]) <= stop if direction > 0 else float(bar["high"]) >= stop
        target_hit = float(bar["high"]) >= target if direction > 0 else float(bar["low"]) <= target
        if stop_hit and target_hit:
            return "STOP_AMBIGUOUS_FIRST"
        if stop_hit:
            return "STOP"
        if target_hit:
            return "TARGET"
    return "TIMEOUT"


def _net_per_unit(direction: int, entry: float, exit_price: float) -> float:
    gross = direction * (exit_price - entry)
    return gross - FEE_RATE_PER_FILL * (entry + exit_price) - 2.0 * TICK_SIZE


def _sequence_record(
    frame: pd.DataFrame,
    burst_position: int,
    retrace_position: int,
    entry_position: int,
    direction: int,
    spec: SequenceSpec,
) -> dict[str, Any] | None:
    burst = frame.iloc[burst_position]
    retrace = frame.iloc[retrace_position]
    entry_bar = frame.iloc[entry_position]
    atr = float(entry_bar["atr20"])
    entry = float(entry_bar["close"])
    structural_stop = (
        float(retrace["low"]) - 0.05 * atr
        if direction > 0
        else float(retrace["high"]) + 0.05 * atr
    )
    minimum_stop_distance = 0.25 * atr
    if direction > 0:
        stop = min(structural_stop, entry - minimum_stop_distance)
    else:
        stop = max(structural_stop, entry + minimum_stop_distance)
    gross_risk = abs(entry - stop)
    net_stop_loss = -_net_per_unit(direction, entry, stop)
    if gross_risk <= 0 or net_stop_loss <= 0:
        return None

    previous_high = float(entry_bar["previous_session_high"])
    previous_low = float(entry_bar["previous_session_low"])
    previous_range = previous_high - previous_low
    if direction > 0:
        external_target = previous_high if previous_high > entry else previous_high + 0.50 * previous_range
    else:
        external_target = previous_low if previous_low < entry else previous_low - 0.50 * previous_range
    external_net_rr = _net_per_unit(direction, entry, external_target) / net_stop_loss

    record: dict[str, Any] = {
        "spec": spec.name,
        "burst_time": burst.name.isoformat(),
        "retrace_time": retrace.name.isoformat(),
        "entry_time": entry_bar.name.isoformat(),
        "direction": "LONG" if direction > 0 else "SHORT",
        "entry": entry,
        "stop": stop,
        "gross_risk": gross_risk,
        "net_stop_loss_per_unit": net_stop_loss,
        "external_target": external_target,
        "external_target_net_rr": external_net_rr,
        "burst_imbalance": float(burst["imbalance"]),
        "burst_volume_ratio": float(burst["volume_ratio"]),
        "burst_trade_ratio": float(burst["trade_ratio"]),
        "burst_body_atr": float(burst["body_atr"]),
        "retrace_volume_fraction": float(retrace["volume"] / burst["volume"]),
        "retrace_trade_fraction": float(retrace["trade_count"] / burst["trade_count"]),
        "retrace_imbalance": float(retrace["imbalance"]),
        "entry_imbalance": float(entry_bar["imbalance"]),
        "minutes_burst_to_entry": int((entry_bar.name - burst.name).total_seconds() // 60),
    }
    for horizon in (15, 30, 45, 60):
        future = frame.iloc[entry_position + 1 : entry_position + 1 + horizon]
        if future.empty:
            continue
        final = float(future.iloc[-1]["close"])
        record[f"net_return_{horizon}m"] = _net_per_unit(direction, entry, final) / entry
        if direction > 0:
            mfe_price = float(future["high"].max())
            mae_price = float(future["low"].min())
        else:
            mfe_price = float(future["low"].min())
            mae_price = float(future["high"].max())
        record[f"net_mfe_{horizon}m_r"] = _net_per_unit(direction, entry, mfe_price) / net_stop_loss
        record[f"net_mae_{horizon}m_r"] = -_net_per_unit(direction, entry, mae_price) / net_stop_loss
        for multiple in (1.2, 1.5, 2.0):
            target = entry + direction * multiple * gross_risk
            record[f"outcome_{multiple:.1f}r_{horizon}m"] = _first_touch(
                future,
                direction=direction,
                stop=stop,
                target=target,
            )
    external_future = frame.iloc[entry_position + 1 : entry_position + 61]
    record["external_target_outcome_60m"] = (
        _first_touch(
            external_future,
            direction=direction,
            stop=stop,
            target=external_target,
        )
        if external_net_rr >= 1.20
        else "FAILED_COST_AFTER_GEOMETRY"
    )
    return record


def _summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {"events": len(events)}
    if not events:
        return result
    for horizon in (15, 30, 45, 60):
        returns = np.asarray([float(event[f"net_return_{horizon}m"]) for event in events])
        result[f"horizon_{horizon}m"] = {
            "mean_net_return": float(returns.mean()),
            "median_net_return": float(np.median(returns)),
            "positive_share": float((returns > 0).mean()),
            "median_net_mfe_r": float(np.median([event[f"net_mfe_{horizon}m_r"] for event in events])),
            "median_net_mae_r": float(np.median([event[f"net_mae_{horizon}m_r"] for event in events])),
        }
        for multiple in (1.2, 1.5, 2.0):
            key = f"outcome_{multiple:.1f}r_{horizon}m"
            outcomes = [event[key] for event in events]
            result[f"{multiple:.1f}r_{horizon}m"] = {
                value: outcomes.count(value) for value in sorted(set(outcomes))
            }
    outcomes = [event["external_target_outcome_60m"] for event in events]
    result["external_target_60m"] = {
        value: outcomes.count(value) for value in sorted(set(outcomes))
    }
    result["median_external_target_net_rr"] = float(
        np.median([event["external_target_net_rr"] for event in events])
    )
    result["median_minutes_burst_to_entry"] = float(
        np.median([event["minutes_burst_to_entry"] for event in events])
    )
    return result


def run(config_path: Path, output: Path, data_cache: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    instrument = _build_instrument(config)
    bar_type = BarType.from_str(str(config["bar_type"]))
    output.mkdir(parents=True, exist_ok=True)
    window_payloads: dict[str, Any] = {}

    for window in config["suites"]["screen"]:
        start = _parse_utc(window["start"])
        end = _parse_utc(window["end"])
        loaded = load_official_binance_bars(
            symbol="BTCUSDT",
            interval="1m",
            load_start=start - timedelta(days=7),
            load_end=end + timedelta(hours=2),
            bar_type=bar_type,
            instrument=instrument,
            cache_dir=data_cache,
        )
        frame = _features(loaded.frame)
        events_by_spec: dict[str, list[dict[str, Any]]] = {spec.name: [] for spec in SPECS}
        evaluation_positions = np.flatnonzero((frame.index >= start) & (frame.index < end))
        for burst_position in evaluation_positions:
            burst = frame.iloc[int(burst_position)]
            for spec in SPECS:
                direction = _burst_side(burst, spec)
                if direction == 0:
                    continue
                retrace_position = _contracted_retrace(
                    frame,
                    int(burst_position),
                    direction,
                    spec.maximum_retrace_bars,
                )
                if retrace_position is None:
                    continue
                entry_position = _reacceleration(
                    frame,
                    retrace_position,
                    direction,
                    spec.maximum_reacceleration_bars,
                )
                if entry_position is None or frame.index[entry_position] >= end:
                    continue
                record = _sequence_record(
                    frame,
                    int(burst_position),
                    retrace_position,
                    entry_position,
                    direction,
                    spec,
                )
                if record is not None:
                    events_by_spec[spec.name].append(record)
        payload = {
            "window": window,
            "data_quality": loaded.quality,
            "specs": {
                spec.name: {
                    "definition": asdict(spec),
                    "summary": _summary(events_by_spec[spec.name]),
                    "events": events_by_spec[spec.name],
                }
                for spec in SPECS
            },
        }
        window_payloads[window["name"]] = payload
        (output / f"{window['name']}.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    combined: dict[str, Any] = {}
    for spec in SPECS:
        events = [
            event
            for payload in window_payloads.values()
            for event in payload["specs"][spec.name]["events"]
        ]
        combined[spec.name] = _summary(events)
    result = {
        "candidate": "candidate-08-top-hour-sequence-probe",
        "purpose": "causal sequence diagnostic, not NautilusTrader performance evidence",
        "fixed_specs": [asdict(spec) for spec in SPECS],
        "combined": combined,
        "windows": {
            name: {
                "window": payload["window"],
                "spec_summaries": {
                    spec_name: spec_payload["summary"]
                    for spec_name, spec_payload in payload["specs"].items()
                },
            }
            for name, payload in window_payloads.items()
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
