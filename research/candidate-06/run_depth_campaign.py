"""Staged NautilusTrader campaign for passive-liquidity response scenarios."""

from __future__ import annotations

import argparse
from collections import Counter
import copy
from datetime import date
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback
from typing import Any, Callable, Mapping

from prepare_liquidity_series import (
    LiquidityArchiveUnavailable,
    PreparedLiquidity,
    prepare,
)


WINDOWS = (
    (date(2024, 2, 26), date(2024, 3, 4)),
    (date(2024, 9, 23), date(2024, 9, 30)),
    (date(2024, 4, 22), date(2024, 4, 29)),
)


def _base(raw: Mapping[str, Any]) -> dict[str, Any]:
    config = copy.deepcopy(dict(raw))
    config["logic"].update(
        {
            "engine": "DEPTH_LIQUIDITY_VACUUM_REPLENISHMENT",
            "dlvr_depth_warmup": 24,
            "dlvr_flow_ratio": 0.10,
            "dlvr_body_atr": 0.30,
            "dlvr_vacuum_z": -0.50,
            "dlvr_support_z": -0.10,
            "dlvr_near_imbalance": 0.05,
            "dlvr_replenish_z": 0.75,
            "dlvr_replenish_change": 0.20,
            "dlvr_reversal_imbalance": 0.03,
            "dlvr_require_depth_confirmation": True,
            "dlvr_enable_vacuum": True,
            "dlvr_enable_replenishment_reversal": True,
            "dlvr_retest_bars": 15,
            "dlvr_boundary_tolerance_atr": 0.08,
            "dlvr_retest_band_atr": 0.25,
            "dlvr_retest_max_flow": 0.15,
            "dlvr_response_flow": 0.08,
            "dlvr_response_body_atr": 0.20,
            "dlvr_response_imbalance": 0.0,
            "dlvr_stop_buffer_atr": 0.06,
            "dlvr_projection_fraction": 1.0,
            "minimum_structural_rr": 0.90,
            "cooldown_bars": 3,
            "max_holding_bars": 60,
            "minimum_net_rr_after_entry_delay": 0.60,
            "max_entry_drift_atr": 0.40,
            "sac_entry_confirmation": "NONE",
            "sac_failed_defense_action": "ABSTAIN",
            "enforce_favorable_drift_guard": True,
        },
    )
    return config


def _unchanged(_: dict[str, Any]) -> None:
    return None


def _vacuum_only(config: dict[str, Any]) -> None:
    config["logic"]["dlvr_enable_replenishment_reversal"] = False


def _price_only_ablation(config: dict[str, Any]) -> None:
    # Exactly one core variable is removed: passive-liquidity confirmation.
    # Price pools, aggressive flow, timing, retest, response, stop, target,
    # execution and risk are unchanged.
    config["logic"]["dlvr_require_depth_confirmation"] = False


VARIANTS: tuple[
    tuple[str, str, Callable[[dict[str, Any]], None], bool],
    ...
] = (
    (
        "dlvr_passive_liquidity_bifurcation",
        "A pool breach is interpreted as continuation under a passive-liquidity vacuum or reversal under replenishment, but only after a later held retest and separate response.",
        _unchanged,
        True,
    ),
    (
        "dlvr_vacuum_continuation_only",
        "Continuation component only: aggressive pool breach plus depleted opposing passive liquidity, held retest, and separate directional response.",
        _vacuum_only,
        True,
    ),
    (
        "dlvr_price_only_ablation",
        "Single-variable ablation: identical pool breach, flow, timing, retest, response, stop and target logic without passive-liquidity confirmation.",
        _price_only_ablation,
        False,
    ),
)


def _child_environment(candidate_dir: Path, repository: Path) -> dict[str, str]:
    environment = dict(os.environ)
    paths = [
        str(candidate_dir / "depth_shim"),
        str(candidate_dir),
        str(repository / "src"),
    ]
    existing = environment.get("PYTHONPATH")
    if existing:
        paths.append(existing)
    environment["PYTHONPATH"] = os.pathsep.join(paths)
    return environment


