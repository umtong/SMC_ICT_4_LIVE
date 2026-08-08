#!/usr/bin/env python3
"""Sequential version of Candidate 27's independent-episode diagnostic.

A candidate seed blocks all later quarter-hour events until its thirty-minute
reset decision is observed.  If that reset is not adverse, scanning resumes at
the reset minute.  This reproduces the live state machine and prevents a later
overlapping signal from being selected with hindsight.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any

import pandas as pd

from funding_interval_episode_diagnostic import _build_frame
from funding_interval_episode_diagnostic import _episode_from_seed
from funding_interval_episode_diagnostic import _funding_interval_start


def _summary(episodes: pd.DataFrame) -> dict[str, Any]:
    if episodes.empty:
        return {
            "episodes": 0,
            "mean_gross_bps": None,
            "mean_net_bps": None,
            "median_net_bps": None,
            "net_hit_rate": None,
            "mean_mfe_bps": None,
            "mean_mae_bps": None,
            "prior_interval_extreme_hit_rate": None,
            "full_interval_extension_hit_rate": None,
        }
    return {
        "episodes": int(len(episodes)),
        "mean_gross_bps": float(episodes["gross_time_exit_bps"].mean()),
        "mean_net_bps": float(episodes["net_time_exit_bps"].mean()),
        "median_net_bps": float(episodes["net_time_exit_bps"].median()),
        "net_hit_rate": float((episodes["net_time_exit_bps"] > 0.0).mean()),
        "mean_mfe_bps": float(episodes["mfe_bps"].mean()),
        "mean_mae_bps": float(episodes["mae_bps"].mean()),
        "prior_interval_extreme_hit_rate": float(
            episodes["prior_interval_extreme_hit"].astype(bool).mean()
        ),
        "full_interval_extension_hit_rate": float(
            episodes["full_interval_extension_hit"].astype(bool).mean()
        ),
        "mean_prior_interval_extreme_risk_bps": float(
            episodes["prior_interval_extreme_risk_bps"].mean()
        ),
        "mean_full_interval_extension_risk_bps": float(
            episodes["full_interval_extension_risk_bps"].mean()
        ),
    }


def run_diagnostic(
    *,
    symbol: str,
    required_state: str,
    build_start: date,
    build_end: date,
    evaluation_start: date,
    evaluation_end: date,
    cache: Path,
    output: Path,
    configured_round_trip_bps: float,
) -> dict[str, Any]:
    if required_state not in {"CALM", "MIXED", "STRESSED"}:
        raise ValueError("required_state must be CALM, MIXED or STRESSED")
    if not build_start <= evaluation_start <= evaluation_end <= build_end:
        raise ValueError("evaluation must be contained in build range")
    if build_end < evaluation_end + timedelta(days=1):
        raise ValueError("build must include the post-evaluation exit day")

    output.mkdir(parents=True, exist_ok=True)
    frame = _build_frame(
        symbol=symbol,
        build_start=build_start,
        build_end=build_end,
        cache=cache,
        output=output,
    )
    evaluation_open = pd.Timestamp(evaluation_start, tz="UTC")
    evaluation_close = pd.Timestamp(evaluation_end + timedelta(days=1), tz="UTC")

    episodes: list[dict[str, Any]] = []
    observed_seeds = 0
    rejected_resets = 0
    blocked_overlaps = 0
    interval = _funding_interval_start(evaluation_open)
    while interval < evaluation_close:
        next_funding = interval + pd.Timedelta(hours=8)
        candidates = frame[
            frame["minute"].between(
                interval + pd.Timedelta(minutes=15),
                next_funding - pd.Timedelta(minutes=60),
                inclusive="both",
            )
            & frame["is_qh"]
            & frame["feature_ready"].astype(bool)
            & frame["qh_abnormal"].astype(bool)
            & frame["qh_persistent"].astype(bool)
            & frame["liquidity_state"].eq(required_state)
        ].sort_values("minute")

        available_from = interval + pd.Timedelta(minutes=15)
        for seed_index, seed in candidates.iterrows():
            seed_minute = seed["minute"]
            if seed_minute < available_from:
                blocked_overlaps += 1
                continue
            reset_index = int(seed_index) + 30
            if reset_index >= len(frame):
                break
            reset = frame.iloc[reset_index]
            reset_minute = reset["minute"]
            if reset_minute >= next_funding - pd.Timedelta(minutes=25):
                break

            observed_seeds += 1
            available_from = reset_minute
            side = int(seed["qh_side"])
            if side * (float(reset["close"]) - float(seed["close"])) >= 0.0:
                rejected_resets += 1
                continue

            episode = _episode_from_seed(
                frame=frame,
                seed_index=int(seed_index),
                interval_start=interval,
                next_funding=next_funding,
                configured_round_trip_bps=configured_round_trip_bps,
            )
            if episode is not None:
                episodes.append(episode)
                break
        interval = next_funding

    episode_frame = pd.DataFrame(episodes)
    episode_frame.to_csv(output / "funding_interval_episodes.csv", index=False)
    result = {
        "candidate": "candidate-27-sequential-independent-funding-interval-diagnostic",
        "symbol": symbol,
        "required_state": required_state,
        "build_start": str(build_start),
        "build_end": str(build_end),
        "evaluation_start": str(evaluation_start),
        "evaluation_end": str(evaluation_end),
        "configured_round_trip_bps": configured_round_trip_bps,
        "state_machine": {
            "observed_seeds": observed_seeds,
            "rejected_resets": rejected_resets,
            "blocked_overlapping_signals": blocked_overlaps,
            "episode_limit": "one per eight-hour funding interval",
            "pending_seed_policy": (
                "later quarter-hour events are ignored until the active seed's "
                "thirty-minute reset decision is observed"
            ),
        },
        "summary": _summary(episode_frame),
    }
    (output / "diagnostic.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--required-state", required=True)
    parser.add_argument("--build-start", required=True)
    parser.add_argument("--build-end", required=True)
    parser.add_argument("--evaluation-start", required=True)
    parser.add_argument("--evaluation-end", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--round-trip-bps", type=float, default=20.0)
    args = parser.parse_args()
    result = run_diagnostic(
        symbol=args.symbol,
        required_state=args.required_state,
        build_start=date.fromisoformat(args.build_start),
        build_end=date.fromisoformat(args.build_end),
        evaluation_start=date.fromisoformat(args.evaluation_start),
        evaluation_end=date.fromisoformat(args.evaluation_end),
        cache=args.cache.resolve(),
        output=args.output.resolve(),
        configured_round_trip_bps=args.round_trip_bps,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
