#!/usr/bin/env python3
"""Run one frozen BTC spot/perpetual CVPD week through NautilusTrader."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import traceback

import pandas as pd

from cross_venue_data import assert_synchronized_completed_bars, load_spot_week
from cross_venue_nautilus_runner import run_cross_venue_nautilus_backtest
from market_data import load_week, write_quality
from run_validation import calculate_metrics
from smc_ict_4.event_log import write_events
from smc_ict_4.manifest import (
    build_data_manifest,
    create_run_manifest,
    write_data_manifest,
    write_json_atomic,
)

HERE = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--week-index", type=int, default=0)
    parser.add_argument("--allow-gate-fail", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
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
    run_id = (
        f"candidate-06-cvpd-{week_start.strftime('%Y%m%d')}-"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    )
    data_root = Path(os.getenv("SMC4_RESEARCH_DATA", ".research-data/candidate-06")).resolve()

    try:
        perpetual = load_week(config["validation"]["symbol"], week_start, data_root)
        spot = load_spot_week(config["validation"]["symbol"], week_start, data_root)
        assert_synchronized_completed_bars(perpetual.frame, spot.frame)
        perpetual_quality = write_quality(output / "perpetual_data_quality.json", perpetual.quality)
        spot_quality = write_quality(output / "spot_data_quality.json", spot.quality)
        source_files = tuple((*perpetual.source_files, *spot.source_files))
        manifest = build_data_manifest(
            data_root,
            dataset=f"binance-spot-usdtm-cross-venue-{config['validation']['symbol']}-1m-{week_start.isoformat()}",
            include=source_files,
            metadata_values={
                "week_start_utc": week_start.isoformat(),
                "perpetual_quality_report": str(perpetual_quality),
                "spot_quality_report": str(spot_quality),
                "timestamp_contract": "spot and perpetual source open_time + 1 minute; exact completed-time equality required",
                "execution_instrument": "BTCUSDT-PERP.BINANCE",
                "context_instrument": "BTCUSDT.BINANCE spot",
            },
        )
        manifest_path = write_data_manifest(output / "data_manifest.json", manifest)
        result = run_cross_venue_nautilus_backtest(
            perpetual.frame,
            spot.frame,
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
        metrics = calculate_metrics(
            config=config,
            week_start=week_start,
            result=result,
            rows=len(perpetual.frame),
        )
        metrics["cross_venue_context"] = {
            "spot_rows": len(spot.frame),
            "perpetual_rows": len(perpetual.frame),
            "exact_timestamp_match": True,
            "spot_provider": "Binance public spot daily klines",
            "perpetual_provider": "Binance public USDT-M futures daily klines",
        }
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
                    "cross_venue_context": True,
                },
            ),
        )
        if metrics["errors"]:
            (output / "errors.log").write_text("\n".join(metrics["errors"]) + "\n", encoding="utf-8")
        print("CANDIDATE06_CVPD_METRICS_JSON=" + json.dumps(metrics, sort_keys=True, separators=(",", ":"), allow_nan=False))
        print(f"CANDIDATE06_CVPD_GATE={'PASS' if metrics['gate_passed'] else 'FAIL'}")
        if not metrics["gate_passed"] and not args.allow_gate_fail:
            return 2
        return 0
    except Exception:
        trace = traceback.format_exc()
        (output / "errors.log").write_text(trace, encoding="utf-8")
        write_json_atomic(
            output / "run.json",
            create_run_manifest(
                run_id=run_id,
                candidate=config.get("candidate", "candidate-06-cvpd"),
                config_path=config_path,
                extra={
                    "week_index": args.week_index,
                    "week_start_utc": week_start.isoformat(),
                    "status": "exception",
                    "nautilus_only": True,
                    "cross_venue_context": True,
                },
            ),
        )
        print(trace, file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
