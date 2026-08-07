"""One-variable temporal-preexistence ablation for the clean UOAM logic failure."""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import json
from pathlib import Path
from typing import Any, Mapping

from run_objective_lifecycle_matrix import _base, _implementation_ok, _run_variant


VARIANTS = (
    (
        "uoam_strict_confirmed_before_acceptance_reference",
        "Exact clean UOAM reference: the objective source and pivot confirmation both predate the completed accepting auction.",
        "CONFIRMED_BEFORE_ACCEPTANCE",
        False,
    ),
    (
        "uoam_source_preexists_confirm_by_acceptance_end",
        "Single-variable ablation: the objective source predates the accepting auction, while its causal pivot confirmation may complete during that auction; the objective must still remain beyond the accepting extreme.",
        "SOURCE_BEFORE_CONFIRM_BY_ACCEPTANCE_END",
        True,
    ),
)


def _counts(output: Path) -> dict[str, int]:
    counter: Counter[str] = Counter()
    path = output / "scenario_events.jsonl"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                payload = json.loads(line)
                counter[str(payload.get("reason_code", "UNKNOWN"))] += 1
    return dict(counter)


def _run(
    config: Mapping[str, Any],
    *,
    name: str,
    description: str,
    eligible: bool,
    week_index: int,
    root: Path,
    candidate_dir: Path,
    repository: Path,
) -> dict[str, Any]:
    record = _run_variant(
        config,
        name=name,
        description=description,
        eligible=eligible,
        week_index=week_index,
        root=root,
        candidate_dir=candidate_dir,
        repository=repository,
    )
    record["reason_counts"] = _counts(repository / record["run_output"])
    return record


def _same_metrics(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "geometric_daily_nav_growth",
        "trades",
        "wins",
        "win_rate",
        "profit_factor",
        "max_drawdown_nav",
        "net_pnl_after_cost",
    )
    differences: dict[str, Any] = {}
    for key in keys:
        a, b = left.get(key), right.get(key)
        equal = (
            abs(float(a) - float(b)) <= 1e-12
            if isinstance(a, (int, float)) and isinstance(b, (int, float))
            else a == b
        )
        if not equal:
            differences[key] = {"expected": a, "actual": b}
    return {"passed": not differences, "keys": list(keys), "differences": differences}


