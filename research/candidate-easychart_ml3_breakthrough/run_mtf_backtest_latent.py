#!/usr/bin/env python3
"""Run latent-state integrated auction logic in one four-symbol account."""
from __future__ import annotations

from pathlib import Path
import json
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

from latent_auction import LatentAuctionBundle  # noqa: E402
import run_mtf_backtest_integrated as _integrated_runner  # noqa: E402


_integrated_runner._flow_runner._runner.EasyChartRE1NaturalBundle = LatentAuctionBundle


def _rewrite(output: Path) -> None:
    values = {
        "candidate": "candidate-easychart_ml3_latent_auction",
        "direction_policy": (
            "SEPARATE_LIQUIDITY_DRAW_TREND_PERSISTENCE_AUCTION_LOCATION_AND_"
            "CONTROL_TRANSFER_WITH_EVENT_SPECIFIC_NONLINEAR_ROUTING"
        ),
        "market_policy": (
            "PRICE_VOLUME_TO_DIRECTION_LIQUIDITY_STRUCTURE_TO_EVENT_TO_"
            "OB_FVG_RETEST_ENTRY_TO_OPPOSING_LIQUIDITY_EXIT"
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


def main() -> None:
    destination = _integrated_runner._flow_runner._output_path(sys.argv)
    _integrated_runner._flow_runner._runner.main()
    if destination is not None:
        _integrated_runner._flow_runner._rewrite_metadata(destination)
        _rewrite(destination)


if __name__ == "__main__":
    main()
