#!/usr/bin/env python3
"""Generate the broad mechanism-owned plan universe in one continuous account."""
from __future__ import annotations

import json
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

import run_mtf_backtest_re1 as _runner  # noqa: E402
from execution_re1_flow import EasyChartRE1FlowStrategy  # noqa: E402
from mtf_data_re1_flow import add_symbol_mtf_flow_data  # noqa: E402
from opportunity_universe import (  # noqa: E402
    EasyChartMLOpportunityUniverse,
    OPPORTUNITY_UNIVERSE_POLICY,
)

_runner.EasyChartRE1NaturalBundle = EasyChartMLOpportunityUniverse
_runner.EasyChartRE1Strategy = EasyChartRE1FlowStrategy
_runner.add_symbol_mtf_data = add_symbol_mtf_flow_data


def _output_path(argv: list[str]) -> Path | None:
    try:
        return Path(argv[argv.index("--output") + 1])
    except (ValueError, IndexError):
        return None


def _rewrite_metadata(output: Path) -> None:
    metadata = {
        "candidate": "candidate-easychart-ml-system-opportunity-universe",
        "opportunity_universe_policy": OPPORTUNITY_UNIVERSE_POLICY,
        "purpose": (
            "RESEARCH_PLAN_HARVEST_ONLY_ALL_COMPLETE_CAUSAL_PLANS_ARE_RECORDED_BEFORE_"
            "GLOBAL_SLOT_ARBITRATION"
        ),
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
