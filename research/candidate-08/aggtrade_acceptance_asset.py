"""Run the unchanged ten-second acceptance scenario for one allowed asset and fixed week."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import timedelta
import json
from pathlib import Path
import sys
from typing import Any

import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from aggtrade_acceptance_probe import detect_acceptance_events  # noqa: E402
from aggtrade_orderflow_probe import (  # noqa: E402
    _context,
    _summary,
    load_ten_second_aggtrades,
)
from range_fvg_ltf_multiasset_probe import ASSETS, _load_frame  # noqa: E402
from range_fvg_logic import RangeFVGConfig  # noqa: E402
from run import _parse_utc  # noqa: E402


def run(
    *,
    config_path: Path,
    symbol: str,
    window_name: str,
    output: Path,
    data_cache: Path,
) -> dict[str, Any]:
    if symbol not in ASSETS:
        raise ValueError(f"unsupported asset: {symbol}")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    windows = {item["name"]: item for item in config["suites"]["screen"]}
    if window_name not in windows:
        raise ValueError(f"unknown fixed screen window: {window_name}")
    window = windows[window_name]
    start = _parse_utc(str(window["start"]))
    end = _parse_utc(str(window["end"]))
    frame, kline_sources, kline_quality = _load_frame(
        symbol=symbol,
        load_start=start - timedelta(days=10),
        load_end=end + timedelta(hours=4, minutes=10),
        cache_dir=data_cache / "klines",
    )
    ten, agg_sources, agg_quality = load_ten_second_aggtrades(
        symbol=symbol,
        start=start,
        end=end + timedelta(hours=4),
        cache_dir=data_cache / "aggTrades",
    )
    pattern = RangeFVGConfig.from_mapping(dict(config["pattern"]))
    context_times, context_bars, snapshots = _context(frame, pattern)
    records, diagnostics = detect_acceptance_events(
        data=ten,
        context_times=context_times,
        context_bars=context_bars,
        snapshots=snapshots,
        tick=float(ASSETS[symbol]["tick"]),
    )
    in_window = [
        {**record, "symbol": symbol, "window": window_name}
        for record in records
        if start <= pd.Timestamp(record["confirmation_time"]) < end
    ]
    result = {
        "candidate": "candidate-08-aggtrade-acceptance-only",
        "purpose": "unchanged per-asset causal path diagnostic; not NautilusTrader execution evidence",
        "symbol": symbol,
        "window": window,
        "tick_size": float(ASSETS[symbol]["tick"]),
        "thresholds_changed_by_asset": False,
        "diagnostics": diagnostics,
        "summary": _summary(in_window),
        "by_boundary_source": {
            source: _summary([record for record in in_window if record["boundary_source"] == source])
            for source in sorted({record["boundary_source"] for record in in_window})
        },
        "records": in_window,
        "aggtrade_data_quality": agg_quality,
        "aggtrade_source_files": [asdict(source) for source in agg_sources],
        "kline_data_quality": kline_quality,
        "kline_source_files": [asdict(source) for source in kline_sources],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config_range_fvg.json")
    parser.add_argument("--symbol", choices=tuple(ASSETS), required=True)
    parser.add_argument("--window", choices=("screen-01", "screen-02", "screen-03"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-cache", type=Path, required=True)
    args = parser.parse_args()
    result = run(
        config_path=args.config.resolve(),
        symbol=args.symbol,
        window_name=args.window,
        output=args.output.resolve(),
        data_cache=args.data_cache.resolve(),
    )
    print(json.dumps({
        "symbol": result["symbol"],
        "window": result["window"],
        "summary": result["summary"],
        "diagnostics": result["diagnostics"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
