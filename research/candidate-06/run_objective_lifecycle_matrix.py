"""Predeclared OLAR first-week diagnostics and sealed-week validation.

Selection is not a best-return search. The full objective-lifecycle hypothesis
is the only eligible strategy. The objective-reuse ablation and unchanged HML
parent are diagnostic controls on the same newly frozen BTC week.
"""

from __future__ import annotations

import argparse
from collections import Counter
import copy
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from register_objective_lifecycle_engine import register


VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "name": "olar_full",
        "description": (
            "Completed 60m acceptance -> current 15m directional leg -> confirmed 5m "
            "swing/equal pool sweep -> separate 1m response -> unresolved one-use pool "
            "or leg-extreme objective; opposing control acceptance and leg-origin loss "
            "are structural invalidations."
        ),
        "engine": "OBJECTIVE_LIFECYCLE_ACCEPTANCE_RELAY",
        "one_use": True,
        "eligible": True,
    },
    {
        "name": "olar_objective_reuse_ablation",
        "description": (
            "Controlled ablation: identical HTF/control-leg/sweep/response/exit logic, "
            "but confirmed target objectives may be reused across later legs."
        ),
        "engine": "OBJECTIVE_LIFECYCLE_ACCEPTANCE_RELAY",
        "one_use": False,
        "eligible": False,
    },
    {
        "name": "hml_parent_reference",
        "description": (
            "Unchanged leading HML parent on the same new week: structural-only HTF "
            "bias and target selection from confirmed pools plus rolling extrema."
        ),
        "engine": "HIERARCHICAL_MULTI_LIQUIDITY",
        "one_use": None,
        "eligible": False,
    },
)


def _run(
    config_path: Path,
    output: Path,
    week_index: int,
    candidate_dir: Path,
    repository: Path,
) -> dict[str, Any]:
    completed = subprocess.run(
        [
            sys.executable,
            str(candidate_dir / "run_validation.py"),
            "--config",
            str(config_path),
            "--output",
            str(output),
            "--week-index",
            str(week_index),
            "--allow-gate-fail",
        ],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
    )
    record: dict[str, Any] = {
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-12000:],
    }
    metrics_path = output / "metrics.json"
    if metrics_path.exists():
        record["metrics"] = json.loads(metrics_path.read_text(encoding="utf-8"))
        record["gate_passed"] = bool(record["metrics"].get("gate_passed"))
        record["terminal_classification"] = (
            "GATE_PASSED" if record["gate_passed"] else "LOGIC_GATE_FAILED"
        )
    else:
        record["gate_passed"] = False
        record["terminal_classification"] = "IMPLEMENTATION_OR_RUNTIME_FAILURE"
    return record


def _evidence_counts(run_output: Path) -> dict[str, Any]:
    reasons: Counter[str] = Counter()
    event_types: Counter[str] = Counter()
    families: Counter[str] = Counter()
    targets: Counter[str] = Counter()
    outcomes: Counter[str] = Counter()

    events_path = run_output / "scenario_events.jsonl"
    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            reasons[str(payload.get("reason_code", "UNKNOWN"))] += 1
            event_types[str(payload.get("event_type", "UNKNOWN"))] += 1

    trades_path = run_output / "trades.json"
    if trades_path.exists():
        payload = json.loads(trades_path.read_text(encoding="utf-8"))
        for trade in payload.get("trades", []):
            families[str(trade.get("family", "UNKNOWN"))] += 1
            targets[str(trade.get("target_reason", "UNKNOWN"))] += 1
            outcomes[str(trade.get("outcome", "UNKNOWN"))] += 1

    return {
        "reason_counts": dict(sorted(reasons.items())),
        "event_type_counts": dict(sorted(event_types.items())),
        "trade_family_counts": dict(sorted(families.items())),
        "target_reason_counts": dict(sorted(targets.items())),
        "outcome_counts": dict(sorted(outcomes.items())),
    }


