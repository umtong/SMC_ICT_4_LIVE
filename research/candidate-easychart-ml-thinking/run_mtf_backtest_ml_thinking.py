#!/usr/bin/env python3
"""Run EasyChart causal candidate generation with the ML-thinking router."""
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
    except ValueError as exc:
        raise SystemExit(f"{name} is required") from exc
    try:
        value = argv[index + 1]
    except IndexError as exc:
        raise SystemExit(f"{name} requires a value") from exc
    del argv[index : index + 2]
    return value


model_path = Path(_pop_option(sys.argv, "--ml-model")).resolve()
os.environ["EASYCHART_ML_MODEL_PATH"] = str(model_path)

import run_mtf_backtest_re1 as _runner  # noqa: E402
from easychart_re1_flow import EasyChartRE1FlowBundle  # noqa: E402
from execution_ml_thinking import EasyChartMLThinkingStrategy  # noqa: E402
from mtf_data_re1_flow import add_symbol_mtf_flow_data  # noqa: E402

_runner.EasyChartRE1NaturalBundle = EasyChartRE1FlowBundle
_runner.EasyChartRE1Strategy = EasyChartMLThinkingStrategy
_runner.add_symbol_mtf_data = add_symbol_mtf_flow_data


def _output_path(argv: list[str]) -> Path | None:
    try:
        return Path(argv[argv.index("--output") + 1])
    except (ValueError, IndexError):
        return None


def _rewrite_metadata(output: Path) -> None:
    metadata = {
        "candidate": "candidate-easychart-ml-thinking",
        "ml_model_path": str(model_path),
        "ml_policy": (
            "deterministic causal EasyChart plans -> target-before-stop probability -> "
            "plan-specific post-cost expected R -> select highest positive EV"
        ),
        "ml_risk_policy": "unchanged fixed 3% stop-risk sizing; no partial entry or exit",
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
