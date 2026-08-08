#!/usr/bin/env python3
"""W2-first LCOR campaign using NautilusTrader-only execution."""

from __future__ import annotations

import argparse
from collections import Counter
import copy
import json
import math
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping


VARIANTS = (
    (
        "lcor_full_cash_ownership_relay",
        (
            "Perpetual-led external sweep plus extreme OI contraction, initial "
            "spot non-acceptance, later spot ownership, retained forced removal, "
            "strictly later perpetual acceptance, first owned pullback and "
            "renewed same-direction initiative."
        ),
        0.35,
        True,
    ),
    (
        "lcor_without_material_removal_retention_ablation",
        (
            "Attribution only: keep the complete venue-ownership chronology but "
            "require only that OI has not rebuilt above the pre-event baseline; "
            "material retained-removal fraction is removed."
        ),
        0.0,
        False,
    ),
)


def _base(raw: Mapping[str, Any]) -> dict[str, Any]:
    config = copy.deepcopy(dict(raw))
    config["candidate"] = "candidate-06-lcor-v8.0"
    config["version"] = "8.0.0"
    config["hypothesis"] = (
        "An extreme OI-contraction external sweep need not reverse. When the "
        "leveraged venue clears weak inventory first, a later cash-auction "
        "acceptance can assume ownership of same-direction continuation; only "
        "strictly later perpetual acceptance, retained forced removal, an owned "
        "pullback and resumed initiative may trade."
    )
    config.setdefault("validation", {})["stage"] = "lcor_w2_first"
    config["logic"].update(
        {
            "engine": "LIQUIDATION_CASH_OWNERSHIP_RELAY",
            "signal_submission_timing": "ON_SIGNAL_CLOSE",
            # Shared causal accumulation aliases used by the parent framework.
            "ciot_auction_period_minutes": 15,
            "ciot_entry_window_minutes": 13,
            "ciot_oi_history_minutes": 1440,
            "ciot_min_prior_oi_changes": 36,
            "ciot_oi_change_quantile": 0.85,
            "ciot_spot_atr_bars": 20,
            "ciot_retest_band_atr": 0.35,
            "ciot_max_opposing_flow": 0.12,
            "ciot_extension_atr": 0.05,
            "ciot_projection_fraction": 1.0,
            # LCOR categorical state sequence.
            "lcor_event_move_atr": 0.30,
            "lcor_metric_flow_floor": 0.06,
            "lcor_min_sweep_atr": 0.10,
            "lcor_accept_close_atr": 0.05,
            "lcor_spot_hold_tolerance_atr": 0.03,
            "lcor_perp_accept_close_atr": 0.04,
            "lcor_response_flow_ratio": 0.05,
            "lcor_response_close_location": 0.58,
            "lcor_retest_band_atr": 0.35,
            "lcor_max_opposing_flow": 0.12,
            "lcor_extension_atr": 0.05,
            "lcor_stop_buffer_atr": 0.08,
            "lcor_projection_fraction": 1.0,
            "lcor_episode_bars": 30,
            "lcor_post_signal_context_bars": 8,
            "lcor_cooldown_bars": 2,
            "minimum_structural_rr": 0.75,
            "minimum_net_rr_after_entry_delay": 0.60,
            "max_entry_drift_atr": 0.40,
            "max_holding_bars": 45,
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
            str(candidate_dir / "run_liquidation_cash_ownership_validation.py"),
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
            record["error"] = error_path.read_text(
                encoding="utf-8",
                errors="replace",
            )[-16000:]
    return record


def _counts(root: Path) -> dict[str, Any]:
    reasons: Counter[str] = Counter()
    states: Counter[str] = Counter()
    families: Counter[str] = Counter()
    targets: Counter[str] = Counter()
    events = root / "scenario_events.jsonl"
    if events.exists():
        for line in events.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            reasons[str(event.get("reason_code", "UNKNOWN"))] += 1
            states[str(event.get("next_state", "UNKNOWN"))] += 1
    trades = root / "trades.json"
    if trades.exists():
        for trade in json.loads(trades.read_text(encoding="utf-8")).get("trades", []):
            families[str(trade.get("family", "UNKNOWN"))] += 1
            targets[str(trade.get("target_reason", "UNKNOWN"))] += 1
    return {
        "reason_counts": dict(reasons),
        "next_state_counts": dict(states),
        "trade_family_counts": dict(families),
        "target_reason_counts": dict(targets),
    }


def _week_feasible(record: Mapping[str, Any]) -> bool:
    metrics = record.get("metrics")
    if not isinstance(metrics, Mapping):
        return False
    trades = int(metrics.get("trades", 0))
    wins = int(metrics.get("wins", 0))
    growth = float(metrics.get("geometric_daily_nav_growth", 0.0))
    pf = metrics.get("profit_factor")
    return bool(
        growth >= 0.01
        and trades >= 2
        and wins >= 1
        and pf is not None
        and float(pf) > 1.0
        and float(metrics.get("max_drawdown_nav", 1.0)) <= 0.25
        and not metrics.get("errors")
    )


def _diagnose(record: Mapping[str, Any]) -> dict[str, Any]:
    metrics = record.get("metrics")
    if not isinstance(metrics, Mapping):
        return {
            "classification": "IMPLEMENTATION_OR_DATA_FAILURE",
            "error": record.get("error") or record.get("stderr_tail"),
        }
    diagnostics = dict(metrics.get("diagnostics", {}))
    counts = dict(record.get("causal_counts", {}))
    reasons = dict(counts.get("reason_counts", {}))
    trades = int(metrics.get("trades", 0))
    growth = float(metrics.get("geometric_daily_nav_growth", 0.0))
    pf = metrics.get("profit_factor")
    if trades == 0:
        if reasons.get(
            "PERPETUAL_LED_EXTERNAL_SWEEP_WITH_EXTREME_OI_CONTRACTION_AND_INITIAL_SPOT_NON_ACCEPTANCE",
            0,
        ) == 0:
            classification = "NO_FORCED_LIQUIDATION_LEAD_EVENTS"
        elif reasons.get(
            "LATER_SPOT_ACCEPTANCE_ASSUMED_OWNERSHIP_AFTER_DELEVERAGING",
            0,
        ) == 0:
            classification = "NO_LATER_CASH_OWNERSHIP_TRANSFER"
        elif reasons.get(
            "PERPETUAL_ACCEPTED_ONLY_AFTER_STRICTLY_EARLIER_CASH_OWNERSHIP",
            0,
        ) == 0:
            classification = "NO_STRICTLY_LATER_PERPETUAL_ACCEPTANCE"
        elif reasons.get(
            "FIRST_OPPOSING_FLOW_PULLBACK_HELD_CASH_OWNED_BOUNDARY",
            0,
        ) == 0:
            classification = "NO_FIRST_CASH_OWNED_PULLBACK"
        elif reasons.get("LCOR_ENTRY_ARMED", 0) == 0:
            classification = "NO_RENEWED_CASH_OWNED_INITIATIVE"
        else:
            classification = "ENTRY_ARMED_BUT_EXECUTION_REJECTED"
    elif growth <= 0.0 or (pf is not None and float(pf) < 1.0):
        classification = "NEGATIVE_COST_AFTER_EXPECTANCY"
    elif _week_feasible(record):
        classification = "W2_STRUCTURAL_FEASIBILITY_PASSED"
    else:
        classification = "POSITIVE_BUT_W2_FEASIBILITY_INCOMPLETE"
    return {
        "classification": classification,
        "geometric_daily_nav_growth": growth,
        "trades": trades,
        "wins": metrics.get("wins"),
        "win_rate": metrics.get("win_rate"),
        "profit_factor": pf,
        "max_drawdown_nav": metrics.get("max_drawdown_nav"),
        "gate_failures": metrics.get("gate_failures", []),
        "entry_abstentions": diagnostics.get("entry_abstentions", {}),
        "causal_counts": counts,
    }


def _aggregate(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    metrics = [record.get("metrics") for record in records]
    if not all(isinstance(item, Mapping) for item in metrics):
        return {}
    typed = [dict(item) for item in metrics if isinstance(item, Mapping)]
    days = sum(float(item.get("evaluation_days", 0.0)) for item in typed)
    multiple = math.prod(
        float(item.get("ending_nav", 0.0)) / float(item.get("starting_nav", 1.0))
        for item in typed
    )
    growth = (
        multiple ** (1.0 / days) - 1.0
        if days > 0.0 and multiple > 0.0
        else -1.0
    )
    trades = sum(int(item.get("trades", 0)) for item in typed)
    wins = sum(int(item.get("wins", 0)) for item in typed)
    return {
        "evaluation_days": days,
        "nav_multiple_product": multiple,
        "pooled_geometric_daily_nav_growth": growth,
        "trades": trades,
        "wins": wins,
        "win_rate": wins / trades if trades else 0.0,
        "positive_weeks": sum(
            float(item.get("geometric_daily_nav_growth", 0.0)) > 0.0
            for item in typed
        ),
        "worst_weekly_max_drawdown": max(
            float(item.get("max_drawdown_nav", 0.0)) for item in typed
        ),
        "largest_positive_trade_share": max(
            float(item.get("largest_positive_trade_share", 1.0)) for item in typed
        ),
        "errors": [error for item in typed for error in item.get("errors", [])],
    }


def _long_gate(aggregate: Mapping[str, Any]) -> bool:
    return bool(
        float(aggregate.get("pooled_geometric_daily_nav_growth", 0.0)) >= 0.01
        and int(aggregate.get("trades", 0)) >= 10
        and float(aggregate.get("win_rate", 0.0)) >= 0.45
        and int(aggregate.get("positive_weeks", 0)) >= 2
        and float(aggregate.get("worst_weekly_max_drawdown", 1.0)) <= 0.25
        and float(aggregate.get("largest_positive_trade_share", 1.0)) <= 0.40
        and not aggregate.get("errors")
    )


def _render(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Candidate 06 v8.0 Liquidation-to-Cash Ownership Relay",
        "",
        f"Terminal status: `{summary.get('terminal_status')}`",
        (
            f"Selected: `{summary.get('selected')}`"
            if summary.get("selected")
            else "Selected: none"
        ),
        f"Long evaluation authorized: `{summary.get('long_evaluation_authorized')}`",
        "",
        "|variant|week|eligible|gate|geom/day|trades|wins|win rate|PF|max DD|diagnosis|",
        "|---|---:|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for record in [
        *summary.get("w2_results", []),
        *summary.get("frozen_validation", []),
    ]:
        metrics = record.get("metrics", {})
        diagnosis = record.get("diagnosis", {})
        lines.append(
            (
                "|{name}|{week}|{eligible}|{gate}|{growth:.6%}|{trades}|{wins}|"
                "{win:.2%}|{pf}|{dd:.2%}|{diagnosis}|"
            ).format(
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
                diagnosis=diagnosis.get("classification"),
            ),
        )
    aggregate = summary.get("aggregate")
    if aggregate:
        lines.extend(
            [
                "",
                "## Frozen aggregate",
                "",
                f"- Evaluation days: `{aggregate.get('evaluation_days')}`",
                f"- Trades: `{aggregate.get('trades')}`",
                f"- Wins: `{aggregate.get('wins')}`",
                f"- Win rate: `{float(aggregate.get('win_rate', 0.0)):.2%}`",
                (
                    "- Pooled geometric NAV growth/day: "
                    f"`{float(aggregate.get('pooled_geometric_daily_nav_growth', 0.0)):.6%}`"
                ),
                f"- Positive weeks: `{aggregate.get('positive_weeks')}/3`",
                (
                    "- Worst weekly max drawdown: "
                    f"`{float(aggregate.get('worst_weekly_max_drawdown', 0.0)):.2%}`"
                ),
            ],
        )
    lines.extend(
        [
            "",
            "## Fixed causal contract",
            "",
            "- The current OI loss is compared only with prior completed OI losses.",
            "- The initiating perpetual sweep must occur before any accepted spot discovery.",
            "- Spot acceptance must be later than the initiating event; perpetual acceptance must be later still.",
            "- Material forced-OI removal, cash ownership, a distinct pullback, a distinct resumption, the stop and a still-live objective belong to one episode.",
            "- The retained-removal ablation is attribution-only and cannot select.",
            "- Orders, fills, fees, slippage, positions and whole-account NAV remain in NautilusTrader.",
            "- Planned loss remains three percent of current whole-account NAV and one global slot remains unchanged.",
        ],
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
        default=Path("artifacts/candidate-06/lcor-w2-first"),
    )
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    raw = json.loads((candidate_dir / "config.json").read_text(encoding="utf-8"))
    base = _base(raw)
    configs: dict[str, dict[str, Any]] = {}
    w2: list[dict[str, Any]] = []

    for name, description, retention_fraction, eligible in VARIANTS:
        config = copy.deepcopy(base)
        config["candidate_variant"] = name
        config["variant_description"] = description
        config["logic"][
            "lcor_forced_removal_retention_fraction"
        ] = retention_fraction
        configs[name] = config
        config_dir = root / "configs"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / f"{name}-week-2.json"
        config_path.write_text(
            json.dumps(config, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        run_output = root / "runs" / name / "week-2"
        record = _run(config_path, run_output, 1, candidate_dir, repository)
        record.update(
            {
                "name": name,
                "description": description,
                "eligible_for_selection": eligible,
                "week_index": 1,
                "config_path": str(config_path.relative_to(repository)),
                "run_output": str(run_output.relative_to(repository)),
                "causal_counts": _counts(run_output),
            },
        )
        record["diagnosis"] = _diagnose(record)
        w2.append(record)

    base_summary: dict[str, Any] = {
        "candidate": "candidate-06-lcor-v8.0",
        "design": (
            "forced deleveraging leads external sweep -> later cash acceptance -> "
            "retained OI removal -> strictly later perpetual acceptance -> first "
            "cash-owned pullback -> renewed same-direction initiative -> same-leg "
            "stop and still-live objective"
        ),
        "variant_priority": [VARIANTS[0][0]],
        "selection_rule": (
            "the full categorical family must reach at least 1% post-cost W2 "
            "geometric growth with at least two closed trades, one win, PF>1 and "
            "<=25% drawdown; attribution ablation cannot select"
        ),
        "w2_results": w2,
        "frozen_validation": [],
        "selected": None,
        "aggregate": None,
        "long_evaluation_authorized": False,
    }
    valid = all(
        int(record.get("returncode", 1)) == 0
        and isinstance(record.get("metrics"), Mapping)
        for record in w2
    )
    if not valid:
        summary = {
            **base_summary,
            "terminal_status": "IMPLEMENTATION_OR_DATA_FAILURE",
            "error": "At least one predeclared W2 variant did not produce valid Nautilus metrics.",
        }
        _write(root, summary)
        return 5

    selected_record = w2[0]
    if not _week_feasible(selected_record):
        summary = {
            **base_summary,
            "terminal_status": "W2_LIQUIDATION_CASH_OWNERSHIP_GATE_FAILED",
        }
        _write(root, summary)
        return 2

    selected = str(selected_record["name"])
    locked = copy.deepcopy(configs[selected])
    locked["validation"]["stage"] = "lcor_frozen_three_week"
    locked_path = candidate_dir / "config.lcor.locked.json"
    locked_path.write_text(
        json.dumps(locked, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    frozen: list[dict[str, Any]] = []
    for week_index in (0, 2):
        config_path = root / "configs" / f"{selected}-week-{week_index + 1}.json"
        config_path.write_text(
            json.dumps(locked, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        run_output = root / "runs" / selected / f"week-{week_index + 1}"
        record = _run(
            config_path,
            run_output,
            week_index,
            candidate_dir,
            repository,
        )
        record.update(
            {
                "name": selected,
                "description": VARIANTS[0][1],
                "eligible_for_selection": True,
                "week_index": week_index,
                "config_path": str(config_path.relative_to(repository)),
                "run_output": str(run_output.relative_to(repository)),
                "causal_counts": _counts(run_output),
            },
        )
        record["diagnosis"] = _diagnose(record)
        frozen.append(record)
        if int(record.get("returncode", 1)) != 0 or not isinstance(
            record.get("metrics"),
            Mapping,
        ):
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

    ordered = [
        next(item for item in frozen if item["week_index"] == 0),
        selected_record,
        next(item for item in frozen if item["week_index"] == 2),
    ]
    aggregate = _aggregate(ordered)
    authorized = _long_gate(aggregate)
    summary = {
        **base_summary,
        "selected": selected,
        "locked_config": str(locked_path.relative_to(repository)),
        "frozen_validation": frozen,
        "aggregate": aggregate,
        "long_evaluation_authorized": authorized,
        "terminal_status": (
            "FROZEN_THREE_WEEK_LIQUIDATION_CASH_OWNERSHIP_GATE_PASSED"
            if authorized
            else "FROZEN_THREE_WEEK_LIQUIDATION_CASH_OWNERSHIP_TARGET_NOT_REPLICATED"
        ),
    }
    _write(root, summary)
    return 0 if authorized else 3


if __name__ == "__main__":
    raise SystemExit(main())
