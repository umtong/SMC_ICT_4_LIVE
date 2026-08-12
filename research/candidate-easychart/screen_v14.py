#!/usr/bin/env python3
"""Integrate source-shaped Trap, peer-state routing and first objective.

Earlier diagnostics were version-fragmented: v10 routed generic delayed
reclaims cross-sectionally, v12 required the source-stated W/M Trap shape, and
v13 replaced a far session target with a nearer confirmed directional pivot.
This wrapper composes those roles into one diagnostic without adding another
threshold or confirmation.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

import screen_v10 as _base
from market_v12 import EasyChartWMTrapEngine


# The source interaction is selected before importing v13, whose router wraps
# the existing v10 cross-sectional policy with the first-objective policy.
_base.EasyChartSessionTrapEngine = EasyChartWMTrapEngine
import screen_v13 as _target_router  # noqa: E402

_ORIGINAL_RUN = _base.run


def run(args):
    metrics = _ORIGINAL_RUN(args)
    output = Path(args.output).resolve()
    metrics["candidate"] = "candidate-easychart-v14"
    metrics["semantic_integration"] = {
        "interaction": "SOURCE_SHAPED_WM_TRAP",
        "state_router": "ADAPTED_CROSS_SECTIONAL_ISOLATED_OR_BROAD",
        "target": "FIRST_PRECONFIRMED_DC_PIVOT_OR_DECLARED_FAR_FALLBACK",
    }
    (output / "metrics.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    audit_rows = list(_target_router.LAST_TARGET_AUDIT_ROWS)
    pd.DataFrame(audit_rows).to_csv(output / "target_router_audit.csv", index=False)
    (output / "target_router_audit.jsonl").write_text(
        "".join(
            json.dumps(row, sort_keys=True, default=str) + "\n"
            for row in audit_rows
        ),
        encoding="utf-8",
    )
    run_document = json.loads((output / "run.json").read_text(encoding="utf-8"))
    run_document["candidate"] = "candidate-easychart-v14"
    run_document["engine"] = "FAST_DIAGNOSTIC_NOT_AUTHORITATIVE_V14"
    run_document["composition"] = [
        "market_v12.EasyChartWMTrapEngine",
        "screen_v10.cross_sectional_router",
        "screen_v13.first_directional_objective",
    ]
    run_document["target_router_audit_rows"] = len(audit_rows)
    (output / "run.json").write_text(
        json.dumps(run_document, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    return metrics


_base.run = run


if __name__ == "__main__":
    _base.main()
