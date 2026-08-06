"""Acceptance-only ten-second aggregate-trade diagnostic.

This module intentionally removes the failed sweep-absorption reversal family. A completed
4-hour/day/week boundary becomes tradeable only after high aggressive flow closes beyond it, a
lower-energy ten-second retest holds, and a separate same-direction flow burst resumes. The stop
uses the observed retest extreme; the target remains the nearest active completed external level.
No exchange or account simulation is performed.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
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

from aggtrade_orderflow_probe import (  # noqa: E402
    PendingEvent,
    _context,
    _crossed_boundary,
    _snapshot_after_latest_complete,
    _summary,
    _trade_record,
    load_ten_second_aggtrades,
)
from data import load_official_binance_bars  # noqa: E402
from range_fvg_logic import RangeFVGConfig  # noqa: E402
from run import _build_instrument, _parse_utc  # noqa: E402


def acceptance_interaction(
    row: pd.Series,
    *,
    boundary_level: float,
    outward: int,
    atr: float,
) -> bool:
    """Classify observable aggressive-flow acceptance beyond a completed boundary."""

    outward_flow = outward * float(row["imbalance"])
    outward_body = outward * float(row["close"] - row["open"])
    high_activity = (
        float(row["volume_ratio"]) >= 1.50
        and float(row["trade_ratio"]) >= 1.20
    )
    accepted = (
        float(row["close"]) >= boundary_level + 0.05 * atr
        if outward > 0
        else float(row["close"]) <= boundary_level - 0.05 * atr
    )
    located = (
        float(row["close_location"]) >= 0.65
        if outward > 0
        else float(row["close_location"]) <= 0.35
    )
    return (
        accepted
        and located
        and high_activity
        and outward_flow >= 0.20
        and outward_body >= 0.08 * atr
    )


def acceptance_retest_holds(
    row: pd.Series,
    *,
    boundary_level: float,
    outward: int,
    atr: float,
    displacement_volume: float,
    displacement_trade_count: float,
    displacement_imbalance: float,
) -> bool:
    touched = (
        float(row["low"]) <= boundary_level + 0.05 * atr
        if outward > 0
        else float(row["high"]) >= boundary_level - 0.05 * atr
    )
    held = (
        float(row["close"]) >= boundary_level
        if outward > 0
        else float(row["close"]) <= boundary_level
    )
    contracted = (
        float(row["volume"]) <= 0.80 * displacement_volume
        and float(row["trade_count"]) <= 0.90 * displacement_trade_count
        and abs(float(row["imbalance"])) < abs(displacement_imbalance)
    )
    return touched and held and contracted


def acceptance_reaccelerates(
    row: pd.Series,
    *,
    outward: int,
    atr: float,
    retest_high: float,
    retest_low: float,
    retest_volume: float,
    retest_trade_count: float,
) -> bool:
    break_structure = (
        float(row["close"]) > retest_high + 0.01 * atr
        if outward > 0
        else float(row["close"]) < retest_low - 0.01 * atr
    )
    located = (
        float(row["close_location"]) >= 0.65
        if outward > 0
        else float(row["close_location"]) <= 0.35
    )
    directional_body = outward * float(row["close"] - row["open"])
    directional_flow = outward * float(row["imbalance"])
    return (
        break_structure
        and located
        and directional_body >= 0.05 * atr
        and directional_flow >= 0.10
        and float(row["volume"]) >= retest_volume
        and float(row["trade_count"]) >= retest_trade_count
    )


def detect_acceptance_events(
    *,
    data: pd.DataFrame,
    context_times: np.ndarray,
    context_bars: tuple[Any, ...],
    snapshots: tuple[tuple[Any, ...], ...],
    tick: float,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    records: list[dict[str, Any]] = []
    diagnostics: Counter[str] = Counter()
    consumed: set[str] = set()
    pending: PendingEvent | None = None
    scenario_counter = 0

    for position in range(1, len(data.index)):
        row = data.iloc[position]
        required = (
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
            float(row["imbalance"]),
            float(row["volume_ratio"]),
            float(row["trade_ratio"]),
            float(row["close_location"]),
        )
        if not all(np.isfinite(value) for value in required):
            continue
        timestamp_ns = int(data.index[position].as_unit("ns").value)
        context = _snapshot_after_latest_complete(
            timestamp_ns,
            context_times,
            context_bars,
            snapshots,
        )
        if context is None:
            continue
        five_bar, levels = context
        atr = float(five_bar.atr)
        handled_pending = pending is not None

        if pending is not None:
            outward = pending.outward_direction
            if position > pending.expiry_position:
                diagnostics["ACCEPTANCE_SEQUENCE_TIMEOUT"] += 1
                pending = None
            else:
                reclaimed = (
                    float(row["close"]) < pending.boundary.level - 0.02 * atr
                    if outward > 0
                    else float(row["close"]) > pending.boundary.level + 0.02 * atr
                )
                if reclaimed:
                    diagnostics["ACCEPTANCE_RECLAIMED"] += 1
                    pending = None
                elif pending.retest_position is None:
                    if acceptance_retest_holds(
                        row,
                        boundary_level=pending.boundary.level,
                        outward=outward,
                        atr=atr,
                        displacement_volume=pending.displacement_volume,
                        displacement_trade_count=pending.displacement_trade_count,
                        displacement_imbalance=pending.displacement_imbalance,
                    ):
                        pending.retest_position = position
                        pending.retest_high = float(row["high"])
                        pending.retest_low = float(row["low"])
                        pending.retest_volume = float(row["volume"])
                        pending.retest_trade_count = float(row["trade_count"])
                        pending.expiry_position = position + 3
                        diagnostics["ACCEPTANCE_RETEST_HELD"] += 1
                else:
                    assert pending.retest_high is not None
                    assert pending.retest_low is not None
                    assert pending.retest_volume is not None
                    assert pending.retest_trade_count is not None
                    if acceptance_reaccelerates(
                        row,
                        outward=outward,
                        atr=atr,
                        retest_high=pending.retest_high,
                        retest_low=pending.retest_low,
                        retest_volume=pending.retest_volume,
                        retest_trade_count=pending.retest_trade_count,
                    ):
                        record, reason = _trade_record(
                            pending=pending,
                            confirmation_position=position,
                            data=data,
                            levels=levels,
                            atr=atr,
                            tick=tick,
                        )
                        diagnostics[reason] += 1
                        if record is not None:
                            records.append(record)
                        pending = None
            if handled_pending:
                continue

        crossed = _crossed_boundary(
            levels,
            previous_close=float(data.iloc[position - 1]["close"]),
            high=float(row["high"]),
            low=float(row["low"]),
            atr=atr,
            consumed=consumed,
        )
        if crossed is None:
            continue
        boundary, outward = crossed
        if not acceptance_interaction(
            row,
            boundary_level=boundary.level,
            outward=outward,
            atr=atr,
        ):
            diagnostics["NON_ACCEPTANCE_INTERACTION_IGNORED"] += 1
            continue

        scenario_counter += 1
        consumed.add(boundary.level_id)
        pending = PendingEvent(
            scenario_id=f"agg-acceptance-{scenario_counter:06d}",
            family="BREAKOUT_ACCEPTANCE_CONTINUATION",
            trade_direction=outward,
            outward_direction=outward,
            boundary=boundary,
            armed_position=position,
            expiry_position=position + 6,
            extreme=float(row["high"] if outward > 0 else row["low"]),
            reference_high=float(row["high"]),
            reference_low=float(row["low"]),
            displacement_volume=float(row["volume"]),
            displacement_trade_count=float(row["trade_count"]),
            displacement_imbalance=float(row["imbalance"]),
        )
        diagnostics["ACCEPTANCE_ARMED"] += 1

    return records, dict(sorted(diagnostics.items()))


def run(
    *,
    config_path: Path,
    window_name: str,
    output: Path,
    data_cache: Path,
) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    windows = {item["name"]: item for item in config["suites"]["screen"]}
    if window_name not in windows:
        raise ValueError(f"unknown fixed screen window: {window_name}")
    window = windows[window_name]
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
        cache_dir=data_cache / "klines",
    )
    ten, agg_sources, agg_quality = load_ten_second_aggtrades(
        symbol="BTCUSDT",
        start=start,
        end=end + timedelta(hours=4),
        cache_dir=data_cache / "aggTrades",
    )
    pattern = RangeFVGConfig.from_mapping(dict(config["pattern"]))
    context_times, context_bars, snapshots = _context(loaded.frame, pattern)
    records, diagnostics = detect_acceptance_events(
        data=ten,
        context_times=context_times,
        context_bars=context_bars,
        snapshots=snapshots,
        tick=0.1,
    )
    in_window = [
        record
        for record in records
        if start <= pd.Timestamp(record["confirmation_time"]) < end
    ]
    result = {
        "candidate": "candidate-08-aggtrade-acceptance-only",
        "purpose": "causal acceptance-only path diagnostic; not NautilusTrader execution evidence",
        "window": window,
        "unchanged_thresholds": True,
        "removed_failed_family": "SWEEP_ABSORPTION_REVERSAL",
        "diagnostics": diagnostics,
        "summary": _summary(in_window),
        "by_boundary_source": {
            source: _summary([record for record in in_window if record["boundary_source"] == source])
            for source in sorted({record["boundary_source"] for record in in_window})
        },
        "records": in_window,
        "aggtrade_data_quality": agg_quality,
        "aggtrade_source_files": [asdict(source) for source in agg_sources],
        "kline_data_quality": loaded.quality,
        "kline_source_files": [asdict(source) for source in loaded.source_files],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config_range_fvg.json")
    parser.add_argument("--window", choices=("screen-01", "screen-02", "screen-03"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-cache", type=Path, required=True)
    args = parser.parse_args()
    result = run(
        config_path=args.config.resolve(),
        window_name=args.window,
        output=args.output.resolve(),
        data_cache=args.data_cache.resolve(),
    )
    print(json.dumps({
        "window": result["window"],
        "diagnostics": result["diagnostics"],
        "summary": result["summary"],
        "by_boundary_source": result["by_boundary_source"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
