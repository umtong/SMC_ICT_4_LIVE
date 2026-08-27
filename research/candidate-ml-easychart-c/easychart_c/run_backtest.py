#!/usr/bin/env python3
"""Run EasyChart C through the existing NautilusTrader multi-symbol engine."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
CANDIDATE = HERE.parent
RESEARCH = CANDIDATE.parent
for path in (
    HERE,
    CANDIDATE,
    RESEARCH / "candidate-easychart-ml-system",
    RESEARCH / "candidate-easychart_re1",
    RESEARCH / "candidate-easychart-v5",
    RESEARCH / "candidate-easychart-v3",
):
    sys.path.insert(0, str(path))


def pop_option(argv: list[str], name: str) -> str:
    try:
        index = argv.index(name)
        value = argv[index + 1]
    except (ValueError, IndexError) as exc:
        raise SystemExit(f"{name} is required") from exc
    del argv[index : index + 2]
    return value


model_path = Path(pop_option(sys.argv, "--model")).resolve()
metadata_path = Path(pop_option(sys.argv, "--metadata")).resolve()
os.environ["EASYCHART_C_MODEL_PATH"] = str(model_path)
os.environ["EASYCHART_C_METADATA_PATH"] = str(metadata_path)

import robust_router_system  # noqa: E402,F401
import run_mtf_backtest_re1 as runner  # noqa: E402
from mtf_data_re1_flow import add_symbol_mtf_flow_data  # noqa: E402
from opportunity_universe import (  # noqa: E402
    EasyChartMLOpportunityUniverse,
    OPPORTUNITY_UNIVERSE_POLICY,
)
from easychart_c.core import MODEL_VERSION  # noqa: E402
from easychart_c.nautilus_strategy import EasyChartCCausalResponseStrategy  # noqa: E402

runner.EasyChartRE1NaturalBundle = EasyChartMLOpportunityUniverse
runner.EasyChartRE1Strategy = EasyChartCCausalResponseStrategy
runner.add_symbol_mtf_data = add_symbol_mtf_flow_data


def output_path(argv: list[str]) -> Path | None:
    try:
        return Path(argv[argv.index("--output") + 1])
    except (ValueError, IndexError):
        return None


def rewrite_metadata(output: Path) -> None:
    metadata = {
        "candidate": "candidate-ml-easychart-c",
        "model_version": MODEL_VERSION,
        "opportunity_universe_policy": OPPORTUNITY_UNIVERSE_POLICY,
        "decision_policy": (
            "DIRECTION_AND_LIQUIDITY_STRUCTURE_TO_CONFIRMED_PRICE_VOLUME_RESPONSE_"
            "THEN_COST_VIABLE_ONE_R_FIRST_OBJECTIVE_PROBABILITY_ROUTING"
        ),
        "risk_and_exit_policy": (
            "ONE_ACCOUNT_ONE_POSITION_FIXED_THREE_PERCENT_STOP_RISK_FULL_ENTRY_"
            "NO_PARTIAL_EXIT_PREENTRY_STOP_AND_TARGET"
        ),
    }
    for name in ("metrics.json", "run.json"):
        path = output / name
        if not path.exists():
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        record.update(metadata)
        path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    destination = output_path(sys.argv)
    runner.main()
    if destination is not None:
        rewrite_metadata(destination)
