#!/usr/bin/env python3
"""Insert the missing causal short/long volatility state for V31.

The V31 feature grammar already consumes ``short_vol`` and ``long_vol`` but the
cross-asset bar builder inherited from V29 only provides the longer common and
BTC volatility fields.  This patch computes a one-hour and twelve-hour BTC
realized-volatility ratio using returns shifted by one completed 3-minute bar,
so the current event bar cannot leak into its own state.
"""
from __future__ import annotations

import argparse
from pathlib import Path

OLD = '''    bars = aggregate_bars(minutes)\n    features = feature_frame(bars)\n'''
NEW = '''    bars = aggregate_bars(minutes)\n    btc_return_lag = bars.btcusdt_futures_return.shift(1)\n    bars["short_vol"] = btc_return_lag.rolling(20, min_periods=12).std()\n    bars["long_vol"] = btc_return_lag.rolling(240, min_periods=120).std()\n    features = feature_frame(bars)\n'''


def apply(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    if NEW in source:
        return False
    if OLD not in source:
        raise RuntimeError("V31 aggregate/feature handoff not found")
    path.write_text(source.replace(OLD, NEW, 1), encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "path",
        type=Path,
        nargs="?",
        default=Path("research/candidate-03/derive_nt_lvcfr_v31_signals.py"),
    )
    args = parser.parse_args()
    print(f"V31 causal volatility patch applied={apply(args.path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
