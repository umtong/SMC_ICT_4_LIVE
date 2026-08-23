#!/usr/bin/env python3
"""Run local, daily and complete H4 auctions in one account."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from easychart_re1_skilled_h4_auction import EasyChartRE1SkilledH4AuctionBundle
import run_mtf_backtest_re1_flow as _flow_runner


_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1SkilledH4AuctionBundle


def _rewrite_metadata(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_re1_skilled_h4_auction",
        "policy": (
            "RESPONSE_CONFIRMED_LOCAL_AND_PREVIOUS_DAY_AUCTIONS_PLUS_COMPLETE_"
            "H4_REJECTION_AND_ACCEPTANCE_AUCTIONS"
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


if __name__ == "__main__":
    destination = _flow_runner._output_path(sys.argv)
    _flow_runner._runner.main()
    if destination is not None:
        _flow_runner._rewrite_metadata(destination)
        _rewrite_metadata(destination)
