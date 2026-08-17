#!/usr/bin/env python3
"""Run the integrated robust EasyChart ML system in one four-symbol account."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parent
for path in (
    HERE,
    RESEARCH / "candidate-easychart_re1",
    RESEARCH / "candidate-easychart-v5",
    RESEARCH / "candidate-easychart-v3",
):
    sys.path.insert(0, str(path))


def _pop_option(argv: list[str], name: str) -> str:
    try:
        index = argv.index(name)
        value = argv[index + 1]
    except (ValueError, IndexError) as exc:
        raise SystemExit(f"{name} is required") from exc
    del argv[index : index + 2]
    return value


model_path = Path(_pop_option(sys.argv, "--ml-model")).resolve()
os.environ["EASYCHART_ML_SYSTEM_MODEL_PATH"] = str(model_path)

# Patch the shared router namespace before execution_ml_system imports its public
# objects. This guarantees identical trace semantics in training and execution.
import robust_router_system  # noqa: E402,F401
import run_mtf_backtest_re1 as _runner  # noqa: E402
from execution_ml_system import EasyChartMLSystemStrategy  # noqa: E402
from mtf_data_re1_flow import add_symbol_mtf_flow_data  # noqa: E402
from opportunity_universe import (  # noqa: E402
    EasyChartMLOpportunityUniverse,
    OPPORTUNITY_UNIVERSE_POLICY,
)
from robust_router_system import MODEL_VERSION  # noqa: E402

_runner.EasyChartRE1NaturalBundle = EasyChartMLOpportunityUniverse
_runner.EasyChartRE1Strategy = EasyChartMLSystemStrategy
_runner.add_symbol_mtf_data = add_symbol_mtf_flow_data


def _output_path(argv: list[str]) -> Path | None:
    try:
        return Path(argv[argv.index("--output") + 1])
    except (ValueError, IndexError):
        return None


def _rewrite_metadata(output: Path) -> None:
    metadata = {
        "candidate": "candidate-easychart-ml-system",
        "ml_model_path": str(model_path),
        "ml_model_version": MODEL_VERSION,
        "opportunity_universe_policy": OPPORTUNITY_UNIVERSE_POLICY,
        "decision_policy": (
            "COMPLETE_CAUSAL_AUCTION_PLANS_PLUS_SYNCHRONIZED_PRIOR_ONLY_MARKET_STATE_"
            "TO_ENVIRONMENT_ROBUST_TARGET_BEFORE_STOP_PROBABILITY_THEN_MAXIMUM_POSITIVE_"
            "EXPECTED_LOG_NAV_GROWTH"
        ),
        "risk_and_exit_policy": (
            "UNCHANGED_FIXED_3_PERCENT_STOP_RISK_ONE_FULL_POSITION_NO_PARTIAL_"
            "IMMUTABLE_PREENTRY_STOP_AND_TARGET"
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
    destination = _output_path(sys.argv)
    _runner.main()
    if destination is not None:
        _rewrite_metadata(destination)
