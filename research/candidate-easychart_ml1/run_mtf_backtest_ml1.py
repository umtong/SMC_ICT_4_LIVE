#!/usr/bin/env python3
"""Run EasyChart ML1 in one continuous four-symbol NautilusTrader account."""
from __future__ import annotations

import argparse
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

from candidate_bundle_ml1 import (  # noqa: E402
    EasyChartML1CandidateBundle,
    ML1_CANDIDATE_SELECTION_SEPARATION_RULE,
)
from execution_ml1 import (  # noqa: E402
    EasyChartML1Strategy,
    ML1RuntimeConfig,
    configure_ml1_runtime,
)
from ml1_features import FEATURE_NAMES  # noqa: E402
from ml1_model import PortableBinaryModel  # noqa: E402
import run_mtf_backtest_re1_flow as _flow_runner  # noqa: E402


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartML1CandidateBundle
_flow_runner._runner.EasyChartRE1Strategy = EasyChartML1Strategy


def _runtime_args(argv: list[str]) -> tuple[ML1RuntimeConfig, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--ml-mode", choices=("shadow", "select"), default="shadow")
    parser.add_argument(
        "--ml-model",
        type=Path,
        default=HERE / "models" / "bootstrap_shadow.json",
    )
    parser.add_argument("--ml-min-probability", type=float)
    parser.add_argument("--ml-probability-edge", type=float)
    parser.add_argument("--ml-min-expected-net-r", type=float)
    parser.add_argument("--ml-target-slippage-ticks", type=int, default=1)
    parser.add_argument("--ml-allow-shadow-model", action="store_true")
    known, remaining = parser.parse_known_args(argv[1:])
    runtime = ML1RuntimeConfig(
        mode=known.ml_mode,
        model_path=known.ml_model,
        min_probability=known.ml_min_probability,
        probability_edge=known.ml_probability_edge,
        min_expected_net_r=known.ml_min_expected_net_r,
        target_slippage_ticks=known.ml_target_slippage_ticks,
        allow_shadow_model=known.ml_allow_shadow_model,
    )
    return runtime, [argv[0], *remaining]


def _rewrite_metadata(output: Path, runtime: ML1RuntimeConfig) -> None:
    model = PortableBinaryModel.load(runtime.model_path)
    values = {
        "candidate": "candidate-easychart_ml1",
        "ml_mode": runtime.mode,
        "ml_model_path": str(runtime.model_path),
        "ml_model_id": model.model_id,
        "ml_model_status": model.status,
        "ml_feature_count": len(FEATURE_NAMES),
        "ml_min_probability_override": runtime.min_probability,
        "ml_probability_edge_override": runtime.probability_edge,
        "ml_min_expected_net_r_override": runtime.min_expected_net_r,
        "ml_target_slippage_ticks": runtime.target_slippage_ticks,
        "ml_policy": (
            "RE1_CAUSAL_MECHANISM_GENERATES_IMMUTABLE_PLAN; CALIBRATED_TARGET_BEFORE_STOP_"
            "PROBABILITY_AND_POST_COST_EXPECTED_R_SELECT_OR_ABSTAIN; ONE_GLOBAL_POSITION"
        ),
        "ml_candidate_policy": ML1_CANDIDATE_SELECTION_SEPARATION_RULE,
        "ml_no_symbol_identity": True,
        "ml_runtime_dependency": "PURE_PYTHON_JSON_MODEL; SCIKIT_LEARN_TRAINING_ONLY",
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
    configure_ml1_runtime(runtime)
    sys.argv[:] = remaining
    destination = _flow_runner._output_path(sys.argv)
    _flow_runner._runner.main()
    if destination is not None:
        _flow_runner._rewrite_metadata(destination)
        _rewrite_metadata(destination, runtime)


if __name__ == "__main__":
    main()
