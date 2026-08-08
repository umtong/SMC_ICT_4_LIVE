#!/usr/bin/env python3
"""Run the frozen V12 diagnostic with robust closed-position mapping.

NautilusTrader can emit a valid closed position whose open and close timestamps
are identical. Pandas represents the empty duration field as NaN even though
realized PnL and closure timestamps are valid. The research classifier does not
use holding time to define state, so such rows are preserved with duration 0
instead of being discarded or crashing the causal join.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

import spot_perp_state_diagnostics as diagnostic


def _map_positions(
    lifecycle_path: Path,
    positions_path: Path,
) -> dict[str, dict[str, Any]]:
    lifecycle = diagnostic._load_json(lifecycle_path).get("events", [])
    fills = [item for item in lifecycle if item.get("type") == "GLOBAL_ENTRY_FILLED"]
    positions = pd.read_csv(positions_path)
    if positions.empty and not fills:
        return {}
    positions = positions.sort_values("ts_opened", kind="stable").reset_index(drop=True)
    if len(fills) != len(positions.index):
        raise RuntimeError(
            f"global fill/position mismatch for {positions_path}: "
            f"fills={len(fills)} positions={len(positions.index)}",
        )

    mapped: dict[str, dict[str, Any]] = {}
    for event, (_, position) in zip(fills, positions.iterrows(), strict=True):
        symbol = str(position["instrument_id"]).split("-PERP", 1)[0]
        if symbol != str(event.get("symbol")):
            raise RuntimeError(
                f"fill/position symbol mismatch: event={event.get('symbol')} position={symbol}",
            )
        duration_raw = position["duration_ns"]
        duration_ns = 0 if pd.isna(duration_raw) else int(float(duration_raw))
        mapped[str(event["scenario_id"])] = {
            "symbol": symbol,
            "pnl": float(diagnostic._decimal(position["realized_pnl"])),
            "duration_ns": duration_ns,
            "ts_opened": str(position["ts_opened"]),
            "ts_closed": str(position["ts_closed"]),
            "peak_qty": str(position["peak_qty"]),
        }
    return mapped


diagnostic._map_positions = _map_positions


if __name__ == "__main__":
    raise SystemExit(diagnostic.main())
