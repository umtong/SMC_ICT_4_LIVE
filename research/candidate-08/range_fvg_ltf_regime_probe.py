"""Auction-regime decomposition for the unchanged range-FVG one-minute MSS trigger.

Signals, entry, micro invalidation, external target, costs, and first-touch outcomes are unchanged.
The script assigns each causal trigger to predeclared structural market states: completed four-hour
direction, two-auction persistence, day-open position, four-hour range overlap, 60-minute
efficiency, and broad-market alignment. This is diagnostic only and is used to choose one economic
regime hypothesis before a fresh holdout, not to optimize numerical thresholds.
"""

from __future__ import annotations

import argparse
from collections import Counter
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

from range_fvg_ltf_multiasset_probe import ASSETS, _load_frame  # noqa: E402
import range_fvg_ltf_probe as ltf  # noqa: E402
from range_fvg_logic import RangeFVGConfig, build_range_fvg_signals  # noqa: E402
from run import _ns, _parse_utc  # noqa: E402


REGIMES = (
    "BASE",
    "LATEST_H4_ALIGNED",
    "TWO_H4_ALIGNED",
    "H4_AND_DAY_POSITION",
    "H4_LOW_OVERLAP_EXPANSION",
    "AUCTION_CONFLUENCE",
    "BROAD_MARKET_CONFLUENCE",
)


def _enrich(frame: pd.DataFrame) -> pd.DataFrame:
    data = ltf._features(frame)
    h4_key = data.index.floor("4h")
    h4 = data.groupby(h4_key, sort=True).agg(
        h4_open=("open", "first"),
        h4_high=("high", "max"),
        h4_low=("low", "min"),
        h4_close=("close", "last"),
    )
    for lag in (1, 2):
        data[f"h4_open_lag{lag}"] = h4_key.map(h4["h4_open"].shift(lag))
        data[f"h4_high_lag{lag}"] = h4_key.map(h4["h4_high"].shift(lag))
        data[f"h4_low_lag{lag}"] = h4_key.map(h4["h4_low"].shift(lag))
        data[f"h4_close_lag{lag}"] = h4_key.map(h4["h4_close"].shift(lag))
        data[f"h4_direction_lag{lag}"] = np.sign(
            data[f"h4_close_lag{lag}"] - data[f"h4_open_lag{lag}"]
        )
    intersection = (
        np.minimum(data["h4_high_lag1"], data["h4_high_lag2"])
        - np.maximum(data["h4_low_lag1"], data["h4_low_lag2"])
    ).clip(lower=0.0)
    minimum_width = np.minimum(
        data["h4_high_lag1"] - data["h4_low_lag1"],
        data["h4_high_lag2"] - data["h4_low_lag2"],
    )
    data["h4_overlap_fraction"] = intersection / minimum_width.replace(0, np.nan)

    day_key = data.index.floor("D")
    day_open = data.groupby(day_key, sort=True)["open"].first()
    data["day_open"] = day_key.map(day_open)
    movement = data["close"].diff()
    path = movement.abs().shift(1).rolling(60, min_periods=45).sum()
    data["efficiency_60m"] = (
        data["close"].shift(1) - data["close"].shift(61)
    ).abs() / path.replace(0, np.nan)
    data["direction_60m"] = np.sign(data["close"].shift(1) - data["close"].shift(61))
    data["return_15m"] = data["close"].shift(1) / data["close"].shift(16) - 1.0
    return data


def _regime_flags(
    *,
    symbol: str,
    trigger_time: pd.Timestamp,
    direction: int,
    entry: float,
    frames: dict[str, pd.DataFrame],
) -> tuple[dict[str, bool], dict[str, Any]]:
    row = frames[symbol].loc[trigger_time]
    latest_h4 = int(np.sign(float(row["h4_direction_lag1"]))) == direction
    two_h4 = latest_h4 and int(np.sign(float(row["h4_direction_lag2"]))) == direction
    day_position = direction * (entry - float(row["day_open"])) > 0
    overlap = float(row["h4_overlap_fraction"])
    low_overlap = np.isfinite(overlap) and overlap <= 0.50
    efficiency = float(row["efficiency_60m"])
    direction60 = int(np.sign(float(row["direction_60m"])))
    efficient_aligned = np.isfinite(efficiency) and efficiency >= 0.20 and direction60 == direction

    signs: dict[str, int] = {}
    for asset, frame in frames.items():
        if trigger_time not in frame.index:
            signs[asset] = 0
            continue
        value = float(frame.loc[trigger_time, "return_15m"])
        signs[asset] = int(np.sign(value)) if np.isfinite(value) else 0
    breadth = sum(value == direction for value in signs.values())
    btc_aligned = signs.get("BTCUSDT", 0) == direction
    flags = {
        "BASE": True,
        "LATEST_H4_ALIGNED": latest_h4,
        "TWO_H4_ALIGNED": two_h4,
        "H4_AND_DAY_POSITION": latest_h4 and day_position,
        "H4_LOW_OVERLAP_EXPANSION": latest_h4 and low_overlap,
        "AUCTION_CONFLUENCE": latest_h4 and day_position and efficient_aligned,
        "BROAD_MARKET_CONFLUENCE": latest_h4 and btc_aligned and breadth >= 3,
    }
    details = {
        "h4_direction_lag1": float(row["h4_direction_lag1"]),
        "h4_direction_lag2": float(row["h4_direction_lag2"]),
        "h4_overlap_fraction": overlap,
        "day_open": float(row["day_open"]),
        "day_position_aligned": day_position,
        "efficiency_60m": efficiency,
        "direction_60m": float(row["direction_60m"]),
        "return_15m_signs": signs,
        "breadth_15m_aligned": breadth,
        "btc_15m_aligned": btc_aligned,
    }
    return flags, details


