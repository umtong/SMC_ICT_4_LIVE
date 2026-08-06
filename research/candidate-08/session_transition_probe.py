"""Structural time-boundary holdout for burst-retrace-reacceleration hypotheses.

This script is a causal feature/outcome diagnostic, not an exchange simulator and not performance
evidence. It compares predeclared participant-transition clocks over 12 independently selected BTC
weeks. A hypothesis is eligible for NautilusTrader implementation only when opportunity and
cost-after outcome remain distributed across windows rather than concentrated in one week.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import timedelta
import json
from pathlib import Path
import sys
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from nautilus_trader.model.data import BarType

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from data import load_official_binance_bars  # noqa: E402
from qh_probe import ProbeSpec, _features, _side  # noqa: E402
from qh_sequence_probe import (  # noqa: E402
    _contracted_retrace,
    _first_touch,
    _net_per_unit,
    _reacceleration,
    _sequence_record,
)
from run import _build_instrument, _parse_utc  # noqa: E402


LONDON = ZoneInfo("Europe/London")
NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class StructuralSpec:
    name: str
    boundary: str
    regime: str
    maximum_retrace_bars: int = 6
    maximum_reacceleration_bars: int = 4


BASE_SIGNAL = ProbeSpec(
    name="STRUCTURAL_BOUNDARY_BASE",
    require_lag_alignment=True,
    top_of_hour_only=False,
)


def _enrich(frame: pd.DataFrame) -> pd.DataFrame:
    result = _features(frame)
    movement = result["close"].diff()
    path = movement.abs().shift(1).rolling(60, min_periods=45).sum()
    result["efficiency_60m"] = (
        result["close"].shift(1) - result["close"].shift(61)
    ).abs() / path.replace(0, np.nan)
    result["direction_60m"] = np.sign(
        result["close"].shift(1) - result["close"].shift(61)
    )
    return result


def _is_london_open(timestamp: pd.Timestamp) -> bool:
    local = timestamp.to_pydatetime().astimezone(LONDON)
    return local.hour == 8 and local.minute == 0


def _is_new_york_open(timestamp: pd.Timestamp) -> bool:
    local = timestamp.to_pydatetime().astimezone(NEW_YORK)
    return local.hour == 9 and local.minute == 30


def _boundary_matches(timestamp: pd.Timestamp, boundary: str) -> bool:
    hour = int(timestamp.hour)
    minute = int(timestamp.minute)
    if boundary == "TOP_HOUR":
        return minute == 0
    if boundary == "FOUR_HOUR":
        return minute == 0 and hour % 4 == 0
    if boundary == "FUNDING":
        return minute == 0 and hour in (0, 8, 16)
    asia = minute == 0 and hour == 0
    london = _is_london_open(timestamp)
    new_york = _is_new_york_open(timestamp)
    if boundary == "GLOBAL_OPENS":
        return asia or london or new_york
    if boundary == "LONDON_NY":
        return london or new_york
    raise ValueError(f"unsupported structural boundary: {boundary}")


def _regime_allows(row: pd.Series, direction: int, regime: str) -> bool:
    if regime == "PREVIOUS_SESSION":
        return int(np.sign(float(row["previous_session_direction"]))) == direction
    if regime == "EFFICIENCY_60M":
        efficiency = float(row["efficiency_60m"])
        auction_direction = int(np.sign(float(row["direction_60m"])))
        return np.isfinite(efficiency) and efficiency >= 0.25 and auction_direction == direction
    raise ValueError(f"unsupported regime: {regime}")


def _burst_side(row: pd.Series, spec: StructuralSpec) -> int:
    if not _boundary_matches(row.name, spec.boundary):
        return 0
    direction = _side(row, BASE_SIGNAL)
    if direction == 0 or not _regime_allows(row, direction, spec.regime):
        return 0
    return direction


def _extend_180m(
    record: dict[str, Any],
    frame: pd.DataFrame,
    entry_position: int,
    direction: int,
) -> None:
    future = frame.iloc[entry_position + 1 : entry_position + 181]
    if future.empty:
        record["external_outcome_180m"] = "NO_FUTURE_DATA"
        record["external_net_r_proxy_180m"] = 0.0
        return
    entry = float(record["entry"])
    stop = float(record["stop"])
    target = float(record["external_target"])
    net_stop = float(record["net_stop_loss_per_unit"])
    net_rr = float(record["external_target_net_rr"])
    if net_rr < 1.20:
        record["external_outcome_180m"] = "FAILED_COST_AFTER_GEOMETRY"
        record["external_net_r_proxy_180m"] = float("nan")
        return
    outcome = _first_touch(
        future,
        direction=direction,
        stop=stop,
        target=target,
    )
    record["external_outcome_180m"] = outcome
    if outcome == "TARGET":
        proxy = net_rr
    elif outcome in ("STOP", "STOP_AMBIGUOUS_FIRST"):
        proxy = -1.0
    else:
        proxy = _net_per_unit(direction, entry, float(future.iloc[-1]["close"])) / net_stop
    record["external_net_r_proxy_180m"] = float(proxy)

    for multiple in (1.2, 1.5):
        fixed_target = entry + direction * multiple * abs(entry - stop)
        fixed_outcome = _first_touch(
            future,
            direction=direction,
            stop=stop,
            target=fixed_target,
        )
        fixed_gain = _net_per_unit(direction, entry, fixed_target) / net_stop
        record[f"fixed_{multiple:.1f}r_outcome_180m"] = fixed_outcome
        record[f"fixed_{multiple:.1f}r_cost_after_rr"] = float(fixed_gain)


def _summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {"events": len(events)}
    if not events:
        return result
    eligible = [
        event
        for event in events
        if event.get("external_outcome_180m") != "FAILED_COST_AFTER_GEOMETRY"
        and np.isfinite(float(event.get("external_net_r_proxy_180m", np.nan)))
    ]
    result["cost_after_external_target_eligible"] = len(eligible)
    result["geometry_pass_share"] = len(eligible) / len(events)
    result["median_external_target_net_rr"] = float(
        np.median([float(event["external_target_net_rr"]) for event in events])
    )
    result["median_minutes_burst_to_entry"] = float(
        np.median([float(event["minutes_burst_to_entry"]) for event in events])
    )
    if eligible:
        outcomes = [str(event["external_outcome_180m"]) for event in eligible]
        proxy = np.asarray(
            [float(event["external_net_r_proxy_180m"]) for event in eligible],
            dtype=float,
        )
        result["external_180m"] = {
            "outcomes": {value: outcomes.count(value) for value in sorted(set(outcomes))},
            "mean_net_r_proxy": float(proxy.mean()),
            "median_net_r_proxy": float(np.median(proxy)),
            "positive_share": float((proxy > 0).mean()),
            "total_net_r_proxy": float(proxy.sum()),
        }
    for horizon in (15, 30, 45, 60):
        values = np.asarray(
            [float(event[f"net_return_{horizon}m"]) for event in events],
            dtype=float,
        )
        result[f"horizon_{horizon}m"] = {
            "mean_net_return": float(values.mean()),
            "median_net_return": float(np.median(values)),
            "positive_share": float((values > 0).mean()),
        }
    for multiple in (1.2, 1.5):
        key = f"fixed_{multiple:.1f}r_outcome_180m"
        outcomes = [str(event[key]) for event in events]
        result[f"fixed_{multiple:.1f}r_180m"] = {
            "outcomes": {value: outcomes.count(value) for value in sorted(set(outcomes))},
            "median_cost_after_rr": float(
                np.median([float(event[f"fixed_{multiple:.1f}r_cost_after_rr"]) for event in events])
            ),
        }
    return result


def run(
    config_path: Path,
    holdout_path: Path,
    output: Path,
    data_cache: Path,
) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    holdout = json.loads(holdout_path.read_text(encoding="utf-8"))
    specs = [StructuralSpec(**item) for item in holdout["structural_specs"]]
    instrument = _build_instrument(config)
    bar_type = BarType.from_str(str(config["bar_type"]))
    output.mkdir(parents=True, exist_ok=True)
    windows: dict[str, Any] = {}

    for window in holdout["windows"]:
        start = _parse_utc(str(window["start"]))
        end = _parse_utc(str(window["end"]))
        loaded = load_official_binance_bars(
            symbol="BTCUSDT",
            interval="1m",
            load_start=start - timedelta(days=7),
            load_end=end + timedelta(hours=4),
            bar_type=bar_type,
            instrument=instrument,
            cache_dir=data_cache,
        )
        frame = _enrich(loaded.frame)
        events_by_spec: dict[str, list[dict[str, Any]]] = {spec.name: [] for spec in specs}
        evaluation_positions = np.flatnonzero((frame.index >= start) & (frame.index < end))
        for burst_position_raw in evaluation_positions:
            burst_position = int(burst_position_raw)
            burst = frame.iloc[burst_position]
            for spec in specs:
                direction = _burst_side(burst, spec)
                if direction == 0:
                    continue
                retrace_position = _contracted_retrace(
                    frame,
                    burst_position,
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
                    burst_position,
                    retrace_position,
                    entry_position,
                    direction,
                    spec,  # type: ignore[arg-type]
                )
                if record is None:
                    continue
                record["boundary"] = spec.boundary
                record["regime"] = spec.regime
                record["window"] = window["name"]
                record["efficiency_60m"] = float(burst["efficiency_60m"])
                record["direction_60m"] = float(burst["direction_60m"])
                _extend_180m(record, frame, entry_position, direction)
                events_by_spec[spec.name].append(record)
        payload = {
            "window": window,
            "data_quality": loaded.quality,
            "specs": {
                spec.name: {
                    "definition": spec.__dict__,
                    "summary": _summarize(events_by_spec[spec.name]),
                    "events": events_by_spec[spec.name],
                }
                for spec in specs
            },
        }
        windows[window["name"]] = payload
        (output / f"{window['name']}.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True, allow_nan=True) + "\n",
            encoding="utf-8",
        )

    combined: dict[str, Any] = {}
    for spec in specs:
        events = [
            event
            for payload in windows.values()
            for event in payload["specs"][spec.name]["events"]
        ]
        summary = _summarize(events)
        event_windows = sorted({str(event["window"]) for event in events})
        eligible_windows = sorted(
            {
                str(event["window"])
                for event in events
                if event.get("external_outcome_180m") != "FAILED_COST_AFTER_GEOMETRY"
            }
        )
        positive_windows = []
        for name in eligible_windows:
            values = [
                float(event["external_net_r_proxy_180m"])
                for event in events
                if event["window"] == name
                and np.isfinite(float(event.get("external_net_r_proxy_180m", np.nan)))
            ]
            if values and sum(values) > 0:
                positive_windows.append(name)
        summary.update(
            {
                "windows_with_events": len(event_windows),
                "event_window_names": event_windows,
                "windows_with_cost_eligible_events": len(eligible_windows),
                "positive_cost_after_proxy_windows": len(positive_windows),
                "positive_window_names": positive_windows,
            }
        )
        combined[spec.name] = summary

    result = {
        "candidate": "candidate-08-structural-session-transition-holdout",
        "purpose": "causal diagnostic only; not NautilusTrader execution evidence",
        "selection_seed": holdout["selection_seed"],
        "selection_rule": holdout["selection_rule"],
        "fixed_specs": holdout["structural_specs"],
        "combined": combined,
        "windows": {
            name: {
                "window": payload["window"],
                "spec_summaries": {
                    spec_name: spec_payload["summary"]
                    for spec_name, spec_payload in payload["specs"].items()
                },
            }
            for name, payload in windows.items()
        },
    }
    (output / "summary.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=True) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument(
        "--holdout-config",
        type=Path,
        default=HERE / "session_holdout_config.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-cache", type=Path, required=True)
    args = parser.parse_args()
    result = run(
        args.config.resolve(),
        args.holdout_config.resolve(),
        args.output.resolve(),
        args.data_cache.resolve(),
    )
    print(json.dumps(result["combined"], indent=2, sort_keys=True, allow_nan=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
