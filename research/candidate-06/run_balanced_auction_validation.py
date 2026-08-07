#!/usr/bin/env python3
"""Run one frozen BAVR BTC week through NautilusTrader."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import traceback

import pandas as pd

from agg_trade_profile_data import load_week_profiles, write_profile_quality
from balanced_auction_nautilus_runner import run_balanced_auction_nautilus_backtest
from market_data import load_week, write_quality
from run_validation import calculate_metrics
from smc_ict_4.event_log import write_events
from smc_ict_4.manifest import build_data_manifest, create_run_manifest, write_data_manifest, write_json_atomic

HERE = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--week-index", type=int, default=0)
    parser.add_argument("--allow-gate-fail", action="store_true")
    args = parser.parse_args()
    config_path = Path(args.config).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    weeks = [date.fromisoformat(value) for value in config["validation"]["frozen_week_starts_utc"]]
    if not 0 <= args.week_index < len(weeks):
        raise ValueError(f"week-index out of range: {args.week_index}")
    if args.week_index > 0 and config["validation"]["stage"] == "first_week":
        raise RuntimeError("later weeks are sealed until the first-week gate passes")
    week_start = weeks[args.week_index]
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    run_id = f"candidate-06-bavr-{week_start.strftime('%Y%m%d')}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    data_root = Path(os.getenv("SMC4_RESEARCH_DATA", ".research-data/candidate-06")).resolve()

    try:
        bars = load_week(config["validation"]["symbol"], week_start, data_root)
        profiles = load_week_profiles(
            config["validation"]["symbol"],
            week_start,
            data_root,
            period_minutes=int(config["logic"]["bavr_profile_period_minutes"]),
            value_area_fraction=float(config["logic"]["bavr_value_area_fraction"]),
        )
        bar_quality = write_quality(output / "bar_data_quality.json", bars.quality)
        profile_quality = write_profile_quality(output / "agg_trade_profile_quality.json", profiles.quality)
        manifest = build_data_manifest(
            data_root,
            dataset=f"binance-usdtm-bavr-{config['validation']['symbol']}-{week_start.isoformat()}",
            include=tuple((*bars.source_files, *profiles.source_files)),
            metadata_values={
                "week_start_utc": week_start.isoformat(),
                "bar_quality_report": str(bar_quality),
                "profile_quality_report": str(profile_quality),
                "execution_instrument": "BTCUSDT-PERP.BINANCE",
                "context": "checksum-verified USD-M aggTrades compressed into completed auction profiles",
            },
        )
        manifest_path = write_data_manifest(output / "data_manifest.json", manifest)
        result = run_balanced_auction_nautilus_backtest(
            bars.frame,
            profiles,
            config=config["execution"],
            logic_params=config["logic"],
        )
        result.fills.to_csv(output / "orders.csv", index=False)
        result.positions.to_csv(output / "positions.csv", index=False)
        result.account.to_csv(output / "account.csv", index=False)
        write_events(output / "scenario_events.jsonl", result.strategy.events)
        pd.DataFrame(result.strategy.closed_trades).to_csv(output / "trades.csv", index=False)
        pd.DataFrame(result.strategy.equity_samples).to_csv(output / "equity.csv", index=False)
        write_json_atomic(output / "trades.json", {"trades": result.strategy.closed_trades})
        metrics = calculate_metrics(config=config, week_start=week_start, result=result, rows=len(bars.frame))
        metrics["agg_trade_profile_context"] = dict(profiles.quality)
        write_json_atomic(output / "metrics.json", metrics)
        write_json_atomic(
            output / "run.json",
            create_run_manifest(
                run_id=run_id,
                candidate=config["candidate"],
                config_path=config_path,
                data_manifest_path=manifest_path,
                extra={
                    "week_index": args.week_index,
                    "week_start_utc": week_start.isoformat(),
                    "validation_stage": config["validation"]["stage"],
                    "nautilus_only": True,
                    "agg_trade_profile_context": True,
                },
            ),
        )
        if metrics["errors"]:
            (output / "errors.log").write_text("\n".join(metrics["errors"]) + "\n", encoding="utf-8")
        print("CANDIDATE06_BAVR_METRICS_JSON=" + json.dumps(metrics, sort_keys=True, separators=(",", ":"), allow_nan=False))
        print(f"CANDIDATE06_BAVR_GATE={'PASS' if metrics['gate_passed'] else 'FAIL'}")
        return 0 if metrics["gate_passed"] or args.allow_gate_fail else 2
    except Exception:
        trace = traceback.format_exc()
        (output / "errors.log").write_text(trace, encoding="utf-8")
        write_json_atomic(
            output / "run.json",
            create_run_manifest(
                run_id=run_id,
                candidate=config.get("candidate", "candidate-06-bavr"),
                config_path=config_path,
                extra={
                    "week_index": args.week_index,
                    "week_start_utc": week_start.isoformat(),
                    "status": "exception",
                    "nautilus_only": True,
                },
            ),
        )
        print(trace, file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
