"""One-time unchanged-week-2 attribution for SIAR acceptance variables.

This is not a selection run. It replays the same sealed 2024-09-23 BTC week
through the existing NautilusTrader runner and removes surprise/impact
classification one variable at a time.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from run_adaptive_fresh_matrix import _evidence
from run_equilibrium_matrix import _run
from run_surprise_impact_matrix import VARIANTS, _base


def _metric(record: dict[str, Any], name: str, default: float = 0.0) -> float:
    metrics = record.get("metrics") or {}
    value = metrics.get(name, default)
    return float(default if value is None else value)


def _delta(left: dict[str, Any], right: dict[str, Any]) -> dict[str, float]:
    """Return left-minus-right diagnostics, not a fitted composite score."""
    return {
        "geometric_daily_nav_growth": (
            _metric(left, "geometric_daily_nav_growth")
            - _metric(right, "geometric_daily_nav_growth")
        ),
        "net_pnl_after_cost": (
            _metric(left, "net_pnl_after_cost")
            - _metric(right, "net_pnl_after_cost")
        ),
        "trades": _metric(left, "trades") - _metric(right, "trades"),
        "win_rate": _metric(left, "win_rate") - _metric(right, "win_rate"),
        "profit_factor": (
            _metric(left, "profit_factor")
            - _metric(right, "profit_factor")
        ),
        "max_drawdown_nav": (
            _metric(left, "max_drawdown_nav")
            - _metric(right, "max_drawdown_nav")
        ),
    }


def _same_number(left: Any, right: Any, tolerance: float = 1e-9) -> bool:
    if left is None or right is None:
        return left is right
    return abs(float(left) - float(right)) <= tolerance


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/candidate-06/siar-holdout-attribution"),
    )
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    base = _base(candidate_dir)
    results: list[dict[str, Any]] = []
    implementation_failures: list[str] = []

    for name, description, use_surprise, use_impact, eligible in VARIANTS:
        config = copy.deepcopy(base)
        config["candidate_variant"] = f"{name}_sealed_week_2_attribution"
        config["variant_description"] = (
            description
            + " One-time causal attribution replay on the unchanged sealed "
              "2024-09-23 BTC week; not eligible for candidate selection."
        )
        config.setdefault("validation", {})["stage"] = "sealed_week_2_logic_attribution"
        config["logic"].update(
            {
                "siar_use_flow_surprise": use_surprise,
                "siar_use_impact_efficiency": use_impact,
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
                "flow_surprise": use_surprise,
                "impact_efficiency": use_impact,
                "eligible_for_selection": False,
                "causal_evidence": _evidence(run_output),
            },
        )
        if int(record.get("returncode", 1)) != 0 or not record.get("metrics"):
            implementation_failures.append(name)
        results.append(record)

    by_name = {record["name"]: record for record in results}
    full = by_name["siar_full"]
    surprise_only = by_name["siar_surprise_only_ablation"]
    impact_only = by_name["siar_impact_only_ablation"]
    freshness = by_name["siar_freshness_reference"]

    # The surprise-only variant is the unchanged selected configuration already
    # replayed on week 2. Exact reproduction separates infrastructure drift from
    # strategy differences.
    reference_path = (
        repository
        / "artifacts/candidate-06/siar-first-week/locked-week-2/metrics.json"
     )
    reproduction: dict[str, Any] = {
        "reference_path": str(reference_path.relative_to(repository)),
        "available": reference_path.exists(),
        "passed": False,
        "checks": {},
    }
    if reference_path.exists() and surprise_only.get("metrics"):
        reference = json.loads(reference_path.read_text(encoding="utf-8"))
        current = surprise_only["metrics"]
        keys = (
            "geometric_daily_nav_growth",
            "net_pnl_after_cost",
            "trades",
            "win_rate",
            "profit_factor",
            "max_drawdown_nav",
        )
        checks = {
            key: {
                "reference": reference.get(key),
                "current": current.get(key),
                "match": _same_number(reference.get(key), current.get(key)),
            }
            for key in keys
        }
        reproduction["checks"] = checks
        reproduction["passed"] = all(item["match"] for item in checks.values())
        if not reproduction["passed"]:
            implementation_failures.append("surprise_only_reproduction_mismatch")

    positive_growth = [
        record["name"]
        for record in results
        if record.get("metrics")
        and _metric(record, "geometric_daily_nav_growth") > 0.0
    ]
    summary = {
        "candidate": "SIAR sealed-week-2 causal attribution",
        "week_start_utc": "2024-09-23",
        "week_end_utc_exclusive": "2024-09-30",
        "purpose": (
            "Measure whether impact-efficiency classification suppresses the "
            "known holdout losses, and whether it supplies positive alpha or "
              "only precision filtering. No threshold, session, risk or execution "
              "parameter is changed."
        ),
        "selection_authorized": False,
        "long_evaluation_authorized": False,
        "results": results,
        "reproduction_check": reproduction,
        "attribution_deltas": {
            "impact_added_to_surprise": _delta(full, surprise_only),
            "surprise_added_to_impact": _delta(full, impact_only),
            "full_vs_freshness_reference": _delta(full, freshness),
            "surprise_only_vs_freshness_reference": _delta(surprise_only, freshness),
            "impact_only_vs_freshness_reference": _delta(impact_only, freshness),
        },
        "positive_growth_variants": positive_growth,
        "implementation_failures": sorted(set(implementation_failures)),
        "terminal_status": (
            "IMPLEMENTATION_FAILURE_REQUIRES_SAME_WEEK_RERUN"
            if implementation_failures
            else "SEALED_WEEK_2_ATTRIBUTION_COMPLETE"
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