def _evidence_counts(run_output: Path) -> dict[str, Any]:
    reasons: Counter[str] = Counter()
    event_types: Counter[str] = Counter()
    families: Counter[str] = Counter()
    targets: Counter[str] = Counter()

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

    return {
        "reason_counts": dict(sorted(reasons.items())),
        "event_type_counts": dict(sorted(event_types.items())),
        "trade_family_counts": dict(sorted(families.items())),
        "target_reason_counts": dict(sorted(targets.items())),
    }


def _run_one(
    config: Mapping[str, Any],
    *,
    name: str,
    description: str,
    eligible: bool,
    week_index: int,
    prepared: PreparedLiquidity,
    root: Path,
    candidate_dir: Path,
    repository: Path,
) -> dict[str, Any]:
    runtime = copy.deepcopy(dict(config))
    runtime["logic"]["depth_series_path"] = str(prepared.path.resolve())
    runtime["logic"]["dlvr_liquidity_source"] = prepared.source
    runtime["logic"]["depth_max_age_minutes"] = max(
        2.0,
        min(10.0, prepared.max_gap_minutes + 1.0),
    )
    if week_index > 0:
        runtime.setdefault("validation", {})["stage"] = "three_week_validation"

    config_dir = root / "configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / f"{name}-week-{week_index + 1}.json"
    config_path.write_text(
        json.dumps(runtime, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    run_output = root / "runs" / name / f"week-{week_index + 1}"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "run_validation",
            "--config",
            str(config_path),
            "--output",
            str(run_output),
            "--week-index",
            str(week_index),
            "--allow-gate-fail",
        ],
        cwd=repository,
        env=_child_environment(candidate_dir, repository),
        text=True,
        capture_output=True,
        check=False,
    )
    record: dict[str, Any] = {
        "name": name,
        "description": description,
        "eligible_for_selection": eligible,
        "week_index": week_index,
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-5000:],
        "stderr_tail": completed.stderr[-16000:],
        "data_source": prepared.source,
        "data_measurement": prepared.measurement,
        "data_rows": prepared.rows,
        "data_max_gap_minutes": prepared.max_gap_minutes,
        "config_path": str(config_path.relative_to(repository)),
        "run_output": str(run_output.relative_to(repository)),
    }
    metrics_path = run_output / "metrics.json"
    if metrics_path.exists():
        record["metrics"] = json.loads(metrics_path.read_text(encoding="utf-8"))
        record["gate_passed"] = bool(record["metrics"].get("gate_passed"))
    else:
        record["gate_passed"] = False
        errors_path = run_output / "errors.log"
        if errors_path.exists():
            record["errors_tail"] = errors_path.read_text(encoding="utf-8")[-16000:]
    record["causal_evidence"] = _evidence_counts(run_output)
    return record


def _implementation_ok(records: list[dict[str, Any]]) -> bool:
    return bool(records) and all(
        int(record.get("returncode", 1)) == 0 and isinstance(record.get("metrics"), dict)
        for record in records
    )


