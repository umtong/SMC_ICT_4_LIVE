#!/usr/bin/env python3
"""Diagnose recorded Nautilus FAR trade paths on already-opened development data.

This is explicitly a TEMPORARY_TEST. It may identify a causal failure mode, but
cannot advance a candidate, establish alpha or restore validation eligibility.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import json
from math import isfinite
from pathlib import Path
from statistics import median
import sys
from typing import Any

import pandas as pd

HERE = Path(__file__).resolve().parent
CORE = HERE.parent / "core_far_continuous_v1"
SOURCE = HERE.parent / "session_portfolio_v1"
for path in (HERE, CORE, SOURCE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from aggregate import (  # noqa: E402
    csv_rows,
    decimal_value,
    first_timestamp_ns,
    lifecycle_entry_fills,
    load_json,
    load_object,
    match_position_to_fill,
    plan_rows,
    symbol_from_instrument,
    write_json,
)
from run_leadership_scdam_base import SYMBOLS, load_symbol_bars  # noqa: E402

UTC = timezone.utc
MINUTE_NS = 60 * 1_000_000_000
HORIZONS_MINUTES = (1, 5, 15, 30, 60, 240)


def utc_timestamp(ns: int) -> pd.Timestamp:
    return pd.Timestamp(ns, unit="ns", tz="UTC")


def minutes_between(start_ns: int, end_ns: int | None) -> float | None:
    if end_ns is None:
        return None
    return (end_ns - start_ns) / MINUTE_NS


def first_threshold_time(
    frame: pd.DataFrame,
    *,
    entry: float,
    risk: float,
    direction: str,
    threshold_r: float,
) -> int | None:
    if frame.empty:
        return None
    if direction == "LONG":
        favorable = frame["high"] - entry
    else:
        favorable = entry - frame["low"]
    reached = favorable >= threshold_r * risk
    if not bool(reached.any()):
        return None
    return int(frame.index[reached.argmax()].value)


def first_boundary_time(
    frame: pd.DataFrame,
    *,
    level: float,
    direction: str,
) -> int | None:
    if frame.empty:
        return None
    breached = frame["low"] <= level if direction == "LONG" else frame["high"] >= level
    if not bool(breached.any()):
        return None
    return int(frame.index[breached.argmax()].value)


def safe_ratio(numerator: float, denominator: float) -> float | None:
    if denominator <= 0.0:
        return None
    value = numerator / denominator
    return value if isfinite(value) else None


class DailyBars:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.cache: dict[tuple[str, date], pd.DataFrame] = {}
        self.manifest: list[dict[str, Any]] = []

    def one_day(self, symbol: str, day: date) -> pd.DataFrame:
        key = (symbol, day)
        if key not in self.cache:
            frame, manifest = load_symbol_bars(
                symbol,
                day,
                day,
                self.data_dir,
            )
            self.cache[key] = frame
            self.manifest.extend(manifest)
        return self.cache[key]

    def interval(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        cursor = start
        while cursor <= end:
            frames.append(self.one_day(symbol, cursor))
            cursor += timedelta(days=1)
        return pd.concat(frames).sort_index()


def exit_type(
    orders: list[dict[str, str]],
    *,
    instrument_id: str,
    closed_ns: int,
) -> str:
    matches: list[tuple[int, str]] = []
    for row in orders:
        if row.get("instrument_id") != instrument_id:
            continue
        if str(row.get("status", "")).upper() != "FILLED":
            continue
        tags = str(row.get("tags", "")).upper()
        if "STOP_LOSS" not in tags and "TAKE_PROFIT" not in tags:
            continue
        try:
            ts_ns = first_timestamp_ns(row, ("ts_last", "ts_init"))
        except ValueError:
            continue
        label = "STOP_LOSS" if "STOP_LOSS" in tags else "TAKE_PROFIT"
        matches.append((abs(ts_ns - closed_ns), label))
    if not matches:
        return "OTHER"
    matches.sort(key=lambda item: item[0])
    return matches[0][1]


def close_at_or_before(frame: pd.DataFrame, timestamp: pd.Timestamp) -> float:
    selected = frame.loc[frame.index <= timestamp, "close"]
    if selected.empty:
        raise ValueError(f"no completed close at or before {timestamp}")
    return float(selected.iloc[-1])


def horizon_snapshot(
    returns: pd.DataFrame,
    *,
    entry_ts: pd.Timestamp,
    minutes: int,
    candidate: str,
    peers: list[str],
) -> dict[str, Any]:
    cutoff = entry_ts + pd.Timedelta(minutes=minutes)
    selected = returns.loc[(returns.index > entry_ts) & (returns.index <= cutoff)]
    if selected.empty:
        return {
            "minutes": minutes,
            "candidate_directional_return": None,
            "peer_median_directional_return": None,
            "aligned_peer_count": 0,
            "unanimous_peer_support_fraction": None,
        }
    row = selected.iloc[-1]
    peer_values = [float(row[symbol]) for symbol in peers]
    unanimous = (selected[peers] > 0.0).all(axis=1)
    return {
        "minutes": minutes,
        "candidate_directional_return": float(row[candidate]),
        "peer_median_directional_return": median(peer_values),
        "aligned_peer_count": sum(value > 0.0 for value in peer_values),
        "unanimous_peer_support_fraction": float(unanimous.mean()),
    }


def diagnose_trade(
    *,
    block: str,
    position: dict[str, str],
    plan: dict[str, Any],
    bars: DailyBars,
    orders: list[dict[str, str]],
) -> dict[str, Any]:
    symbol = symbol_from_instrument(str(position["instrument_id"]))
    opened_ns = first_timestamp_ns(
        position,
        ("ts_opened", "ts_init", "open_time", "entry_time"),
    )
    closed_ns = first_timestamp_ns(
        position,
        ("ts_closed", "ts_last", "close_time", "exit_time"),
    )
    opened_ts = utc_timestamp(opened_ns)
    closed_ts = utc_timestamp(closed_ns)
    start_day = opened_ts.date()
    end_day = closed_ts.date()
    frames = {
        market: bars.interval(market, start_day, end_day)
        for market in SYMBOLS
    }

    direction = str(plan["direction"]).upper()
    sign = 1.0 if direction == "LONG" else -1.0
    entry = float(position["avg_px_open"])
    terminal = float(position["avg_px_close"])
    stop = float(plan["stop"])
    target = float(plan["target"])
    risk = entry - stop if direction == "LONG" else stop - entry
    target_distance = target - entry if direction == "LONG" else entry - target
    if risk <= 0.0 or target_distance <= 0.0:
        raise ValueError(
            f"{plan['scenario_id']}: invalid realized geometry "
            f"entry={entry} stop={stop} target={target}"
        )

    candidate_frame = frames[symbol]
    pre_terminal = candidate_frame.loc[
        (candidate_frame.index > opened_ts) & (candidate_frame.index < closed_ts)
    ]
    if pre_terminal.empty:
        favorable = 0.0
        adverse = 0.0
    elif direction == "LONG":
        favorable = max(0.0, float(pre_terminal["high"].max()) - entry)
        adverse = max(0.0, entry - float(pre_terminal["low"].min()))
    else:
        favorable = max(0.0, entry - float(pre_terminal["low"].min()))
        adverse = max(0.0, float(pre_terminal["high"].max()) - entry)
    terminal_directional_move = sign * (terminal - entry)
    favorable_including_terminal = max(favorable, terminal_directional_move, 0.0)
    adverse_including_terminal = max(adverse, -terminal_directional_move, 0.0)

    threshold_times = {
        f"{threshold:g}R": first_threshold_time(
            pre_terminal,
            entry=entry,
            risk=risk,
            direction=direction,
            threshold_r=threshold,
        )
        for threshold in (0.25, 0.5, 1.0)
    }
    details = plan.get("details") if isinstance(plan.get("details"), dict) else {}
    leadership = (
        details.get("market_leadership")
        if isinstance(details.get("market_leadership"), dict)
        else {}
    )
    pool_level = float(details["pool_level"])
    zone_low = float(details["zone_low"])
    zone_high = float(details["zone_high"])
    sweep_extreme = float(details["sweep_extreme"])
    pool_breach_ns = first_boundary_time(
        pre_terminal,
        level=pool_level,
        direction=direction,
    )
    zone_level = zone_low if direction == "LONG" else zone_high
    zone_traversal_ns = first_boundary_time(
        pre_terminal,
        level=zone_level,
        direction=direction,
    )
    sweep_retest_ns = first_boundary_time(
        pre_terminal,
        level=sweep_extreme,
        direction=direction,
    )

    reference = {
        market: close_at_or_before(frame, opened_ts)
        for market, frame in frames.items()
    }
    closes = pd.concat(
        {
            market: frame.loc[
                (frame.index >= opened_ts) & (frame.index <= closed_ts),
                "close",
            ]
            for market, frame in frames.items()
        },
        axis=1,
        join="inner",
    ).dropna()
    returns = pd.DataFrame(index=closes.index)
    for market in SYMBOLS:
        returns[market] = sign * (closes[market] / reference[market] - 1.0)
    peers = [market for market in SYMBOLS if market != symbol]
    post_entry = returns.loc[returns.index > opened_ts]
    unanimous = (
        (post_entry[peers] > 0.0).all(axis=1)
        if not post_entry.empty
        else pd.Series(dtype=bool)
    )
    first_support_break_ns: int | None = None
    if not unanimous.empty:
        broken = ~unanimous
        if bool(broken.any()):
            first_support_break_ns = int(broken.index[broken.argmax()].value)

    horizons = {
        str(minutes): horizon_snapshot(
            returns,
            entry_ts=opened_ts,
            minutes=minutes,
            candidate=symbol,
            peers=peers,
        )
        for minutes in HORIZONS_MINUTES
    }
    terminal_snapshot = None
    if not returns.empty:
        row = returns.iloc[-1]
        terminal_snapshot = {
            "candidate_directional_return": float(row[symbol]),
            "peer_median_directional_return": median(
                float(row[peer]) for peer in peers
            ),
            "aligned_peer_count": sum(float(row[peer]) > 0.0 for peer in peers),
        }

    costed_loss_per_unit = float(
        decimal_value(plan["expected_total_loss"]) / decimal_value(plan["quantity"])
    )
    outcome = exit_type(
        orders,
        instrument_id=str(position["instrument_id"]),
        closed_ns=closed_ns,
    )
    half_r_ns = threshold_times["0.5R"]
    return {
        "block": block,
        "scenario_id": str(plan["scenario_id"]),
        "symbol": symbol,
        "session_family": str(details.get("pool_source", "UNSPECIFIED")),
        "direction": direction,
        "outcome": outcome,
        "opened_utc": opened_ts.isoformat(),
        "closed_utc": closed_ts.isoformat(),
        "duration_minutes": minutes_between(opened_ns, closed_ns),
        "entry": entry,
        "stop": stop,
        "target": target,
        "structural_risk": risk,
        "costed_loss_per_unit": costed_loss_per_unit,
        "target_distance": target_distance,
        "mfe_price": favorable_including_terminal,
        "mae_price": adverse_including_terminal,
        "mfe_structural_r": safe_ratio(favorable_including_terminal, risk),
        "mae_structural_r": safe_ratio(adverse_including_terminal, risk),
        "mfe_costed_r": safe_ratio(favorable_including_terminal, costed_loss_per_unit),
        "target_progress": safe_ratio(favorable_including_terminal, target_distance),
        "first_threshold_minutes": {
            key: minutes_between(opened_ns, value)
            for key, value in threshold_times.items()
        },
        "pool_boundary_breached": pool_breach_ns is not None,
        "pool_boundary_breach_minutes": minutes_between(opened_ns, pool_breach_ns),
        "pool_boundary_breached_before_half_r": (
            pool_breach_ns is not None
            and (half_r_ns is None or pool_breach_ns < half_r_ns)
        ),
        "displacement_zone_retraversed": zone_traversal_ns is not None,
        "displacement_zone_retraversal_minutes": minutes_between(
            opened_ns,
            zone_traversal_ns,
        ),
        "sweep_extreme_retested": sweep_retest_ns is not None,
        "sweep_extreme_retest_minutes": minutes_between(opened_ns, sweep_retest_ns),
        "post_entry_peer_support": {
            "first_unanimous_support_break_minutes": minutes_between(
                opened_ns,
                first_support_break_ns,
            ),
            "unanimous_fraction_full_position": (
                float(unanimous.mean()) if not unanimous.empty else None
            ),
            "horizons": horizons,
            "terminal": terminal_snapshot,
        },
        "entry_semantics": {
            "reason": leadership.get("reason"),
            "stop_model": details.get("stop_model"),
            "candidate_event_move": leadership.get("candidate_event_move"),
            "confirmation_impulse": leadership.get("confirmation_impulse"),
            "event_direction_rank": leadership.get("event_direction_rank"),
            "trailing_direction_rank": leadership.get("trailing_direction_rank"),
            "event_path_efficiency": leadership.get("event_path_efficiency"),
            "event_standardized_displacement": leadership.get(
                "event_standardized_displacement"
            ),
            "candidate_trailing_direction_score": (
                leadership.get("directional_trend_scores", {}).get(symbol)
                if isinstance(leadership.get("directional_trend_scores"), dict)
                else None
            ),
            "market_median_trailing_direction_score": (
                median(
                    float(value)
                    for value in leadership["directional_trend_scores"].values()
                )
                if isinstance(leadership.get("directional_trend_scores"), dict)
                and leadership["directional_trend_scores"]
                else None
            ),
        },
    }


def classify_path(record: dict[str, Any]) -> str:
    if record["outcome"] == "TAKE_PROFIT":
        return "TARGET_DELIVERED"
    mfe = float(record["mfe_structural_r"] or 0.0)
    if mfe < 0.25:
        return "NO_MEANINGFUL_POST_ENTRY_DELIVERY"
    if mfe < 0.5:
        return "MINOR_DELIVERY_THEN_FAILURE"
    if mfe < 1.0:
        return "PARTIAL_DELIVERY_THEN_FAILURE"
    return "ONE_R_OR_MORE_THEN_FAILURE"


def median_or_none(values: list[float | None]) -> float | None:
    selected = [float(value) for value in values if value is not None]
    return median(selected) if selected else None


def diagnose(results_root: Path, data_dir: Path) -> dict[str, Any]:
    aggregate_result = load_object(CORE / "aggregate.json")
    if aggregate_result.get("classification") != "DEVELOPMENT_GATE_FAILED":
        raise ValueError("path diagnosis is authorized only for the rejected development candidate")
    protocol = load_object(CORE / "protocol.json")
    bars = DailyBars(data_dir)
    records: list[dict[str, Any]] = []
    errors: list[str] = []

    for block in protocol["selection"]["blocks"]:
        root = results_root / block
        plans = plan_rows(load_json(root / "submitted_plans.json"))
        by_scenario = {str(plan["scenario_id"]): plan for plan in plans}
        fills = lifecycle_entry_fills(load_object(root / "order_lifecycle.json"))
        positions = csv_rows(root / "positions.csv")
        orders = csv_rows(root / "orders.csv")
        used: set[str] = set()
        for index, position in enumerate(positions):
            try:
                symbol = symbol_from_instrument(str(position["instrument_id"]))
                opened_ns = first_timestamp_ns(
                    position,
                    ("ts_opened", "ts_init", "open_time", "entry_time"),
                )
                fill = match_position_to_fill(
                    symbol=symbol,
                    opened_ns=opened_ns,
                    fills=fills,
                    used_scenarios=used,
                )
                scenario_id = str(fill["scenario_id"])
                plan = by_scenario[scenario_id]
                record = diagnose_trade(
                    block=block,
                    position=position,
                    plan=plan,
                    bars=bars,
                    orders=orders,
                )
                record["path_classification"] = classify_path(record)
                records.append(record)
                used.add(scenario_id)
            except Exception as exc:
                errors.append(f"{block} position[{index}]: {type(exc).__name__}: {exc}")

    losses = [record for record in records if record["outcome"] == "STOP_LOSS"]
    winners = [record for record in records if record["outcome"] == "TAKE_PROFIT"]
    classification_counts = Counter(
        str(record["path_classification"]) for record in records
    )
    stop_model_counts: dict[str, dict[str, int]] = {}
    for record in records:
        model = str(record["entry_semantics"]["stop_model"])
        bucket = stop_model_counts.setdefault(
            model,
            {"trades": 0, "wins": 0, "losses": 0},
        )
        bucket["trades"] += 1
        if record["outcome"] == "TAKE_PROFIT":
            bucket["wins"] += 1
        elif record["outcome"] == "STOP_LOSS":
            bucket["losses"] += 1

    losses_before_half_r = sum(
        bool(record["pool_boundary_breached_before_half_r"])
        for record in losses
    )
    losses_peer_break_5m = sum(
        (
            record["post_entry_peer_support"][
                "first_unanimous_support_break_minutes"
            ]
            is not None
            and float(
                record["post_entry_peer_support"][
                    "first_unanimous_support_break_minutes"
                ]
            )
            <= 5.0
        )
        for record in losses
    )
    result = {
        "schema": "candidate-11-core-far-path-diagnostic-v1",
        "candidate": aggregate_result["candidate"],
        "research_stage": "TEMPORARY_TEST",
        "development_data_opened": True,
        "can_advance_candidate": False,
        "can_claim_alpha": False,
        "validation_eligible": False,
        "success_claim": False,
        "purpose": (
            "Separate no-follow-through, partial-delivery, target-distance and "
            "post-entry peer-persistence failures after the development gate rejected FAR."
        ),
        "records_complete": not errors and len(records) == int(
            aggregate_result["closed_trades"]
        ),
        "errors": errors,
        "trades": len(records),
        "wins": len(winners),
        "losses": len(losses),
        "path_classification_counts": dict(classification_counts),
        "loss_diagnostics": {
            "median_mfe_structural_r": median_or_none(
                [record["mfe_structural_r"] for record in losses]
            ),
            "median_target_progress": median_or_none(
                [record["target_progress"] for record in losses]
            ),
            "median_duration_minutes": median_or_none(
                [record["duration_minutes"] for record in losses]
            ),
            "no_meaningful_delivery_count": sum(
                float(record["mfe_structural_r"] or 0.0) < 0.25
                for record in losses
            ),
            "below_half_r_delivery_count": sum(
                float(record["mfe_structural_r"] or 0.0) < 0.5
                for record in losses
            ),
            "pool_boundary_failed_before_half_r_count": losses_before_half_r,
            "unanimous_peer_support_broke_within_5m_count": losses_peer_break_5m,
            "median_unanimous_peer_support_fraction": median_or_none(
                [
                    record["post_entry_peer_support"][
                        "unanimous_fraction_full_position"
                    ]
                    for record in losses
                ]
            ),
        },
        "stop_model_outcomes": stop_model_counts,
        "records": records,
        "data_manifest": bars.manifest,
        "interpretation_contract": [
            "Thresholds such as 0.25R and 0.5R classify paths only; they are not admission parameters.",
            "This diagnostic cannot delete losing scenarios or authorize a new candidate.",
            "A next candidate is allowed only if one market-state assumption is replaced causally.",
        ],
    }
    return result


def render_markdown(result: dict[str, Any]) -> str:
    diagnostics = result["loss_diagnostics"]
    lines = [
        "# Candidate 11 core FAR path diagnostic",
        "",
        "**TEMPORARY_TEST — cannot advance the candidate or claim alpha**",
        "",
        f"- trades: `{result['trades']}`",
        f"- wins / losses: `{result['wins']} / {result['losses']}`",
        f"- records complete: `{result['records_complete']}`",
        f"- median losing MFE: `{diagnostics['median_mfe_structural_r']}` structural R",
        f"- median losing target progress: `{diagnostics['median_target_progress']}`",
        f"- losses below 0.25R: `{diagnostics['no_meaningful_delivery_count']}`",
        f"- losses below 0.5R: `{diagnostics['below_half_r_delivery_count']}`",
        (
            "- losses whose reclaimed pool failed before 0.5R: "
            f"`{diagnostics['pool_boundary_failed_before_half_r_count']}`"
        ),
        (
            "- losses whose unanimous peer support broke within five minutes: "
            f"`{diagnostics['unanimous_peer_support_broke_within_5m_count']}`"
        ),
        "",
        "## Trade paths",
        "",
    ]
    for record in result["records"]:
        lines.append(
            f"- {record['scenario_id']}: {record['outcome']}, "
            f"MFE={record['mfe_structural_r']:.4f}R, "
            f"target_progress={record['target_progress']:.4f}, "
            f"class={record['path_classification']}"
        )
    lines.extend(
        (
            "",
            "## Interpretation limits",
            "",
            *[f"- {item}" for item in result["interpretation_contract"]],
            "",
        )
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = diagnose(args.results, args.data_dir)
    write_json(args.output, result)
    args.output.with_suffix(".md").write_text(
        render_markdown(result),
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0 if result["records_complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
