#!/usr/bin/env python3
"""Enrich frozen scenario signals with a strict pre-signal liquidity registry.

This module changes no entry, side, stop, scenario, timing, risk, fee or fill
assumption. For every frozen signal it first reconstructs the existing execution
registry (30/60/120/240/480/720-minute extrema and the immediately completed
8-hour boundary). Signals already executable under that registry remain byte-
semantically unchanged.

Only when the existing registry has no cost-valid destination, V44 searches:

* active right-confirmed pivot pools which have not traded since confirmation;
* untouched highs/lows of completed 8-hour auctions;
* untouched previous-day highs/lows; and
* untouched previous-week highs/lows.

The selected level must have existed before the signal, lie in the trade
direction and provide at least the unchanged minimum net-R after costs. The
NautilusTrader strategy revalidates every declaration at signal time.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import json
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable

import pandas as pd

import parent_session_liquidity_transfer_compiler as v43
import rich_signal_compiler_v22 as v22
from nt_liquidity_strategy import net_r_at_price
from nt_low_impact_external_strategy import TARGET_WINDOWS
from nt_low_impact_external_strategy import choose_external_liquidity_target


DEFAULT_COST_RATE = 0.00075
DEFAULT_MINIMUM_NET_R = 1.20


@dataclass(frozen=True, slots=True)
class RegistryLevel:
    side: int
    price: float
    source: str
    observed_index: int


@dataclass(frozen=True, slots=True)
class RegistryTarget:
    price: float
    source: str
    observed_index: int
    net_r: float


def finite(value: Any) -> float:
    return v43.finite(value)


def _period_key(timestamp: pd.Timestamp, period: str) -> pd.Timestamp:
    if period == "session":
        return timestamp.floor("8h")
    day = timestamp.normalize()
    if period == "day":
        return day
    if period == "week":
        return day - pd.Timedelta(days=int(day.weekday()))
    raise ValueError(period)


def _period_source(period: str, key: pd.Timestamp, side: int) -> str:
    label = key.strftime("%Y%m%dT%H%M")
    suffix = "high" if side > 0 else "low"
    prefix = {
        "session": "completed_parent_session",
        "day": "completed_previous_day",
        "week": "completed_previous_week",
    }[period]
    return f"{prefix}_{label}_{suffix}"


def active_calendar_liquidity_snapshots(
    data: pd.DataFrame,
) -> list[tuple[RegistryLevel, ...]]:
    """Publish completed calendar levels and remove them on first later touch."""

    periods = ("session", "day", "week")
    starts = {period: 0 for period in periods}
    keys = {
        period: _period_key(data.index[0], period)
        for period in periods
    }
    active: list[RegistryLevel] = []
    snapshots: list[tuple[RegistryLevel, ...]] = []

    for index in range(len(data)):
        timestamp = data.index[index]
        for period in periods:
            key = _period_key(timestamp, period)
            if key == keys[period]:
                continue
            frame = data.iloc[starts[period] : index]
            if not frame.empty:
                observed = index - 1
                active.extend(
                    (
                        RegistryLevel(
                            1,
                            float(frame["high"].max()),
                            _period_source(period, keys[period], 1),
                            observed,
                        ),
                        RegistryLevel(
                            -1,
                            float(frame["low"].min()),
                            _period_source(period, keys[period], -1),
                            observed,
                        ),
                    )
                )
            starts[period] = index
            keys[period] = key

        high = finite(data["high"].iloc[index])
        low = finite(data["low"].iloc[index])
        if math.isfinite(high) and math.isfinite(low):
            active = [
                level
                for level in active
                if not (
                    (level.side > 0 and high >= level.price)
                    or (level.side < 0 and low <= level.price)
                )
            ]
        snapshots.append(tuple(active))
    return snapshots


def persistent_pivot_snapshots(
    data: pd.DataFrame,
    config: Any,
) -> list[tuple[v43.CausalPool, ...]]:
    """Use V43 right-confirmed pools without an arbitrary clock-age expiry."""

    parameters = SimpleNamespace(
        pivot_left=int(config.pivot_left),
        pivot_right=int(config.pivot_right),
        pool_max_age_minutes=len(data) + 1,
        pool_merge_atr=float(config.pool_merge_atr),
        sweep_min_atr=float(config.sweep_min_atr),
    )
    return v43.active_causal_pool_snapshots(data, parameters)


def existing_execution_levels(
    data: pd.DataFrame,
    sessions: list[v43.ParentSession | None],
    signal_index: int,
    side: int,
) -> list[tuple[str, float]]:
    """Reconstruct the exact strict-V34 registry before the signal bar."""

    history = data.iloc[:signal_index]
    levels: list[tuple[str, float]] = []
    for window in TARGET_WINDOWS:
        if len(history) < int(window):
            continue
        selected = history.iloc[-int(window) :]
        price = (
            float(selected["high"].max())
            if side > 0
            else float(selected["low"].min())
        )
        levels.append(
            (f"rolling_{window}m_{'high' if side > 0 else 'low'}", price)
        )
    session = sessions[signal_index]
    if session is not None:
        levels.append(
            (
                "previous_8h_session_boundary",
                session.high if side > 0 else session.low,
            )
        )
    return levels


def _untouched_after_confirmation(
    data: pd.DataFrame,
    pool: v43.CausalPool,
    signal_index: int,
) -> bool:
    start = int(pool.observed_index) + 1
    if start > signal_index:
        return False
    frame = data.iloc[start : signal_index + 1]
    if frame.empty:
        return True
    return bool(
        float(frame["high"].max()) < pool.level
        if pool.side > 0
        else float(frame["low"].min()) > pool.level
    )


def expanded_registry_levels(
    data: pd.DataFrame,
    calendar: list[tuple[RegistryLevel, ...]],
    pivots: list[tuple[v43.CausalPool, ...]],
    signal_index: int,
    side: int,
    config: Any,
) -> list[RegistryLevel]:
    result: list[RegistryLevel] = [
        level
        for level in calendar[signal_index]
        if level.side == side and level.observed_index < signal_index
    ]
    for pool in pivots[signal_index]:
        if (
            pool.side != side
            or pool.observed_index >= signal_index
            or signal_index - pool.observed_index
            < int(config.pool_min_age_minutes)
            or pool.prominence_atr
            < float(config.pool_min_prominence_atr)
            or not _untouched_after_confirmation(data, pool, signal_index)
        ):
            continue
        result.append(
            RegistryLevel(
                side=side,
                price=float(pool.level),
                source=(
                    f"causal_pivot_pool_{pool.pool_id}_"
                    f"{'high' if side > 0 else 'low'}"
                ),
                observed_index=int(pool.observed_index),
            )
        )
    return result


def choose_registry_target(
    levels: Iterable[RegistryLevel],
    *,
    entry: float,
    stop: float,
    side: int,
    cost_rate: float,
    minimum_net_r: float,
) -> RegistryTarget | None:
    price_loss = side * (entry - stop)
    planned_loss = price_loss + cost_rate * (entry + stop)
    if price_loss <= 0.0 or planned_loss <= 0.0:
        return None
    unique: dict[float, RegistryLevel] = {}
    for level in levels:
        if (
            level.side != side
            or level.observed_index < 0
            or not math.isfinite(level.price)
            or side * (level.price - entry) <= 0.0
        ):
            continue
        unique.setdefault(level.price, level)
    ordered = sorted(
        unique.values(),
        key=lambda level: side * (level.price - entry),
    )
    for level in ordered:
        net_r = net_r_at_price(
            entry,
            level.price,
            side,
            planned_loss,
            cost_rate,
        )
        if math.isfinite(net_r) and net_r >= minimum_net_r:
            return RegistryTarget(
                price=level.price,
                source=level.source,
                observed_index=level.observed_index,
                net_r=net_r,
            )
    return None


def enrich_signals(
    rows: list[dict[str, Any]],
    data: pd.DataFrame,
    config: Any,
    *,
    cost_rate: float = DEFAULT_COST_RATE,
    minimum_net_r: float = DEFAULT_MINIMUM_NET_R,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sessions = v43.completed_parent_sessions(data)
    calendar = active_calendar_liquidity_snapshots(data)
    pivots = persistent_pivot_snapshots(data, config)
    enriched: list[dict[str, Any]] = []
    counts = Counter()
    sources = Counter()
    scenarios = Counter()
    declared_net_r: list[float] = []

    for raw in rows:
        row = dict(raw)
        details = dict(row.get("details") or {})
        signal_index = int(row["signal_index"])
        side = int(row["side"])
        if not 0 <= signal_index < len(data):
            raise RuntimeError(f"signal index outside data: {signal_index}")
        expected_time = pd.Timestamp(row["signal_time"])
        actual_time = data.index[signal_index]
        if expected_time != actual_time:
            raise RuntimeError(
                f"signal/data time mismatch at {signal_index}: "
                f"{expected_time} != {actual_time}"
            )
        entry = finite(data["close"].iloc[signal_index])
        stop = finite(row["stop_level"])
        existing = choose_external_liquidity_target(
            existing_execution_levels(
                data,
                sessions,
                signal_index,
                side,
            ),
            entry=entry,
            stop=stop,
            side=side,
            cost_rate=cost_rate,
            minimum_net_r=minimum_net_r,
        )
        counts["signals"] += 1
        if existing is not None:
            counts["existing_registry_target"] += 1
            enriched.append(row)
            continue

        target = choose_registry_target(
            expanded_registry_levels(
                data,
                calendar,
                pivots,
                signal_index,
                side,
                config,
            ),
            entry=entry,
            stop=stop,
            side=side,
            cost_rate=cost_rate,
            minimum_net_r=minimum_net_r,
        )
        if target is None:
            counts["still_without_target"] += 1
            enriched.append(row)
            continue

        details.update(
            {
                "causal_target_reference": target.price,
                "causal_target_source": target.source,
                "causal_target_observed_index": target.observed_index,
                "causal_target_net_r_at_compilation": target.net_r,
                "causal_target_registry": (
                    "active_right_confirmed_pivots_and_untouched_"
                    "completed_session_day_week_boundaries"
                ),
                "target_enrichment_changed_entry_logic": False,
            }
        )
        row["details"] = details
        enriched.append(row)
        counts["new_declared_target"] += 1
        sources[target.source.split("_")[0]] += 1
        scenarios[str(row.get("scenario"))] += 1
        declared_net_r.append(target.net_r)

    summary = {
        "candidate": "candidate-04-v44-causal-target-registry-enrichment",
        "controlled_change": (
            "entries, directions, stops, scenarios and timing frozen; declare "
            "a target only when strict V34 execution registry has none"
        ),
        "counts": dict(counts),
        "declared_source_prefix_counts": dict(sources),
        "declared_scenario_counts": dict(scenarios),
        "declared_net_r": {
            "minimum": min(declared_net_r) if declared_net_r else None,
            "maximum": max(declared_net_r) if declared_net_r else None,
            "median": (
                float(pd.Series(declared_net_r).median())
                if declared_net_r else None
            ),
        },
        "future_information_used": False,
        "measured_move_target_used": False,
    }
    return enriched, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signals", type=Path, required=True)
    parser.add_argument("--base-config", type=Path, required=True)
    parser.add_argument("--rich-dir", type=Path, required=True)
    parser.add_argument("--kline-dir", type=Path, required=True)
    parser.add_argument("--build-start", required=True)
    parser.add_argument("--build-end", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--download-klines", action="store_true")
    parser.add_argument("--cost-rate", type=float, default=DEFAULT_COST_RATE)
    parser.add_argument(
        "--minimum-net-r",
        type=float,
        default=DEFAULT_MINIMUM_NET_R,
    )
    args = parser.parse_args()

    rows = json.loads(args.signals.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise SystemExit("signals must be a JSON list of objects")
    config = v22.Config.load(args.base_config)
    build_start = pd.Timestamp(args.build_start, tz="UTC")
    build_end = (
        pd.Timestamp(args.build_end, tz="UTC")
        + pd.Timedelta(hours=23, minutes=59)
    )
    data, _ = v22._load_data(
        args.rich_dir,
        args.kline_dir,
        build_start,
        build_end,
        config,
        download_klines=args.download_klines,
    )
    enriched, summary = enrich_signals(
        rows,
        data,
        config,
        cost_rate=args.cost_rate,
        minimum_net_r=args.minimum_net_r,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "signals.json").write_text(
        json.dumps(enriched, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
