#!/usr/bin/env python3
"""Command-line entry point for candidate-02."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import unittest


def describe() -> str:
    return (
        "Liquidity Cascade Reclaim: confirmed external liquidity -> finite sweep -> "
        "auction reclaim -> opposite MSS/displacement -> FVG retest -> nearest opposing liquidity"
    )


def _self_test(candidate_dir: Path) -> int:
    suite = unittest.defaultTestLoader.discover(str(candidate_dir / "tests"), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def main(argv: list[str] | None = None) -> int:
    candidate_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=describe())
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("describe")
    sub.add_parser("self-test")
    select = sub.add_parser("select-windows")
    select.add_argument("--config", type=Path, default=candidate_dir / "config.json")
    validate = sub.add_parser("validate")
    validate.add_argument("--config", type=Path, default=candidate_dir / "config.json")
    validate.add_argument("--output", type=Path, required=True)
    validate.add_argument("--cache", type=Path, default=Path(".cache/candidate-02/binance-vision"))
    args = parser.parse_args(argv)

    if args.command == "describe":
        print(describe())
        return 0
    if args.command == "self-test":
        return _self_test(candidate_dir)

    from backtest import deterministic_week_selection, run_screen

    config = json.loads(args.config.read_text(encoding="utf-8"))
    if args.command == "select-windows":
        values = config["validation"]["random_week_selection"]
        selected = deterministic_week_selection(
            seed=int(values["seed"]),
            population_start=values["population_start"],
            population_end=values["population_end"],
            count=int(values["count"]),
        )
        print(json.dumps(selected, indent=2))
        return 0
    if args.command == "validate":
        metrics = run_screen(config_path=args.config, output=args.output, cache_root=args.cache)
        print(json.dumps(metrics, indent=2, sort_keys=True))
        return 0 if metrics["target_met"] else 2
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