def _render(summary: Mapping[str, Any]) -> str:
    lines = [
        "# UOAM Temporal-Preexistence Ablation",
        "",
        f"Terminal status: `{summary.get('terminal_status')}`",
        f"Selected: `{summary.get('selected')}`" if summary.get("selected") else "Selected: none",
        "",
        "|variant|week|gate|geom/day|trades|wins|win rate|PF|max DD|bindings|no-objective|",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for record in [*summary.get("first_week_results", []), *summary.get("frozen_validation", [])]:
        metrics = record.get("metrics", {})
        reasons = record.get("reason_counts", {})
        lines.append(
            "|{name}|{week}|{gate}|{growth:.6%}|{trades}|{wins}|{win:.2%}|{pf}|{dd:.2%}|{bindings}|{none}|".format(
                name=record.get("name"),
                week=int(record.get("week_index", 0)) + 1,
                gate=record.get("gate_passed"),
                growth=float(metrics.get("geometric_daily_nav_growth", -1.0)),
                trades=metrics.get("trades"),
                wins=metrics.get("wins"),
                win=float(metrics.get("win_rate", 0.0)),
                pf=metrics.get("profit_factor"),
                dd=float(metrics.get("max_drawdown_nav", 0.0)),
                bindings=reasons.get("PREEXISTING_UNRESOLVED_OBJECTIVE_BOUND", 0),
                none=reasons.get("NO_PREEXISTING_UNRESOLVED_OBJECTIVE", 0),
            ),
        )
    lines.extend(
        [
            "",
            "## Controlled variable",
            "",
            "Only objective confirmation timing changes. Source-time precedence, untouched objective, target ladder, sweep, response, entry, stop, target, timeout, cost, fill and three-percent NAV risk remain fixed.",
            "",
            f"Strict regression: `{summary.get('strict_regression')}`",
        ],
    )
    return "\n".join(lines) + "\n"


def _write(root: Path, summary: Mapping[str, Any]) -> None:
    (root / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (root / "SUMMARY.md").write_text(_render(summary), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("artifacts/candidate-06/uoam-temporal-ablation"))
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    raw = json.loads((candidate_dir / "config.json").read_text(encoding="utf-8"))
    base = _base(raw)

    configs: dict[str, dict[str, Any]] = {}
    first: list[dict[str, Any]] = []
    for name, description, timing_mode, eligible in VARIANTS:
        config = copy.deepcopy(base)
        config["candidate_variant"] = name
        config["variant_description"] = description
        config["logic"].update(
            {
                "uoam_use_objective_lifecycle": True,
                "uoam_exit_open_position_on_invalidation": True,
                "uoam_objective_timing_mode": timing_mode,
            },
        )
        configs[name] = config
        first.append(
            _run(
                config,
                name=name,
                description=description,
                eligible=eligible,
                week_index=0,
                root=root,
                candidate_dir=candidate_dir,
                repository=repository,
            ),
        )

    existing_path = repository / "artifacts/candidate-06/uoam-first-week/summary.json"
    existing = json.loads(existing_path.read_text(encoding="utf-8"))
    expected_record = next(
        value for value in existing["first_week_results"]
        if value["name"] == "uoam_bound_objective_with_causal_exit"
    )
    strict = first[0]
    regression = _same_metrics(expected_record["metrics"], strict.get("metrics", {}))
    implementation_ok = _implementation_ok(first) and regression["passed"]
    selected = first[1]["name"] if first[1].get("gate_passed") else None
    summary: dict[str, Any] = {
        "candidate": "candidate-06-uoam-temporal-ablation-v2.2",
        "changed_variable": "objective pivot confirmation must predate acceptance start versus objective source predates acceptance and confirmation completes by acceptance end",
        "unchanged_contracts": [
            "completed 60-minute accepted auction",
            "objective source predates accepting auction",
            "objective remains beyond accepting extreme",
            "confirmed five-minute swing/equal pool detector",
            "counter-bias sweep and separate one-minute response",
            "one entry per objective and ordered ladder",
            "origin/boundary/replacement invalidation",
            "same market entry, structural stop and bound target",
            "Nautilus fills, fees, slippage, timeout and NAV accounting",
            "whole-account three-percent planned-loss sizing",
        ],
        "implementation_status": "PASS" if implementation_ok else "FAIL",
        "strict_regression": regression,
        "first_week_results": first,
        "frozen_validation": [],
        "selected": selected,
        "all_three_weeks_passed": False,
        "long_evaluation_authorized": False,
    }
    if not implementation_ok:
        summary["terminal_status"] = "IMPLEMENTATION_OR_STRICT_REGRESSION_FAILURE"
        _write(root, summary)
        return 5
    if selected is None:
        summary["terminal_status"] = "TEMPORAL_ABLATION_FIRST_WEEK_LOGIC_FAILED"
        summary["discarded_family"] = "UNRESOLVED_OBJECTIVE_LIFECYCLE"
        _write(root, summary)
        return 2

    locked = copy.deepcopy(configs[selected])
    locked.setdefault("validation", {})["stage"] = "three_week_validation"
    locked_path = candidate_dir / "config.uoam_temporal.locked.json"
    locked_path.write_text(json.dumps(locked, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary["locked_config"] = str(locked_path.relative_to(repository))
    frozen: list[dict[str, Any]] = []
    for week_index in (1, 2):
        record = _run(
            locked,
            name=selected,
            description=VARIANTS[1][1],
            eligible=True,
            week_index=week_index,
            root=root,
            candidate_dir=candidate_dir,
            repository=repository,
        )
        frozen.append(record)
        if not _implementation_ok([record]):
            summary.update(
                {
                    "frozen_validation": frozen,
                    "implementation_status": "HOLDOUT_RUNTIME_FAILURE",
                    "terminal_status": "IMPLEMENTATION_OR_RUNTIME_FAILURE",
                },
            )
            _write(root, summary)
            return 5
    all_three = all(value.get("gate_passed") for value in frozen)
    summary.update(
        {
            "frozen_validation": frozen,
            "all_three_weeks_passed": all_three,
            "long_evaluation_authorized": all_three,
            "terminal_status": "THREE_WEEK_GATE_PASSED" if all_three else "TEMPORAL_ABLATION_FROZEN_HOLDOUT_FAILED",
        },
    )
    if not all_three:
        summary["discarded_family"] = "UNRESOLVED_OBJECTIVE_LIFECYCLE"
    _write(root, summary)
    return 0 if all_three else 3


if __name__ == "__main__":
    raise SystemExit(main())
