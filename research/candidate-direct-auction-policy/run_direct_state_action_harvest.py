from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

from direct_state_action_harvest import HarvestConfig, harvest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--warmup-days", type=int, default=20)
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"])
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    summary = harvest(
        HarvestConfig(
            start=start,
            end=end,
            load_start=start - timedelta(days=args.warmup_days),
            symbols=tuple(args.symbols),
            cache=args.cache,
            output=args.output,
        )
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
