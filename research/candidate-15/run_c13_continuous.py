#!/usr/bin/env python3
"""Run the exact Candidate 13 frozen policy in one continuous account.

This script changes only the evaluation calendar and the aggregate gate.  The
strategy, state machine, risk sizing, global mutex, orders, fills, fees and NAV
remain owned by the exact Git blobs recorded by Candidate 13 and NautilusTrader.
"""
from __future__ import annotations

import argparse
from datetime import date
from decimal import Decimal
from hashlib import sha1, sha256
import importlib.util
import json
import math
from pathlib import Path
import sys
from typing import Any


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return payload


def git_blob_oid(payload: bytes) -> str:
    return sha1(f"blob {len(payload)}\0".encode() + payload).hexdigest()


def verify_source(source_dir: Path, protocol: dict[str, Any]) -> dict[str, Any]:
    expected = protocol["source"]["locked_blobs"]
    files: dict[str, Any] = {}
    mismatches: list[str] = []
    for name, expected_oid in sorted(expected.items()):
        path = source_dir / name
        if not path.is_file():
            mismatches.append(f"{name}: missing")
            continue
        payload = path.read_bytes()
        actual_oid = git_blob_oid(payload)
        matched = actual_oid == expected_oid
        if not matched:
            mismatches.append(
                f"{name}: expected {expected_oid}, actual {actual_oid}",
            )
        files[name] = {
            "bytes": len(payload),
            "sha256": sha256(payload).hexdigest(),
            "expected_git_blob": expected_oid,
            "actual_git_blob": actual_oid,
            "matched": matched,
        }
    if mismatches:
        raise RuntimeError("frozen Candidate 13 source mismatch:\n" + "\n".join(mismatches))
    return {
        "schema": "candidate-15-v14-source-lock-v1",
        "origin_branch": protocol["source"]["origin_branch"],
        "all_matched": True,
        "files": files,
    }


