#!/usr/bin/env python3
"""Causal signal-count preflight for one frozen BTC week (not a backtest)."""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from nt_lvcfr_data import CandidateConfig, prepare_signal_schedule


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--week-start", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("nt_lvcfr_config.json"))
    parser.add_argument("--require-minimum", action="store_true")
    args = parser.parse_args()
    config = CandidateConfig.load(args.config)
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = prepare_signal_schedule(week_start=args.week_start, output_root=args.output, config=config)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    if args.require_minimum and int(manifest["signals"]) < config.minimum_episodes:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
