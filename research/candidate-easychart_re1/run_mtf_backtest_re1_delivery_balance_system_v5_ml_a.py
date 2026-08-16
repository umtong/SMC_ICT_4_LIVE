#!/usr/bin/env python3
"""Run delivery/balance v5 with a frozen portable ML_a plan scorer."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from execution_re1_ml_a import EasyChartRE1MLAEnvStrategy
import run_mtf_backtest_re1_delivery_balance_system_v5 as _v5


def _pop(option: str, default: str | None = None) -> str:
    try:
        index = sys.argv.index(option)
    except ValueError:
        if default is None:
            raise SystemExit(f"missing {option}")
        return default
    try:
        value = sys.argv[index + 1]
    except IndexError as exc:
        raise SystemExit(f"missing value for {option}") from exc
    del sys.argv[index:index + 2]
    return value


def rewrite_metadata(output: Path, *, model: str, policy: str, probability: float) -> None:
    values = {
        "candidate": "candidate-easychart_re1_ml_a",
        "ml_a_model_path": model,
        "ml_a_policy": policy,
        "ml_a_minimum_probability": probability,
        "ml_a_scope": "RANK_OR_DECLINE_COMPLETE_IMMUTABLE_PLANS_ONLY",
        "ml_a_fixed_plan_contract": "ENTRY_STOP_TARGET_AND_3_PERCENT_RISK_UNCHANGED",
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
    model = _pop("--model")
    policy = _pop("--ml-policy", "quality")
    minimum_probability = float(_pop("--minimum-probability", "0.60"))
    destination = _v5.flow_runner._output_path(sys.argv)
    os.environ["ML_A_MODEL_PATH"] = model
    os.environ["ML_A_POLICY"] = policy
    os.environ["ML_A_MIN_PROBABILITY"] = str(minimum_probability)
    _v5.flow_runner._runner.EasyChartRE1Strategy = EasyChartRE1MLAEnvStrategy
    _v5.flow_runner._runner.main()
    if destination is not None:
        _v5.flow_runner._rewrite_metadata(destination)
        _v5.rewrite_metadata(destination)
        rewrite_metadata(
            destination,
            model=str(Path(model).name),
            policy=policy,
            probability=minimum_probability,
        )
