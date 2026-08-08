#!/usr/bin/env python3
"""Run the unchanged OIUT full hypothesis on all three sealed BTC weeks."""

from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
from typing import Any, Mapping

from run_open_interest_unwind_transfer_matrix import (
    VARIANTS,
    _base,
    _counts,
    _diagnose,
    _run,
)


def render(summary: Mapping[str, Any]) -> str:
    aggregate = summary.get("aggregate", {})
    lines = [
        "# Candidate 06 OIUT frozen three-week diagnostic",
        "",
        f"Terminal status: `{summary.get('terminal_status')}`",
        f"Long evaluation authorized: `{summary.get('long_evaluation_authorized')}`",
        "",
        (
            "|week|gate|geom/day|trades|wins|win rate|PF|max DD|"
            "ending NAV|classification|"
        ),
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for record in summary.get("records", []):
        metrics = record.get("metrics", {})
        diagnosis = record.get("diagnosis", {})
        lines.append(
            (
                "|{week}|{gate}|{growth:.6%}|{trades}|{wins}|{win:.2%}|"
                "{pf}|{dd:.2%}|{nav:.2f}|{classification}|"
            ).format(
                week=int(record.get("week_index", 0)) + 1,
                gate=record.get("gate_passed"),
                growth=float(metrics.get("geometric_daily_nav_growth", 0.0)),
                trades=metrics.get("trades"),
                wins=metrics.get("wins"),
                win=float(metrics.get("win_rate", 0.0)),
                pf=metrics.get("profit_factor"),
                dd=float(metrics.get("max_drawdown_nav", 0.0)),
                nav=float(metrics.get("ending_nav", 0.0)),
                classification=diagnosis.get("classification"),
            ),
        )
    lines.extend(
        [
            "",
            "## Aggregate",
            "",
            f"- Evaluation days: `{aggregate.get('evaluation_days')}`",
            f"- Trades: `{aggregate.get('trades')}`",
            f"- Wins: `{aggregate.get('wins')}`",
            f"- Win rate: `{float(aggregate.get('win_rate', 0.0)):.2%}`",
            (
                "- Pooled independent-week geometric NAV growth/day: "
                f"`{float(aggregate.get('pooled_geometric_daily_nav_growth', 0.0)):.6%}`"
            ),
            f"- Worst weekly max drawdown: `{float(aggregate.get('worst_weekly_max_drawdown', 0.0)):.2%}`",
            f"- Positive weeks: `{aggregate.get('positive_weeks')}/3`",
            f"- Existing full gate passes: `{aggregate.get('gate_passes')}/3`",
            "",
            "The same market hypothesis, parameters, costs, structural stops, targets,",
            "3% whole-NAV planned loss, and NautilusTrader execution are used in every week.",
        ],
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/candidate-06/oiut-frozen-diagnostic"),
    )
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)

    raw = json.loads((candidate_dir / "config.json").read_text(encoding="utf-8"))
    config = _base(raw)
    name, description, enable_reversal, enable_continuation, require_counter_rebuild = VARIANTS[0]
    config["candidate_variant"] = name
    config["variant_description"] = description
    config.setdefault("validation", {})["stage"] = "frozen_three_week_diagnostic"
    config["logic"].update(
        {
            "oiir_enable_build": False,
            "oiir_enable_unwind": True,
            "oiir_enable_unwind_reversal": enable_reversal,
            "oiir_enable_unwind_continuation": enable_continuation,
            "oiir_require_counter_inventory_rebuild": require_counter_rebuild,
        },
    )

    records: list[dict[str, Any]] = []
    for week_index in range(3):
        frozen = copy.deepcopy(config)
        config_dir = root / "configs"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / f"{name}-week-{week_index + 1}.json"
        config_path.write_text(
            json.dumps(frozen, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        run_output = root / "runs" / name / f"week-{week_index + 1}"
        record = _run(
            config_path,
            run_output,
            week_index,
            candidate_dir,
            repository,
        )
        record.update(
            {
                "name": name,
                "description": description,
                "eligible_for_selection": True,
                "week_index": week_index,
                "config_path": str(config_path.relative_to(repository)),
                "causal_counts": _counts(run_output),
            },
        )
        record["diagnosis"] = _diagnose(record)
        records.append(record)

    implementation_ok = all(
        int(record.get("returncode", 1)) == 0
        and isinstance(record.get("metrics"), Mapping)
        for record in records
    )
    if implementation_ok:
        metrics = [record["metrics"] for record in records]
        evaluation_days = sum(float(item.get("evaluation_days", 0.0)) for item in metrics)
        nav_multiple = math.prod(
            float(item.get("ending_nav", 0.0)) / float(item.get("starting_nav", 1.0))
            for item in metrics
        )
        pooled_growth = (
            nav_multiple ** (1.0 / evaluation_days) - 1.0
            if evaluation_days > 0.0 and nav_multiple > 0.0
            else -1.0
        )
        trades = sum(int(item.get("trades", 0)) for item in metrics)
        wins = sum(int(item.get("wins", 0)) for item in metrics)
        positive_weeks = sum(
            float(item.get("geometric_daily_nav_growth", 0.0)) > 0.0
            for item in metrics
        )
        gate_passes = sum(bool(record.get("gate_passed")) for record in records)
        aggregate = {
            "evaluation_days": evaluation_days,
            "nav_multiple_product": nav_multiple,
            "pooled_geometric_daily_nav_growth": pooled_growth,
            "trades": trades,
            "wins": wins,
            "win_rate": wins / trades if trades else 0.0,
            "positive_weeks": positive_weeks,
            "gate_passes": gate_passes,
            "worst_weekly_max_drawdown": max(
                float(item.get("max_drawdown_nav", 0.0)) for item in metrics
            ),
        }
    else:
        aggregate = {}

    long_authorized = bool(
        implementation_ok
        and len(records) == 3
        and all(bool(record.get("gate_passed")) for record in records)
    )
    if not implementation_ok:
        terminal = "IMPLEMENTATION_OR_DATA_FAILURE"
        exit_code = 5
    elif long_authorized:
        terminal = "FROZEN_THREE_WEEK_GATE_PASSED"
        exit_code = 0
    elif (
        float(aggregate.get("pooled_geometric_daily_nav_growth", 0.0)) >= 0.01
        and int(aggregate.get("positive_weeks", 0)) == 3
    ):
        terminal = "POOLED_TARGET_REACHED_BUT_EXISTING_REPLICATION_GATES_FAILED"
        exit_code = 3
    else:
        terminal = "FROZEN_THREE_WEEK_TARGET_NOT_REPLICATED"
        exit_code = 2

    summary = {
        "candidate": "candidate-06-oiut-v5.2-frozen-diagnostic",
        "design": (
            "unchanged completed OI contraction bifurcation across all three "
            "sealed BTC weeks; no first-week gate shortcut and no parameter change"
        ),
        "records": records,
        "aggregate": aggregate,
        "long_evaluation_authorized": long_authorized,
        "terminal_status": terminal,
    }
    (root / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (root / "SUMMARY.md").write_text(render(summary), encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
