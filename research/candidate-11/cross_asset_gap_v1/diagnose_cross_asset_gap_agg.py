#!/usr/bin/env python3
"""Run the frozen cross-asset-gap detector on verified aggTrades 1s bars.

The detector and all thresholds remain in :mod:`diagnose_cross_asset_gap`.
This adapter only replaces its unavailable historical one-second-kline loader
with Candidate 11's already-tested Binance aggTrades -> causally completed
one-second bar loader from :mod:`run_microstructure_nautilus`.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
CANDIDATE_ROOT = HERE.parent
if str(CANDIDATE_ROOT) not in sys.path:
    sys.path.insert(0, str(CANDIDATE_ROOT))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import diagnose_cross_asset_gap as detector  # noqa: E402
import run_microstructure_nautilus as agg_source  # noqa: E402


def load_symbol_from_aggtrades(
    symbol: str,
    start: Any,
    end_inclusive: Any,
    data_dir: Path,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Return the exact columns consumed by the frozen detector.

    The inherited loader is sequentially invoked, so rebinding its historical
    module-level symbol is deterministic and cannot cross-contaminate symbols.
    Signed quote notional reconstructs taker-buy quote exactly:
    ``buy_quote = (total_quote + signed_quote) / 2``.
    """
    agg_source.SYMBOL = symbol
    frame, records = agg_source.load_one_second_bars(
        start,
        end_inclusive,
        data_dir / symbol,
    )
    result = frame.copy()
    result["quote_volume"] = result["quote_notional"]
    result["taker_buy_quote"] = (
        (result["quote_notional"] + result["signed_notional"]) / 2.0
    ).clip(lower=0.0)
    result["taker_buy_quote"] = np.minimum(
        result["taker_buy_quote"],
        result["quote_volume"],
    )
    result = result[
        [
            "open",
            "high",
            "low",
            "close",
            "volume",
            "quote_volume",
            "trade_count",
            "taker_buy_quote",
        ]
    ]
    for record in records:
        record["dataset"] = "Binance USD-M daily aggregate trades"
        record["causal_aggregation"] = "trade timestamp floored to second plus one second"
    return result, records


def write_json(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--interval", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    detector.load_symbol = load_symbol_from_aggtrades
    output = args.output.resolve()
    detector.diagnose(
        protocol_path=args.protocol.resolve(),
        interval=args.interval,
        output_dir=output,
    )
    manifest_path = output / "data_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "schema": "candidate-11-cross-asset-gap-aggtrades-data-v1",
            "dataset": "Binance USD-M daily aggregate trades",
            "bar_visibility": "aggregate-trade timestamp floored to second plus one second",
            "loader_source": "research/candidate-11/run_microstructure_nautilus.py",
            "detector_source": "diagnose_cross_asset_gap.py",
        }
    )
    write_json(manifest_path, manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