def _summary(records: list[dict[str, Any]], regime: str) -> dict[str, Any]:
    selected = [record for record in records if record["regime_flags"][regime]]
    eligible = [record for record in selected if record["cost_after_geometry_passed"]]
    proxies = np.asarray([record["net_r_proxy_240m"] for record in eligible], dtype=float)
    positive_windows = []
    for window in sorted({record["window"] for record in eligible}):
        values = [
            float(record["net_r_proxy_240m"])
            for record in eligible
            if record["window"] == window
        ]
        if values and sum(values) > 0:
            positive_windows.append(window)
    return {
        "triggers": len(selected),
        "cost_after_eligible": len(eligible),
        "outcomes": dict(sorted(Counter(record["outcome_240m"] for record in eligible).items())),
        "total_net_r_proxy": float(proxies.sum()) if proxies.size else 0.0,
        "mean_net_r_proxy": float(proxies.mean()) if proxies.size else 0.0,
        "median_net_r_proxy": float(np.median(proxies)) if proxies.size else 0.0,
        "positive_share": float((proxies > 0).mean()) if proxies.size else 0.0,
        "windows_with_eligible": len({record["window"] for record in eligible}),
        "positive_windows": len(positive_windows),
        "positive_window_names": positive_windows,
        "assets_with_eligible": len({record["symbol"] for record in eligible}),
    }


def run(config_path: Path, output: Path, data_cache: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    pattern = RangeFVGConfig.from_mapping(dict(config["pattern"]))
    windows = list(config["suites"]["screen"])
    output.mkdir(parents=True, exist_ok=True)
    all_records: list[dict[str, Any]] = []

    for window in windows:
        start = _parse_utc(str(window["start"]))
        end = _parse_utc(str(window["end"]))
        frames: dict[str, pd.DataFrame] = {}
        source_frames: dict[str, pd.DataFrame] = {}
        for symbol in ASSETS:
            frame, _, _ = _load_frame(
                symbol=symbol,
                load_start=start - timedelta(days=10),
                load_end=end + timedelta(hours=4, minutes=10),
                cache_dir=data_cache,
            )
            source_frames[symbol] = frame
            frames[symbol] = _enrich(frame)

        for symbol, asset_config in ASSETS.items():
            ltf.TICK = float(asset_config["tick"])
            bundle = build_range_fvg_signals(source_frames[symbol], pattern)
            signals = [
                signal
                for timestamp, items in bundle.signals_by_time_ns.items()
                if _ns(start) <= timestamp < _ns(end)
                for signal in items
            ]
            for signal in signals:
                record, reason = ltf._evaluate_signal(signal, frames[symbol])
                if record is None or reason != "TRIGGERED":
                    continue
                trigger_time = pd.Timestamp(record["trigger_time"])
                if trigger_time not in frames[symbol].index:
                    continue
                direction = 1 if record["direction"] == "LONG" else -1
                flags, regime_details = _regime_flags(
                    symbol=symbol,
                    trigger_time=trigger_time,
                    direction=direction,
                    entry=float(record["entry"]),
                    frames=frames,
                )
                record.update(
                    {
                        "symbol": symbol,
                        "window": window["name"],
                        "regime_flags": flags,
                        "regime_details": regime_details,
                    }
                )
                all_records.append(record)

    summaries = {regime: _summary(all_records, regime) for regime in REGIMES}
    by_window = {
        window["name"]: {
            regime: _summary(
                [record for record in all_records if record["window"] == window["name"]],
                regime,
            )
            for regime in REGIMES
        }
        for window in windows
    }
    result = {
        "candidate": "candidate-08-range-fvg-ltf-regime-probe",
        "purpose": "structural market-state decomposition only; no execution or performance claim",
        "unchanged_trade_contract": "completed external level, five-minute FVG context, CE touch, one-minute MSS, micro invalidation, genuine external target, 6 bp per fill and adverse tick reserve",
        "predeclared_regimes": {
            "LATEST_H4_ALIGNED": "last completed 4h candle direction aligns",
            "TWO_H4_ALIGNED": "last two completed 4h candle directions align",
            "H4_AND_DAY_POSITION": "last completed 4h aligns and entry is on directional side of current UTC day open",
            "H4_LOW_OVERLAP_EXPANSION": "last completed 4h aligns and overlap with preceding 4h range is <= 50%",
            "AUCTION_CONFLUENCE": "last completed 4h, day-open position, and causal 60m efficiency >=20% all align",
            "BROAD_MARKET_CONFLUENCE": "last completed 4h aligns, BTC 15m return aligns, and at least 3 of 4 assets align",
        },
        "summaries": summaries,
        "by_window": by_window,
        "records": all_records,
    }
    (output / "summary.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config_range_fvg.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-cache", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.config.resolve(), args.output.resolve(), args.data_cache.resolve())
    print(json.dumps({
        "summaries": result["summaries"],
        "by_window": result["by_window"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
