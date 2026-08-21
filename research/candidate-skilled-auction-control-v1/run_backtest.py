#!/usr/bin/env python3
"""Run skilled auction control v1 in the existing continuous Nautilus account."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import run_mtf_backtest_re1_flow as _flow
from skilled_auction_control_v1 import (
    CAUSAL_EPISODE_OWNER_RULE,
    CHANNEL_ACCEPTANCE_OWNER_RULE,
    CHANNEL_REJECTION_OWNER_RULE,
    SkilledAuctionControlV1Bundle,
)


_flow._runner.EasyChartRE1NaturalBundle = SkilledAuctionControlV1Bundle


def _rewrite(output: Path) -> None:
    metadata = {
        "candidate": "candidate-skilled-auction-control-v1",
        "decision_policy": (
            "channel liquidity sweep/reclaim -> next 5m hold -> first later 1m price-volume response; "
            "or accepted channel control transfer -> next 5m outside hold -> first detached return -> "
            "active liquidity-delivery alignment; immutable full-position plan"
        ),
        "mechanism_owners": [
            "CHANNEL_REJECTION",
            "CHANNEL_ACCEPTANCE",
        ],
        "rule_provenance": [
            CHANNEL_REJECTION_OWNER_RULE,
            CHANNEL_ACCEPTANCE_OWNER_RULE,
            CAUSAL_EPISODE_OWNER_RULE,
        ],
    }
    _flow._rewrite_metadata(output)
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
