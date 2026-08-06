"""Execute the frozen candidate-09 weekly research protocol."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from math import exp, log
from pathlib import Path
from typing import Any, Mapping

from backtest import run_backtest
from downloader import download_week, validate_frozen_selection
from smc_ict_4.manifest import (
    build_data_manifest,
    create_run_manifest,
    write_data_manifest,
    write_json_atomic,
)


def _product(values: list[float]) -> float:
    result = 1.0
    for value in values:
        result *= value
    return result


def _aggregate_variant(
    variant: str,
    runs: list[Mapping[str, Any]],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    initial = float(config["initial_nav"])
    ratios = [float(run["final_nav"]) / initial for run in runs]
    total_days = sum(int(run["daily"]["calendar_days"]) for run in runs)
    pooled_growth = _product(ratios) ** (1.0 / total_days) - 1.0 if ratios and all(r > 0 for r in ratios) else -1.0

    trades = sum(int(run["trade_metrics"]["trades"]) for run in runs)
    wins = sum(int(run["trade_metrics"]["wins"]) for run in runs)
    gross_profit = sum(float(run["trade_metrics"]["gross_profit"]) for run in runs)
    gross_loss = sum(float(run["trade_metrics"]["gross_loss"]) for run in runs)
    scenario: dict[str, dict[str, Any]] = {}
    for run in runs:
        for name, values in run["trade_metrics"]["scenario_attribution"].items():
            bucket = scenario.setdefault(name, {"trades": 0, "wins": 0, "pnl": 0.0})
            bucket["trades"] += int(values["trades"])
            bucket["wins"] += int(values["wins"])
            bucket["pnl"] += float(values["pnl"])
    for bucket in scenario.values():
        bucket["win_rate"] = bucket["wins"] / bucket["trades"] if bucket["trades"] else 0.0

    reason_counts: Counter[str] = Counter()
    state_counts: Counter[str] = Counter()
    for run in runs:
        reason_counts.update({str(k): int(v) for k, v in run["event_reason_counts"].items()})
        state_counts.update({str(k): int(v) for k, v in run["event_state_counts"].items()})

    gates = config["success_gate"]
    gate_results = {
        "pooled_growth": pooled_growth >= float(gates["minimum_pooled_geometric_daily_growth"]),
        "each_week_growth": all(
            float(run["geometric_daily_growth"])
            >= float(gates["minimum_each_week_geometric_daily_growth"])
            for run in runs
        ),
        "trades_each_week": all(
            int(run["trade_metrics"]["trades"]) >= int(gates["minimum_trades_per_week"])
            for run in runs
        ),
        "profit_not_top3_concentrated": all(
            float(run["trade_metrics"]["top3_positive_pnl_concentration"])
            <= float(gates["maximum_top3_positive_pnl_concentration"])
            for run in runs
            if float(run["trade_metrics"]["gross_profit"]) > 0.0
        ),
        "drawdown": all(
            float(run["max_drawdown"]) <= float(gates["maximum_drawdown"]) for run in runs
        ),
        "flat_and_no_rejections": all(
            bool(run["flat_at_end"]) and not run["strategy"]["rejections"] for run in runs
        ),
    }

    return {
        "variant": variant,
        "weeks": [
            {
                "week": run["week"],
                "final_nav": run["final_nav"],
                "total_return": run["total_return"],
                "geometric_daily_growth": run["geometric_daily_growth"],
                "max_drawdown": run["max_drawdown"],
                "trades": run["trade_metrics"]["trades"],
                "win_rate": run["trade_metrics"]["win_rate"],
                "profit_factor": run["trade_metrics"]["profit_factor"],
                "top3_positive_pnl_concentration": run["trade_metrics"][
                    "top3_positive_pnl_concentration"
                ],
                "plan_counts": run["strategy"]["plan_counts"],
                "rejections": len(run["strategy"]["rejections"]),
            }
            for run in runs
        ],
        "pooled_geometric_daily_growth": pooled_growth,
        "total_calendar_days": total_days,
        "trades": trades,
        "wins": wins,
        "win_rate": wins / trades if trades else 0.0,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0.0 else (float("inf") if gross_profit > 0.0 else 0.0),
        "scenario_attribution": scenario,
        "event_reason_counts": dict(reason_counts),
        "event_state_counts": dict(state_counts),
        "gate_results": gate_results,
        "passes_all_gates": all(gate_results.values()),
    }


def _diagnose(
    base: Mapping[str, Any],
    no_flow: Mapping[str, Any],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    implementation_error = not bool(base["gate_results"]["flat_and_no_rejections"])
    target = float(config["success_gate"]["minimum_pooled_geometric_daily_growth"])
    base_growth = float(base["pooled_geometric_daily_growth"])
    ablation_growth = float(no_flow["pooled_geometric_daily_growth"])

    if implementation_error:
        status = "IMPLEMENTATION_ERROR"
        action = (
            "Control execution/accounting variables, repair rejected or non-flat orders, "
            "and rerun the identical frozen weeks."
        )
    elif bool(base["passes_all_gates"]):
        status = "WEEKLY_GATE_PASSED"
        action = "Freeze logic and advance to a longer untouched BTC evaluation."
    else:
        status = "LOGIC_GATE_FAILED"
        if ablation_growth > base_growth:
            action = (
                "The one-variable no-flow ablation improved growth.  Aggressor-flow polarity "
                "is the largest removable drag and must be structurally revised rather than tuned."
            )
        else:
            action = (
                "Removing aggressor flow did not improve the candidate.  The flow gate contributes "
                "selectivity; failure lies in level/branch/exit structure rather than this variable."
            )

    base_trades = int(base["trades"])
    if base_trades == 0:
        largest_factor = "No executable scenario survived cost-aware target and stop validation."
    elif base_growth < target:
        scenario = base.get("scenario_attribution", {})
        if scenario:
            worst = min(scenario.items(), key=lambda item: float(item[1]["pnl"]))
            largest_factor = f"Largest observed PnL drag: {worst[0]} ({float(worst[1]['pnl']):.2f} USDT)."
        else:
            largest_factor = "Trade expectancy after effective costs was insufficient."
    else:
        largest_factor = "Cross-week consistency or concentration gate, not pooled growth, failed."

    working_parts: list[str] = []
    if base_growth > ablation_growth:
        working_parts.append("Aggressor-flow polarity improved the base over its one-variable ablation.")
    if any(float(week["geometric_daily_growth"]) > 0.0 for week in base["weeks"]):
        working_parts.append("The state engine produced positive cost-after growth in at least one frozen week.")
    if base.get("scenario_attribution"):
        profitable = [
            name
            for name, values in base["scenario_attribution"].items()
            if float(values["pnl"]) > 0.0
        ]
        if profitable:
            working_parts.append("Profitable scenario component(s): " + ", ".join(sorted(profitable)) + ".")
    if not working_parts:
        working_parts.append("No component produced repeatable positive cost-after evidence in the frozen weeks.")

    return {
        "status": status,
        "action": action,
        "largest_performance_factor": largest_factor,
        "working_components": working_parts,
        "ablation": {
            "variable_removed": "aggressor-flow polarity gate",
            "base_pooled_geometric_daily_growth": base_growth,
            "no_flow_pooled_geometric_daily_growth": ablation_growth,
            "difference": base_growth - ablation_growth,
        },
        "known_failure_conditions": [
            "Both sides of the active dealing range are breached inside one one-minute bar; intrabar ordering is unresolved and the event is not traded.",
            "No previously observed external liquidity target exists beyond an accepted continuation breach.",
            "The target does not clear the configured all-in 6.5 bps per side effective cost at the minimum net reward-to-risk.",
            "The reclaim or acceptance state is not confirmed before the causal classification timeout.",
            "A held continuation retest does not occur before the acceptance state expires.",
            "The scenario remains open for 90 completed one-minute bars; information decay forces a time exit.",
            "Bar-only OHLC data cannot reconstruct actual queue position, spread path, or high/low ordering; costs are conservatively folded into commissions and adaptive deterministic ordering is used.",
        ],
    }


def _summary_markdown(
    *,
    config: Mapping[str, Any],
    base: Mapping[str, Any],
    no_flow: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> str:
    lines = [
        "# Candidate-09 frozen-week result",
        "",
        f"- Generated: {datetime.now(timezone.utc).isoformat()}",
        f"- Status: **{decision['status']}**",
        f"- Base pooled geometric daily growth: **{float(base['pooled_geometric_daily_growth']):.4%}**",
        f"- No-flow ablation growth: **{float(no_flow['pooled_geometric_daily_growth']):.4%}**",
        f"- Base trades / win rate: **{base['trades']} / {float(base['win_rate']):.2%}**",
        f"- Risk per accepted trade: **{float(config['risk_fraction']):.2%} of current NAV (maximum)**",
        f"- Effective cost: **{float(config['effective_fee_rate_per_side']):.4%} per side**",
        "",
        "## Frozen weeks",
        "",
        "| Week | Final NAV | Geometric/day | MDD | Trades | Win rate | PF |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for week in base["weeks"]:
        pf = week["profit_factor"]
        pf_text = "inf" if pf == float("inf") else f"{float(pf):.2f}"
        lines.append(
            f"| {week['week']} | {float(week['final_nav']):,.2f} | "
            f"{float(week['geometric_daily_growth']):.3%} | "
            f"{float(week['max_drawdown']):.2%} | {week['trades']} | "
            f"{float(week['win_rate']):.2%} | {pf_text} |"
        )
    lines.extend(
        [
            "",
            "## Gate result",
            "",
        ]
    )
    for name, passed in base["gate_results"].items():
        lines.append(f"- {'PASS' if passed else 'FAIL'} — `{name}`")
    lines.extend(
        [
            "",
            "## Controlled diagnosis",
            "",
            decision["largest_performance_factor"],
            "",
            decision["action"],
            "",
            "### Working components",
            "",
        ]
    )
    for item in decision["working_components"]:
        lines.append(f"- {item}")
    lines.extend(["", "### Known failure conditions", ""])
    for item in decision["known_failure_conditions"]:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## Reproduction",
            "",
            "```bash",
            "smc4 doctor",
            "python -m unittest discover -s research/candidate-09/tests -p 'test_*.py' -v",
            "PYTHONPATH=src python research/candidate-09/run_research.py \\",
            "  --config research/candidate-09/config.json \\",
            "  --output artifacts/candidate-09",
            "```",
            "",
            "Every PnL result above comes from NautilusTrader's BacktestEngine, execution engine, "
            "portfolio and account reports.  The candidate does not contain a parallel backtest or accounting engine.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="research/candidate-09/config.json")
    parser.add_argument("--output", default="artifacts/candidate-09")
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    data_dir = output / "data"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    validate_frozen_selection(config)

    feature_paths: dict[str, Path] = {}
    for week in config["weeks"]:
        feature_paths[str(week["name"])] = download_week(
            symbol=str(config["symbol"]),
            interval=str(config["interval"]),
            start_date=str(week["start"]),
            days=int(week["days"]),
            output_dir=data_dir,
        )

    data_manifest = build_data_manifest(
        data_dir,
        dataset="binance-vision-um-btcusdt-1m-frozen-weeks",
        metadata_values={
            "candidate": config["candidate"],
            "selection": config["selection"],
            "weeks": config["weeks"],
            "timestamp_contract": "completed close_time_ns is both event and observation time for each bar",
        },
    )
    data_manifest_path = write_data_manifest(output / "data_manifest.json", data_manifest)

    results: dict[str, list[dict[str, Any]]] = {}
    for variant in config["variants"]:
        variant_runs: list[dict[str, Any]] = []
        for week in config["weeks"]:
            week_name = str(week["name"])
            metrics = run_backtest(
                research_config_path=config_path,
                feature_path=feature_paths[week_name],
                output_dir=output / "runs" / variant / week_name,
                variant=str(variant),
                data_manifest_path=data_manifest_path,
                week_name=week_name,
                start_date=str(week["start"]),
            )
            variant_runs.append(metrics)
        results[str(variant)] = variant_runs

    aggregates = {
        variant: _aggregate_variant(variant, runs, config)
        for variant, runs in results.items()
    }
    base = aggregates["base"]
    no_flow = aggregates["no_flow"]
    decision = _diagnose(base, no_flow, config)

    aggregate_payload = {
        "candidate": config["candidate"],
        "selection": config["selection"],
        "weeks": config["weeks"],
        "variants": aggregates,
        "decision": decision,
    }
    write_json_atomic(output / "aggregate.json", aggregate_payload)
    write_json_atomic(output / "decision.json", decision)
    (output / "SUMMARY.md").write_text(
        _summary_markdown(config=config, base=base, no_flow=no_flow, decision=decision),
        encoding="utf-8",
    )
    write_json_atomic(
        output / "run.json",
        create_run_manifest(
            run_id="candidate09-frozen-weeks",
            candidate=str(config["candidate"]),
            config_path=config_path,
            data_manifest_path=data_manifest_path,
            extra={
                "protocol": "three deterministic random BTC weeks plus one-variable no-flow ablation",
                "base_result": {
                    "pooled_geometric_daily_growth": base["pooled_geometric_daily_growth"],
                    "trades": base["trades"],
                    "passes_all_gates": base["passes_all_gates"],
                },
                "decision": decision,
            },
        ),
    )

    print(json.dumps(aggregate_payload, indent=2, sort_keys=True))
    return 2 if decision["status"] == "IMPLEMENTATION_ERROR" else 0


if __name__ == "__main__":
    raise SystemExit(main())