def import_file(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def causal_episode_count(plan_rows: list[dict[str, Any]]) -> int:
    """Conservative pre-fill episode count from submitted plans.

    Candidate 13 already has a single global slot.  This additionally collapses
    plans sharing the same scenario, sweep timestamp and directional objective.
    It is intentionally not used to inflate the completed-trade count.
    """
    episodes: set[tuple[Any, ...]] = set()
    for plan in plan_rows:
        details = plan.get("details") if isinstance(plan.get("details"), dict) else {}
        leadership = (
            details.get("market_leadership")
            if isinstance(details.get("market_leadership"), dict)
            else {}
        )
        episodes.add(
            (
                plan.get("scenario"),
                plan.get("direction"),
                details.get("sweep_ts_ns", plan.get("observed_ts_ns")),
                details.get("pool_price", details.get("liquidity_price")),
                leadership.get("leader"),
            )
        )
    return len(episodes)


def render_result(summary: dict[str, Any]) -> str:
    checks = summary["gate_checks"]
    lines = [
        "# Candidate 15 V14 — Candidate 13 frozen continuous-account result",
        "",
        f"**{summary['classification']}**",
        "",
        f"- interval: `{summary['start']} -> {summary['end_exclusive']}`",
        f"- observed calendar days: `{summary['evaluation_calendar_days']}`",
        f"- starting / final NAV: `{summary['starting_nav']} / {summary['final_nav']}`",
        f"- NAV multiple: `{summary['nav_multiple']:.10f}`",
        f"- daily geometric growth: `{summary['daily_geometric_growth']:.10f}`",
        f"- completed trades: `{summary['closed_trades']}`",
        f"- wins / losses: `{summary['wins']} / {summary['losses']}`",
        f"- win rate: `{summary['win_rate']:.6f}`",
        f"- submitted plans / unique causal episodes: `{summary['submitted_plans']} / {summary['unique_submitted_causal_episodes']}`",
        f"- completed independent-trade proxy: `{summary['independent_completed_trade_proxy']}`",
        f"- required independent trades: `{summary['required_independent_completed_trades']}`",
        f"- maximum closed-trade drawdown: `{summary['closed_trade_max_drawdown']:.10f}`",
        "",
        "## Gate checks",
    ]
    lines.extend(f"- {name}: `{value}`" for name, value in checks.items())
    lines.extend(
        (
            "",
            "## Interpretation",
            summary["interpretation"],
            "",
            "This result is produced by one NautilusTrader account. Weekly NAV resets and multiplication of weekly returns are not used.",
        )
    )
    return "\n".join(lines) + "\n"


def execute(protocol_path: Path, source_dir: Path, output_dir: Path) -> dict[str, Any]:
    protocol = load_object(protocol_path)
    continuous = protocol["continuous_evaluation"]
    start = date.fromisoformat(continuous["start"])
    end = date.fromisoformat(continuous["end_exclusive"])
    evaluation_days = (end - start).days
    if evaluation_days <= 0:
        raise ValueError("continuous interval must contain at least one day")

    source_lock = verify_source(source_dir, protocol)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "source_lock.json", source_lock)

    config = load_object(source_dir / "base_config.json")
    run_id = str(continuous["run_id"])
    config["candidate"] = protocol["candidate"]
    config["selection"]["warmup_days"] = int(continuous["warmup_days"])
    config["selection"]["evaluation_days"] = evaluation_days
    config["selection"]["weeks"] = {
        run_id: {
            "start": continuous["start"],
            "end_exclusive": continuous["end_exclusive"],
        }
    }
    config["account"]["starting_nav"] = str(continuous["starting_nav"])
    config["account"]["risk_fraction"] = float(protocol["execution_lock"]["risk_fraction"])
    config["logic"]["risk_fraction"] = float(protocol["execution_lock"]["risk_fraction"])
    config["execution"]["effective_maker_rate"] = str(
        protocol["execution_lock"]["effective_maker_rate"],
    )
    config["execution"]["effective_taker_rate"] = str(
        protocol["execution_lock"]["effective_taker_rate"],
    )
    config["logic"]["effective_maker_rate"] = float(
        protocol["execution_lock"]["effective_maker_rate"],
    )
    config["logic"]["effective_taker_rate"] = float(
        protocol["execution_lock"]["effective_taker_rate"],
    )
    required_trades = math.ceil(
        evaluation_days
        * float(
            protocol["target_gate"][
                "minimum_independent_completed_trades_per_calendar_day"
            ]
        )
    )
    config["gates"]["complete"]["min_closed_trades_per_week"] = required_trades
    config["gates"]["complete"]["min_daily_geometric_growth"] = float(
        protocol["target_gate"]["minimum_daily_geometric_growth"],
    )
    config["candidate15_v14_continuous_contract"] = {
        "schema": protocol["schema"],
        "source_lock": "source_lock.json",
        "continuous_account": True,
        "weekly_nav_reset": False,
        "required_independent_completed_trades": required_trades,
    }
    effective_config = output_dir / "effective_config.json"
    write_json(effective_config, config)

    if str(source_dir) not in sys.path:
        sys.path.insert(0, str(source_dir))
    runner = import_file(
        "candidate15_v14_frozen_run_leadership_scdam",
        source_dir / "run_leadership_scdam.py",
    )
    metrics = runner.run(effective_config, run_id, output_dir)
    metrics = load_object(output_dir / "metrics.json")
    metrics.update(
        {
            "candidate": protocol["candidate"],
            "candidate15_v14_protocol": protocol["schema"],
            "continuous_account": True,
            "weekly_nav_reset": False,
            "required_independent_completed_trades": required_trades,
            "success_claim": False,
        }
    )
    write_json(output_dir / "metrics.json", metrics)

    audit_module = import_file(
        "candidate15_v14_evidence_audit",
        source_dir / "evidence_audit.py",
    )
    audit = audit_module.audit(output_dir, "LONG")
    audit.update(
        {
            "schema": "candidate-15-v14-continuous-audit-v1",
            "source_lock_passed": source_lock["all_matched"],
            "continuous_account": True,
            "weekly_nav_reset": False,
        }
    )
    write_json(output_dir / "audit.json", audit)

    plan_payload = load_object(output_dir / "submitted_plans.json")
    plan_rows = plan_payload.get("plans", [])
    if not isinstance(plan_rows, list):
        plan_rows = []
    unique_episodes = causal_episode_count(
        [plan for plan in plan_rows if isinstance(plan, dict)]
    )
    closed_trades = int(metrics.get("closed_trades", 0))
    independent_completed_proxy = min(closed_trades, unique_episodes)
    safety_keys = (
        "evidence_complete",
        "metric_recalculation_passed",
        "risk_budget_passed",
        "global_slot_passed",
        "partial_entry_protection_passed",
        "no_liquidation_passed",
        "engine_errors_absent",
    )
    safety_passed = source_lock["all_matched"] and all(
        audit.get(key) is True for key in safety_keys
    )
    daily_growth = float(metrics.get("daily_geometric_growth", -1.0))
    final_nav = Decimal(str(metrics.get("final_nav", "0")))
    starting_nav = Decimal(str(metrics.get("starting_nav", continuous["starting_nav"])))
    growth_passed = daily_growth >= float(
        protocol["target_gate"]["minimum_daily_geometric_growth"],
    )
    frequency_passed = independent_completed_proxy >= required_trades
    positive_nav = final_nav > 0
    target_passed = safety_passed and growth_passed and frequency_passed and positive_nav

    if target_passed:
        classification = "V14_CONTINUOUS_TARGET_GATE_PASSED"
        interpretation = (
            "The exact frozen policy passed both the after-cost growth and day-trading "
            "frequency gates in one continuous account."
        )
    elif safety_passed and growth_passed:
        classification = "V14_CONTINUOUS_ALPHA_WITH_FREQUENCY_SHORTFALL"
        interpretation = (
            "The frozen policy retained the minimum after-cost growth rate, but it did "
            "not generate enough independent completed opportunities. Preserve the core "
            "and add genuinely independent scenario families rather than loosening it."
        )
    elif safety_passed:
        classification = "V14_CONTINUOUS_POLICY_REJECTED"
        interpretation = (
            "The exact frozen policy did not retain the minimum continuous-account "
            "growth rate. Its useful state, execution and risk components may be reused, "
            "but this policy must not be promoted as the final system."
        )
    else:
        classification = "V14_IMPLEMENTATION_OR_EVIDENCE_FAILURE"
        interpretation = (
            "The run failed one or more source, accounting, risk, global-slot, protection "
            "or liquidation audits and cannot be used as alpha evidence."
        )

    summary = {
        "schema": "candidate-15-v14-c13-continuous-summary-v1",
        "classification": classification,
        "target_passed": target_passed,
        "start": continuous["start"],
        "end_exclusive": continuous["end_exclusive"],
        "evaluation_calendar_days": evaluation_days,
        "starting_nav": str(starting_nav),
        "final_nav": str(final_nav),
        "nav_multiple": float(final_nav / starting_nav) if starting_nav > 0 else 0.0,
        "daily_geometric_growth": daily_growth,
        "closed_trades": closed_trades,
        "wins": int(metrics.get("wins", 0)),
        "losses": int(metrics.get("losses", 0)),
        "win_rate": float(metrics.get("win_rate", 0.0)),
        "payoff_ratio": metrics.get("payoff_ratio"),
        "submitted_plans": int(metrics.get("submitted_plans", len(plan_rows))),
        "unique_submitted_causal_episodes": unique_episodes,
        "independent_completed_trade_proxy": independent_completed_proxy,
        "required_independent_completed_trades": required_trades,
        "closed_trade_max_drawdown": float(
            metrics.get("closed_trade_max_drawdown", 0.0),
        ),
        "source_lock_passed": source_lock["all_matched"],
        "safety_audit_passed": safety_passed,
        "gate_checks": {
            "continuous_account": True,
            "weekly_nav_reset_absent": True,
            "source_lock": source_lock["all_matched"],
            "all_safety_audits": safety_passed,
            "daily_geometric_growth": growth_passed,
            "independent_completed_trade_frequency": frequency_passed,
            "positive_final_nav": positive_nav,
        },
        "audit_classification": audit.get("classification"),
        "interpretation": interpretation,
    }
    write_json(output_dir / "summary.json", summary)
    (output_dir / "RESULT.md").write_text(render_result(summary), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    execute(args.protocol.resolve(), args.source.resolve(), args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
