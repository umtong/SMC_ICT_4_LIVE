#!/usr/bin/env python3
"""Run one frozen CIRB parent-ledger week with five-second response resolution."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import traceback

import pandas as pd

from cirb_execution_resolution import (
    build_child_plans,
    freeze_parent_events,
    load_five_second_week,
    serialize_plans,
)
from cirb_five_second_nautilus import run_cirb_five_second_nautilus_backtest
from futures_metrics_data import load_week as load_metrics_week, write_quality as write_metrics_quality
from market_data import load_week as load_bar_week, write_quality as write_bar_quality
from run_validation import calculate_metrics
from smc_ict_4.event_log import write_events
from smc_ict_4.manifest import (
    build_data_manifest,
    create_run_manifest,
    write_data_manifest,
    write_json_atomic,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--baseline-events", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--week-index", type=int, required=True)
    parser.add_argument(
        "--variant",
        choices=("full", "discharge-only"),
        default="full",
    )
    parser.add_argument("--allow-gate-fail", action="store_true")
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    weeks = [
        date.fromisoformat(value)
        for value in config["validation"]["frozen_week_starts_utc"]
    ]
    if not 0 <= args.week_index < len(weeks):
        raise ValueError(f"week-index out of range: {args.week_index}")
    week_start = weeks[args.week_index]
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    baseline_events = Path(args.baseline_events).resolve()
    if not baseline_events.is_file():
        raise FileNotFoundError(f"baseline event ledger missing: {baseline_events}")
    data_root = Path(
        os.getenv("SMC4_RESEARCH_DATA", ".research-data/candidate-06")
    ).resolve()
    run_id = (
        f"candidate-06-cirb-5s-{args.variant}-{week_start.strftime('%Y%m%d')}-"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    )

    try:
        symbol = str(config["validation"]["symbol"])
        one_minute = load_bar_week(symbol, week_start, data_root)
        metrics_data = load_metrics_week(symbol, week_start, data_root)
        five_second = load_five_second_week(symbol, week_start, data_root)
        bar_quality_path = write_bar_quality(
            output / "one_minute_data_quality.json",
            one_minute.quality,
        )
        metrics_quality_path = write_metrics_quality(
            output / "futures_metrics_quality.json",
            metrics_data.quality,
        )
        five_second_quality_path = output / "five_second_data_quality.json"
        write_json_atomic(five_second_quality_path, dict(five_second.quality))

        parents, parent_audit = freeze_parent_events(
            baseline_events_path=baseline_events,
            one_minute_frame=one_minute.frame,
            metrics=metrics_data.observations,
            logic_params=config["logic"],
        )
        if int(parent_audit["semantic_drift_count"]) != 0:
            raise RuntimeError(
                "parent reconstruction changed authoritative semantics: "
                f"{parent_audit['semantic_drift_count']}"
            )
        plans, generation = build_child_plans(
            parents=parents,
            five_second_frame=five_second.frame,
            metrics=metrics_data.observations,
            logic_params=config["logic"],
            enable_discharge=True,
            enable_counter_inventory=args.variant == "full",
        )
        generation = {
            **generation,
            "variant": args.variant,
            "parent_signal_identity_hash": parent_audit[
                "baseline_entry_identity_hash"
            ],
            "parent_event_identity_hash": parent_audit[
                "parent_event_identity_hash"
            ],
            "semantic_drift_count": parent_audit["semantic_drift_count"],
        }
        write_json_atomic(output / "parent_audit.json", parent_audit)
        write_json_atomic(output / "child_plans.json", {"plans": serialize_plans(plans)})

        manifest = build_data_manifest(
            data_root,
            dataset=(
                f"binance-usdm-cirb-parent-frozen-5s-{symbol}-"
                f"{week_start.isoformat()}"
            ),
            include=tuple(
                (
                    *one_minute.source_files,
                    *metrics_data.source_files,
                    *five_second.source_files,
                )
            ),
            metadata_values={
                "week_start_utc": week_start.isoformat(),
                "variant": args.variant,
                "one_minute_quality_report": str(bar_quality_path),
                "metrics_quality_report": str(metrics_quality_path),
                "five_second_quality_report": str(five_second_quality_path),
                "baseline_events": str(baseline_events),
                "parent_event_identity_hash": parent_audit[
                    "parent_event_identity_hash"
                ],
                "baseline_entry_identity_hash": parent_audit[
                    "baseline_entry_identity_hash"
                ],
                "execution_instrument": "BTCUSDT-PERP.BINANCE",
                "causal_contract": (
                    "one-minute Nautilus parent ledger frozen before completed "
                    "five-second response evaluation"
                ),
            },
        )
        manifest_path = write_data_manifest(output / "data_manifest.json", manifest)

        result = run_cirb_five_second_nautilus_backtest(
            five_second.frame,
            plans,
            config=config["execution"],
            logic_params=config["logic"],
            generation_diagnostics=generation,
        )
        result.fills.to_csv(output / "orders.csv", index=False)
        result.positions.to_csv(output / "positions.csv", index=False)
        result.account.to_csv(output / "account.csv", index=False)
        write_events(output / "scenario_events.jsonl", result.strategy.events)
        pd.DataFrame(result.strategy.closed_trades).to_csv(
            output / "trades.csv", index=False
        )
        pd.DataFrame(result.strategy.equity_samples).to_csv(
            output / "equity.csv", index=False
        )
        write_json_atomic(
            output / "trades.json", {"trades": result.strategy.closed_trades}
        )

        metrics = calculate_metrics(
            config=config,
            week_start=week_start,
            result=result,
            rows=len(five_second.frame),
        )
        metrics["candidate"] = "candidate-06-cirb-parent-frozen-5s"
        metrics["candidate_version"] = "6.1.0-resolution-ablation"
        metrics["bar_interval"] = "5s"
        metrics["variant"] = args.variant
        metrics["parent_audit"] = {
            key: value
            for key, value in parent_audit.items()
            if key not in {"parent_rows", "baseline_entry_rows"}
        }
        resolution = metrics["diagnostics"]["cirb_execution_resolution"]
        abstentions = metrics["diagnostics"].get("entry_abstentions", {})
        resolution["still_rr_eroded"] = int(
            abstentions.get("NET_REWARD_RISK_ERODED_AFTER_DELAY", 0)
        )
        plan_by_signal = {plan.signal.scenario_id: plan for plan in plans}
        resolution["rescued_by_5s_closed_trades"] = sum(
            bool(plan_by_signal.get(str(trade.get("scenario_id"))).baseline_rr_eroded)
            for trade in result.strategy.closed_trades
            if plan_by_signal.get(str(trade.get("scenario_id"))) is not None
        )
        resolution["parent_identity_passed"] = (
            int(parent_audit["semantic_drift_count"]) == 0
        )
        metrics["execution_assumptions"]["bar_path"] = (
            "Nautilus adaptive high/low ordering on native five-second bars"
        )
        write_json_atomic(output / "metrics.json", metrics)
        write_json_atomic(
            output / "run.json",
            create_run_manifest(
                run_id=run_id,
                candidate=metrics["candidate"],
                config_path=config_path,
                data_manifest_path=manifest_path,
                extra={
                    "week_index": args.week_index,
                    "week_start_utc": week_start.isoformat(),
                    "variant": args.variant,
                    "nautilus_only": True,
                    "parent_frozen": True,
                    "response_resolution": "5s",
                    "semantic_drift_count": parent_audit[
                        "semantic_drift_count"
                    ],
                },
            ),
        )
        if metrics["errors"]:
            (output / "errors.log").write_text(
                "\n".join(metrics["errors"]) + "\n", encoding="utf-8"
            )
        print(
            "CANDIDATE06_CIRB_5S_METRICS_JSON="
            + json.dumps(
                metrics,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
        print(
            "CANDIDATE06_CIRB_5S_GATE="
            + ("PASS" if metrics["gate_passed"] else "FAIL")
        )
        return 0 if metrics["gate_passed"] or args.allow_gate_fail else 2
    except Exception:
        trace = traceback.format_exc()
        (output / "errors.log").write_text(trace, encoding="utf-8")
        write_json_atomic(
            output / "run.json",
            create_run_manifest(
                run_id=run_id,
                candidate="candidate-06-cirb-parent-frozen-5s",
                config_path=config_path,
                extra={
                    "week_index": args.week_index,
                    "week_start_utc": week_start.isoformat(),
                    "variant": args.variant,
                    "status": "exception",
                    "nautilus_only": True,
                },
            ),
        )
        print(trace, file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
