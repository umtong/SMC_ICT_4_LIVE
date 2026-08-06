"""One-time week-2 AFHR ablation through the existing NautilusTrader runner.

This experiment is deliberately narrow: it replays only the information-rich
failed holdout week and removes adaptive quality and directional freshness one
at a time.  It does not select a production candidate or authorize a long run.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from run_adaptive_fresh_matrix import VARIANTS, _base, _evidence
from run_equilibrium_matrix import _run


def _metric(record: dict[str, Any], name: str, default: float = 0.0) -> float:
    metrics = record.get("metrics") or {}
    value = metrics.get(name, default)
    return float(default if value is None else value)


def _delta(left: dict[str, Any], right: dict[str, Any]) -> dict[str, float]:
    """Return left-minus-right diagnostics without creating a new score."""
    return {
        "geometric_daily_nav_growth": _metric(left, "geometric_daily_nav_growth")
        - _metric(right, "geometric_daily_nav_growth"),
        "net_pnl_after_cost": _metric(left, "net_pnl_after_cost")
        - _metric(right, "net_pnl_after_cost"),
        "trades": _metric(left, "trades") - _metric(right, "trades"),
        "win_rate": _metric(left, "win_rate") - _metric(right, "win_rate"),
        "max_drawdown_nav": _metric(left, "max_drawdown_nav")
        - _metric(right, "max_drawdown_nav"),
        "profit_factor": _metric(left, "profit_factor")
        - _metric(right, "profit_factor"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/candidate-06/afhr-holdout-ablation"),
    )
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    base = _base(candidate_dir)
    results: list[dict[str, Any]] = []
    implementation_failures: list[str] = []
    for name, description, adaptive_quality, extreme_freshness, eligible in VARIANTS:
        config = copy.deepcopy(base)
        config["candidate_variant"] = f"{name}_holdout_week_2"
        config["variant_description"] = (
            description
            + " One-time causal attribution replay on the unchanged 2024-09-23 holdout week."
        )
        config.setdefault("validation", {})["stage"] = "week_2_logic_ablation"
        config["logic"].update(
            {
                "afhr_use_adaptive_quality": adaptive_quality,
                "afhr_use_extreme_freshness": extreme_freshness,
            },
        )
        config_path = output / f"{name}.json"
        config_path.write_text(
            json.dumps(config, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        run_output = output / name
        record = _run(config_path, run_output, 1, candidate_dir, repository)
        record.update(
            {
                "name": name,
                "description": description,
                "adaptive_quality": adaptive_quality,
                "extreme_freshness": extreme_freshness,
                "eligible_for_selection": eligible,
                "causal_evidence": _evidence(run_output),
            },
        )
        if int(record.get("returncode", 1)) != 0 or not record.get("metrics"):
            implementation_failures.append(name)
        results.append(record)

    by_name = {record["name"]: record for record in results}
    full = by_name["afhr_full"]
    quality_only = by_name["afhr_quality_only_ablation"]
    freshness_only = by_name["afhr_freshness_only_ablation"]
    parent = by_name["afhr_parent_hml_reference"]
    attribution = {
        "quality_standalone_vs_parent": _delta(quality_only, parent),
        "freshness_standalone_vs_parent": _delta(freshness_only, parent),
        "freshness_increment_given_quality": _delta(full, quality_only),
        "quality_increment_given_freshness": _delta(full, freshness_only),
        "full_vs_parent": _delta(full, parent),
    }

    def _growth(record: dict[str, Any]) -> float:
        return _metric(record, "geometric_daily_nav_growth")

    positive_variants = [
        record["name"]
        for record in results
        if record.get("metrics") and _growth(record) > 0.0
    ]
    summary = {
        "experiment": "unchanged week-2 one-variable AFHR ablation",
        "week_start_utc": "2024-09-23",
        "week_end_utc_exclusive": "2024-09-30",
        "implementation_failures": implementation_failures,
        "results": results,
        "attribution_deltas": attribution,
        "positive_growth_variants": positive_variants,
        "decision_rule": {
            "retain_variable": (
                "A variable may be retained only if its removal causally worsens post-cost NAV, "
                "trade independence or recoverable drawdown without merely suppressing all opportunity."
            ),
            "do_not_do": (
                "No threshold tuning, time-window fitting, risk scaling or candidate selection is permitted in this replay."
            ),
        },
        "terminal_status": (
            "IMPLEMENTATION_FAILURE_REQUIRES_SAME_WEEK_RERUN"
            if implementation_failures
            else "HOLDOUT_ABLATION_COMPLETE"
        ),
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, sort_keys=True))
    return 1 if implementation_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
