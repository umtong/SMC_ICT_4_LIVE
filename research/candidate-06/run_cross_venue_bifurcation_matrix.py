#!/usr/bin/env python3
"""Predeclared CVPD spot/perpetual bifurcation campaign in NautilusTrader."""

from __future__ import annotations

import argparse
from collections import Counter
import copy
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping


VARIANTS = (
    (
        "cvpd_full_bifurcation",
        "Perpetual-only false-break reversion plus spot-led perpetual catch-up, both requiring prior-only robust basis state.",
        True,
        True,
        True,
        True,
    ),
    (
        "cvpd_perpetual_false_break_only",
        "Predeclared mechanism isolation: only perpetual-only liquidity sweeps unconfirmed by spot may reverse.",
        True,
        False,
        True,
        True,
    ),
    (
        "cvpd_spot_led_relay_only",
        "Predeclared mechanism isolation: only accepted spot discovery followed by a separate perpetual catch-up may continue.",
        False,
        True,
        True,
        True,
    ),
    (
        "cvpd_price_divergence_without_basis_ablation",
        "One-variable ablation of the robust spot-perpetual basis residual gate; all auction, response, stop, target, cost and risk rules remain fixed.",
        True,
        True,
        False,
        False,
    ),
)


def _base(raw: Mapping[str, Any]) -> dict[str, Any]:
    config = copy.deepcopy(dict(raw))
    config["version"] = "3.0.0"
    config["hypothesis"] = (
        "A completed liquidity break is tradable only when spot and perpetual venues disagree in a mechanism-consistent way: "
        "leveraged perpetual-only excess should reclaim, while spot-led accepted discovery should be relayed by the perpetual."
    )
    config["logic"].update(
        {
            "engine": "CROSS_VENUE_PRICE_DISCOVERY_BIFURCATION",
            "cvpd_period_minutes": 15,
            "cvpd_entry_window_minutes": 13,
            "cvpd_basis_lookback": 120,
            "cvpd_basis_min_history": 60,
            "cvpd_basis_z_threshold": 1.50,
            "cvpd_lag_basis_z_ceiling": 0.50,
            "cvpd_spot_atr_bars": 20,
            "cvpd_spot_volume_bars": 60,
            "cvpd_min_sweep_atr": 0.10,
            "cvpd_confirm_tolerance_atr": 0.03,
            "cvpd_spot_accept_close_atr": 0.05,
            "cvpd_spot_body_atr": 0.25,
            "cvpd_spot_flow_ratio": 0.04,
            "cvpd_spot_relative_volume": 0.90,
            "cvpd_perp_shock_flow_ratio": 0.03,
            "cvpd_response_bars": 3,
            "cvpd_response_body_atr": 0.15,
            "cvpd_response_flow_ratio": 0.02,
            "cvpd_response_close_location": 0.60,
            "cvpd_perp_accept_close_atr": 0.04,
            "cvpd_stop_buffer_atr": 0.08,
            "cvpd_projection_fraction": 0.50,
            "cvpd_cooldown_bars": 2,
            "minimum_structural_rr": 0.75,
            "minimum_net_rr_after_entry_delay": 0.60,
            "max_entry_drift_atr": 0.40,
            "max_holding_bars": 60,
            "sac_entry_confirmation": "NONE",
            "sac_failed_defense_action": "ABSTAIN",
            "enforce_favorable_drift_guard": True,
        },
    )
    return config


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
            str(candidate_dir / "run_cross_venue_validation.py"),
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
        "stdout_tail": completed.stdout[-5000:],
        "stderr_tail": completed.stderr[-16000:],
    }
    metrics_path = output / "metrics.json"
    if metrics_path.exists():
        record["metrics"] = json.loads(metrics_path.read_text(encoding="utf-8"))
        record["gate_passed"] = bool(record["metrics"].get("gate_passed"))
    else:
        record["gate_passed"] = False
        error_path = output / "errors.log"
        if error_path.exists():
            record["error"] = error_path.read_text(encoding="utf-8", errors="replace")[-16000:]
    return record


