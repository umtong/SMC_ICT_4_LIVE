#!/usr/bin/env python3
"""Prepare one frozen BTC week with the official aggTrades execution proxy."""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from nt_lvcfr_data import CandidateConfig
from nt_lvcfr_trade_proxy import prepare_trade_proxy_week


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--week-start", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()

    config = CandidateConfig.load(args.config)
    manifest = prepare_trade_proxy_week(
        week_start=args.week_start,
        output_root=args.output.resolve(),
        config=config,
    )
    print(
        json.dumps(
            {
                "candidate": manifest["candidate"],
                "week_start": manifest["week_start"],
                "signals": manifest["signals"],
                "execution_source": manifest["catalog"]["execution_source"],
                "aggtrade_source_rows": manifest["catalog"]["aggtrade_source_rows"],
                "quote_ticks_retained": manifest["catalog"]["quote_ticks_retained"],
                "funding_updates": manifest["catalog"]["funding_updates"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
