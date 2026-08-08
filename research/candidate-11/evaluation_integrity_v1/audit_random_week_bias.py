#!/usr/bin/env python3
"""Audit why isolated random-week evidence fails to transfer.

This module is deliberately not a backtest engine. It consumes a frozen
evidence snapshot, quantifies adaptive reuse, sample uncertainty, directional
concentration and opportunity-density feasibility, then writes a fail-closed
research classification. It never upgrades development evidence into a
holdout claim.
"""
from __future__ import annotations

import argparse
from math import comb, log
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def binomial_survival(successes: int, trials: int, probability: float) -> float:
    """Return P[X >= successes] for X ~ Binomial(trials, probability)."""
    return sum(
        comb(trials, value)
        * probability**value
        * (1.0 - probability) ** (trials - value)
        for value in range(successes, trials + 1)
    )


def exact_two_sided_lower_bound(
    successes: int,
    trials: int,
    confidence: float = 0.95,
) -> float:
    """Exact Clopper-Pearson lower confidence bound using bisection."""
    if not 0 <= successes <= trials:
        raise ValueError("successes must be between zero and trials")
    if trials <= 0:
        raise ValueError("trials must be positive")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between zero and one")
    if successes == 0:
        return 0.0

    target = (1.0 - confidence) / 2.0
    lower = 0.0
    upper = 1.0
    for _ in range(120):
        midpoint = (lower + upper) / 2.0
        if binomial_survival(successes, trials, midpoint) < target:
            lower = midpoint
        else:
            upper = midpoint
    return (lower + upper) / 2.0


def direction_share(record: dict[str, Any], direction: str) -> float:
    directions = record["directions"]
    total = sum(int(value) for value in directions.values())
    return int(directions.get(direction, 0)) / total if total else 0.0


def required_trade_density_from_realized_path(
    *,
    nav_multiple: float,
    trades: int,
    target_daily_growth: float,
) -> float | None:
    """Trade/day needed if realized average log return per trade persisted.

    This is a feasibility diagnostic, not a forecast. It intentionally uses
    the observed account-NAV path rather than a fitted payoff model.
    """
    if nav_multiple <= 0.0 or trades <= 0:
        return None
    average_log_return = log(nav_multiple) / trades
    if average_log_return <= 0.0:
        return None
    return log(1.0 + target_daily_growth) / average_log_return


def audit(snapshot: dict[str, Any]) -> dict[str, Any]:
    sequence = snapshot["same_calendar_adaptive_sequence"]
    versions = sequence["versions"]
    diagnostic = snapshot["multi_session_diagnostic"]
    holdout = snapshot["multi_session_untouched_holdout"]
    combined = snapshot["combined_context"]
    target = float(snapshot["project_target_daily_geometric_growth"])

    interval_signatures = [tuple(sequence["interval_set"]) for _ in versions]
    adaptive_reuse = len(versions) > 1 and len(set(interval_signatures)) == 1
    version_results = [
        {
            "version": item["version"],
            "closed_trades": int(item["closed_trades"]),
            "wins": int(item["wins"]),
            "losses": int(item["losses"]),
            "win_rate": (
                int(item["wins"]) / int(item["closed_trades"])
                if int(item["closed_trades"])
                else 0.0
            ),
            "daily_geometric_growth": float(item["daily_geometric_growth"]),
        }
        for item in versions
    ]

    diagnostic_trials = int(diagnostic["closed_trades"])
    holdout_trials = int(holdout["closed_trades"])
    combined_trials = int(combined["closed_trades"])
    lower_bounds = {
        "candidate13_final_7_of_7": exact_two_sided_lower_bound(7, 7),
        "multi_session_diagnostic": exact_two_sided_lower_bound(
            int(diagnostic["wins"]), diagnostic_trials
        ),
        "multi_session_holdout": exact_two_sided_lower_bound(
            int(holdout["wins"]), holdout_trials
        ),
        "combined_context": exact_two_sided_lower_bound(
            int(combined["wins"]), combined_trials
        ),
    }

    densities = {
        "diagnostic_trades_per_day": diagnostic_trials
        / int(diagnostic["calendar_days"]),
        "holdout_trades_per_day": holdout_trials / int(holdout["calendar_days"]),
        "combined_trades_per_day": combined_trials / int(combined["calendar_days"]),
        "required_combined_trades_per_day_if_realized_average_log_trade_persisted": (
            required_trade_density_from_realized_path(
                nav_multiple=float(combined["pooled_nav_multiple"]),
                trades=combined_trials,
                target_daily_growth=target,
            )
        ),
    }
    required = densities[
        "required_combined_trades_per_day_if_realized_average_log_trade_persisted"
    ]
    density_shortfall = (
        None if required is None else required - densities["combined_trades_per_day"]
    )

    diagnostic_short_share = direction_share(diagnostic, "SHORT")
    holdout_short_share = direction_share(holdout, "SHORT")
    direction_shift = abs(diagnostic_short_share - holdout_short_share)

    classifications = []
    if adaptive_reuse:
        classifications.append("ADAPTIVE_REUSE_OF_OPENED_RANDOM_WEEKS")
    if diagnostic_trials < 20:
        classifications.append("UNDERPOWERED_TRADE_SAMPLE")
    if lower_bounds["multi_session_diagnostic"] < 0.8:
        classifications.append("POINT_WIN_RATE_NOT_STATISTICALLY_SECURE")
    if diagnostic_short_share >= 0.8:
        classifications.append("DIRECTION_AND_LATENT_REGIME_CONCENTRATION")
    if direction_shift >= 0.4:
        classifications.append("DEVELOPMENT_HOLDOUT_DOMAIN_SHIFT")
    if float(combined["daily_geometric_growth"]) < target:
        classifications.append("COMBINED_EVIDENCE_BELOW_PROJECT_GROWTH_TARGET")
    if density_shortfall is not None and density_shortfall > 0.0:
        classifications.append("OPPORTUNITY_DENSITY_SHORTFALL")
    if snapshot["fresh_mechanism_screen"]["total_events"] == 0:
        classifications.append("FRESH_CAUSAL_SCREEN_FOUND_NO_EXECUTABLE_EVENTS")

    return {
        "schema": "candidate-11-evaluation-integrity-audit-v1",
        "classification": "RANDOM_WEEK_EVIDENCE_CONTAMINATED_AND_UNDERPOWERED",
        "success_claim": False,
        "random_week_success_is_valid_holdout_evidence": False,
        "adaptive_reuse": adaptive_reuse,
        "same_calendar_versions_observed": len(versions),
        "same_calendar_version_results": version_results,
        "exact_95pct_win_rate_lower_bounds": lower_bounds,
        "direction_diagnostics": {
            "development_short_share": diagnostic_short_share,
            "untouched_holdout_short_share": holdout_short_share,
            "absolute_short_share_shift": direction_shift,
            "development_direction_counts": diagnostic["directions"],
            "untouched_holdout_direction_counts": holdout["directions"],
        },
        "opportunity_density": {
            **densities,
            "required_minus_observed_combined_trades_per_day": density_shortfall,
        },
        "growth_diagnostics": {
            "development_daily_geometric_growth": float(
                diagnostic["daily_geometric_growth"]
            ),
            "untouched_holdout_daily_geometric_growth": float(
                holdout["daily_geometric_growth"]
            ),
            "combined_context_daily_geometric_growth": float(
                combined["daily_geometric_growth"]
            ),
            "project_target_daily_geometric_growth": target,
            "development_minus_holdout": float(
                diagnostic["daily_geometric_growth"]
            )
            - float(holdout["daily_geometric_growth"]),
        },
        "effective_sample_diagnostics": {
            "development_closed_trades": diagnostic_trials,
            "development_date_direction_clusters": int(
                diagnostic["trade_clusters_by_utc_date_and_direction"]
            ),
            "untouched_holdout_closed_trades": holdout_trials,
            "untouched_holdout_date_direction_clusters": int(
                holdout["trade_clusters_by_utc_date_and_direction"]
            ),
            "note": (
                "Date-direction clusters are still an upper bound on independent "
                "economic events; several dates can share one market-wide regime."
            ),
        },
        "failure_modes": classifications,
        "root_cause": (
            "Calendar randomization did not create independent validation. "
            "The same opened weeks were reused across source revisions, sparse "
            "trades were treated as five weekly samples, and the development "
            "trades concentrated in one short-side latent regime. Fresh data "
            "therefore revealed the base rate and domain shift hidden by the "
            "adaptively selected point estimate."
        ),
        "decision": (
            "Permanently classify W10-W14 as development-only and H1-H3 as "
            "consumed holdout. No future source may claim validation from "
            "either set. Spend another unseen interval only after the new "
            "contiguous-block protocol's development gate is met."
        ),
    }


