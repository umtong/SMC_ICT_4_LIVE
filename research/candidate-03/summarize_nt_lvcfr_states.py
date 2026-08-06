#!/usr/bin/env python3
"""Summarize scenario contributions from NautilusTrader-native evidence.

The utility never reconstructs fills, PnL, positions or NAV from market prices.
It only aggregates the native account PnL already emitted by the fixed
NautilusTrader strategy and run path.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any


def _float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _read_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _day_from_ns(raw: Any) -> str:
    timestamp = int(raw) / 1_000_000_000
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat()


def summarize(
    *,
    metrics_path: Path,
    episodes_path: Path,
    signals_path: Path,
) -> dict[str, Any]:
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    episodes = _read_rows(episodes_path)
    signals = json.loads(signals_path.read_text(encoding="utf-8"))
    signal_by_id = {str(signal["scenario_id"]): signal for signal in signals}

    by_state: dict[str, dict[str, Any]] = {}
    day_pnl: dict[str, float] = {}
    episode_pnls: list[float] = []
    positive_pnls: list[float] = []
    all_exit_reasons: dict[str, int] = {}
    unmatched: list[str] = []

    for episode in episodes:
        scenario_id = str(episode.get("scenario_id", ""))
        scheduled = signal_by_id.get(scenario_id)
        state = str(
            episode.get("scenario_kind")
            or (scheduled or {}).get("scenario_kind")
            or "UNKNOWN"
        )
        pnl = _float(episode.get("native_account_pnl"))
        returned = _float(episode.get("return"))
        legs_raw = episode.get("legs") or "[]"
        try:
            legs = json.loads(legs_raw)
        except json.JSONDecodeError:
            legs = []
        net_r = sum(_float(leg.get("net_r")) for leg in legs)
        state_row = by_state.setdefault(
            state,
            {
                "episodes": 0,
                "wins": 0,
                "losses": 0,
                "native_account_pnl": 0.0,
                "episode_returns": [],
                "net_r": 0.0,
                "exit_reasons": {},
            },
        )
        state_row["episodes"] += 1
        state_row["wins" if pnl > 0.0 else "losses"] += 1
        state_row["native_account_pnl"] += pnl
        state_row["episode_returns"].append(returned)
        state_row["net_r"] += net_r
        for leg in legs:
            reason = str(leg.get("exit_reason") or "UNKNOWN")
            state_row["exit_reasons"][reason] = (
                state_row["exit_reasons"].get(reason, 0) + 1
            )
            all_exit_reasons[reason] = all_exit_reasons.get(reason, 0) + 1
        if scenario_id not in signal_by_id:
            unmatched.append(scenario_id)
        day = _day_from_ns(episode["end_time_ns"])
        day_pnl[day] = day_pnl.get(day, 0.0) + pnl
        episode_pnls.append(pnl)
        if pnl > 0.0:
            positive_pnls.append(pnl)

    for row in by_state.values():
        count = int(row["episodes"])
        row["win_rate"] = row["wins"] / count if count else 0.0
        row["mean_episode_pnl"] = row["native_account_pnl"] / count if count else 0.0
        row["mean_episode_return"] = mean(row.pop("episode_returns")) if count else 0.0
        row["mean_net_r"] = row["net_r"] / count if count else 0.0
        row["exit_reasons"] = dict(sorted(row["exit_reasons"].items()))

    total_positive = sum(positive_pnls)
    positive_desc = sorted(positive_pnls, reverse=True)
    positive_hhi = (
        sum((value / total_positive) ** 2 for value in positive_pnls)
        if total_positive > 0.0
        else 0.0
    )
    state_pnls = [
        _float(row["native_account_pnl"])
        for row in by_state.values()
    ]
    profitable_states = sum(value > 0.0 for value in state_pnls)

    return {
        "candidate": metrics.get("candidate"),
        "engine": metrics.get("engine"),
        "week_start_utc": metrics.get("week_start_utc"),
        "source_metrics": {
            key: metrics.get(key)
            for key in (
                "initial_nav",
                "final_nav",
                "net_return",
                "daily_geometric_growth",
                "independent_episodes",
                "wins",
                "losses",
                "win_rate",
                "mean_episode_pnl",
                "max_drawdown",
                "native_orders",
                "native_positions",
                "entry_rejections",
                "incomplete_at_end",
            )
        },
        "scheduled_signals": len(signals),
        "executed_episodes": len(episodes),
        "unmatched_episode_scenario_ids": sorted(set(unmatched)),
        "by_scenario_kind": dict(sorted(by_state.items())),
        "profitable_scenario_kinds": profitable_states,
        "total_scenario_kinds": len(by_state),
        "all_exit_reasons": dict(sorted(all_exit_reasons.items())),
        "daily_native_account_pnl": dict(sorted(day_pnl.items())),
        "concentration": {
            "total_positive_episode_pnl": total_positive,
            "largest_positive_episode_pnl": positive_desc[0] if positive_desc else 0.0,
            "top_two_positive_episode_pnl": sum(positive_desc[:2]),
            "largest_win_share_of_positive_pnl": (
                positive_desc[0] / total_positive
                if positive_desc and total_positive > 0.0
                else 0.0
            ),
            "top_two_win_share_of_positive_pnl": (
                sum(positive_desc[:2]) / total_positive
                if positive_desc and total_positive > 0.0
                else 0.0
            ),
            "positive_pnl_hhi": positive_hhi,
            "net_pnl_without_largest_win": (
                sum(episode_pnls) - positive_desc[0]
                if positive_desc
                else sum(episode_pnls)
            ),
            "net_pnl_without_top_two_wins": (
                sum(episode_pnls) - sum(positive_desc[:2])
            ),
        },
        "accounting_source": "NautilusTrader native_account_pnl only",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics", type=Path, required=True)
    parser.add_argument("--episodes", type=Path, required=True)
    parser.add_argument("--signals", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = summarize(
        metrics_path=args.metrics.resolve(),
        episodes_path=args.episodes.resolve(),
        signals_path=args.signals.resolve(),
    )
    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.resolve().write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