def _diagnose_record(record: Mapping[str, Any], gate: Mapping[str, Any]) -> dict[str, Any]:
    metrics = dict(record.get("metrics", {}))
    if not metrics:
        return {
            "classification": "IMPLEMENTATION_OR_RUNTIME_FAILURE",
            "largest_performance_factor": "no valid Nautilus metrics",
            "working_components": [],
        }

    failures = set(metrics.get("gate_failures", []))
    growth = float(metrics.get("geometric_daily_nav_growth", -1.0))
    trades = int(metrics.get("trades", 0))
    win_rate = float(metrics.get("win_rate", 0.0))
    profit_factor = metrics.get("profit_factor")
    maximum_drawdown = float(metrics.get("max_drawdown_nav", 0.0))

    if growth <= 0.0 or (profit_factor is not None and float(profit_factor) < 1.0):
        classification = "NEGATIVE_COST_AFTER_EXPECTANCY"
        largest = "direction/timing classification produced losses after fees and one-tick slippage"
    elif trades < int(gate["minimum_trades"]):
        classification = "INSUFFICIENT_INDEPENDENT_OPPORTUNITIES"
        largest = "scenario sequence was too selective to compound at the required opportunity rate"
    elif win_rate < float(gate["minimum_win_rate"]):
        classification = "DIRECTION_OR_RESPONSE_CONFIRMATION_FAILURE"
        largest = "accepted direction or separate response did not predict the next structural leg reliably"
    elif maximum_drawdown > float(gate["maximum_drawdown"]):
        classification = "UNRECOVERABLE_LOSS_CLUSTERING"
        largest = "losses clustered under repeated or persistent market-state classification"
    elif "profit_concentration" in failures:
        classification = "PROFIT_CONCENTRATION"
        largest = "positive expectancy depended on too few winning events"
    else:
        classification = "PARTIAL_GATE_FAILURE"
        largest = ", ".join(sorted(failures)) or "unknown"

    working: list[str] = []
    if growth > 0.0:
        working.append("positive cost-after geometric NAV growth")
    if profit_factor is not None and float(profit_factor) > 1.0:
        working.append("positive aggregate profit factor after explicit costs")
    if win_rate >= float(gate["minimum_win_rate"]):
        working.append("directional hit rate met the ex-ante gate")
    if maximum_drawdown <= float(gate["maximum_drawdown"]):
        working.append("NAV drawdown remained within the recoverable gate")
    if int(metrics.get("wins", 0)) >= int(gate["minimum_positive_trades"]):
        working.append("minimum count of positive trades was reached")

    return {
        "classification": classification,
        "largest_performance_factor": largest,
        "working_components": working,
        "gate_failures": sorted(failures),
    }


def _ablation_interpretation(results: list[dict[str, Any]]) -> dict[str, Any]:
    full = next(
        (record for record in results if record.get("name") == "dlvr_passive_liquidity_bifurcation"),
        None,
    )
    ablation = next(
        (record for record in results if record.get("name") == "dlvr_price_only_ablation"),
        None,
    )
    if not full or not ablation or not full.get("metrics") or not ablation.get("metrics"):
        return {"available": False}
    full_metrics = full["metrics"]
    ablation_metrics = ablation["metrics"]
    delta_growth = float(full_metrics["geometric_daily_nav_growth"]) - float(
        ablation_metrics["geometric_daily_nav_growth"],
    )
    delta_win_rate = float(full_metrics["win_rate"]) - float(ablation_metrics["win_rate"])
    delta_trades = int(full_metrics["trades"]) - int(ablation_metrics["trades"])
    if delta_growth > 0.0:
        interpretation = "PASSIVE_LIQUIDITY_CONFIRMATION_IMPROVED_COST_AFTER_EXPECTANCY"
    elif delta_growth < 0.0:
        interpretation = "PASSIVE_LIQUIDITY_CONFIRMATION_REDUCED_COST_AFTER_EXPECTANCY"
    else:
        interpretation = "NO_MEASURABLE_GROWTH_EFFECT"
    return {
        "available": True,
        "removed_variable": "passive-liquidity vacuum/replenishment confirmation",
        "unchanged": [
            "fast liquidity-pool detector",
            "aggressive-flow direction",
            "provisional shock timing",
            "held structural retest",
            "separate response",
            "structural stop and objective",
            "Nautilus order, fill, fee, position and NAV accounting",
        ],
        "delta_full_minus_ablation": {
            "geometric_daily_nav_growth": delta_growth,
            "win_rate": delta_win_rate,
            "trades": delta_trades,
        },
        "interpretation": interpretation,
    }


