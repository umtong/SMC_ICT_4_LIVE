#!/usr/bin/env python3
"""Run frozen Candidate 05 v54 over the Candidate 29 exact continuous grid."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
from typing import Any

import run_continuous as _continuous
from nautilus_trader.config import ImportableStrategyConfig

CANDIDATE05 = Path(__file__).resolve().parents[1] / "candidate-05"


def _v54_strategy_config(
    *,
    strategy_path: str,
    config_path: str,
    config: dict[str, Any],
) -> ImportableStrategyConfig:
    del strategy_path, config_path
    return ImportableStrategyConfig(
        strategy_path="long_v54_strategy:Candidate32Strategy",
        config_path="long_v54_strategy:Candidate32Config",
        config=config,
    )


def run(
    *,
    input_root: Path,
    output: Path,
    workspace: Path,
    cache: Path,
    symbol: str,
    start: date,
    end: date,
    config_path: Path,
) -> dict[str, Any]:
    # The shared runner retains exact chunk/hash/grid/account contracts.  Only
    # its import target is changed from Candidate 19 to the frozen v54 class.
    _continuous._strategy_config = _v54_strategy_config
    result = _continuous.run(
        input_root=input_root,
        output=output,
        workspace=workspace,
        cache=cache,
        symbol=symbol,
        start=start,
        end=end,
        config_path=config_path,
    )
    result["candidate"] = "candidate-32-frozen-candidate05-v54-continuous"
    result["alpha_parent"] = "candidate05_v54_failed_inventory_acceptance"
    result["alpha_decision_overrides"] = 0
    result["storage_only_adapter"] = True
    (output / "metrics.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=True) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--config", type=Path, default=CANDIDATE05 / "config.json")
    args = parser.parse_args()
    result = run(
        input_root=args.input_root.resolve(),
        output=args.output.resolve(),
        workspace=args.workspace.resolve(),
        cache=args.cache.resolve(),
        symbol=args.symbol,
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        config_path=args.config.resolve(),
    )
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=True))


if __name__ == "__main__":
    main()
