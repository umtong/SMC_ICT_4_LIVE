#!/usr/bin/env python3
"""Directly inspect Candidate 4t trades and skipped profitable states.

This is not a pass/fail framework. It reduces the actual result to the few market-
logic failures that should determine the next code change.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

FEATURE_PREFIXES = (
    "auction_", "approach_", "trajectory_", "response_", "control_",
    "p_", "ownership_", "expected_", "stopping_", "global_", "same_",
)


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, low_memory=False) if path.exists() else pd.DataFrame()


def fmt(value: Any, digits: int = 4) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    return f"{number:.{digits}f}" if math.isfinite(number) else "n/a"


def table(frame: pd.DataFrame, columns: list[str], limit: int = 30) -> str:
    available = [column for column in columns if column in frame]
    if frame.empty or not available:
        return "_none_"
    return frame[available].head(limit).to_markdown(index=False)


def standardized_separation(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty or "win" not in trades:
        return pd.DataFrame()
    win = trades.win.astype(bool)
    rows: list[dict[str, Any]] = []
    for column in trades.columns:
        if not column.startswith(FEATURE_PREFIXES):
            continue
        values = pd.to_numeric(trades[column], errors="coerce")
        if values.notna().sum() < max(8, int(0.25 * len(trades))):
            continue
        winners = values[win].dropna()
        losers = values[~win].dropna()
        if len(winners) < 3 or len(losers) < 3:
            continue
        pooled = float(values.std(ddof=0))
        if not math.isfinite(pooled) or pooled <= 1e-12:
            continue
        rows.append({
            "feature": column,
            "winner_median": float(winners.median()),
            "loser_median": float(losers.median()),
            "median_separation_sigma": float((winners.median() - losers.median()) / pooled),
            "winner_mean": float(winners.mean()),
            "loser_mean": float(losers.mean()),
        })
    output = pd.DataFrame(rows)
    if output.empty:
        return output
    return output.assign(
        absolute_separation=output.median_separation_sigma.abs()
    ).sort_values("absolute_separation", ascending=False).drop(columns="absolute_separation")


def calibration(trades: pd.DataFrame, column: str) -> pd.DataFrame:
    if trades.empty or column not in trades or "win" not in trades:
        return pd.DataFrame()
    values = pd.to_numeric(trades[column], errors="coerce")
    valid = trades.loc[values.notna()].copy()
    if len(valid) < 8:
        return pd.DataFrame()
    ranks = values[values.notna()].rank(method="first")
    bins = min(5, max(2, len(valid) // 8))
    valid["bin"] = pd.qcut(ranks, q=bins, labels=False, duplicates="drop")
    return valid.groupby("bin", as_index=False).agg(
        trades=("win", "size"),
        observed_target_first=("win", "mean"),
        mean_prediction=(column, "mean"),
        mean_net_r=("net_r", "mean"),
    )


def blocked_missed(missed: pd.DataFrame) -> pd.DataFrame:
    if missed.empty:
        return missed
    output = missed.copy()
    for column in (
        "expected_enter_log", "expected_wait_log", "same_episode_wait_log",
        "global_commitment_cost", "p_ownership", "p_fill", "p_resolve",
        "net_r",
    ):
        if column in output:
            output[column] = pd.to_numeric(output[column], errors="coerce")
    if "expected_enter_log" in output and "expected_wait_log" in output:
        output["rejection_margin"] = output.expected_wait_log - output.expected_enter_log
    return output.sort_values(
        [column for column in ("net_r", "p_ownership") if column in output],
        ascending=False,
    )


def infer_primary_failure(summary: dict[str, Any], trades: pd.DataFrame, missed: pd.DataFrame) -> list[str]:
    observations: list[str] = []
    if trades.empty:
        observations.append(
            "The integrated account completed no resolved trade; the current bottleneck is opportunity construction or excessive continuation value, not exit management."
        )
        return observations
    win_rate = float(trades.win.astype(bool).mean()) if "win" in trades else math.nan
    mean_r = float(pd.to_numeric(trades.net_r, errors="coerce").mean())
    high_conf = trades[
        pd.to_numeric(trades.get("p_ownership", np.nan), errors="coerce") >= 0.7
    ]
    if len(high_conf) >= 5 and float(high_conf.win.astype(bool).mean()) < 0.5:
        observations.append(
            "High estimated ownership still loses frequently. The principal error is causal ownership inference, not an insufficient confidence cutoff."
        )
    if win_rate < 0.5 and mean_r < 0:
        observations.append(
            "Losses dominate both frequency and expectancy, so widening targets or loosening entries would amplify a direction/control error."
        )
    if "gross_rr" in trades:
        low_rr = pd.to_numeric(trades.gross_rr, errors="coerce") < 1.2
        if low_rr.mean() > 0.5 and mean_r <= 0:
            observations.append(
                "Most selected routes barely clear 1R; after costs the system has too little geometric margin when ownership is imperfect."
            )
    if "holding_minutes" in trades:
        hold = pd.to_numeric(trades.holding_minutes, errors="coerce")
        if hold.median() > 12 * 60:
            observations.append(
                "Median holding time is no longer day-trading scale; unresolved route distance or destination selection is too slow."
            )
    if not missed.empty and "net_r" in missed:
        missed_positive = pd.to_numeric(missed.net_r, errors="coerce") > 0
        if missed_positive.sum() > len(trades):
            observations.append(
                "The no-trade set contains more profitable resolved states than the account traded. Waiting/global reservation is discarding opportunity and must be compared against actual blocked value."
            )
    if not observations:
        observations.append(
            "No single coarse failure dominates; family/phase and trajectory separation below should determine the next market-logic change."
        )
    return observations


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--prefix", default="fresh")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary_path = args.result / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    account_summary = summary.get(args.prefix, summary.get("development_oof", {}))
    trades = read_csv(args.result / f"{args.prefix}_trades.csv")
    losses = read_csv(args.result / f"{args.prefix}_loss_clinic.csv")
    missed = blocked_missed(read_csv(args.result / f"{args.prefix}_missed_opportunity_clinic.csv"))
    separation = standardized_separation(trades)
    calibration_ownership = calibration(trades, "p_ownership")
    calibration_stopping = calibration(trades, "stopping_advantage")

    lines = [
        f"# Candidate 4t {args.prefix} direct trade review",
        "",
        "## Account result",
        "",
        "```json",
        json.dumps(account_summary, ensure_ascii=False, indent=2, default=str),
        "```",
        "",
        "## What the actual result says",
        "",
    ]
    lines.extend(f"- {item}" for item in infer_primary_failure(account_summary, trades, missed))
    lines.extend([
        "",
        "## Families and phases actually traded",
        "",
        table(
            trades.groupby([column for column in ("family", "auction_phase") if column in trades], as_index=False).agg(
                trades=("net_r", "size"),
                target_first_rate=("win", "mean"),
                mean_net_r=("net_r", "mean"),
            ).sort_values("mean_net_r") if not trades.empty and "family" in trades else pd.DataFrame(),
            ["family", "auction_phase", "trades", "target_first_rate", "mean_net_r"],
            50,
        ),
        "",
        "## Causal/trajectory differences between winners and losses",
        "",
        table(
            separation,
            ["feature", "winner_median", "loser_median", "median_separation_sigma", "winner_mean", "loser_mean"],
            40,
        ),
        "",
        "## Ownership calibration on selected trades",
        "",
        table(calibration_ownership, list(calibration_ownership.columns), 10),
        "",
        "## Stopping-advantage calibration on selected trades",
        "",
        table(calibration_stopping, list(calibration_stopping.columns), 10),
        "",
        "## Actual losses",
        "",
        table(
            losses.sort_values("net_r") if "net_r" in losses else losses,
            [
                "period", "symbol", "family", "auction_phase", "side", "net_r",
                "gross_rr", "p_ownership", "p_fill", "p_resolve",
                "expected_enter_log", "same_episode_wait_log",
                "global_commitment_cost", "stopping_advantage", "holding_minutes",
                "trajectory_delta__auction_progress_r",
                "trajectory_delta__auction_failure_pressure",
            ],
            100,
        ),
        "",
        "## Profitable states the account did not trade",
        "",
        table(
            missed,
            [
                "period", "symbol", "family", "auction_phase", "side", "net_r",
                "gross_rr", "p_ownership", "p_fill", "p_resolve",
                "expected_enter_log", "same_episode_wait_log",
                "global_commitment_cost", "expected_wait_log", "rejection_margin",
                "holding_minutes",
            ],
            100,
        ),
        "",
    ])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    separation.to_csv(args.output.with_name("winner_loss_feature_separation.csv"), index=False)
    calibration_ownership.to_csv(args.output.with_name("ownership_calibration.csv"), index=False)
    calibration_stopping.to_csv(args.output.with_name("stopping_calibration.csv"), index=False)
    print(args.output)


if __name__ == "__main__":
    main()