def _render(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Candidate 06 v1.8 Passive-Liquidity Vacuum / Replenishment Response",
        "",
        "The passive-liquidity detector and trading scenario are separate. The initial pool breach is provisional; a later retest and a separate response are mandatory.",
        "",
        f"Implementation status: `{summary.get('implementation_status')}`",
        f"Data status: `{summary.get('data_status')}`",
        f"Selected: `{summary.get('selected')}`" if summary.get("selected") else "Selected: none",
        "",
        "|variant|week|eligible|rc|gate|geom/day|trades|win rate|PF|max DD|largest win share|failures|",
        "|---|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    records = [
        *summary.get("first_week_results", []),
        *summary.get("frozen_validation", []),
    ]
    for record in records:
        metrics = record.get("metrics", {})
        lines.append(
            "|{name}|{week}|{eligible}|{rc}|{gate}|{growth:.6%}|{trades}|{win:.2%}|{pf}|{dd:.2%}|{share:.2%}|{failures}|".format(
                name=record.get("name"),
                week=int(record.get("week_index", 0)) + 1,
                eligible=record.get("eligible_for_selection"),
                rc=record.get("returncode"),
                gate=record.get("gate_passed"),
                growth=float(metrics.get("geometric_daily_nav_growth", -1.0)),
                trades=metrics.get("trades"),
                win=float(metrics.get("win_rate", 0.0)),
                pf=metrics.get("profit_factor"),
                dd=float(metrics.get("max_drawdown_nav", 0.0)),
                share=float(metrics.get("largest_positive_trade_share", 1.0)),
                failures=", ".join(metrics.get("gate_failures", [])),
            ),
        )

    lines.extend(["", "## Controlled ablation", ""])
    ablation = summary.get("ablation", {})
    lines.append(f"- interpretation: `{ablation.get('interpretation')}`")
    lines.append(f"- delta full minus ablation: `{ablation.get('delta_full_minus_ablation')}`")

    lines.extend(["", "## Candidate diagnoses", ""])
    for name, diagnosis in summary.get("diagnoses", {}).items():
        lines.append(
            f"- **{name}**: `{diagnosis.get('classification')}` — {diagnosis.get('largest_performance_factor')}",
        )
        working = diagnosis.get("working_components", [])
        if working:
            lines.append(f"  - working: {', '.join(working)}")

    if summary.get("error"):
        lines.extend(["", "## Terminal error", "", "```text", str(summary["error"])[-12000:], "```"])
    return "\n".join(lines) + "\n"


