#!/usr/bin/env python3
"""Harvest every ML3v3 opportunity in one four-symbol causal shadow account."""
from __future__ import annotations

import json
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parent
for candidate in (
    HERE,
    RESEARCH / "candidate-easychart_ml3v2",
    RESEARCH / "candidate-easychart_re1",
    RESEARCH / "candidate-easychart-v5",
    RESEARCH / "candidate-easychart-v3",
    RESEARCH / "candidate-easychart-v2",
):
    text = str(candidate)
    if text not in sys.path:
        sys.path.insert(0, text)

from execution_ml1 import ML1RuntimeConfig, configure_ml1_runtime  # noqa: E402
from execution_shadow_ml3v3 import EasyChartML3V3ShadowStrategy  # noqa: E402
from opportunity_union import (  # noqa: E402
    ACCOUNT_BUCKET_EPISODE_RULE,
    EXACT_GEOMETRY_DEDUPLICATION_RULE,
    OPPORTUNITY_UNION_RULE,
    EasyChartML3V3OpportunityUnion,
)
import run_mtf_backtest_re1_flow as _flow_runner  # noqa: E402


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartML3V3OpportunityUnion
_flow_runner._runner.EasyChartRE1Strategy = EasyChartML3V3ShadowStrategy


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_ml3v3_shadow_union",
        "research_role": "CAUSAL_OPPORTUNITY_HARVEST_NOT_A_PERFORMANCE_BASELINE",
        "opportunity_policy": OPPORTUNITY_UNION_RULE,
        "exact_geometry_policy": EXACT_GEOMETRY_DEDUPLICATION_RULE,
        "account_bucket_episode_policy": ACCOUNT_BUCKET_EPISODE_RULE,
        "selector": "NONSELECTIVE_SHADOW_FEATURE_HARVEST",
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
    bootstrap = RESEARCH / "candidate-easychart_ml3v2" / "models" / "bootstrap_shadow.json"
    configure_ml1_runtime(ML1RuntimeConfig(mode="shadow", model_path=bootstrap))
    destination = _flow_runner._output_path(sys.argv)
    _flow_runner._runner.main()
    if destination is not None:
        _flow_runner._rewrite_metadata(destination)
        _rewrite_metadata(destination)


if __name__ == "__main__":
    main()
