#!/usr/bin/env python3
"""Decompose calendar-day log growth into event rate and event quality.

For a non-overlapping one-slot account path:

    log(NAV_T / NAV_0) / days
      = (closed economic events / days)
        * (log(NAV_T / NAV_0) / closed economic events)

The identity separates a frequency failure from a per-event expectancy failure.
It is descriptive evidence only and never replaces NautilusTrader NAV.
"""
from __future__ import annotations

import argparse
from math import exp, log
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain an object")
    return value


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def component(record: dict[str, Any]) -> dict[str, float | int]:
    days = int(record["calendar_days"])
    trades = int(record["closed_trades"])
    multiple = float(record["pooled_nav_multiple"])
    if days <= 0 or trades <= 0 or multiple <= 0.0:
        raise ValueError("days, trades and NAV multiple must be positive")
    total_log_growth = log(multiple)
    event_rate = trades / days
    average_log_growth_per_trade = total_log_growth / trades
    log_growth_per_day = total_log_growth / days
    return {
        "calendar_days": days,
        "closed_trades": trades,
        "nav_multiple": multiple,
        "event_rate_per_day": event_rate,
        "average_log_growth_per_trade": average_log_growth_per_trade,
        "equivalent_average_return_per_trade": exp(average_log_growth_per_trade) - 1.0,
        "log_growth_per_day": log_growth_per_day,
        "daily_geometric_growth": exp(log_growth_per_day) - 1.0,
        "identity_reconstruction": event_rate * average_log_growth_per_trade,
    }


def decompose(snapshot: dict[str, Any]) -> dict[str, Any]:
    development = component(snapshot["multi_session_diagnostic"])
    holdout = component(snapshot["multi_session_untouched_holdout"])
    combined = component(snapshot["combined_context"])

    lambda_d = float(development["event_rate_per_day"])
    lambda_h = float(holdout["event_rate_per_day"])
    quality_d = float(development["average_log_growth_per_trade"])
    quality_h = float(holdout["average_log_growth_per_trade"])
    log_gap = float(holdout["log_growth_per_day"]) - float(
        development["log_growth_per_day"]
    )

    # Two-factor Shapley decomposition. The two contributions sum exactly to
    # the development -> holdout calendar-day log-growth gap.
    frequency_contribution = (lambda_h - lambda_d) * (quality_d + quality_h) / 2.0
    quality_contribution = (quality_h - quality_d) * (lambda_d + lambda_h) / 2.0
    absolute_gap = abs(log_gap)

    return {
        "schema": "candidate-11-growth-decomposition-v1",
        "classification": "BOTH_EVENT_RATE_AND_EVENT_QUALITY_FAILED",
        "success_claim": False,
        "development": development,
        "untouched_holdout": holdout,
        "combined_context": combined,
        "development_to_holdout": {
            "event_rate_ratio": lambda_h / lambda_d,
            "event_rate_change_per_day": lambda_h - lambda_d,
            "average_log_growth_per_trade_change": quality_h - quality_d,
            "calendar_log_growth_gap_per_day": log_gap,
            "shapley_frequency_contribution": frequency_contribution,
            "shapley_quality_contribution": quality_contribution,
            "shapley_sum": frequency_contribution + quality_contribution,
            "absolute_gap_share_frequency": (
                abs(frequency_contribution) / absolute_gap if absolute_gap else 0.0
            ),
            "absolute_gap_share_quality": (
                abs(quality_contribution) / absolute_gap if absolute_gap else 0.0
            ),
            "counterfactual_holdout_frequency_with_development_quality": (
                exp(lambda_h * quality_d) - 1.0
            ),
            "counterfactual_development_frequency_with_holdout_quality": (
                exp(lambda_d * quality_h) - 1.0
            ),
        },
        "interpretation": (
            "Holdout activity fell to less than half the development event rate, "
            "but the larger failure was event quality: average log growth per "
            "closed trade changed from positive to negative. No-trade dilution "
            "alone cannot explain the sign reversal."
        ),
    }


def render(result: dict[str, Any]) -> str:
    development = result["development"]
    holdout = result["untouched_holdout"]
    change = result["development_to_holdout"]
    return "\n".join(
        [
            "# Candidate 11 growth decomposition",
            "",
            f"**{result['classification']}**",
            "",
            result["interpretation"],
            "",
            "## Development versus untouched holdout",
            "",
            f"- event rate/day: `{development['event_rate_per_day']:.6f}` -> `{holdout['event_rate_per_day']:.6f}`",
            f"- average log growth/trade: `{development['average_log_growth_per_trade']:.6f}` -> `{holdout['average_log_growth_per_trade']:.6f}`",
            f"- equivalent average return/trade: `{development['equivalent_average_return_per_trade']:.6%}` -> `{holdout['equivalent_average_return_per_trade']:.6%}`",
            f"- daily geometric growth: `{development['daily_geometric_growth']:.6%}` -> `{holdout['daily_geometric_growth']:.6%}`",
            f"- event-rate ratio: `{change['event_rate_ratio']:.6f}`",
            f"- Shapley share of absolute log-growth gap from frequency: `{change['absolute_gap_share_frequency']:.6%}`",
            f"- Shapley share of absolute log-growth gap from event quality: `{change['absolute_gap_share_quality']:.6%}`",
            "",
            "The decomposition is descriptive and uses the recorded NautilusTrader account-NAV multiples.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--snapshot", type=Path, default=ROOT / "evidence_snapshot.json"
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "growth_decomposition.json"
    )
    args = parser.parse_args()
    result = decompose(load(args.snapshot))
    write(args.output, result)
    args.output.with_suffix(".md").write_text(render(result), encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
