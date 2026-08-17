#!/usr/bin/env python3
"""Run the EasyChart RE1 ML3 system in one continuous Nautilus account."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from easychart_re1_complete_bot_policy import COMPLETE_OPPORTUNITY_ROUTER_RULE
from easychart_re1_complete_bot_policy_v2 import UNIFIED_CONTINUATION_CONTEXT_RULE
from easychart_re1_ml3_base_policy import (
    LOCAL_ENGINE_TIMEFRAME_POLICY,
    EasyChartRE1ML3BasePolicyBundle,
)
from execution_re1_ml3 import (
    EasyChartRE1ML3Strategy,
    ML3_MODEL_ENV,
    ML3_ROUTING_POLICY,
)
from ml3_meta_model import ML3MetaModel
from ml3_online_features import FEATURE_SCHEMA_VERSION
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1ML3BasePolicyBundle
_flow_runner._runner.EasyChartRE1Strategy = EasyChartRE1ML3Strategy


def _consume_model_argument(argv: list[str]) -> Path:
    try:
        index = argv.index("--ml3-model")
        raw = argv[index + 1]
    except (ValueError, IndexError) as exc:
        raise SystemExit("--ml3-model PATH is required; ML3 has no deterministic fallback") from exc
    del argv[index : index + 2]
    path = Path(raw).expanduser().resolve()
    model = ML3MetaModel.load(path)
    os.environ[ML3_MODEL_ENV] = str(path)
    _consume_model_argument.model = model
    return path


_consume_model_argument.model = None  # type: ignore[attr-defined]


def _rewrite_metadata(output: Path, model_path: Path, model: ML3MetaModel) -> None:
    values = {
        "candidate": "candidate-easychart_re1_ml3",
        "policy": "COMPLETE_EASYCHART_GEOMETRY_WITH_CAUSAL_TARGET_BEFORE_STOP_META_LABEL",
        "ml3_routing_policy": ML3_ROUTING_POLICY,
        "ml3_model_path": str(model_path),
        "ml3_model_sha256": model.sha256,
        "ml3_feature_schema_version": FEATURE_SCHEMA_VERSION,
        "complete_policy_rule": COMPLETE_OPPORTUNITY_ROUTER_RULE,
        "unified_continuation_context_rule": UNIFIED_CONTINUATION_CONTEXT_RULE,
        "local_engine_timeframe_policy": LOCAL_ENGINE_TIMEFRAME_POLICY,
        "ml3_geometry_contract": "MODEL_DOES_NOT_CHANGE_ENTRY_STOP_TARGET_RISK_SIZE_OR_POSITION_MANAGEMENT",
        "ml3_failure_contract": "MISSING_CORRUPT_INCOMPATIBLE_MODEL_ABORTS;MISSING_CAUSAL_FEATURE_REJECTS_PLAN",
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
    model_path = _consume_model_argument(sys.argv)
    model = _consume_model_argument.model
    assert isinstance(model, ML3MetaModel)
    destination = _flow_runner._output_path(sys.argv)
    _flow_runner._runner.main()
    if destination is not None:
        _flow_runner._rewrite_metadata(destination)
        _rewrite_metadata(destination, model_path, model)


if __name__ == "__main__":
    main()