def _render(summary: dict[str, Any]) -> str:
    lines = [
        "# Candidate 07 Objective-Lifecycle Acceptance Relay",
        "",
        "The full causal hypothesis is the only selection-eligible variant. "
        "Ablations are attribution controls, not fallback strategies.",
        "",
        f"Full first-week gate: `{summary['full_first_week_passed']}`",
        f"Selected: `{summary['selected']}`",
        f"All three weeks passed: `{summary['all_three_weeks_passed']}`",
        f"Long evaluation authorized: `{summary['long_evaluation_authorized']}`",
        "",
        "|variant|eligible|rc|classification|gate|geom/day|trades|win rate|PF|max DD|failures|",
        "|---|---|---:|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for record in summary["first_week_results"]:
        metrics = record.get("metrics", {})
        pf = metrics.get("profit_factor")
        lines.append(
            "|{name}|{eligible}|{rc}|{classification}|{gate}|{growth:.6%}|{trades}|"
            "{win:.2%}|{pf}|{dd:.2%}|{failures}|".format(
                name=record["name"],
                eligible=record["eligible"],
                rc=record["returncode"],
                classification=record["terminal_classification"],
                gate=record.get("gate_passed"),
                growth=float(metrics.get("geometric_daily_nav_growth", -1.0)),
                trades=metrics.get("trades", 0),
                win=float(metrics.get("win_rate", 0.0)),
                pf=pf,
                dd=float(metrics.get("max_drawdown_nav", 0.0)),
                failures=", ".join(metrics.get("gate_failures", [])),
            ),
        )
    for record in summary["frozen_validation"]:
        metrics = record.get("metrics", {})
        lines.extend(
            [
                "",
                f"## Frozen week {int(record['week_index']) + 1}",
                "",
                f"- classification: `{record['terminal_classification']}`",
                f"- gate: `{record.get('gate_passed')}`",
                f"- geometric daily NAV growth: `{metrics.get('geometric_daily_nav_growth')}`",
                f"- trades: `{metrics.get('trades')}`",
                f"- win rate: `{metrics.get('win_rate')}`",
                f"- maximum drawdown: `{metrics.get('max_drawdown_nav')}`",
                f"- failures: `{metrics.get('gate_failures')}`",
            ],
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/candidate-07/olar-first-week"),
    )
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    register(candidate_dir)
    base_path = candidate_dir / "config.olar.json"
    base = json.loads(base_path.read_text(encoding="utf-8"))

    results: list[dict[str, Any]] = []
    for variant in VARIANTS:
        config = copy.deepcopy(base)
        config["candidate_variant"] = variant["name"]
        config["variant_description"] = variant["description"]
        config["logic"]["engine"] = variant["engine"]
        if variant["one_use"] is not None:
            config["logic"]["olar_use_objective_one_use"] = bool(variant["one_use"])
        path = output / f"{variant['name']}.json"
        path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        run_output = output / variant["name"]
        record = _run(path, run_output, 0, candidate_dir, repository)
        record.update(
            {
                "name": variant["name"],
                "description": variant["description"],
                "eligible": variant["eligible"],
                "engine": variant["engine"],
                "objective_one_use": variant["one_use"],
                "causal_evidence": _evidence_counts(run_output),
            },
        )
        results.append(record)

    full = next(record for record in results if record["name"] == "olar_full")
    full_has_metrics = "metrics" in full
    full_passed = bool(full.get("gate_passed"))
    selected = "olar_full" if full_passed else None
    frozen: list[dict[str, Any]] = []
    locked_path: Path | None = None

    if full_passed:
        locked = json.loads((output / "olar_full.json").read_text(encoding="utf-8"))
        locked.setdefault("validation", {})["stage"] = "three_week_validation"
        locked_path = candidate_dir / "config.olar.locked.json"
        locked_path.write_text(json.dumps(locked, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        for week_index in (1, 2):
            week_output = output / f"locked-week-{week_index + 1}"
            record = _run(
                locked_path,
                week_output,
                week_index,
                candidate_dir,
                repository,
            )
            record["week_index"] = week_index
            record["causal_evidence"] = _evidence_counts(week_output)
            frozen.append(record)

    all_three = (
        full_passed
        and len(frozen) == 2
        and all(record.get("gate_passed") for record in frozen)
    )
    if not full_has_metrics:
        decision = "IMPLEMENTATION_OR_RUNTIME_FAILURE_BEFORE_FIRST_WEEK_METRICS"
    elif not full_passed:
        decision = "FULL_OLAR_FIRST_WEEK_LOGIC_GATE_FAILED"
    elif not all_three:
        decision = "FULL_OLAR_SEALED_WEEK_GENERALIZATION_FAILED"
    else:
        decision = "FULL_OLAR_THREE_WEEK_GATE_PASSED"

    summary = {
        "candidate": "candidate-07-olar-v1.0",
        "design": (
            "completed 60m acceptance -> current completed 15m directional leg -> "
            "confirmed 5m swing/equal liquidity sweep -> separate 1m response -> "
            "nearest unresolved one-use confirmed objective; opposing control acceptance "
            "or active-leg-origin loss invalidates the path"
        ),
        "implementation_contract": (
            "existing NautilusTrader execution, fixed 3% NAV loss budget, costs, fills, "
            "single global slot, and accounting remain unchanged"
        ),
        "selection_rule": (
            "only olar_full is eligible; ablation and HML parent are diagnostic controls"
        ),
        "variant_priority": [value["name"] for value in VARIANTS],
        "full_first_week_passed": full_passed,
        "selected": selected,
        "locked_config": None if locked_path is None else str(locked_path.relative_to(repository)),
        "first_week_results": results,
        "frozen_validation": frozen,
        "all_three_weeks_passed": all_three,
        "long_evaluation_authorized": all_three,
        "terminal_decision": decision,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output / "SUMMARY.md").write_text(_render(summary), encoding="utf-8")

    if not full_has_metrics:
        return 1
    if not full_passed:
        return 2
    if not all_three:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