def _write_summary(root: Path, summary: dict[str, Any]) -> None:
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
        default=Path("artifacts/candidate-06/dlvr-first-week"),
    )
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    raw = json.loads((candidate_dir / "config.json").read_text(encoding="utf-8"))
    gate = raw["gate"]

    base_summary: dict[str, Any] = {
        "candidate": "candidate-06-passive-liquidity-response-v1.8",
        "design": "prior-normalized official passive liquidity -> provisional pool-break interpretation -> later held retest -> separate response -> structural objective",
        "pattern_scenario_separation": {
            "detector": "PriorOnlyPassiveLiquidityDetector plus existing causal fast-pool primitives",
            "scenario": "DepthLiquidityVacuumReplenishmentEngine ordered state machine",
        },
        "execution": "NautilusTrader BacktestEngine only; native orders, fills, fees, positions and portfolio NAV",
        "risk_fraction": float(raw["execution"]["risk_fraction"]),
        "variant_priority": [name for name, *_ in VARIANTS],
        "selection_rule": "first eligible first-week gate pass in fixed causal priority; price-only ablation is diagnostic and cannot be selected",
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
            "data_status": "OFFICIAL_PASSIVE_LIQUIDITY_ARCHIVE_UNAVAILABLE",
            "terminal_status": "DATASET_UNAVAILABLE",
            "error": str(exc),
            "ablation": {"available": False},
            "diagnoses": {},
        }
        _write_summary(root, summary)
        return 4
    except Exception:
        summary = {
            **base_summary,
            "implementation_status": "DATA_PREPARATION_FAILURE",
            "data_status": "INVALID_OR_INCOMPLETE",
            "terminal_status": "IMPLEMENTATION_OR_DATA_FAILURE",
            "error": traceback.format_exc(),
            "ablation": {"available": False},
            "diagnoses": {},
        }
        _write_summary(root, summary)
        return 5

    base_config = _base(raw)
    configs: dict[str, dict[str, Any]] = {}
    first_results: list[dict[str, Any]] = []
    for name, description, mutate, eligible in VARIANTS:
        config = copy.deepcopy(base_config)
        mutate(config)
        config["candidate_variant"] = name
        config["variant_description"] = description
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

    diagnoses = {
        record["name"]: _diagnose_record(record, gate)
        for record in first_results
    }
    summary = {
        **base_summary,
        "implementation_status": "PASS" if _implementation_ok(first_results) else "FAIL",
        "data_status": "PASS",
        "data_source": prepared_first.source,
        "data_measurement": prepared_first.measurement,
        "first_week_results": first_results,
        "ablation": _ablation_interpretation(first_results),
        "diagnoses": diagnoses,
    }
    if not _implementation_ok(first_results):
        summary["terminal_status"] = "IMPLEMENTATION_OR_RUNTIME_FAILURE"
        summary["error"] = "At least one controlled first-week run did not produce valid Nautilus metrics."
        _write_summary(root, summary)
        return 5

    selected = next(
        (
            record["name"]
            for record in first_results
            if record.get("eligible_for_selection") and record.get("gate_passed")
        ),
        None,
    )
    summary["selected"] = selected
    if selected is None:
        summary["terminal_status"] = "FIRST_WEEK_LOGIC_GATE_FAILED"
        summary["discarded"] = {
            record["name"]: diagnoses[record["name"]]
            for record in first_results
            if record.get("eligible_for_selection")
        }
        _write_summary(root, summary)
        return 2

    locked = copy.deepcopy(configs[selected])
    locked.setdefault("validation", {})["stage"] = "three_week_validation"
    locked["logic"]["depth_series_path"] = "__RUNTIME_OFFICIAL_PASSIVE_LIQUIDITY_SERIES__"
    locked["logic"]["dlvr_liquidity_source"] = prepared_first.source
    locked_path = candidate_dir / "config.dlvr.locked.json"
    locked_path.write_text(
        json.dumps(locked, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
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
            _write_summary(root, summary)
            return 5
        frozen_record = _run_one(
            configs[selected],
            name=selected,
            description=next(
                description
                for name, description, _, _ in VARIANTS
                if name == selected
            ),
            eligible=True,
            week_index=week_index,
            prepared=prepared,
            root=root,
            candidate_dir=candidate_dir,
            repository=repository,
        )
        frozen.append(frozen_record)
        if not _implementation_ok([frozen_record]):
            summary.update(
                {
                    "implementation_status": "HOLDOUT_RUNTIME_FAILURE",
                    "terminal_status": "IMPLEMENTATION_OR_RUNTIME_FAILURE",
                    "error": frozen_record.get("errors_tail") or frozen_record.get("stderr_tail"),
                    "frozen_validation": frozen,
                },
            )
            _write_summary(root, summary)
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
                f"week-{int(record['week_index']) + 1}": _diagnose_record(record, gate)
                for record in frozen
            },
        },
    )
    if not all_three:
        summary["discarded"] = {
            selected: {
                "classification": "FAILED_UNCHANGED_FROZEN_HOLDOUT",
                "largest_performance_factor": "the selected causal contract did not preserve cost-after expectancy across frozen market regimes",
                "first_week": diagnoses[selected],
                "holdouts": summary["holdout_diagnoses"],
            },
        }
    _write_summary(root, summary)
    return 0 if all_three else 3


if __name__ == "__main__":
    raise SystemExit(main())