def _causal_counts(run_output: Path) -> dict[str, Any]:
    reasons: Counter[str] = Counter()
    events = run_output / "scenario_events.jsonl"
    if events.exists():
        for line in events.read_text(encoding="utf-8").splitlines():
            if line.strip():
                payload = json.loads(line)
                reasons[str(payload.get("reason_code", "UNKNOWN"))] += 1
    families: Counter[str] = Counter()
    targets: Counter[str] = Counter()
    trades_path = run_output / "trades.json"
    if trades_path.exists():
        for trade in json.loads(trades_path.read_text(encoding="utf-8")).get("trades", []):
            families[str(trade.get("family", "UNKNOWN"))] += 1
            targets[str(trade.get("target_reason", "UNKNOWN"))] += 1
    return {
        "reason_counts": dict(reasons),
        "trade_family_counts": dict(families),
        "target_reason_counts": dict(targets),
    }


def _diagnose(record: Mapping[str, Any]) -> dict[str, Any]:
    metrics = dict(record.get("metrics", {}))
    if not metrics:
        return {
            "classification": "IMPLEMENTATION_OR_DATA_FAILURE",
            "error": record.get("error") or record.get("stderr_tail"),
        }
    diagnostics = dict(metrics.get("diagnostics", {}))
    growth = float(metrics.get("geometric_daily_nav_growth", 0.0))
    trades = int(metrics.get("trades", 0))
    wins = int(metrics.get("wins", 0))
    profit_factor = metrics.get("profit_factor")
    if trades == 0:
        classification = "NO_COMPLETED_CROSS_VENUE_RESPONSE"
    elif growth <= 0.0 or (profit_factor is not None and float(profit_factor) < 1.0):
        classification = "NEGATIVE_COST_AFTER_EXPECTANCY"
    elif not metrics.get("gate_passed"):
        classification = "POSITIVE_BUT_GATE_INCOMPLETE"
    else:
        classification = "GATE_PASSED"
    return {
        "classification": classification,
        "geometric_daily_nav_growth": growth,
        "trades": trades,
        "wins": wins,
        "win_rate": metrics.get("win_rate"),
        "profit_factor": profit_factor,
        "max_drawdown_nav": metrics.get("max_drawdown_nav"),
        "largest_positive_trade_share": metrics.get("largest_positive_trade_share"),
        "gate_failures": metrics.get("gate_failures", []),
        "entry_abstentions": diagnostics.get("entry_abstentions", {}),
        "cross_venue_context": metrics.get("cross_venue_context", {}),
    }


def _render(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Candidate 06 v3.0 Cross-Venue Price-Discovery Bifurcation",
        "",
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
                growth=float(metrics.get("geometric_daily_nav_growth", 0.0)),
                trades=metrics.get("trades"),
                wins=metrics.get("wins"),
                win=float(metrics.get("win_rate", 0.0)),
                pf=metrics.get("profit_factor"),
                dd=float(metrics.get("max_drawdown_nav", 0.0)),
                failures=", ".join(metrics.get("gate_failures", [])),
            ),
        )
    lines.extend(["", "## Diagnoses", ""])
    for name, diagnosis in summary.get("diagnoses", {}).items():
        lines.append(f"- **{name}**: `{diagnosis.get('classification')}` — {diagnosis}")
    lines.extend(
        [
            "",
            "## Fixed causal contract",
            "",
            "- Spot and perpetual bars must share the exact completed one-minute timestamp.",
            "- Basis and activity baselines exclude the current decision bar.",
            "- The initiating divergence bar cannot emit an entry.",
            "- A separate perpetual response is required.",
            "- The perpetual is the only traded instrument; one native Nautilus account and one global slot are used.",
            "- Risk remains three percent of whole-account NAV per approved trade.",
        ],
    )
    if summary.get("error"):
        lines.extend(["", "## Error", "", "```text", str(summary["error"])[-16000:], "```"])
    return "\n".join(lines) + "\n"


