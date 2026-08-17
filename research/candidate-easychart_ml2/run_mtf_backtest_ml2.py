#!/usr/bin/env python3
"""Run EasyChart ML2 in one continuous four-symbol NautilusTrader account."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parent
ML1 = RESEARCH / "candidate-easychart_ml1"
for candidate in (
    HERE,
    ML1,
    RESEARCH / "candidate-easychart_re1",
    RESEARCH / "candidate-easychart-v5",
    RESEARCH / "candidate-easychart-v4",
    RESEARCH / "candidate-easychart-v3",
):
    text = str(candidate)
    if text not in sys.path:
        sys.path.insert(0, text)

from candidate_bundle_ml2 import (  # noqa: E402
    EasyChartML2CandidateBundle,
    ML2_DIAGONAL_TIMEFRAME_CONTRACT_RULE,
)
from execution_ml1 import ML1RuntimeConfig, configure_ml1_runtime  # noqa: E402
from execution_ml2 import (  # noqa: E402
    EasyChartML2Strategy,
    ML2RuntimeConfig,
    configure_ml2_runtime,
)
from ml1_features import FEATURE_NAMES  # noqa: E402
from ml1_model import PortableBinaryModel  # noqa: E402
from ml2_model import CatBoostProbabilityModel  # noqa: E402
import run_mtf_backtest_re1_flow as _flow_runner  # noqa: E402


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartML2CandidateBundle
_flow_runner._runner.EasyChartRE1Strategy = EasyChartML2Strategy


def _runtime_args(argv: list[str]) -> tuple[ML2RuntimeConfig, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--ml-mode", choices=("shadow", "select"), default="shadow")
    parser.add_argument("--ml-model", type=Path, default=HERE / "models" / "untrained.json")
    known, remaining = parser.parse_known_args(argv[1:])
    return (
        ML2RuntimeConfig(mode=known.ml_mode, model_metadata=known.ml_model),
        [argv[0], *remaining],
    )


def _model_identity(runtime: ML2RuntimeConfig) -> tuple[str, str]:
    if runtime.mode == "select":
        model = CatBoostProbabilityModel(runtime.model_metadata)
        return model.model_id, model.status
    shadow = PortableBinaryModel.load(ML1 / "models" / "bootstrap_shadow.json")
    return shadow.model_id, shadow.status


def _rewrite_metadata(output: Path, runtime: ML2RuntimeConfig) -> None:
    model_id, model_status = _model_identity(runtime)
    values = {
        "candidate": "candidate-easychart_ml2",
        "ml_mode": runtime.mode,
        "ml_model_metadata": str(runtime.model_metadata),
        "ml_model_id": model_id,
        "ml_model_status": model_status,
        "ml_feature_count": len(FEATURE_NAMES),
        "ml_policy": (
            "RE1_CAUSAL_MECHANISM_GENERATES_IMMUTABLE_RR_GE_1_PLAN; "
            "CATBOOST_ESTIMATES_CALIBRATED_TARGET_BEFORE_STOP_PROBABILITY; "
            "POSITIVE_EXPECTED_LOG_NAV_GROWTH_AT_FIXED_3_PERCENT_RISK_SELECTS; "
            "SIMULTANEOUS_CANDIDATES_RANK_BY_EXPECTED_LOG_GROWTH; "
            "NO_CONFIDENCE_SIZING_OR_ADDITIONAL_RISK_LIMIT"
        ),
        "ml_no_symbol_identity": True,
        "ml_runtime_dependency": "CATBOOST_OFFICIAL_PYTHON_PACKAGE",
        "risk_policy": "INHERITED_FIXED_APPROX_3_PERCENT_NAV_LOSS_AT_FROZEN_STOP",
        "implementation_repair": ML2_DIAGONAL_TIMEFRAME_CONTRACT_RULE,
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
    runtime, remaining = _runtime_args(sys.argv)
    configure_ml1_runtime(
        ML1RuntimeConfig(
            mode="shadow",
            model_path=ML1 / "models" / "bootstrap_shadow.json",
        )
    )
    configure_ml2_runtime(runtime)
    sys.argv[:] = remaining
    destination = _flow_runner._output_path(sys.argv)
    _flow_runner._runner.main()
    if destination is not None:
        _flow_runner._rewrite_metadata(destination)
        _rewrite_metadata(destination, runtime)


if __name__ == "__main__":
    main()
