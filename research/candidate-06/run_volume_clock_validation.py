#!/usr/bin/env python3
"""Run one frozen VCIB BTC week through NautilusTrader."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import traceback

import pandas as pd

from market_data import load_week, write_quality
from run_validation import calculate_metrics
from smc_ict_4.event_log import write_events
from smc_ict_4.manifest import (
    build_data_manifest,
    create_run_manifest,
    write_data_manifest,
    write_json_atomic,
)
from volume_clock_nautilus_runner import run_volume_clock_nautilus_backtest


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
    run_id = (
        f"candidate-06-vcib-{week_start.strftime('%Y%m%d')}-"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    )
    data_root = Path(os.getenv("SMC4_RESEARCH_DATA", ".research-data/candidate-06")).resolve()
    try:
        bars = load_week(config["validation"]["symbol"], week_start, data_root)
        quality_path = write_quality(output / "data_quality.json", bars.quality)
        manifest = build_data_manifest(
            data_root,
            dataset=f"binance-usdtm-vcib-{config['validation']['symbol']}-{week_start.isoformat()}",
            include=bars.source_files,
            metadata_values={
                "week_start_utc": week_start.isoformat(),
                "quality_report": str(quality_path),
                "execution_instrument": "BTCUSDT-PERP.BINANCE",
                "context": "completed one-minute bars compressed by a prior-only volume clock",
            },
        )
        manifest_path = write_data_manifest(output / "data_manifest.json", manifest)
        result = run_volume_clock_nautilus_backtest(
            bars.frame,
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
            rows=len(bars.frame),
        )
        metrics["volume_clock_context"] = dict(result.strategy.diagnostics.get("volume_clock_context", {}))
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
                    "volume_clock": True,
                },
            ),
        )
        if metrics["errors"]:
            (output / "errors.log").write_text("\n".join(metrics["errors"]) + "\n", encoding="utf-8")
        print(
            "CANDIDATE06_VCIB_METRICS_JSON="
            + json.dumps(metrics, sort_keys=True, separators=(",", ":"), allow_nan=False),
        )
        print(f"CANDIDATE06_VCIB_GATE={'PASS' if metrics['gate_passed'] else 'FAIL'}")
        return 0 if metrics["gate_passed"] or args.allow_gate_fail else 2
    except Exception:
        trace = traceback.format_exc()
        (output / "errors.log").write_text(trace, encoding="utf-8")
        write_json_atomic(
            output / "run.json",
            create_run_manifest(
                run_id=run_id,
                candidate=config.get("candidate", "candidate-06-vcib"),
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
