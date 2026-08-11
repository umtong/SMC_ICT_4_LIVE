#!/usr/bin/env python3
"""Candidate 60 forced-flow release development and conditional fresh accounts."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
C57 = ROOT / "research" / "candidate-57"
SOURCE = C57 / "jump_taker_alignment_fresh_campaign.py"
SPEC = importlib.util.spec_from_file_location(
    "candidate60_force_release_reused_campaign", SOURCE
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import reusable jump campaign: {SOURCE}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

BASE_WORK = ROOT / ".work" / "candidate-60-jump-force-release-v1"
BASE_ARTIFACTS = ROOT / "artifacts" / "candidate-60-jump-force-release-v1"
EVIDENCE = HERE / "evidence" / "jump-force-release-v1"
BASE_CACHE = ROOT / ".cache" / "candidate-60-jump-force-release-v1"
FREEZE = HERE / "JUMP_FORCE_RELEASE_V1_FREEZE.md"


@dataclass(frozen=True)
class Stage:
    name: str
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


DEVELOPMENT = Stage("development", date(2026, 4, 6), date(2026, 4, 19))
POLICY_FRESH = Stage("policy_fresh", date(2026, 6, 8), date(2026, 6, 21))
CELLS: dict[str, dict[str, Any]] = {
    "price_confirmation_control": {
        "force_mode": "price_confirmation_control",
    },
    "oi_unwind": {
        "force_mode": "oi_unwind",
    },
    "oi_unwind_flow_flip": {
        "force_mode": "oi_unwind_flow_flip",
    },
}
FORCE_DIAGNOSTIC_KEYS = (
    "candidate60_force_release_adapter",
    "jump_force_release_mode",
    "jump_force_event_lookback_minutes",
    "jump_force_release_thresholds_searched",
    "jump_force_release_checks",
    "jump_force_release_metrics_unresolved",
    "jump_force_release_oi_rejections",
    "jump_force_release_terminal_flow_rejections",
    "jump_force_release_acceptances",
    "jump_force_release_policy_changed_source",
    "jump_force_release_policy_changed_risk",
    "jump_force_release_policy_changed_management",
    "global_position_violations",
    "max_open_positions_observed",
    "max_simultaneous_entry_intents",
    "order_rejections",
)
FORCE_SCENARIO_KEYS = (
    "force_prior_metrics_ts",
    "force_source_metrics_ts",
    "force_confirmation_metrics_ts",
    "force_prior_open_interest",
    "force_source_open_interest",
    "force_confirmation_open_interest",
    "force_event_oi_change_fraction",
    "force_post_boundary_oi_change_fraction",
    "force_oi_unwind",
    "force_source_taker_ratio",
    "force_confirmation_taker_ratio",
    "force_source_flow_on_impulse_side",
    "force_confirmation_flow_on_reversal_side",
    "force_flow_improved_toward_reversal",
    "force_taker_flow_flip",
    "jump_force_release_mode",
    "jump_force_release_state_pass",
)


def safe(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (date, Path)):
        return str(value)
    if isinstance(value, dict):
        return {str(key): safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe(item) for item in value]
    return value


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(safe(value), indent=2, sort_keys=True, allow_nan=False, default=str)
        + "\n",
        encoding="utf-8",
    )


def number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def configure_stage(stage: Stage) -> None:
    MODULE.WORK = BASE_WORK / stage.name
    MODULE.ARTIFACTS = BASE_ARTIFACTS / stage.name
    MODULE.EVIDENCE = EVIDENCE / stage.name
    MODULE.CACHE = BASE_CACHE / stage.name
    sidecar_start = stage.start - timedelta(days=2)
    MODULE.METRICS = MODULE.WORK / (
        f"binance_metrics_{sidecar_start.isoformat()}_{stage.end.isoformat()}.json"
    )
    MODULE.START = stage.start
    MODULE.END = stage.end
    MODULE.DAYS = stage.days


def build_config(cell: str) -> Path:
    source = MODULE.config(cell)
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["strategy"].update(
        {
            "jump_confirmation_minutes": 15,
            "jump_confirmation_bucket_minutes": 5,
            "jump_post_state_mode": "two_bar_price",
            "jump_min_confirmation_elapsed_minutes": 10,
            "jump_oi_max_decline_fraction": 0.01,
            "jump_force_release_mode": str(CELLS[cell]["force_mode"]),
            "jump_force_event_lookback_minutes": 240,
        }
    )
    source.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return source


def download_sidecar(stage: Stage) -> int:
    sidecar_start = stage.start - timedelta(days=2)
    command = [
        sys.executable,
        str(C57 / "download_binance_metrics_sidecar.py"),
        "--start",
        sidecar_start.isoformat(),
        "--end",
        stage.end.isoformat(),
        "--output",
        str(MODULE.METRICS),
        "--cache",
        str(MODULE.CACHE / "metrics"),
    ]
    return subprocess.run(command, cwd=ROOT, check=False).returncode


def run_cell(cell: str) -> int:
    output = MODULE.ARTIFACTS / cell
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(MODULE.C51 / "launch.py"),
        "--config",
        str(build_config(cell)),
        "--start",
        MODULE.START.isoformat(),
        "--end",
        MODULE.END.isoformat(),
        "--cache",
        str(MODULE.CACHE / "bars"),
        "--output",
        str(output),
        "--workspace",
        str(MODULE.WORK / cell / "workspace"),
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(MODULE.C51)
    env["C57_JUMP_TAKER_FILTER_MODE"] = "source_without_taker_filter"
    env["C57_JUMP_ARBITRATION_MODE"] = "taker_conditional"
    env["C57_JUMP_SIDE_MODE"] = "both"
    env["C57_JUMP_TAKER_METRICS_PATH"] = str(MODULE.METRICS)
    return subprocess.run(command, cwd=ROOT, env=env, check=False).returncode


def load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def force_diagnostics(cell: str) -> dict[str, Any]:
    payload = load_json(
        MODULE.ARTIFACTS / cell / "strategy_diagnostics.json", {}
    )
    return {key: payload.get(key) for key in FORCE_DIAGNOSTIC_KEYS}


def force_scenarios(cell: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = load_json(
        MODULE.ARTIFACTS / cell / "closed_scenarios.json", []
    )
    compact: list[dict[str, Any]] = []
    for row in rows:
        diagnostics = row.get("diagnostics") or {}
        compact.append(
            {
                "symbol": row.get("symbol"),
                "side": row.get("side"),
                "episode_ts": row.get("episode_ts"),
                "scenario_id": row.get("scenario_id"),
                "management_exit_reason": row.get("management_exit_reason"),
                "realized_pnl": row.get("realized_pnl"),
                "planned_account_loss": row.get("planned_account_loss"),
                "force_state": {
                    key: diagnostics.get(key)
                    for key in FORCE_SCENARIO_KEYS
                    if key in diagnostics
                },
            }
        )
    return compact


def mechanics(result: dict[str, Any]) -> bool:
    if not result.get("produced"):
        return False
    validity = result.get("end_validity") or {}
    diagnostics = result.get("force_diagnostics") or {}
    return (
        validity.get("no_open_positions_at_end") is not False
        and validity.get("no_active_orders_at_end") is not False
        and validity.get("no_global_position_violation") is not False
        and int(diagnostics.get("global_position_violations") or 0) == 0
        and int(diagnostics.get("max_open_positions_observed") or 0) <= 1
        and int(diagnostics.get("max_simultaneous_entry_intents") or 0) <= 1
        and int(diagnostics.get("order_rejections") or 0) == 0
        and int(diagnostics.get("jump_force_release_policy_changed_source") or 0) == 0
        and int(diagnostics.get("jump_force_release_policy_changed_risk") or 0) == 0
        and int(diagnostics.get("jump_force_release_policy_changed_management") or 0)
        == 0
    )


def actual_trade_map(cell: str) -> dict[tuple[str, int], dict[str, Any]]:
    return MODULE.trade_map(cell)


def actual_r(row: dict[str, Any]) -> float:
    return number(row.get("actual_after_cost_r"), math.nan)


def pair_effect(control_cell: str, candidate_cell: str) -> dict[str, Any]:
    control = actual_trade_map(control_cell)
    candidate = actual_trade_map(candidate_cell)
    shared_keys = sorted(set(control) & set(candidate))
    removed_keys = sorted(set(control) - set(candidate))
    added_keys = sorted(set(candidate) - set(control))
    removed = [control[key] for key in removed_keys]
    added = [candidate[key] for key in added_keys]
    shared = [
        {
            "symbol": key[0],
            "episode_ts": key[1],
            "control_r": actual_r(control[key]),
            "candidate_r": actual_r(candidate[key]),
            "delta_r": actual_r(candidate[key]) - actual_r(control[key]),
        }
        for key in shared_keys
    ]
    positive_control = [
        (key, row)
        for key, row in control.items()
        if math.isfinite(actual_r(row)) and actual_r(row) > 0.0
    ]
    best_key = (
        max(positive_control, key=lambda item: actual_r(item[1]))[0]
        if positive_control
        else None
    )
    best_preserved = (
        best_key in candidate and actual_r(candidate[best_key]) > 0.0
        if best_key is not None
        else True
    )
    return {
        "control": control_cell,
        "candidate": candidate_cell,
        "shared_actual_trade_keys": len(shared_keys),
        "removed_control_trades": removed,
        "added_candidate_trades": added,
        "removed_negative_count": sum(actual_r(row) < 0.0 for row in removed),
        "removed_positive_count": sum(actual_r(row) > 0.0 for row in removed),
        "removed_sum_r": sum(actual_r(row) for row in removed),
        "added_negative_count": sum(actual_r(row) < 0.0 for row in added),
        "added_positive_count": sum(actual_r(row) > 0.0 for row in added),
        "added_sum_r": sum(actual_r(row) for row in added),
        "shared_improved_count": sum(row["delta_r"] > 1e-12 for row in shared),
        "shared_degraded_count": sum(row["delta_r"] < -1e-12 for row in shared),
        "shared_sum_delta_r": sum(row["delta_r"] for row in shared),
        "shared_trade_deltas": shared,
        "best_positive_control_trade_key": best_key,
        "best_positive_control_trade_preserved": bool(best_preserved),
    }


def enrich_result(cell: str, result: dict[str, Any]) -> dict[str, Any]:
    diagnostics = force_diagnostics(cell)
    enriched = {
        **result,
        "declared_policy": CELLS[cell],
        "force_diagnostics": diagnostics,
        "actual_force_scenarios": force_scenarios(cell),
    }
    enriched["mechanics_valid"] = mechanics(enriched)
    return enriched


def run_stage(stage: Stage, cells: list[str]) -> dict[str, Any]:
    configure_stage(stage)
    for path in (MODULE.WORK, MODULE.ARTIFACTS, MODULE.CACHE):
        path.mkdir(parents=True, exist_ok=True)
    if MODULE.EVIDENCE.exists():
        shutil.rmtree(MODULE.EVIDENCE)
    MODULE.EVIDENCE.mkdir(parents=True, exist_ok=True)

    data_status = download_sidecar(stage)
    process_status = data_status
    results: dict[str, dict[str, Any]] = {}
    for cell in cells:
        code = run_cell(cell) if data_status == 0 else 1
        process_status = process_status or code
        raw = MODULE.analyze_cell(cell, code)
        results[cell] = enrich_result(cell, raw)

    effects = {
        cell: pair_effect("price_confirmation_control", cell)
        for cell in cells
        if cell != "price_confirmation_control"
    }
    stage_result = {
        "stage": asdict(stage) | {"days": stage.days},
        "cells": results,
        "pair_effects": effects,
        "metrics_sidecar": {
            "source": "Binance Vision futures/um daily metrics",
            "path": str(MODULE.METRICS),
            "bytes": MODULE.METRICS.stat().st_size
            if MODULE.METRICS.is_file()
            else None,
            "strict_asof_max_age_minutes": 10,
            "event_lookback_minutes": 240,
        },
        "process_status": int(process_status),
    }
    dump(MODULE.EVIDENCE / "stage_comparison.json", stage_result)
    return stage_result


def development_eligibility(
    development: dict[str, Any], candidate_cell: str
) -> dict[str, Any]:
    cells = development["cells"]
    control = cells["price_confirmation_control"]
    candidate = cells[candidate_cell]
    control_account = control.get("actual_account") or {}
    candidate_account = candidate.get("actual_account") or {}
    effect = development["pair_effects"][candidate_cell]
    diagnostics = candidate.get("force_diagnostics") or {}
    changed_decisions = (
        int(diagnostics.get("jump_force_release_oi_rejections") or 0)
        + int(diagnostics.get("jump_force_release_terminal_flow_rejections") or 0)
        > 0
        or int(candidate.get("actual_completed_trades") or 0)
        != int(control.get("actual_completed_trades") or 0)
        or (
            bool(effect.get("shared_trade_deltas"))
            and abs(number(effect.get("shared_sum_delta_r"))) > 1e-12
        )
    )
    causal_trade_effect = (
        int(effect.get("removed_negative_count") or 0)
        > int(effect.get("removed_positive_count") or 0)
        or int(effect.get("shared_improved_count") or 0)
        > int(effect.get("shared_degraded_count") or 0)
    )
    checks = {
        "mechanics_valid": bool(candidate.get("mechanics_valid")),
        "changed_actual_decisions": bool(changed_decisions),
        "at_least_two_completed_trades": int(
            candidate.get("actual_completed_trades") or 0
        )
        >= 2,
        "continuous_return_improved": number(candidate_account.get("total_return"))
        > number(control_account.get("total_return")),
        "drawdown_not_worse": number(candidate_account.get("max_drawdown"))
        <= number(control_account.get("max_drawdown")) + 1e-12,
        "causal_trade_effect": bool(causal_trade_effect),
        "best_positive_control_trade_preserved": bool(
            effect.get("best_positive_control_trade_preserved")
        ),
    }
    return {
        "cell": candidate_cell,
        "eligible_for_policy_fresh": all(checks.values()),
        "checks": checks,
        "effect": effect,
        "control_account": control_account,
        "candidate_account": candidate_account,
    }


def table_lines(stage_result: dict[str, Any], title: str) -> list[str]:
    lines = [
        f"## {title}",
        "",
        "| cell | trades | W/L | PF | geo/day | return | MDD | force checks | OI rejects | flow rejects | accepts |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for cell, result in stage_result.get("cells", {}).items():
        account = result.get("actual_account") or {}
        diag = result.get("force_diagnostics") or {}
        lines.append(
            "| "
            + " | ".join(
                [
                    cell,
                    str(result.get("actual_completed_trades")),
                    f"{account.get('wins')}/{account.get('losses')}",
                    str(account.get("profit_factor")),
                    str(account.get("geometric_daily_growth")),
                    str(account.get("total_return")),
                    str(account.get("max_drawdown")),
                    str(diag.get("jump_force_release_checks")),
                    str(diag.get("jump_force_release_oi_rejections")),
                    str(diag.get("jump_force_release_terminal_flow_rejections")),
                    str(diag.get("jump_force_release_acceptances")),
                ]
            )
            + " |"
        )
    return lines


def main() -> int:
    if not FREEZE.is_file():
        raise RuntimeError("forced-flow release freeze is missing")
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True, exist_ok=True)

    development = run_stage(DEVELOPMENT, list(CELLS))
    eligibility = {
        cell: development_eligibility(development, cell)
        for cell in CELLS
        if cell != "price_confirmation_control"
    }
    eligible_cells = [
        cell
        for cell, decision in eligibility.items()
        if bool(decision["eligible_for_policy_fresh"])
    ]

    fresh: dict[str, Any] | None = None
    if int(development.get("process_status") or 0) == 0 and eligible_cells:
        fresh = run_stage(
            POLICY_FRESH,
            ["price_confirmation_control", *eligible_cells],
        )

    comparison = {
        "experiment": "candidate-60-jump-force-release-v1",
        "binary_gate": False,
        "market_mechanism": (
            "distinguish forced contract extinguishment from fresh position "
            "construction, then require aggressor-flow release before reversal"
        ),
        "thresholds_searched": False,
        "development": development,
        "development_eligibility": eligibility,
        "policy_fresh_consumed": fresh is not None,
        "policy_fresh": fresh,
        "unchanged": [
            "source 4h jump >= 2 prior-only sigma",
            "18-return volatility history",
            "two completed 5m bars and terminal-candle reclaim",
            "peer-taker conditional one-slot arbitration",
            "both directions and four symbols",
            "structural extension stop",
            "original 240-minute source clock",
            "transient +0.4R arm and +1.0R escape",
            "current-NAV 3% planned-loss sizing",
            "project costs and NautilusTrader matching",
        ],
    }
    dump(EVIDENCE / "comparison.json", comparison)

    lines = [
        "# Candidate 60 forced-flow release result",
        "",
        "A price reclaim is tested against target-contract OI accounting and a "
        "neutral-crossing taker-flow transition. No threshold, date, source "
        "jump, geometry or management value is searched after outcomes.",
        "",
        *table_lines(development, "Development — 2026-04-06 to 2026-04-19"),
        "",
        "## Development eligibility",
        "",
    ]
    for cell, decision in eligibility.items():
        lines.append(
            f"- `{cell}`: eligible={decision['eligible_for_policy_fresh']}; "
            + ", ".join(
                f"{name}={value}" for name, value in decision["checks"].items()
            )
        )
    if fresh is None:
        lines.extend(
            [
                "",
                "## Policy-fresh",
                "",
                "Not consumed because no forced-flow cell earned causal development eligibility.",
            ]
        )
    else:
        lines.extend(
            [
                "",
                *table_lines(fresh, "Policy-fresh — 2026-06-08 to 2026-06-21"),
            ]
        )
    (EVIDENCE / "RESULT.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    print(json.dumps(safe(comparison), indent=2, sort_keys=True, allow_nan=False))

    status = int(development.get("process_status") or 0)
    if fresh is not None:
        status = status or int(fresh.get("process_status") or 0)
    if status != 0:
        return status
    for stage_result in (development, fresh):
        if stage_result is None:
            continue
        for result in stage_result.get("cells", {}).values():
            if not result.get("produced") or not result.get("mechanics_valid"):
                return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