def _write(root: Path, summary: Mapping[str, Any]) -> None:
    (root / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (root / "SUMMARY.md").write_text(_render(summary), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("artifacts/candidate-06/cvpd-first-week"))
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    raw = json.loads((candidate_dir / "config.json").read_text(encoding="utf-8"))
    base = _base(raw)
    configs: dict[str, dict[str, Any]] = {}
    first: list[dict[str, Any]] = []
    for name, description, reversion, relay, basis, eligible in VARIANTS:
        config = copy.deepcopy(base)
        config["candidate_variant"] = name
        config["variant_description"] = description
        config["logic"].update(
            {
                "cvpd_enable_perp_reversion": reversion,
                "cvpd_enable_spot_relay": relay,
                "cvpd_use_basis_filter": basis,
            },
        )
        configs[name] = config
        config_dir = root / "configs"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / f"{name}-week-1.json"
        config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        run_output = root / "runs" / name / "week-1"
        record = _run(config_path, run_output, 0, candidate_dir, repository)
        record.update(
            {
                "name": name,
                "description": description,
                "eligible_for_selection": eligible,
                "week_index": 0,
                "config_path": str(config_path.relative_to(repository)),
                "run_output": str(run_output.relative_to(repository)),
                "causal_counts": _causal_counts(run_output),
            },
        )
        first.append(record)

    diagnoses = {record["name"]: _diagnose(record) for record in first}
    valid_metrics = all(int(record.get("returncode", 1)) == 0 and record.get("metrics") for record in first)
    base_summary: dict[str, Any] = {
        "candidate": "candidate-06-cvpd-v3.0",
        "design": "prior completed cross-venue auction -> one-venue liquidity divergence -> robust prior-only basis classification -> separate perpetual response -> structural objective",
        "variant_priority": [name for name, *_ in VARIANTS],
        "selection_rule": "first eligible gate-qualified mechanism in fixed ex-ante priority; no-basis ablation cannot be selected",
        "first_week_results": first,
        "frozen_validation": [],
        "diagnoses": diagnoses,
        "selected": None,
        "all_three_weeks_passed": False,
        "long_evaluation_authorized": False,
    }
    if not valid_metrics:
        summary = {
            **base_summary,
            "terminal_status": "IMPLEMENTATION_OR_DATA_FAILURE",
            "error": "At least one predeclared first-week variant did not produce valid Nautilus metrics.",
        }
        _write(root, summary)
        return 5

    selected = next(
        (
            record["name"]
            for record in first
            if record.get("eligible_for_selection") and record.get("gate_passed")
        ),
        None,
    )
    if selected is None:
        summary = {**base_summary, "terminal_status": "FIRST_WEEK_LOGIC_GATE_FAILED"}
        _write(root, summary)
        return 2

    locked = copy.deepcopy(configs[selected])
    locked.setdefault("validation", {})["stage"] = "three_week_validation"
    locked_path = candidate_dir / "config.cvpd.locked.json"
    locked_path.write_text(json.dumps(locked, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    frozen: list[dict[str, Any]] = []
    for week_index in (1, 2):
        run_output = root / "runs" / selected / f"week-{week_index + 1}"
        config_dir = root / "configs"
        config_path = config_dir / f"{selected}-week-{week_index + 1}.json"
        config_path.write_text(json.dumps(locked, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        record = _run(config_path, run_output, week_index, candidate_dir, repository)
        record.update(
            {
                "name": selected,
                "description": next(value[1] for value in VARIANTS if value[0] == selected),
                "eligible_for_selection": True,
                "week_index": week_index,
                "config_path": str(config_path.relative_to(repository)),
                "run_output": str(run_output.relative_to(repository)),
                "causal_counts": _causal_counts(run_output),
            },
        )
        frozen.append(record)
        if int(record.get("returncode", 1)) != 0 or not record.get("metrics"):
            summary = {
                **base_summary,
                "selected": selected,
                "locked_config": str(locked_path.relative_to(repository)),
                "frozen_validation": frozen,
                "terminal_status": "IMPLEMENTATION_OR_DATA_FAILURE_ON_FROZEN_WEEK",
                "error": record.get("error") or record.get("stderr_tail"),
            }
            _write(root, summary)
            return 5
    all_three = all(record.get("gate_passed") for record in frozen)
    summary = {
        **base_summary,
        "selected": selected,
        "locked_config": str(locked_path.relative_to(repository)),
        "frozen_validation": frozen,
        "holdout_diagnoses": {
            f"week-{record['week_index'] + 1}": _diagnose(record) for record in frozen
        },
        "all_three_weeks_passed": all_three,
        "long_evaluation_authorized": all_three,
        "terminal_status": "THREE_WEEK_GATE_PASSED" if all_three else "FROZEN_HOLDOUT_LOGIC_GATE_FAILED",
    }
    _write(root, summary)
    return 0 if all_three else 3


if __name__ == "__main__":
    raise SystemExit(main())
