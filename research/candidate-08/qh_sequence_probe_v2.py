"""Second fixed sequence diagnostic: quarter-hour scope and efficient-auction regimes.

The first top-hour probe found a small short-horizon effect but too few complete retrace sequences.
This predeclared extension changes only the structural scope: every 15-minute algorithmic boundary
is eligible, while continuation must agree with either the completed 4-hour auction or a causal
60-minute directional-efficiency regime. It remains a diagnostic, not a backtest engine.
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
from qh_sequence_probe import (  # noqa: E402
    _contracted_retrace,
    _reacceleration,
    _sequence_record,
    _summary,
)
from run import _build_instrument, _parse_utc  # noqa: E402


@dataclass(frozen=True, slots=True)
class ExtendedSpec:
    name: str
    top_of_hour_only: bool
    intense_burst: bool
    regime: str
    maximum_retrace_bars: int = 6
    maximum_reacceleration_bars: int = 4


SPECS = (
    ExtendedSpec("QH_INTENSE_CONTRACTED_RETRACE", False, True, "NONE"),
    ExtendedSpec("QH_SESSION_ALIGNED_RETRACE", False, False, "PREVIOUS_SESSION"),
    ExtendedSpec("QH_INTENSE_SESSION_ALIGNED_RETRACE", False, True, "PREVIOUS_SESSION"),
    ExtendedSpec("QH_INTENSE_EFFICIENT_AUCTION_RETRACE", False, True, "EFFICIENCY_60M"),
    ExtendedSpec("TOP_HOUR_INTENSE_EFFICIENT_AUCTION_RETRACE", True, True, "EFFICIENCY_60M"),
)


def _enrich(frame: pd.DataFrame) -> pd.DataFrame:
    result = _features(frame)
    movement = result["close"].diff()
    path = movement.abs().shift(1).rolling(60, min_periods=45).sum()
    displacement = (result["close"].shift(1) - result["close"].shift(61)).abs()
    result["efficiency_60m"] = displacement / path.replace(0, np.nan)
    result["direction_60m"] = np.sign(result["close"].shift(1) - result["close"].shift(61))
    return result


def _burst_side(row: pd.Series, spec: ExtendedSpec) -> int:
    direction = _side(
        row,
        ProbeSpec(
            name=spec.name,
            require_lag_alignment=True,
            top_of_hour_only=spec.top_of_hour_only,
        ),
    )
    if direction == 0:
        return 0
    if spec.intense_burst and not (
        abs(float(row["imbalance"])) >= 0.18
        and float(row["volume_ratio"]) >= 1.50
        and float(row["trade_ratio"]) >= 1.25
        and float(row["body_atr"]) >= 0.50
    ):
        return 0
    if spec.regime == "PREVIOUS_SESSION":
        previous_direction = int(np.sign(float(row["previous_session_direction"])))
        if previous_direction != direction:
            return 0
    elif spec.regime == "EFFICIENCY_60M":
        efficiency = float(row["efficiency_60m"])
        auction_direction = int(np.sign(float(row["direction_60m"])))
        if not np.isfinite(efficiency) or efficiency < 0.25 or auction_direction != direction:
            return 0
    return direction


def run(config_path: Path, output: Path, data_cache: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    instrument = _build_instrument(config)
    bar_type = BarType.from_str(str(config["bar_type"]))
    output.mkdir(parents=True, exist_ok=True)
    windows: dict[str, Any] = {}

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
        frame = _enrich(loaded.frame)
        events_by_spec: dict[str, list[dict[str, Any]]] = {spec.name: [] for spec in SPECS}
        evaluation_positions = np.flatnonzero((frame.index >= start) & (frame.index < end))
        for burst_position_raw in evaluation_positions:
            burst_position = int(burst_position_raw)
            burst = frame.iloc[burst_position]
            for spec in SPECS:
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
                if record is not None:
                    record["efficiency_60m"] = float(burst["efficiency_60m"])
                    record["direction_60m"] = float(burst["direction_60m"])
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
        windows[window["name"]] = payload
        (output / f"{window['name']}.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    combined = {}
    for spec in SPECS:
        events = [
            event
            for payload in windows.values()
            for event in payload["specs"][spec.name]["events"]
        ]
        combined[spec.name] = _summary(events)
    result = {
        "candidate": "candidate-08-quarter-hour-sequence-probe-v2",
        "purpose": "causal sequence diagnostic; no execution or performance claim",
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
            for name, payload in windows.items()
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
