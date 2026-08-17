#!/usr/bin/env python3
"""Run EasyChart ML2 in one continuous four-symbol NautilusTrader account."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parent
for candidate in (
    HERE,
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
    ML2_CANDIDATE_SELECTION_SEPARATION_RULE,
    ML2_DIAGONAL_TIMEFRAME_CONTRACT_RULE,
    ML2_PREPLAN_CONTEXT_OBSERVATION_RULE,
)
from execution_ml2 import (  # noqa: E402
    EasyChartML2Strategy,
    ML2RuntimeConfig,
    configure_ml2_runtime,
)
from ml2_features import CAUSAL_FAMILIES, FEATURE_NAMES  # noqa: E402
from ml2_model import CatBoostProbabilityModel  # noqa: E402
import run_mtf_backtest_re1_flow as _flow_runner  # noqa: E402


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartML2CandidateBundle
_flow_runner._runner.EasyChartRE1Strategy = EasyChartML2Strategy


def _feature_schema_id() -> str:
    payload = "\n".join(FEATURE_NAMES).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _runtime_args(argv: list[str]) -> tuple[ML2RuntimeConfig, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--ml-mode", choices=("shadow", "select"), default="shadow")
    parser.add_argument(
        "--ml-model",
        type=Path,
        default=HERE / "models" / "untrained.json",
        help="CatBoost metadata JSON; ignored in shadow mode.",
    )
    known, remaining = parser.parse_known_args(argv[1:])
    runtime = ML2RuntimeConfig(
        mode=known.ml_mode,
        model_metadata=known.ml_model,
    )
    return runtime, [argv[0], *remaining]


def _model_identity(runtime: ML2RuntimeConfig) -> tuple[str, str, str | None]:
    if runtime.mode == "shadow":
        return "shadow-no-model-v2", "shadow_only", None
    model = CatBoostProbabilityModel(runtime.model_metadata)
    model.assert_selectable()
    return model.model_id, model.status, str(model.model_path)


def _rewrite_metadata(output: Path, runtime: ML2RuntimeConfig) -> None:
    model_id, model_status, model_file = _model_identity(runtime)
    values = {
        "candidate": "candidate-easychart_ml2",
        "ml_mode": runtime.mode,
        "ml_model_metadata": None if runtime.mode == "shadow" else str(runtime.model_metadata),
        "ml_model_file": model_file,
        "ml_model_id": model_id,
        "ml_model_status": model_status,
        "ml_feature_count": len(FEATURE_NAMES),
        "ml_feature_schema_id": _feature_schema_id(),
        "ml_causal_families": list(CAUSAL_FAMILIES),
        "ml_policy": (
            "DETERMINISTIC_CAUSAL_SCENARIO_FREEZES_ENTRY_STOP_TARGET_AND_RR_GE_1; "
            "CATBOOST_ESTIMATES_CALIBRATED_TARGET_BEFORE_STOP_PROBABILITY; "
            "POSITIVE_EXPECTED_LOG_NAV_GROWTH_AT_FIXED_3_PERCENT_RISK_SELECTS; "
            "SIMULTANEOUS_CANDIDATES_RANK_BY_EXPECTED_LOG_GROWTH; "
            "NO_CONFIDENCE_SIZING_NO_STOP_OR_TARGET_CHANGE_NO_EXTRA_RISK_LIMIT"
        ),
        "ml_training_target": (
            "TARGET_BEFORE_STOP_LOGLOSS; NO_TARGET_WIN_RATE_TRADE_FREQUENCY_OR_"
            "USER_EXAMPLE_OBJECTIVE"
        ),
        "ml_candidate_policy": ML2_CANDIDATE_SELECTION_SEPARATION_RULE,
        "ml_preplan_context_policy": ML2_PREPLAN_CONTEXT_OBSERVATION_RULE,
        "ml_diagonal_timeframe_contract": ML2_DIAGONAL_TIMEFRAME_CONTRACT_RULE,
        "ml_no_symbol_identity": True,
        "ml_runtime_dependency": (
            "CATBOOST_OFFICIAL_PYTHON_PACKAGE_IN_SELECT_MODE; NONE_IN_SHADOW_MODE"
        ),
        "risk_policy": "INHERITED_FIXED_APPROX_3_PERCENT_NAV_LOSS_AT_FROZEN_STOP",
        "account_policy": (
            "ONE_CONTINUOUS_ACCOUNT_ONE_GLOBAL_POSITION_FULL_ENTRY_FULL_EXIT_"
            "PREENTRY_STOP_AND_TARGET"
        ),
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
    configure_ml2_runtime(runtime)
    sys.argv[:] = remaining
    destination = _flow_runner._output_path(sys.argv)
    _flow_runner._runner.main()
    if destination is not None:
        _flow_runner._rewrite_metadata(destination)
        _rewrite_metadata(destination, runtime)


if __name__ == "__main__":
    main()
