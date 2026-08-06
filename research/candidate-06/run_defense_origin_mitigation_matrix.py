"""Controlled Nautilus campaign for ADOM post-confirmation entry placement."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Mapping

from run_equilibrium_matrix import _base as _equilibrium_base
from run_equilibrium_matrix import _run


VARIANTS = (
    (
        "adom_defense_origin_limit",
        "Completed 30m SAC retest and next-bar directional defense; passive GTD bracket entry at the defense-bar origin until the current 30m auction ends.",
        "DEFENSE_ORIGIN_LIMIT",
        True,
    ),
    (
        "adom_market_after_defense_reference",
        "Single-variable reference: identical SAC detector, defense, stop, target, costs, risk and Nautilus execution with the existing market entry at defense-bar close.",
        "MARKET_AFTER_DEFENSE",
        False,
    ),
)


def _base(raw: Mapping[str, Any]) -> dict[str, Any]:
    config = _equilibrium_base(copy.deepcopy(dict(raw)))
    config["logic"].update(
        {
            "engine": "FIXED_INTERVAL_AUCTION_RELAY",
            "enable_srr": False,
            "enable_sac": True,
            "auction_period_minutes": 30,
            "auction_entry_window_minutes": 25,
            "auction_sweep_min_atr": 0.10,
            "sac_entry_confirmation": "DIRECTIONAL_BODY",
            "sac_failed_defense_action": "ABSTAIN",
            "fatr_require_depth_confirmation": False,
            "enforce_favorable_drift_guard": True,
            "cooldown_bars": 3,
            "ambiguous_cooldown_bars": 2,
        },
    )
    return config


def _run_variant(
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
    runtime = copy.deepcopy(dict(config))
    if week_index > 0:
        runtime.setdefault("validation", {})["stage"] = "three_week_validation"
    config_dir = root / "configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / f"{name}-week-{week_index + 1}.json"
    config_path.write_text(json.dumps(runtime, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    run_output = root / "runs" / name / f"week-{week_index + 1}"
    record = _run(config_path, run_output, week_index, candidate_dir, repository)
    record.update(
        {
            "name": name,
            "description": description,
            "eligible_for_selection": eligible,
            "week_index": week_index,
            "config_path": str(config_path.relative_to(repository)),
            "run_output": str(run_output.relative_to(repository)),
        },
    )
    return record


def _implementation_ok(records: list[Mapping[str, Any]]) -> bool:
    return bool(records) and all(
        int(record.get("returncode", 1)) == 0 and isinstance(record.get("metrics"), dict)
        for record in records
    )


def _reference_regression(
    reference: Mapping[str, Any],
    repository: Path,
) -> dict[str, Any]:
    baseline_path = (
        repository
        / "artifacts/candidate-06/auction-30m-first-week/auction_30m_directional_defense/metrics.json"
    )
    if not baseline_path.exists() or not reference.get("metrics"):
        return {"passed": False, "reason": "REFERENCE_BASELINE_OR_METRICS_MISSING"}
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    current = reference["metrics"]
    keys = (
        "geometric_daily_nav_growth",
        "trades",
        "wins",
        "win_rate",
        "profit_factor",
        "max_drawdown_nav",
    )
    differences: dict[str, Any] = {}
    passed = True
    for key in keys:
        expected = baseline.get(key)
        actual = current.get(key)
        if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
            equal = abs(float(actual) - float(expected)) <= 1e-12
        else:
            equal = actual == expected
        if not equal:
            passed = False
            differences[key] = {"expected": expected, "actual": actual}
    return {
        "passed": passed,
        "baseline_path": str(baseline_path.relative_to(repository)),
        "keys": list(keys),
        "differences": differences,
    }


def _ablation(full: Mapping[str, Any], reference: Mapping[str, Any]) -> dict[str, Any]:
    if not full.get("metrics") or not reference.get("metrics"):
        return {"available": False}
    left = full["metrics"]
    right = reference["metrics"]
    return {
        "available": True,
        "changed_variable": "post-defense entry placement and lifetime: passive limit at completed defense-bar origin until fixed-auction expiry versus immediate market entry at defense-bar close",
        "unchanged": [
            "completed 30-minute auction construction",
            "SAC displacement and first held retest",
            "next completed directional-body defense",
            "accepted boundary and structural invalidation",
            "structural projection target",
            "fixed three-percent planned-loss risk sizing from whole-account NAV",
            "Nautilus native bracket, fill, fee, position and NAV accounting",
            "one global pending-entry or position slot",
        ],
        "full_minus_reference": {
            "geometric_daily_nav_growth": float(left["geometric_daily_nav_growth"])
            - float(right["geometric_daily_nav_growth"]),
            "trades": int(left["trades"]) - int(right["trades"]),
            "wins": int(left["wins"]) - int(right["wins"]),
            "win_rate": float(left["win_rate"]) - float(right["win_rate"]),
            "profit_factor": (
                None
                if left.get("profit_factor") is None or right.get("profit_factor") is None
                else float(left["profit_factor"]) - float(right["profit_factor"])
            ),
            "max_drawdown_nav": float(left["max_drawdown_nav"])
            - float(right["max_drawdown_nav"]),
        },
    }


def _diagnose(record: Mapping[str, Any], gate: Mapping[str, Any]) -> dict[str, Any]:
    metrics = dict(record.get("metrics", {}))
    if not metrics:
        return {
            "classification": "IMPLEMENTATION_OR_RUNTIME_FAILURE",
            "largest_performance_factor": "no valid Nautilus metrics",
            "working_components": [],
        }
    diagnostics = dict(metrics.get("diagnostics", {}))
    abstentions = dict(diagnostics.get("entry_abstentions", {}))
    terminal = dict(diagnostics.get("unfilled_entry_terminal_counts", {}))
    growth = float(metrics.get("geometric_daily_nav_growth", -1.0))
    trades = int(metrics.get("trades", 0))
    wins = int(metrics.get("wins", 0))
    win_rate = float(metrics.get("win_rate", 0.0))
    profit_factor = metrics.get("profit_factor")
    maximum_drawdown = float(metrics.get("max_drawdown_nav", 0.0))

    if growth <= 0.0 or (profit_factor is not None and float(profit_factor) < 1.0):
        classification = "NEGATIVE_COST_AFTER_EXPECTANCY"
        largest = "filled mitigation entries did not preserve the accepted-auction structural path after costs"
    elif trades < int(gate["minimum_trades"]):
        classification = "INSUFFICIENT_FILLED_INDEPENDENT_OPPORTUNITIES"
        largest = "confirmed auctions did not revisit the defense origin often enough before causal expiry"
    elif win_rate < float(gate["minimum_win_rate"]):
        classification = "MITIGATION_HOLD_FAILURE"
        largest = "a revisit to the defense origin did not reliably resume the accepted direction"
    elif maximum_drawdown > float(gate["maximum_drawdown"]):
        classification = "LOSS_CLUSTERING"
        largest = "mitigation fills clustered into unrecoverable structural invalidations"
    elif wins < int(gate["minimum_positive_trades"]):
        classification = "INSUFFICIENT_POSITIVE_TRADE_COUNT"
        largest = "too few independent accepted-auction legs reached their objective"
    else:
        classification = "PARTIAL_GATE_FAILURE"
        largest = ", ".join(metrics.get("gate_failures", [])) or "unknown gate component"

    working: list[str] = []
    if growth > 0.0:
        working.append("positive cost-after geometric NAV growth")
    if profit_factor is not None and float(profit_factor) > 1.0:
        working.append("positive cost-after profit factor")
    if win_rate >= float(gate["minimum_win_rate"]):
        working.append("directional hit-rate gate")
    if maximum_drawdown <= float(gate["maximum_drawdown"]):
        working.append("recoverable drawdown gate")
    return {
        "classification": classification,
        "largest_performance_factor": largest,
        "working_components": working,
        "entries_submitted": diagnostics.get("entries_submitted"),
        "unfilled_entry_terminal_counts": terminal,
        "entry_abstentions": abstentions,
        "gate_failures": metrics.get("gate_failures", []),
    }


def _render(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Candidate 06 v2.0 Accepted-Auction Defense-Origin Mitigation (ADOM)",
        "",
        "The price scenario and next-bar defense are unchanged. Only post-confirmation entry placement changes from market chase to a native passive GTD limit at the completed defense-bar origin.",
        "",
        f"Implementation status: `{summary.get('implementation_status')}`",
        f"Terminal status: `{summary.get('terminal_status')}`",
        f"Selected: `{summary.get('selected')}`" if summary.get("selected") else "Selected: none",
        "",
        "|variant|week|eligible|gate|geom/day|trades|wins|win rate|PF|max DD|failures|",
        "|---|---:|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for record in [*summary.get("first_week_results", []), *summary.get("frozen_validation", [])]:
        metrics = record.get("metrics", {})
        lines.append(
            "|{name}|{week}|{eligible}|{gate}|{growth:.6%}|{trades}|{wins}|{win:.2%}|{pf}|{dd:.2%}|{failures}|".format(
                name=record.get("name"),
                week=int(record.get("week_index", 0)) + 1,
                eligible=record.get("eligible_for_selection"),
                gate=record.get("gate_passed"),
                growth=float(metrics.get("geometric_daily_nav_growth", -1.0)),
                trades=metrics.get("trades"),
                wins=metrics.get("wins"),
                win=float(metrics.get("win_rate", 0.0)),
                pf=metrics.get("profit_factor"),
                dd=float(metrics.get("max_drawdown_nav", 0.0)),
                failures=", ".join(metrics.get("gate_failures", [])),
            ),
        )
    lines.extend(
        [
            "",
            "## Reference regression",
            "",
            f"`{summary.get('reference_regression')}`",
            "",
            "## Controlled ablation",
            "",
            f"`{summary.get('ablation')}`",
            "",
            "## Diagnoses",
            "",
        ],
    )
    for name, diagnosis in summary.get("diagnoses", {}).items():
        lines.append(
            f"- **{name}**: `{diagnosis.get('classification')}` — {diagnosis.get('largest_performance_factor')}",
        )
        if diagnosis.get("working_components"):
            lines.append(f"  - working: {', '.join(diagnosis['working_components'])}")
        lines.append(
            f"  - submitted/expired: `{diagnosis.get('entries_submitted')}` / `{diagnosis.get('unfilled_entry_terminal_counts')}`",
        )
    if summary.get("error"):
        lines.extend(["", "## Error", "", "```text", str(summary["error"])[-16000:], "```"])
    return "\n".join(lines) + "\n"


def _write(root: Path, summary: Mapping[str, Any]) -> None:
    (root / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (root / "SUMMARY.md").write_text(_render(summary), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/candidate-06/adom-first-week"),
    )
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)

    raw = json.loads((candidate_dir / "config.json").read_text(encoding="utf-8"))
    gate = raw["gate"]
    base = _base(raw)
    configs: dict[str, dict[str, Any]] = {}
    first_results: list[dict[str, Any]] = []
    for name, description, execution_mode, eligible in VARIANTS:
        config = copy.deepcopy(base)
        config["candidate_variant"] = name
        config["variant_description"] = description
        config["logic"]["sac_entry_execution"] = execution_mode
        configs[name] = config
        first_results.append(
            _run_variant(
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

    base_summary: dict[str, Any] = {
        "candidate": "candidate-06-adom-v2.0",
        "design": "completed 30m acceptance -> first held retest -> next completed directional defense -> passive entry at defense origin -> fixed-auction expiry -> unchanged structural bracket",
        "pattern_scenario_separation": {
            "detector": "completed fixed-auction SAC plus completed next-bar defense",
            "execution_scenario": "native Nautilus GTD limit at defense-bar origin with unchanged stop and objective",
        },
        "risk_fraction": float(raw["execution"]["risk_fraction"]),
        "first_week_results": first_results,
        "frozen_validation": [],
        "selected": None,
        "all_three_weeks_passed": False,
        "long_evaluation_authorized": False,
    }
    if not _implementation_ok(first_results):
        summary = {
            **base_summary,
            "implementation_status": "FAIL",
            "terminal_status": "IMPLEMENTATION_OR_RUNTIME_FAILURE",
            "reference_regression": {"passed": False},
            "ablation": {"available": False},
            "diagnoses": {
                record["name"]: _diagnose(record, gate) for record in first_results
            },
            "error": "At least one controlled first-week run did not produce valid Nautilus metrics.",
        }
        _write(root, summary)
        return 5

    full = next(record for record in first_results if record["name"] == "adom_defense_origin_limit")
    reference = next(
        record
        for record in first_results
        if record["name"] == "adom_market_after_defense_reference"
    )
    regression = _reference_regression(reference, repository)
    diagnoses = {record["name"]: _diagnose(record, gate) for record in first_results}
    summary = {
        **base_summary,
        "implementation_status": "PASS" if regression.get("passed") else "FAIL",
        "reference_regression": regression,
        "ablation": _ablation(full, reference),
        "diagnoses": diagnoses,
    }
    if not regression.get("passed"):
        summary["terminal_status"] = "REFERENCE_REGRESSION_FAILURE"
        summary["error"] = "The supposedly unchanged market-entry reference diverged from committed prior Nautilus evidence."
        _write(root, summary)
        return 5

    if not full.get("gate_passed"):
        summary["terminal_status"] = "FIRST_WEEK_LOGIC_GATE_FAILED"
        summary["discarded"] = {
            "adom_defense_origin_limit": {
                **diagnoses["adom_defense_origin_limit"],
                "ablation": summary["ablation"],
            },
        }
        _write(root, summary)
        return 2

    summary["selected"] = "adom_defense_origin_limit"
    locked = copy.deepcopy(configs["adom_defense_origin_limit"])
    locked.setdefault("validation", {})["stage"] = "three_week_validation"
    locked_path = candidate_dir / "config.adom.locked.json"
    locked_path.write_text(json.dumps(locked, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary["locked_config"] = str(locked_path.relative_to(repository))

    frozen: list[dict[str, Any]] = []
    for week_index in (1, 2):
        record = _run_variant(
            locked,
            name="adom_defense_origin_limit",
            description=VARIANTS[0][1],
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
                    "implementation_status": "HOLDOUT_RUNTIME_FAILURE",
                    "terminal_status": "IMPLEMENTATION_OR_RUNTIME_FAILURE",
                    "frozen_validation": frozen,
                    "error": record.get("stderr_tail") or record.get("errors_tail"),
                },
            )
            _write(root, summary)
            return 5

    all_three = all(record.get("gate_passed") for record in frozen)
    summary.update(
        {
            "frozen_validation": frozen,
            "all_three_weeks_passed": all_three,
            "long_evaluation_authorized": all_three,
            "holdout_diagnoses": {
                f"week-{int(record['week_index']) + 1}": _diagnose(record, gate)
                for record in frozen
            },
            "terminal_status": (
                "THREE_WEEK_GATE_PASSED"
                if all_three
                else "FROZEN_HOLDOUT_LOGIC_GATE_FAILED"
            ),
        },
    )
    if not all_three:
        summary["discarded"] = {
            "adom_defense_origin_limit": {
                "classification": "FAILED_UNCHANGED_FROZEN_HOLDOUT",
                "first_week": diagnoses["adom_defense_origin_limit"],
                "holdouts": summary["holdout_diagnoses"],
            },
        }
    _write(root, summary)
    return 0 if all_three else 3


if __name__ == "__main__":
    raise SystemExit(main())
