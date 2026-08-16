#!/usr/bin/env python3
"""Run ML_a on the current skilled-continuation RE1 plan stream.

The EasyChart engine owns scenario creation and immutable entry/stop/target.
ML_a ranks simultaneously available plans by estimated post-cost log growth.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from easychart_re1_skilled_continuation import (
    EasyChartRE1SkilledContinuationBundle,
    LOCAL_AUCTION_SKILLED_ROUTER_RULE,
)
from execution_re1_ml_a import EasyChartRE1MLAEnvStrategy
from mtf_data_re1_flow import add_symbol_mtf_flow_data
from plan_event_values_re1 import plan_event_values
import run_mtf_backtest_re1 as runner


HERE = Path(__file__).resolve().parent
DEFAULT_MODEL = HERE / "models" / "ml_a_geometry_rank_v1.json"
os.environ.setdefault("ML_A_MODEL_PATH", str(DEFAULT_MODEL))
os.environ.setdefault("ML_A_POLICY", "rank")

runner.EasyChartRE1NaturalBundle = EasyChartRE1SkilledContinuationBundle
EasyChartRE1MLAEnvStrategy._plan_event_values = staticmethod(plan_event_values)
runner.EasyChartRE1Strategy = EasyChartRE1MLAEnvStrategy
runner.add_symbol_mtf_data = add_symbol_mtf_flow_data


def _output_path(argv: list[str]) -> Path | None:
    try:
        return Path(argv[argv.index("--output") + 1])
    except (ValueError, IndexError):
        return None


def rewrite_metadata(output: Path) -> None:
    model = Path(os.environ["ML_A_MODEL_PATH"])
    values = {
        "candidate": "ML_a_skilled_continuation",
        "scenario_engine": "EasyChartRE1SkilledContinuationBundle",
        "ml_policy": os.environ.get("ML_A_POLICY", "rank"),
        "ml_model": str(model),
        "ml_role": "RANK_OR_DECLINE_COMPLETE_IMMUTABLE_PLANS_ONLY",
        "skilled_router_rule": LOCAL_AUCTION_SKILLED_ROUTER_RULE,
        "position_contract": "ONE_ACCOUNT_ONE_GLOBAL_POSITION_FIXED_3PCT_NAV_RISK",
    }
    for name in ("metrics.json", "run.json"):
        path = output / name
        if not path.exists():
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        record.update(values)
        path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    destination = _output_path(sys.argv)
    runner.main()
    if destination is not None:
        rewrite_metadata(destination)
