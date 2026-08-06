"""Compare micro-touch stops with the unchanged five-minute structural invalidation.

Signals, consequent-encroachment touch, one-minute MSS trigger, external target, fees, and tick
reserve are unchanged. The only controlled change is the stop: instead of the one-minute touch
extreme, use the original five-minute displacement-origin structural stop. This is a causal path
diagnostic, not a backtest engine or performance claim.
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


def _first_touch(
    future: pd.DataFrame,
    *,
    direction: int,
    stop: float,
    target: float,
) -> tuple[str, str | None]:
    for timestamp, row in future.iterrows():
        stop_hit = float(row["low"]) <= stop if direction > 0 else float(row["high"]) >= stop
        target_hit = float(row["high"]) >= target if direction > 0 else float(row["low"]) <= target
        if stop_hit and target_hit:
            return "STOP_AMBIGUOUS_FIRST", timestamp.isoformat()
        if stop_hit:
            return "STOP", timestamp.isoformat()
        if target_hit:
            return "TARGET", timestamp.isoformat()
    return "TIMEOUT", None


def _summarize(records: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
    eligible = [record for record in records if record[f"{prefix}_cost_after_eligible"]]
    proxies = np.asarray([record[f"{prefix}_net_r_proxy"] for record in eligible], dtype=float)
    return {
        "triggers": len(records),
        "cost_after_eligible": len(eligible),
        "outcomes": dict(sorted(Counter(record[f"{prefix}_outcome"] for record in eligible).items())),
        "total_net_r_proxy": float(proxies.sum()) if proxies.size else 0.0,
        "mean_net_r_proxy": float(proxies.mean()) if proxies.size else 0.0,
        "median_net_r_proxy": float(np.median(proxies)) if proxies.size else 0.0,
        "positive_share": float((proxies > 0).mean()) if proxies.size else 0.0,
        "median_net_rr": float(np.median([record[f"{prefix}_net_rr"] for record in eligible])) if eligible else 0.0,
    }


def run(config_path: Path, output: Path, data_cache: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    pattern = RangeFVGConfig.from_mapping(dict(config["pattern"]))
    windows = list(config["suites"]["screen"])
    output.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []

    for symbol, asset_config in ASSETS.items():
        tick = float(asset_config["tick"])
        ltf.TICK = tick
        for window in windows:
            start = _parse_utc(str(window["start"]))
            end = _parse_utc(str(window["end"]))
            frame, _, quality = _load_frame(
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
            for signal in signals:
                micro, reason = ltf._evaluate_signal(signal, features)
                if micro is None or reason != "TRIGGERED":
                    continue
                direction = 1 if signal.direction.value == "LONG" else -1
                trigger_time = pd.Timestamp(micro["trigger_time"])
                entry = float(micro["entry"])
                target = float(signal.external_target)
                structural_stop = float(signal.structural_stop)
                geometry_valid = (
                    structural_stop < entry < target
                    if direction > 0
                    else target < entry < structural_stop
                )
                if not geometry_valid:
                    continue
                structural_loss = -ltf._net_per_unit(direction, entry, structural_stop)
                structural_gain = ltf._net_per_unit(direction, entry, target)
                structural_rr = structural_gain / structural_loss if structural_loss > 0 else float("-inf")
                future = features.loc[
                    (features.index > trigger_time)
                    & (features.index <= trigger_time + timedelta(minutes=240))
                ]
                structural_outcome, structural_outcome_time = _first_touch(
                    future,
                    direction=direction,
                    stop=structural_stop,
                    target=target,
                )
                if structural_outcome == "TARGET":
                    structural_proxy = structural_rr
                elif structural_outcome in ("STOP", "STOP_AMBIGUOUS_FIRST"):
                    structural_proxy = -1.0
                elif future.empty:
                    structural_proxy = 0.0
                else:
                    structural_proxy = ltf._net_per_unit(
                        direction,
                        entry,
                        float(future.iloc[-1]["close"]),
                    ) / structural_loss
                records.append(
                    {
                        "symbol": symbol,
                        "window": window["name"],
                        "scenario_id": signal.scenario_id,
                        "family": signal.family.value,
                        "direction": signal.direction.value,
                        "trigger_time": micro["trigger_time"],
                        "entry": entry,
                        "target": target,
                        "micro_stop": float(micro["stop"]),
                        "micro_net_rr": float(micro["net_reward_risk"]),
                        "micro_cost_after_eligible": bool(micro["cost_after_geometry_passed"]),
                        "micro_outcome": micro["outcome_240m"],
                        "micro_net_r_proxy": float(micro["net_r_proxy_240m"]),
                        "structural_stop": structural_stop,
                        "structural_net_rr": structural_rr,
                        "structural_cost_after_eligible": structural_gain > 0 and structural_rr >= 1.20,
                        "structural_outcome": structural_outcome,
                        "structural_outcome_time": structural_outcome_time,
                        "structural_net_r_proxy": structural_proxy,
                        "micro_stop_distance": abs(entry - float(micro["stop"])),
                        "structural_stop_distance": abs(entry - structural_stop),
                        "stop_distance_multiplier": abs(entry - structural_stop) / max(abs(entry - float(micro["stop"])), 1e-12),
                        "data_quality": quality,
                    }
                )

    by_asset = {
        symbol: {
            "micro": _summarize([record for record in records if record["symbol"] == symbol], "micro"),
            "structural": _summarize([record for record in records if record["symbol"] == symbol], "structural"),
        }
        for symbol in ASSETS
    }
    by_window = {
        window["name"]: {
            "micro": _summarize([record for record in records if record["window"] == window["name"]], "micro"),
            "structural": _summarize([record for record in records if record["window"] == window["name"]], "structural"),
        }
        for window in windows
    }
    result = {
        "candidate": "candidate-08-range-fvg-ltf-structural-stop-probe",
        "purpose": "single controlled invalidation comparison; not NautilusTrader execution evidence",
        "unchanged": [
            "completed 4h/day/week levels",
            "five-minute acceptance/rejection and FVG",
            "consequent-encroachment touch",
            "one-minute MSS displacement trigger",
            "external target",
            "fees and adverse tick reserve",
        ],
        "changed": "stop from one-minute touch extreme to original five-minute displacement-origin structural invalidation",
        "combined": {
            "micro": _summarize(records, "micro"),
            "structural": _summarize(records, "structural"),
        },
        "by_asset": by_asset,
        "by_window": by_window,
        "records": records,
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
        "combined": result["combined"],
        "by_asset": result["by_asset"],
        "by_window": result["by_window"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
