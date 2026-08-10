#!/usr/bin/env python3
"""Candidate-57 causal-episode forensic replay for the public Winner15m family.

This is not a gate.  It replays the exact source policy through NautilusTrader,
then independently enumerates every source transition and every 4-of-5 near
miss from the same completed candles.  Executed, collision-rejected, slot-
blocked and selector-rejected episodes are preserved with causal state and
post-outcome paths kept in separate fields.
"""
from __future__ import annotations

from collections import defaultdict
import copy
import csv
from datetime import date, datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
C51 = ROOT / "research" / "candidate-51"
for path in (C51, HERE):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

from router import (  # type: ignore  # exact external-v17 router is materialized by workflow
    BarObservation,
    RouteConfig,
    _aggregate_complete,
    _winner_condition,
)
import run as c51run  # type: ignore

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False, default=str) + "\n",
        encoding="utf-8",
    )


def parse_money(value: Any) -> float | None:
    if value is None:
        return None
    match = _NUMBER.search(str(value).replace(",", "").replace("_", ""))
    if match is None:
        return None
    try:
        number = float(match.group(0))
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def dt_ns(value: Any) -> int:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    else:
        stamp = stamp.tz_convert("UTC")
    return int(stamp.value)


def source_config() -> dict[str, Any]:
    base = json.loads((C51 / "config.json").read_text(encoding="utf-8"))
    config = copy.deepcopy(base)
    strategy = config.setdefault("strategy", {})
    for key in (
        "sma_offset_low",
        "sma_offset_high",
        "sma_stop_min_fraction",
        "sma_stop_max_fraction",
        "sma_stop_atr_buffer",
    ):
        strategy.pop(key, None)
    strategy.update(
        {
            "cooldown_minutes": 0,
            "max_hold_minutes": 360,
            "funding_flatten_minute": 60,
            "funding_blackout_before_minutes": -1,
            "funding_blackout_after_minutes": -1,
            "external_family_mode": "winner",
            "winner_bucket_minutes": 15,
            "winner_ema_fast": 10,
            "winner_ema_slow": 30,
            "winner_macd_fast": 12,
            "winner_macd_slow": 26,
            "winner_macd_signal": 9,
            "winner_roc_period": 3,
            "winner_roc_threshold": 0.10,
            "winner_adx_period": 14,
            "winner_adx_threshold": 18.0,
            "winner_volume_period": 20,
            "winner_volume_ratio": 1.0,
            "winner_stop_fraction": 0.025,
            "winner_initial_target_fraction": 0.080,
            "winner_trailing_positive": 0.005,
            "winner_trailing_offset": 0.018,
            "winner_roi_0": 0.080,
            "winner_roi_480": 0.050,
            "winner_roi_1440": 0.030,
            "winner_roi_4320": 0.0,
        }
    )
    return config


def route_config() -> RouteConfig:
    return RouteConfig(
        external_family_mode="winner",
        winner_bucket_minutes=15,
        winner_ema_fast=10,
        winner_ema_slow=30,
        winner_macd_fast=12,
        winner_macd_slow=26,
        winner_macd_signal=9,
        winner_roc_period=3,
        winner_roc_threshold=0.10,
        winner_adx_period=14,
        winner_adx_threshold=18.0,
        winner_volume_period=20,
        winner_volume_ratio=1.0,
        winner_stop_fraction=0.025,
        winner_initial_target_fraction=0.080,
    )