def render_markdown(result: dict[str, Any]) -> str:
    lower = result["exact_95pct_win_rate_lower_bounds"]
    density = result["opportunity_density"]
    growth = result["growth_diagnostics"]
    direction = result["direction_diagnostics"]
    lines = [
        "# Why random weeks looked good and longer evaluation failed",
        "",
        f"**{result['classification']}**",
        "",
        "## Finding",
        "",
        result["root_cause"],
        "",
        "## Direct evidence",
        "",
        (
            f"- The same W10-W14 calendar set was evaluated across "
            f"`{result['same_calendar_versions_observed']}` source generations."
        ),
        (
            "- Candidate 13's final 7/7 point win rate has an exact 95% lower "
            f"bound of only `{lower['candidate13_final_7_of_7']:.6f}`."
        ),
        (
            "- Multi-session development was "
            f"`{growth['development_daily_geometric_growth']:.6%}` daily, while "
            "the untouched holdout was "
            f"`{growth['untouched_holdout_daily_geometric_growth']:.6%}` daily."
        ),
        (
            "- Development short share was "
            f"`{direction['development_short_share']:.2%}` versus "
            f"`{direction['untouched_holdout_short_share']:.2%}` in holdout."
        ),
        (
            "- Combined continuity evidence was "
            f"`{growth['combined_context_daily_geometric_growth']:.6%}` daily, "
            "below the project target of "
            f"`{growth['project_target_daily_geometric_growth']:.2%}`."
        ),
        (
            "- Combined observed trade density was "
            f"`{density['combined_trades_per_day']:.6f}` per day. Holding its "
            "realized average log return per trade fixed would require "
            f"`{density['required_combined_trades_per_day_if_realized_average_log_trade_persisted']:.6f}` "
            "trades per day to reach 1% daily growth."
        ),
        "",
        "## Failure modes",
        "",
    ]
    lines.extend(f"- `{reason}`" for reason in result["failure_modes"])
    lines.extend(
        [
            "",
            "## Binding decision",
            "",
            result["decision"],
            "",
            "This audit is an evidence-integrity result, not an alpha claim.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--snapshot", type=Path, default=ROOT / "evidence_snapshot.json"
    )
    parser.add_argument("--output", type=Path, default=ROOT / "audit.json")
    args = parser.parse_args()
    result = audit(load_object(args.snapshot))
    write_json(args.output, result)
    args.output.with_name("RESULT.md").write_text(
        render_markdown(result), encoding="utf-8"
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
