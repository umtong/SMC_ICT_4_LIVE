#!/usr/bin/env python3
"""Run the intrinsic-auction generator with period-robust ML3v3 arbitration."""
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

from execution_ml3v3 import (  # noqa: E402
    EasyChartML3V3Strategy,
    ML3V3RuntimeConfig,
    configure_ml3v3_runtime,
)
from intrinsic_auction import IntrinsicAuctionBundle  # noqa: E402
from robust_ensemble import PeriodRobustEnsemble  # noqa: E402
import run_mtf_backtest_re1_flow as _flow_runner  # noqa: E402

_flow_runner._runner.EasyChartRE1NaturalBundle = IntrinsicAuctionBundle
_flow_runner._runner.EasyChartRE1Strategy = EasyChartML3V3Strategy


def _runtime_args(argv: list[str]) -> tuple[Path, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--ml3v3-model", type=Path, required=True)
    known, remaining = parser.parse_known_args(argv[1:])
    return known.ml3v3_model, [argv[0], *remaining]


def _rewrite(output: Path, model_path: Path) -> None:
    ensemble = PeriodRobustEnsemble.load(model_path)
    values = {
        "candidate": "candidate-easychart_ml3_breakthrough_ml3v3",
        "hypothesis_generator": "ACTIVE_INTRINSIC_AUCTION_MAP_FIRST_MITIGATION",
        "selector": "PERIOD_ROBUST_AFTER_COST_EXPECTED_LOG_GROWTH",
        "ensemble_id": ensemble.ensemble_id,
        "ensemble_path": str(model_path),
        "ensemble_members": len(ensemble.members),
        "prior_policy_role": "NONE",
        "position_policy": "ONE_GLOBAL_FULL_POSITION_NO_PARTIAL_NO_STOP_RATCHET",
        "risk_policy": "FIXED_APPROX_3_PERCENT_NAV_LOSS_AT_PRE_ENTRY_INVALIDATION",
    }
    for name in ("metrics.json", "run.json"):
        path = output / name
        if not path.exists():
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        record.update(values)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def main() -> None:
    model_path, remaining = _runtime_args(sys.argv)
    model_path = model_path.expanduser().resolve()
    configure_ml3v3_runtime(ML3V3RuntimeConfig(model_path=model_path))
    sys.argv[:] = remaining
    destination = _flow_runner._output_path(sys.argv)
    _flow_runner._runner.main()
    if destination is not None:
        _flow_runner._rewrite_metadata(destination)
        _rewrite(destination, model_path)


if __name__ == "__main__":
    main()
