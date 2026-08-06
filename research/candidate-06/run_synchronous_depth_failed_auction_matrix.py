"""Controlled first-week/holdout campaign for synchronous-depth FAT entries."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import traceback
from typing import Any, Mapping

from prepare_liquidity_series import LiquidityArchiveUnavailable, prepare
from run_depth_campaign import WINDOWS, _implementation_ok, _run_one
from run_equilibrium_matrix import _base as _equilibrium_base


VARIANTS = (
    (
        "fatr_synchronous_depth",
        "Failed SAC defense becomes the existing opposite body+flow trap only when concurrent passive depth replenishes on the trap source side and opens relatively toward its target.",
        True,
        True,
    ),
    (
        "fatr_price_flow_reference",
        "Single-variable ablation: identical failed-auction price, body, flow, stop, objective, Nautilus execution, costs, and fixed 3% risk without passive-depth confirmation.",
        False,
        False,
    ),
)


def _base(raw: Mapping[str, Any]) -> dict[str, Any]:
    config = _equilibrium_base(dict(raw))
    config["logic"].update(
        {
            "engine": "FIXED_INTERVAL_AUCTION_FAILED_TRAP",
            "enable_srr": False,
            "enable_sac": True,
            "auction_period_minutes": 30,
            "auction_entry_window_minutes": 25,
            "auction_sweep_min_atr": 0.10,
            "sac_entry_confirmation": "DIRECTIONAL_BODY",
            "sac_failed_defense_action": "TRAP_RECLAIM_BODY_FLOW",
            "fatr_require_depth_confirmation": True,
            "fatr_depth_pre_window_seconds": 120,
            "fatr_depth_max_age_seconds": 90,
            "fatr_depth_min_pre_records": 2,
            "fatr_depth_min_event_records": 2,
            "fatr_depth_final_records": 1,
            "fatr_depth_min_recovery_fraction": 0.50,
            "enforce_favorable_drift_guard": True,
            "cooldown_bars": 3,
            "ambiguous_cooldown_bars": 2,
        },
    )
    return config


def _ablation(full: Mapping[str, Any], reference: Mapping[str, Any]) -> dict[str, Any]:
    if not full.get("metrics") or not reference.get("metrics"):
        return {"available": False}
    full_metrics = full["metrics"]
    reference_metrics = reference["metrics"]
    growth_delta = float(full_metrics["geometric_daily_nav_growth"]) - float(
        reference_metrics["geometric_daily_nav_growth"],
    )
    pf_full = full_metrics.get("profit_factor")
    pf_reference = reference_metrics.get("profit_factor")
    return {
        "available": True,
        "removed_variable": "synchronous passive-depth resiliency confirmation during the completed failed-defense bar",
        "unchanged": [
            "completed 30-minute auction detector",
            "SAC acceptance and retest",
            "next-completed-bar failed-defense trigger",
            "opposite directional body and taker-flow requirement",
            "structural stop and objective",
            "Nautilus orders, fills, fees, positions and NAV accounting",
            "fixed 3% planned-loss risk sizing",
        ],
        "full_minus_reference": {
            "geometric_daily_nav_growth": growth_delta,
            "trades": int(full_metrics.get("trades", 0)) - int(reference_metrics.get("trades", 0)),
            "wins": int(full_metrics.get("wins", 0)) - int(reference_metrics.get("wins", 0)),
            "max_drawdown_nav": float(full_metrics.get("max_drawdown_nav", 0.0))
            - float(reference_metrics.get("max_drawdown_nav", 0.0)),
            "profit_factor": (
                None
                if pf_full is None or pf_reference is None
                else float(pf_full) - float(pf_reference)
            ),
        },
        "interpretation": (
            "SYNCHRONOUS_DEPTH_IMPROVED_COST_AFTER_EXPECTANCY"
            if growth_delta > 0.0
            else (
                "SYNCHRONOUS_DEPTH_REDUCED_COST_AFTER_EXPECTANCY"
                if growth_delta < 0.0
                else "NO_MEASURABLE_GROWTH_EFFECT"
            )
        ),
    }


def _diagnosis(record: Mapping[str, Any], gate: Mapping[str, Any]) -> dict[str, Any]:
    metrics = dict(record.get("metrics", {}))
    if not metrics:
        return {
            "classification": "IMPLEMENTATION_OR_RUNTIME_FAILURE",
            "largest_performance_factor": "no valid Nautilus metrics",
            "working_components": [],
        }
    growth = float(metrics.get("geometric_daily_nav_growth", -1.0))
    trades = int(metrics.get("trades", 0))
    wins = int(metrics.get("wins", 0))
    win_rate = float(metrics.get("win_rate", 0.0))
    profit_factor = metrics.get("profit_factor")
    drawdown = float(metrics.get("max_drawdown_nav", 0.0))
    failures = list(metrics.get("gate_failures", []))

    if trades == 0:
        classification = "NO_EXECUTABLE_TRAPS_AFTER_CAUSAL_CONFIRMATION"
        largest = "the depth path never confirmed a price/flow trap with a cost-valid bracket"
    elif growth <= 0.0 or (profit_factor is not None and float(profit_factor) < 1.0):
        classification = "NEGATIVE_COST_AFTER_EXPECTANCY"
        largest = "confirmed failed auctions did not predict the opposite structural objective after costs"
    elif trades < int(gate["minimum_trades"]):
        classification = "INSUFFICIENT_INDEPENDENT_OPPORTUNITIES"
        largest = "the causal event was too sparse for the required compounding opportunity rate"
    elif win_rate < float(gate["minimum_win_rate"]):
        classification = "DIRECTION_OR_TIMING_FAILURE"
        largest = "the reversal path was not reliable enough after confirmation"
    elif drawdown > float(gate["maximum_drawdown"]):
        classification = "LOSS_CLUSTERING"
        largest = "confirmed failures clustered into unrecoverable NAV damage"
    else:
        classification = "PARTIAL_GATE_FAILURE"
        largest = ", ".join(failures) or "unknown gate component"

    working: list[str] = []
    if growth > 0.0:
        working.append("positive geometric NAV growth after costs")
    if profit_factor is not None and float(profit_factor) > 1.0:
        working.append("positive profit factor after explicit costs")
    if wins >= int(gate["minimum_positive_trades"]):
        working.append("minimum independent winning-trade count")
    if drawdown <= float(gate["maximum_drawdown"]):
        working.append("drawdown within the recoverable gate")
    return {
        "classification": classification,
        "largest_performance_factor": largest,
        "working_components": working,
        "gate_failures": failures,
    }


def _render(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Candidate 06 v1.9 Failed-Acceptance Trap Resiliency (FATR)",
        "",
        "The existing failed-auction price/flow scenario is held fixed. The only experimental variable is passive-depth behavior observed after the original SAC signal and no later than the completed failed-defense bar.",
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
    lines.extend(["", "## Controlled ablation", ""])
    ablation = summary.get("ablation", {})
    lines.append(f"- interpretation: `{ablation.get('interpretation')}`")
    lines.append(f"- full minus reference: `{ablation.get('full_minus_reference')}`")
    lines.extend(["", "## Diagnoses", ""])
    for name, diagnosis in summary.get("diagnoses", {}).items():
        lines.append(
            f"- **{name}**: `{diagnosis.get('classification')}` — {diagnosis.get('largest_performance_factor')}",
        )
        if diagnosis.get("working_components"):
            lines.append(f"  - working: {', '.join(diagnosis['working_components'])}")
    if summary.get("error"):
        lines.extend(["", "## Error", "", "```text", str(summary["error"])[-12000:], "```"])
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
        default=Path("artifacts/candidate-06/fatr-first-week"),
    )
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)

    raw = json.loads((candidate_dir / "config.json").read_text(encoding="utf-8"))
    gate = raw["gate"]
    base_summary: dict[str, Any] = {
        "candidate": "candidate-06-fatr-v1.9",
        "design": "completed fixed auction -> SAC acceptance/retest -> next-bar failed defense -> opposite body+flow trap -> synchronous source replenishment and target-path opening",
        "pattern_scenario_separation": {
            "detector": "normalized official passive-depth observations through the completed decision bar",
            "scenario": "unchanged FailedAuctionTrapRelayEngine and failed_acceptance_trap contract",
        },
        "execution": "NautilusTrader BacktestEngine only; native orders, fills, fees, positions and portfolio NAV",
        "risk_fraction": float(raw["execution"]["risk_fraction"]),
        "variant_priority": [name for name, *_ in VARIANTS],
        "selection_rule": "only the predeclared synchronous-depth variant is selectable; price/flow reference is a one-variable diagnostic ablation",
        "first_week_results": [],
        "frozen_validation": [],
        "selected": None,
        "all_three_weeks_passed": False,
        "long_evaluation_authorized": False,
    }

    try:
        prepared_first = prepare(
            "BTCUSDT",
            *WINDOWS[0],
            root / "data" / "btc-week-1-passive-liquidity.csv",
        )
    except LiquidityArchiveUnavailable as exc:
        summary = {
            **base_summary,
            "implementation_status": "NOT_EVALUATED",
            "terminal_status": "DATASET_UNAVAILABLE",
            "error": str(exc),
            "ablation": {"available": False},
            "diagnoses": {},
        }
        _write(root, summary)
        return 4
    except Exception:
        summary = {
            **base_summary,
            "implementation_status": "DATA_PREPARATION_FAILURE",
            "terminal_status": "IMPLEMENTATION_OR_DATA_FAILURE",
            "error": traceback.format_exc(),
            "ablation": {"available": False},
            "diagnoses": {},
        }
        _write(root, summary)
        return 5

    base = _base(raw)
    configs: dict[str, dict[str, Any]] = {}
    first_results: list[dict[str, Any]] = []
    for name, description, require_depth, eligible in VARIANTS:
        config = copy.deepcopy(base)
        config["candidate_variant"] = name
        config["variant_description"] = description
        config["logic"]["fatr_require_depth_confirmation"] = require_depth
        configs[name] = config
        first_results.append(
            _run_one(
                config,
                name=name,
                description=description,
                eligible=eligible,
                week_index=0,
                prepared=prepared_first,
                root=root,
                candidate_dir=candidate_dir,
                repository=repository,
            ),
        )

    diagnoses = {record["name"]: _diagnosis(record, gate) for record in first_results}
    full = next(record for record in first_results if record["name"] == "fatr_synchronous_depth")
    reference = next(record for record in first_results if record["name"] == "fatr_price_flow_reference")
    summary: dict[str, Any] = {
        **base_summary,
        "implementation_status": "PASS" if _implementation_ok(first_results) else "FAIL",
        "data_status": "PASS",
        "data_source": prepared_first.source,
        "data_measurement": prepared_first.measurement,
        "first_week_results": first_results,
        "ablation": _ablation(full, reference),
        "diagnoses": diagnoses,
    }
    if not _implementation_ok(first_results):
        summary["terminal_status"] = "IMPLEMENTATION_OR_RUNTIME_FAILURE"
        summary["error"] = "At least one controlled run did not produce valid Nautilus metrics."
        _write(root, summary)
        return 5

    selected = "fatr_synchronous_depth" if full.get("gate_passed") else None
    summary["selected"] = selected
    if selected is None:
        summary["terminal_status"] = "FIRST_WEEK_LOGIC_GATE_FAILED"
        summary["discarded"] = {
            "fatr_synchronous_depth": {
                **diagnoses["fatr_synchronous_depth"],
                "ablation": summary["ablation"],
            },
        }
        _write(root, summary)
        return 2

    locked = copy.deepcopy(configs[selected])
    locked.setdefault("validation", {})["stage"] = "three_week_validation"
    locked["logic"]["depth_series_path"] = "__RUNTIME_OFFICIAL_PASSIVE_LIQUIDITY_SERIES__"
    locked_path = candidate_dir / "config.fatr.locked.json"
    locked_path.write_text(json.dumps(locked, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary["locked_config"] = str(locked_path.relative_to(repository))

    frozen: list[dict[str, Any]] = []
    for week_index in (1, 2):
        try:
            prepared = prepare(
                "BTCUSDT",
                *WINDOWS[week_index],
                root / "data" / f"btc-week-{week_index + 1}-passive-liquidity.csv",
            )
        except Exception:
            summary.update(
                {
                    "implementation_status": "HOLDOUT_DATA_PREPARATION_FAILURE",
                    "terminal_status": "IMPLEMENTATION_OR_DATA_FAILURE",
                    "error": traceback.format_exc(),
                    "frozen_validation": frozen,
                },
            )
            _write(root, summary)
            return 5
        record = _run_one(
            configs[selected],
            name=selected,
            description=VARIANTS[0][1],
            eligible=True,
            week_index=week_index,
            prepared=prepared,
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
                    "error": record.get("errors_tail") or record.get("stderr_tail"),
                    "frozen_validation": frozen,
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
            "terminal_status": (
                "THREE_WEEK_GATE_PASSED"
                if all_three
                else "FROZEN_HOLDOUT_LOGIC_GATE_FAILED"
            ),
            "holdout_diagnoses": {
                f"week-{int(record['week_index']) + 1}": _diagnosis(record, gate)
                for record in frozen
            },
        },
    )
    if not all_three:
        summary["discarded"] = {
            selected: {
                "classification": "FAILED_UNCHANGED_FROZEN_HOLDOUT",
                "largest_performance_factor": "the exact first-week causal contract did not preserve cost-after expectancy in both sealed regimes",
                "first_week": diagnoses[selected],
                "holdouts": summary["holdout_diagnoses"],
            },
        }
    _write(root, summary)
    return 0 if all_three else 3


if __name__ == "__main__":
    raise SystemExit(main())