def run_source_control(
    *, start: date, end: date, cache: Path, work: Path, output: Path
) -> None:
    config_path = work / "winner_source_15m.json"
    dump(config_path, source_config())
    command = [
        sys.executable,
        str(C51 / "launch.py"),
        "--config",
        str(config_path),
        "--start",
        str(start),
        "--end",
        str(end),
        "--cache",
        str(cache),
        "--output",
        str(output),
        "--workspace",
        str(work / "workspace"),
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(C51)
    completed = subprocess.run(command, cwd=ROOT, env=env, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"source-control Nautilus replay failed: {completed.returncode}")


def bars_from_frame(frame: pd.DataFrame) -> tuple[list[BarObservation], dict[int, int]]:
    bars: list[BarObservation] = []
    index_by_ts: dict[int, int] = {}
    for i, row in enumerate(frame.itertuples(index=False)):
        ts = dt_ns(getattr(row, "close_time_dt"))
        bars.append(
            BarObservation(
                ts_event=ts,
                open=float(getattr(row, "open")),
                high=float(getattr(row, "high")),
                low=float(getattr(row, "low")),
                close=float(getattr(row, "close")),
                volume=float(getattr(row, "volume")),
            )
        )
        index_by_ts[ts] = i
    return bars, index_by_ts


def exact_score(diag: Mapping[str, Any], config: RouteConfig) -> float:
    reward_r = config.winner_initial_target_fraction / config.winner_stop_fraction
    quality = (
        max(0.0, float(diag["adx"]) - config.winner_adx_threshold) / 10.0
        + max(0.0, abs(float(diag["roc"])) - config.winner_roc_threshold)
        + max(0.0, float(diag["volume_ratio"]) - config.winner_volume_ratio)
    )
    return reward_r * 10.0 + quality


def votes(diag: Mapping[str, Any], side: int, config: RouteConfig) -> dict[str, bool]:
    if side > 0:
        return {
            "ema": float(diag["ema_fast"]) > float(diag["ema_slow"]),
            "macd": float(diag["macd"]) > float(diag["macd_signal"]),
            "roc": float(diag["roc"]) > config.winner_roc_threshold,
            "adx": float(diag["adx"]) > config.winner_adx_threshold,
            "volume": (
                float(diag["volume_ratio"]) > config.winner_volume_ratio
                and float(diag["volume"]) > 0.0
            ),
        }
    return {
        "ema": float(diag["ema_fast"]) < float(diag["ema_slow"]),
        "macd": float(diag["macd"]) < float(diag["macd_signal"]),
        "roc": float(diag["roc"]) < -config.winner_roc_threshold,
        "adx": float(diag["adx"]) > config.winner_adx_threshold,
        "volume": (
            float(diag["volume_ratio"]) > config.winner_volume_ratio
            and float(diag["volume"]) > 0.0
        ),
    }


def future_path(
    minute_bars: Sequence[BarObservation],
    index_by_ts: Mapping[int, int],
    signal_ts: int,
    side: int,
    entry: float,
    horizon: int = 360,
) -> dict[str, Any]:
    signal_index = index_by_ts.get(signal_ts)
    if signal_index is None:
        return {"available": False}
    path = minute_bars[signal_index + 1 : signal_index + 1 + horizon]
    if not path:
        return {"available": False}

    close_returns: dict[str, float | None] = {}
    for minute in (1, 3, 5, 10, 15, 30, 60, 120, 240, 360):
        if minute <= len(path):
            close_returns[str(minute)] = side * (float(path[minute - 1].close) - entry) / entry
        else:
            close_returns[str(minute)] = None

    mfe = -math.inf
    mae = math.inf
    mfe_minute = None
    mae_minute = None
    first_hits: dict[str, int | None] = {
        "mfe_0p25": None,
        "mfe_0p50": None,
        "mfe_1p00": None,
        "mfe_1p80": None,
        "mae_0p25": None,
        "mae_0p50": None,
        "mae_1p00": None,
        "mae_2p50": None,
    }
    for minute, bar in enumerate(path, start=1):
        favourable = float(bar.high) if side > 0 else float(bar.low)
        adverse = float(bar.low) if side > 0 else float(bar.high)
        favourable_move = side * (favourable - entry) / entry
        adverse_move = side * (adverse - entry) / entry
        if favourable_move > mfe:
            mfe = favourable_move
            mfe_minute = minute
        if adverse_move < mae:
            mae = adverse_move
            mae_minute = minute
        for label, threshold in (
            ("mfe_0p25", 0.0025),
            ("mfe_0p50", 0.0050),
            ("mfe_1p00", 0.0100),
            ("mfe_1p80", 0.0180),
        ):
            if first_hits[label] is None and favourable_move >= threshold:
                first_hits[label] = minute
        for label, threshold in (
            ("mae_0p25", -0.0025),
            ("mae_0p50", -0.0050),
            ("mae_1p00", -0.0100),
            ("mae_2p50", -0.0250),
        ):
            if first_hits[label] is None and adverse_move <= threshold:
                first_hits[label] = minute

    # Diagnostic-only counterfactual of the exact source stop/target/trail.
    # Same-minute ambiguity is resolved adversely: an already-active trail or
    # hard stop is checked before new favourable progress.  A trail activated
    # by a minute cannot be hit by an earlier path within that same minute.
    stop_fraction = 0.025
    target_fraction = 0.080
    trail_activation = 0.018
    trail_gap = 0.005
    trail_active = False
    trail_best: float | None = None
    exit_fraction = side * (float(path[-1].close) - entry) / entry
    exit_minute = len(path)
    exit_reason = "HORIZON_360_CLOSE"
    for minute, bar in enumerate(path, start=1):
        adverse = float(bar.low) if side > 0 else float(bar.high)
        favourable = float(bar.high) if side > 0 else float(bar.low)
        adverse_move = side * (adverse - entry) / entry
        favourable_move = side * (favourable - entry) / entry
        if adverse_move <= -stop_fraction:
            exit_fraction = -stop_fraction
            exit_minute = minute
            exit_reason = "SOURCE_STOP_DIAGNOSTIC"
            break
        if trail_active and trail_best is not None:
            trail_stop = trail_best * (1.0 - side * trail_gap)
            trail_hit = float(bar.low) <= trail_stop if side > 0 else float(bar.high) >= trail_stop
            if trail_hit:
                exit_fraction = side * (trail_stop - entry) / entry
                exit_minute = minute
                exit_reason = "SOURCE_TRAIL_DIAGNOSTIC"
                break
        if favourable_move >= target_fraction:
            exit_fraction = target_fraction
            exit_minute = minute
            exit_reason = "SOURCE_TARGET_DIAGNOSTIC"
            break
        if not trail_active and favourable_move >= trail_activation:
            trail_active = True
            trail_best = favourable
        elif trail_active:
            assert trail_best is not None
            trail_best = max(trail_best, favourable) if side > 0 else min(trail_best, favourable)

    roundtrip_cost_fraction = 0.0015
    return {
        "available": True,
        "bars": len(path),
        "close_return_by_minute": close_returns,
        "mfe_fraction": mfe,
        "mfe_minute": mfe_minute,
        "mae_fraction": mae,
        "mae_minute": mae_minute,
        "first_hits": first_hits,
        "diagnostic_source_exit_fraction_before_cost": exit_fraction,
        "diagnostic_source_exit_fraction_after_15bp": exit_fraction - roundtrip_cost_fraction,
        "diagnostic_source_exit_minute": exit_minute,
        "diagnostic_source_exit_reason": exit_reason,
    }


def read_actual(output: Path) -> tuple[dict[tuple[str, int], dict[str, Any]], list[dict[str, Any]]]:
    scenarios = json.loads((output / "closed_scenarios.json").read_text(encoding="utf-8"))
    events = [
        json.loads(line)
        for line in (output / "scenario_events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    events_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        scenario_id = event.get("scenario_id")
        if scenario_id:
            events_by_id[str(scenario_id)].append(event)

    actual: dict[tuple[str, int], dict[str, Any]] = {}
    intervals: list[dict[str, Any]] = []
    for scenario in scenarios:
        scenario_id = str(scenario.get("scenario_id"))
        grouped = events_by_id.get(scenario_id, [])
        opened = next((item for item in grouped if item.get("event_type") == "POSITION_OPENED"), None)
        closed = next((item for item in reversed(grouped) if item.get("event_type") == "POSITION_CLOSED"), None)
        open_ts = int(opened["ts_event"]) if opened else int(scenario.get("episode_ts") or 0)
        close_ts = int(closed["ts_event"]) if closed else int(scenario.get("ts_event") or 0)
        record = {
            "scenario_id": scenario_id,
            "symbol": str(scenario.get("symbol")),
            "side": int(scenario.get("side") or 0),
            "episode_ts": int(scenario.get("episode_ts") or 0),
            "open_ts": open_ts,
            "close_ts": close_ts,
            "realized_pnl_usdt": parse_money(scenario.get("realized_pnl")),
            "entry_reference": scenario.get("entry_reference"),
            "stop": scenario.get("stop"),
            "target": scenario.get("target"),
            "score": scenario.get("score"),
            "diagnostics": scenario.get("diagnostics"),
        }
        actual[(record["symbol"], record["episode_ts"])] = record
        intervals.append(record)
    intervals.sort(key=lambda item: (item["open_ts"], item["close_ts"]))
    return actual, intervals


def blocked_reason(
    *, symbol: str, ts: int, actual: Mapping[tuple[str, int], Any],
    intervals: Sequence[Mapping[str, Any]], candidates_at_ts: Sequence[Mapping[str, Any]],
) -> str:
    if (symbol, ts) in actual:
        return "ENTERED"
    if any((str(item["symbol"]), ts) in actual for item in candidates_at_ts):
        return "ARBITRATION_REJECTED_SAME_BOUNDARY"
    for item in intervals:
        if int(item["open_ts"]) <= ts <= int(item["close_ts"]):
            return "GLOBAL_SLOT_OCCUPIED"
    return "FLAT_BUT_NOT_ENTERED_REQUIRES_EVENT_AUDIT"


def numeric_summary(values: Iterable[float]) -> dict[str, Any]:
    clean = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not clean:
        return {"count": 0}
    series = pd.Series(clean, dtype=float)
    return {
        "count": len(clean),
        "min": float(series.min()),
        "q25": float(series.quantile(0.25)),
        "median": float(series.median()),
        "q75": float(series.quantile(0.75)),
        "max": float(series.max()),
        "mean": float(series.mean()),
    }


def classify_actual(record: Mapping[str, Any]) -> str:
    pnl = record.get("actual", {}).get("realized_pnl_usdt")
    if pnl is None:
        return "NOT_ENTERED"
    return "ACTUAL_WIN" if float(pnl) > 0.0 else "ACTUAL_LOSS" if float(pnl) < 0.0 else "ACTUAL_FLAT"


def analyze(
    *, frames: Mapping[str, pd.DataFrame], output: Path, evidence: Path
) -> dict[str, Any]:
    config = route_config()
    minute: dict[str, list[BarObservation]] = {}
    index_by_ts: dict[str, dict[int, int]] = {}
    candles: dict[str, list[BarObservation]] = {}
    for symbol in SYMBOLS:
        minute[symbol], index_by_ts[symbol] = bars_from_frame(frames[symbol])
        candles[symbol] = _aggregate_complete(minute[symbol], config.winner_bucket_minutes)

    actual, intervals = read_actual(output)
    source_candidates: list[dict[str, Any]] = []
    near_misses: list[dict[str, Any]] = []

    for symbol in SYMBOLS:
        items = candles[symbol]
        minimum = max(
            config.winner_ema_slow + 3,
            config.winner_macd_slow + config.winner_macd_signal + 3,
            config.winner_adx_period * 2 + 3,
            config.winner_volume_period + 3,
        )
        previous_near = {1: False, -1: False}
        for index in range(minimum, len(items)):
            side, diag = _winner_condition(items, config, index)
            previous_side, _ = _winner_condition(items, config, index - 1)
            ts = int(items[index].ts_event)
            if not int(diag.get("ready", 0)):
                continue
            if side in (-1, 1) and previous_side != side:
                source_candidates.append(
                    {
                        "symbol": symbol,
                        "episode_ts": ts,
                        "side": side,
                        "entry_reference": float(items[index].close),
                        "score": exact_score(diag, config),
                        "causal_state": dict(diag),
                        "future_path_post_outcome_only": future_path(
                            minute[symbol], index_by_ts[symbol], ts, side, float(items[index].close)
                        ),
                    }
                )
            # Selector false-negative map: transition into exactly 4/5 source
            # conditions.  Post-outcome path is diagnostic and cannot be used
            # as a causal feature in the same interval.
            if side == 0:
                vote_rows = {direction: votes(diag, direction, config) for direction in (1, -1)}
                for direction, row in vote_rows.items():
                    is_near = sum(row.values()) == 4
                    if is_near and not previous_near[direction]:
                        near_misses.append(
                            {
                                "symbol": symbol,
                                "episode_ts": ts,
                                "side": direction,
                                "entry_reference": float(items[index].close),
                                "votes": row,
                                "missing_components": [key for key, value in row.items() if not value],
                                "causal_state": dict(diag),
                                "future_path_post_outcome_only": future_path(
                                    minute[symbol], index_by_ts[symbol], ts, direction, float(items[index].close)
                                ),
                            }
                        )
                    previous_near[direction] = is_near
            else:
                previous_near[1] = False
                previous_near[-1] = False

    by_ts: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for item in source_candidates:
        by_ts[int(item["episode_ts"])].append(item)
    for ts, group in by_ts.items():
        group.sort(key=lambda item: (-float(item["score"]), SYMBOLS.index(str(item["symbol"]))))
        same_direction = defaultdict(int)
        for item in group:
            same_direction[int(item["side"])] += 1
        for rank, item in enumerate(group, start=1):
            item["collision_group_size"] = len(group)
            item["same_direction_breadth"] = same_direction[int(item["side"])]
            item["score_rank_at_boundary"] = rank
            item["actual"] = actual.get((str(item["symbol"]), ts))
            item["decision_disposition"] = blocked_reason(
                symbol=str(item["symbol"]), ts=ts, actual=actual,
                intervals=intervals, candidates_at_ts=group,
            )
            item["actual_class"] = classify_actual(item)

    entered = [item for item in source_candidates if item["actual_class"] != "NOT_ENTERED"]
    wins = [item for item in entered if item["actual_class"] == "ACTUAL_WIN"]
    losses = [item for item in entered if item["actual_class"] == "ACTUAL_LOSS"]
    unentered = [item for item in source_candidates if item["actual_class"] == "NOT_ENTERED"]

    def path_value(item: Mapping[str, Any], minute_key: str) -> float:
        value = item["future_path_post_outcome_only"]["close_return_by_minute"].get(minute_key)
        return float(value) if value is not None else math.nan

    missed_positive = [
        item for item in unentered
        if item["future_path_post_outcome_only"].get("available")
        and float(item["future_path_post_outcome_only"].get("diagnostic_source_exit_fraction_after_15bp") or 0.0) > 0.0
    ]
    near_positive = [
        item for item in near_misses
        if item["future_path_post_outcome_only"].get("available")
        and float(item["future_path_post_outcome_only"].get("diagnostic_source_exit_fraction_after_15bp") or 0.0) > 0.0
    ]

    summary = {
        "contract": {
            "analysis_unit": "causal episode boundary; same-timestamp cross-symbol signals are one collision group",
            "causal_fields": "causal_state, votes, collision/breadth/rank and disposition",
            "post_outcome_fields": "future_path_post_outcome_only; never valid as an entry-time feature",
            "source_control_engine": "NautilusTrader BacktestNode",
            "source_control_path": str(output),
        },
        "counts": {
            "all_source_transitions": len(source_candidates),
            "distinct_source_boundaries": len(by_ts),
            "entered": len(entered),
            "actual_wins": len(wins),
            "actual_losses": len(losses),
            "unentered": len(unentered),
            "unentered_diagnostic_positive": len(missed_positive),
            "four_of_five_near_miss_transitions": len(near_misses),
            "near_miss_diagnostic_positive": len(near_positive),
            "actual_position_intervals": len(intervals),
        },
        "disposition_counts": dict(
            pd.Series([item["decision_disposition"] for item in source_candidates]).value_counts()
        ),
        "entered_5m_close_return": {
            "wins": numeric_summary(path_value(item, "5") for item in wins),
            "losses": numeric_summary(path_value(item, "5") for item in losses),
        },
        "entered_15m_close_return": {
            "wins": numeric_summary(path_value(item, "15") for item in wins),
            "losses": numeric_summary(path_value(item, "15") for item in losses),
        },
        "entered_mfe": {
            "wins": numeric_summary(item["future_path_post_outcome_only"]["mfe_fraction"] for item in wins),
            "losses": numeric_summary(item["future_path_post_outcome_only"]["mfe_fraction"] for item in losses),
        },
        "entered_mae": {
            "wins": numeric_summary(item["future_path_post_outcome_only"]["mae_fraction"] for item in wins),
            "losses": numeric_summary(item["future_path_post_outcome_only"]["mae_fraction"] for item in losses),
        },
        "missing_component_counts_all_near_misses": dict(
            pd.Series([item["missing_components"][0] for item in near_misses]).value_counts()
        ),
        "missing_component_counts_positive_near_misses": dict(
            pd.Series([item["missing_components"][0] for item in near_positive]).value_counts()
        ),
        "source_control_metrics": json.loads((output / "metrics.json").read_text(encoding="utf-8")),
    }

    evidence.mkdir(parents=True, exist_ok=True)
    dump(evidence / "summary.json", summary)
    dump(evidence / "source_episodes.json", source_candidates)
    dump(evidence / "near_miss_episodes.json", near_misses)
    dump(evidence / "actual_position_intervals.json", intervals)

    with (evidence / "source_episodes.csv").open("w", newline="", encoding="utf-8") as stream:
        fields = [
            "episode_ts", "symbol", "side", "score", "score_rank_at_boundary",
            "collision_group_size", "same_direction_breadth", "decision_disposition",
            "actual_class", "actual_pnl_usdt", "roc", "adx", "volume_ratio",
            "close_5m", "close_15m", "mfe", "mae", "counterfactual_after_cost",
            "counterfactual_exit_reason",
        ]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for item in source_candidates:
            path = item["future_path_post_outcome_only"]
            actual_row = item.get("actual") or {}
            writer.writerow(
                {
                    "episode_ts": item["episode_ts"],
                    "symbol": item["symbol"],
                    "side": item["side"],
                    "score": item["score"],
                    "score_rank_at_boundary": item["score_rank_at_boundary"],
                    "collision_group_size": item["collision_group_size"],
                    "same_direction_breadth": item["same_direction_breadth"],
                    "decision_disposition": item["decision_disposition"],
                    "actual_class": item["actual_class"],
                    "actual_pnl_usdt": actual_row.get("realized_pnl_usdt"),
                    "roc": item["causal_state"].get("roc"),
                    "adx": item["causal_state"].get("adx"),
                    "volume_ratio": item["causal_state"].get("volume_ratio"),
                    "close_5m": path.get("close_return_by_minute", {}).get("5"),
                    "close_15m": path.get("close_return_by_minute", {}).get("15"),
                    "mfe": path.get("mfe_fraction"),
                    "mae": path.get("mae_fraction"),
                    "counterfactual_after_cost": path.get("diagnostic_source_exit_fraction_after_15bp"),
                    "counterfactual_exit_reason": path.get("diagnostic_source_exit_reason"),
                }
            )

    lines = [
        "# Candidate 57 — Winner15m causal-episode forensic map",
        "",
        "This document is an anatomy map, not a pass/fail gate. Causal entry-time state and post-outcome path fields are explicitly separated.",
        "",
        "## Counts",
        "",
    ]
    for key, value in summary["counts"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "## Dispositions",
            "",
            *[f"- {key}: {value}" for key, value in summary["disposition_counts"].items()],
            "",
            "## Five-minute path separation",
            "",
            f"- actual winners: `{json.dumps(summary['entered_5m_close_return']['wins'], sort_keys=True)}`",
            f"- actual losses: `{json.dumps(summary['entered_5m_close_return']['losses'], sort_keys=True)}`",
            "",
            "## Interpretation rule",
            "",
            "A positive post-outcome counterfactual is not a causal entry feature. It only identifies episodes for blind code/path review and the next untouched policy design.",
        ]
    )
    (evidence / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    start = date.fromisoformat(os.environ.get("C57_START", "2025-03-03"))
    end = date.fromisoformat(os.environ.get("C57_END", "2025-03-09"))
    work = ROOT / ".work" / "candidate-57-winner-forensic-v1"
    cache = ROOT / ".cache" / "candidate-57-winner-forensic-v1"
    output = ROOT / "artifacts" / "candidate-57-winner-forensic-v1" / "source-control"
    evidence = HERE / "evidence" / "winner-forensic-v1"
    for path in (work, output, evidence):
        if path.exists():
            shutil.rmtree(path)
    work.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    run_source_control(start=start, end=end, cache=cache, work=work, output=output)
    input_output = work / "forensic-input"
    frames, _, _ = c51run.load_inputs(start=start, end=end, cache=cache, output=input_output)
    summary = analyze(frames=frames, output=output, evidence=evidence)
    shutil.rmtree(input_output, ignore_errors=True)
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
