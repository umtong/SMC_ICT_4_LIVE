#!/usr/bin/env python3
"""Run latent auction plans with period-robust learned arbitration."""
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

from execution_latent_ml import (  # noqa: E402
    EasyChartLatentMLStrategy,
    IntegratedMLRuntimeConfig,
    configure_integrated_ml_runtime,
)
from integrated_ensemble import IntegratedPeriodEnsemble  # noqa: E402
from latent_auction import LatentAuctionBundle  # noqa: E402
import run_mtf_backtest_re1_flow as _flow_runner  # noqa: E402


_flow_runner._runner.EasyChartRE1NaturalBundle = LatentAuctionBundle
_flow_runner._runner.EasyChartRE1Strategy = EasyChartLatentMLStrategy


def _runtime_args(argv: list[str]) -> tuple[Path, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--latent-model", type=Path, required=True)
    known, remaining = parser.parse_known_args(argv[1:])
    return known.latent_model, [argv[0], *remaining]


def _rewrite_metadata(output: Path, model_path: Path) -> None:
    ensemble = IntegratedPeriodEnsemble.load(model_path)
    values = {
        "candidate": "candidate-easychart_ml3_latent_ml",
        "ensemble_id": ensemble.ensemble_id,
        "ensemble_path": str(model_path),
        "ensemble_members": len(ensemble.members),
        "direction_policy": (
            "SEPARATE_LIQUIDITY_DRAW_TREND_PERSISTENCE_AUCTION_LOCATION_AND_"
            "CONTROL_TRANSFER_WITH_EVENT_SPECIFIC_NONLINEAR_ROUTING"
        ),
        "learned_role": "COMPARE_ONLY_COMPLETE_IMMUTABLE_LATENT_AUCTION_PLANS",
        "decision_policy": (
            "LOWER_PERIOD_QUANTILE_TARGET_FIRST_PROBABILITY_THEN_POSITIVE_"
            "AFTER_COST_FIXED_RISK_EXPECTED_LOG_GROWTH"
        ),
        "account_arbitration": "MAX_EXPECTED_LOG_GROWTH_PER_EXPECTED_OCCUPANCY_HOUR",
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
