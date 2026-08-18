#!/usr/bin/env python3
"""Run integrated hierarchical plans with period-robust learned arbitration."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parent
for candidate in (
    HERE,
    RESEARCH / "candidate-easychart_ml3v3",
    RESEARCH / "candidate-easychart_ml3v2",
    RESEARCH / "candidate-easychart_re1",
    RESEARCH / "candidate-easychart-v5",
    RESEARCH / "candidate-easychart-v3",
    RESEARCH / "candidate-easychart-v2",
):
    text = str(candidate)
    if text not in sys.path:
        sys.path.insert(0, text)

from execution_integrated_ml import (  # noqa: E402
    EasyChartIntegratedMLStrategy,
    IntegratedMLRuntimeConfig,
    configure_integrated_ml_runtime,
)
from integrated_auction import IntegratedAuctionBundle  # noqa: E402
from integrated_ensemble import IntegratedPeriodEnsemble  # noqa: E402
import run_mtf_backtest_re1_flow as _flow_runner  # noqa: E402


_flow_runner._runner.EasyChartRE1NaturalBundle = IntegratedAuctionBundle
_flow_runner._runner.EasyChartRE1Strategy = EasyChartIntegratedMLStrategy


def _runtime_args(argv: list[str]) -> tuple[Path, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--integrated-model", type=Path, required=True)
    known, remaining = parser.parse_known_args(argv[1:])
    return known.integrated_model, [argv[0], *remaining]


def _rewrite_metadata(output: Path, model_path: Path) -> None:
    ensemble = IntegratedPeriodEnsemble.load(model_path)
    values = {
        "candidate": "candidate-easychart_ml3_breakthrough_integrated_ml",
        "ensemble_id": ensemble.ensemble_id,
        "ensemble_path": str(model_path),
        "ensemble_members": len(ensemble.members),
        "ensemble_calibration_windows": list(ensemble.member_windows),
        "market_policy": (
            "PRICE_VOLUME_TO_DIRECTION_LIQUIDITY_STRUCTURE_TO_EVENT_TO_"
            "OB_FVG_RETEST_ENTRY_TO_OPPOSING_LIQUIDITY_EXIT"
        ),
        "learned_role": "COMPARE_ONLY_COMPLETE_IMMUTABLE_CAUSAL_PLANS",
        "decision_policy": (
            "LOWER_PERIOD_QUANTILE_TARGET_FIRST_PROBABILITY_THEN_POSITIVE_"
            "AFTER_COST_FIXED_RISK_EXPECTED_LOG_GROWTH"
        ),
        "account_arbitration": "MAX_EXPECTED_LOG_GROWTH_PER_EXPECTED_OCCUPANCY_HOUR",
        "risk_policy": "FIXED_APPROX_3_PERCENT_NAV_LOSS_AT_PRE_ENTRY_INVALIDATION",
        "position_policy": "ONE_GLOBAL_FULL_POSITION_NO_PARTIAL_NO_STOP_RATCHET",
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


def main() -> None:
    model_path, remaining = _runtime_args(sys.argv)
    model_path = model_path.expanduser().resolve()
    configure_integrated_ml_runtime(
        IntegratedMLRuntimeConfig(model_path=model_path)
    )
    sys.argv[:] = remaining
    destination = _flow_runner._output_path(sys.argv)
    _flow_runner._runner.main()
    if destination is not None:
        _flow_runner._rewrite_metadata(destination)
        _rewrite_metadata(destination, model_path)


if __name__ == "__main__":
    main()
