#!/usr/bin/env python3
"""Execute structural auction control v4 in the existing continuous account."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import run_mtf_backtest_re1_flow as _flow
from structural_auction_control_v4 import StructuralAuctionControlV4Bundle


# Reuse the proven flow-data, strategy, execution, fee and continuous-account
# harness.  Replace only the decision bundle before invoking its real runner.
_flow._runner.EasyChartRE1NaturalBundle = StructuralAuctionControlV4Bundle


def _rewrite(output: Path) -> None:
    _flow._rewrite_metadata(output)
    metadata = {
        "candidate": "candidate-structural-auction-control-v4",
        "decision_policy": (
            "public structure lifecycle -> completed price-volume response -> "
            "non-synthetic structural stop and first causal destination"
        ),
        "mechanism_owners": [
            "CHANNEL_ACCEPTANCE",
            "CHANNEL_REJECTION",
            "CHANNEL_FAILURE",
            "TRENDLINE_ACCEPTANCE",
            "TRENDLINE_REJECTION",
            "CHANNEL_BOUNCE",
            "TRENDLINE_BOUNCE",
        ],
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
    destination = _flow._output_path(sys.argv)
    _flow._runner.main()
    if destination is not None:
        _rewrite(destination)
