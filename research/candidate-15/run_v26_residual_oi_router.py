#!/usr/bin/env python3
"""Run V26 with the existing robust Binance metrics timestamp adapter.

This changes only CSV decoding. The frozen residual, OI, confirmation, cost,
horizon, arbitration and evaluation contracts remain untouched.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import diagnose_v26_residual_oi_router as v26
import run_v17_open_interest as metrics_adapter


# V26 imports diagnose_v17_open_interest as ``oi_common``. Replace only its
# archive/timestamp parser with the already validated V17 adapter.
v26.oi_common.read_metric_archive = metrics_adapter.read_metric_archive
v26.oi_common.load_metrics = metrics_adapter.load_metrics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    v26.execute(args.protocol.resolve(), args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
