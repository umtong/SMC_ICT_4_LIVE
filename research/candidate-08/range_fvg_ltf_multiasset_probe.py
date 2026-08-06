"""Unchanged completed-range/FVG + one-minute MSS transfer probe across four assets.

The thresholds and state sequence are identical to the BTC first-week probe. All data come from
checksum-verified official Binance Vision USD-M monthly klines. This is a causal path diagnostic,
not a custom backtest engine and not a performance claim. It determines whether BTC sparsity is
naturally resolved by asset transfer before a shared NautilusTrader account is implemented.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import timedelta
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from data import (  # noqa: E402
    SourceFile,
    _month_starts,
    _read_month,
    _timestamp_unit,
    _verified_zip,
)
from range_fvg_logic import RangeFVGConfig, build_range_fvg_signals  # noqa: E402
import range_fvg_ltf_probe as ltf  # noqa: E402
from run import _ns, _parse_utc  # noqa: E402


ASSETS = {
    "BTCUSDT": {"tick": 0.1},
    "ETHUSDT": {"tick": 0.01},
    "SOLUSDT": {"tick": 0.001},
    "XRPUSDT": {"tick": 0.0001},
}


def _load_frame(
    *,
    symbol: str,
    load_start: Any,
    load_end: Any,
    cache_dir: Path,
) -> tuple[pd.DataFrame, tuple[SourceFile, ...], dict[str, Any]]:
    frames: list[pd.DataFrame] = []
    sources: list[SourceFile] = []
    symbol_cache = cache_dir / symbol
    for month in _month_starts(load_start, load_end):
        archive_path, source = _verified_zip(symbol_cache, symbol, "1m", month)
        monthly = _read_month(archive_path)
        frames.append(monthly)
        sources.append(
            SourceFile(
                period=source.period,
                url=source.url,
                checksum_url=source.checksum_url,
                sha256=source.sha256,
                size_bytes=source.size_bytes,
                rows=len(monthly.index),
            )
        )
    frame = pd.concat(frames, ignore_index=True)
    unit = _timestamp_unit(frame["close_time"])
    frame.index = pd.to_datetime(frame["close_time"], unit=unit, utc=True)
    frame.index.name = "observed_time"
    frame = frame.loc[(frame.index >= load_start) & (frame.index < load_end)].sort_index().copy()
    duplicates = int(frame.index.duplicated(keep="last").sum())
    if duplicates:
        frame = frame.loc[~frame.index.duplicated(keep="last")]
    deltas = frame.index.to_series().diff().dropna().dt.total_seconds()
    expected = max(1, int((load_end - load_start).total_seconds() // 60))
    missing_ratio = max(0.0, (expected - len(frame.index)) / expected)
    if frame.empty or missing_ratio > 0.002:
        raise RuntimeError(
            f"{symbol} data completeness failed: rows={len(frame.index)} missing={missing_ratio:.6f}"
        )
    quality = {
        "rows": len(frame.index),
        "expected_rows": expected,
        "missing_ratio": missing_ratio,
        "duplicate_rows_removed": duplicates,
        "gap_count_over_61_seconds": int((deltas > 61.0).sum()),
        "max_gap_seconds": float(deltas.max()) if not deltas.empty else 0.0,
        "timestamp_unit_detected": unit,
        "first_observed_time": frame.index[0].isoformat(),
        "last_observed_time": frame.index[-1].isoformat(),
    }
    return frame, tuple(sources), quality


def _summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [record for record in records if record["cost_after_geometry_passed"]]
    outcomes = Counter(record["outcome_240m"] for record in eligible)
    proxies = np.asarray([record["net_r_proxy_240m"] for record in eligible], dtype=float)
    return {
        "micro_triggers": len(records),
        "cost_after_eligible": len(eligible),
        "outcomes_240m": dict(sorted(outcomes.items())),
        "total_net_r_proxy": float(proxies.sum()) if proxies.size else 0.0,
        "mean_net_r_proxy": float(proxies.mean()) if proxies.size else 0.0,
        "median_net_r_proxy": float(np.median(proxies)) if proxies.size else 0.0,
        "positive_share": float((proxies > 0).mean()) if proxies.size else 0.0,
        "median_net_reward_risk": float(
            np.median([record["net_reward_risk"] for record in eligible])
        ) if eligible else 0.0,
        "eligible_scenarios": [
            {
                "scenario_id": record["scenario_id"],
                "direction": record["direction"],
                "trigger_time": record["trigger_time"],
                "outcome_240m": record["outcome_240m"],
                "net_r_proxy_240m": record["net_r_proxy_240m"],
                "net_reward_risk": record["net_reward_risk"],
            }
            for record in eligible
        ],
    }


def run(config_path: Path, output: Path, data_cache: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    pattern = RangeFVGConfig.from_mapping(dict(config["pattern"]))
    windows = list(config["suites"]["screen"])
    output.mkdir(parents=True, exist_ok=True)
    detailed: dict[str, Any] = {}
    all_eligible: list[dict[str, Any]] = []

    for symbol, asset_config in ASSETS.items():
        ltf.TICK = float(asset_config["tick"])
        asset_payload: dict[str, Any] = {}
        for window in windows:
            start = _parse_utc(str(window["start"]))
            end = _parse_utc(str(window["end"]))
            frame, sources, quality = _load_frame(
                symbol=symbol,
                load_start=start - timedelta(days=10),
                load_end=end + timedelta(hours=4, minutes=10),
                cache_dir=data_cache,
            )
            features = ltf._features(frame)
            bundle = build_range_fvg_signals(frame, pattern)
            signals = [
                signal
                for timestamp, items in bundle.signals_by_time_ns.items()
                if _ns(start) <= timestamp < _ns(end)
                for signal in items
            ]
            diagnostics: Counter[str] = Counter()
            records: list[dict[str, Any]] = []
            for signal in signals:
                record, reason = ltf._evaluate_signal(signal, features)
                diagnostics[reason] += 1
                if record is not None:
                    record["symbol"] = symbol
                    record["window"] = window["name"]
                    records.append(record)
                    if record["cost_after_geometry_passed"]:
                        all_eligible.append(record)
            payload = {
                "symbol": symbol,
                "window": window,
                "base_detector_signals": len(signals),
                "diagnostic_counts": dict(sorted(diagnostics.items())),
                "summary": _summarize(records),
                "records": records,
                "data_quality": quality,
                "source_files": [asdict(source) for source in sources],
            }
            asset_payload[window["name"]] = payload
            destination = output / symbol / f"{window['name']}.json"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        detailed[symbol] = asset_payload

    by_asset: dict[str, Any] = {}
    by_window: dict[str, Any] = {}
    for symbol in ASSETS:
        records = [
            record
            for window_payload in detailed[symbol].values()
            for record in window_payload["records"]
        ]
        by_asset[symbol] = _summarize(records)
    for window in windows:
        name = window["name"]
        records = [
            record
            for symbol in ASSETS
            for record in detailed[symbol][name]["records"]
        ]
        by_window[name] = _summarize(records)

    eligible_sorted = sorted(all_eligible, key=lambda item: (item["trigger_time"], item["symbol"]))
    conflicts = 0
    prior_end: pd.Timestamp | None = None
    chronological_first: list[dict[str, Any]] = []
    for record in eligible_sorted:
        trigger = pd.Timestamp(record["trigger_time"])
        outcome_time = record.get("outcome_time")
        end = pd.Timestamp(outcome_time) if outcome_time else trigger + timedelta(minutes=240)
        if prior_end is not None and trigger < prior_end:
            conflicts += 1
            continue
        chronological_first.append(record)
        prior_end = end
    proxies = np.asarray(
        [record["net_r_proxy_240m"] for record in chronological_first],
        dtype=float,
    )
    result = {
        "candidate": "candidate-08-range-fvg-ltf-multiasset-probe",
        "purpose": "unchanged causal path diagnostic; not NautilusTrader execution evidence",
        "assets": list(ASSETS),
        "windows": windows,
        "threshold_transfer": "identical ATR/activity/imbalance thresholds for all assets; only exchange tick differs",
        "by_asset": by_asset,
        "by_window": by_window,
        "combined_all_eligible": _summarize(all_eligible),
        "global_one_position_chronological_first_proxy": {
            "eligible_before_overlap": len(all_eligible),
            "overlap_conflicts_removed": conflicts,
            "trades": len(chronological_first),
            "outcomes": dict(sorted(Counter(record["outcome_240m"] for record in chronological_first).items())),
            "total_net_r_proxy": float(proxies.sum()) if proxies.size else 0.0,
            "mean_net_r_proxy": float(proxies.mean()) if proxies.size else 0.0,
            "positive_share": float((proxies > 0).mean()) if proxies.size else 0.0,
            "selected": [
                {
                    "symbol": record["symbol"],
                    "window": record["window"],
                    "trigger_time": record["trigger_time"],
                    "outcome_240m": record["outcome_240m"],
                    "net_r_proxy_240m": record["net_r_proxy_240m"],
                }
                for record in chronological_first
            ],
        },
    }
    (output / "summary.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config_range_fvg.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-cache", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.config.resolve(), args.output.resolve(), args.data_cache.resolve())
    print(json.dumps({
        "by_asset": result["by_asset"],
        "by_window": result["by_window"],
        "combined_all_eligible": result["combined_all_eligible"],
        "global_one_position_chronological_first_proxy": result["global_one_position_chronological_first_proxy"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
