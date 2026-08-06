"""Path diagnostic for submitted completed-range FVG scenarios.

This module does not simulate orders or account returns. It reads the immutable NautilusTrader
first-window evidence, reloads the same checksum-verified official bars, and decomposes signal,
limit-fill, invalidation, reacceleration, and external-target paths. It is used once to distinguish
entry timing failure from direction, stop, and target failure.
"""

from __future__ import annotations

import argparse
from datetime import timedelta
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd
from nautilus_trader.model.data import BarType

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from data import load_official_binance_bars  # noqa: E402
from range_fvg_logic import RangeFVGConfig, aggregate_five_minute_bars  # noqa: E402
from run import _build_instrument, _parse_utc  # noqa: E402


FEE_RATE = 0.0006
TICK = 0.1


def _money(value: Any) -> float:
    text = str(value).split()[0]
    return float(text)


def _net_per_unit(direction: int, entry: float, exit_price: float) -> float:
    return direction * (exit_price - entry) - FEE_RATE * (entry + exit_price) - 2.0 * TICK


def _first_touch(
    frame: pd.DataFrame,
    *,
    direction: int,
    stop: float,
    target: float,
) -> tuple[str, str | None]:
    for timestamp, row in frame.iterrows():
        stop_hit = float(row["low"]) <= stop if direction > 0 else float(row["high"]) >= stop
        target_hit = float(row["high"]) >= target if direction > 0 else float(row["low"]) <= target
        if stop_hit and target_hit:
            return "STOP_AMBIGUOUS_FIRST", timestamp.isoformat()
        if stop_hit:
            return "STOP", timestamp.isoformat()
        if target_hit:
            return "TARGET", timestamp.isoformat()
    return "TIMEOUT", None


def _reaction_metrics(
    one: pd.DataFrame,
    five: pd.DataFrame,
    *,
    fill_time: pd.Timestamp,
    direction: int,
    entry: float,
    stop: float,
    target: float,
    boundary: float,
    expected_loss: float,
) -> dict[str, Any]:
    path = one.loc[(one.index > fill_time) & (one.index <= fill_time + timedelta(minutes=240))]
    result: dict[str, Any] = {}
    first_touch, touch_time = _first_touch(
        path,
        direction=direction,
        stop=stop,
        target=target,
    )
    result["first_touch_240m"] = first_touch
    result["first_touch_time"] = touch_time
    for horizon in (5, 15, 30, 60, 120, 240):
        segment = path.loc[path.index <= fill_time + timedelta(minutes=horizon)]
        if segment.empty:
            continue
        favorable_exit = (
            float(segment["high"].max()) if direction > 0 else float(segment["low"].min())
        )
        adverse_exit = (
            float(segment["low"].min()) if direction > 0 else float(segment["high"].max())
        )
        final_close = float(segment.iloc[-1]["close"])
        result[f"net_mfe_{horizon}m_r"] = _net_per_unit(direction, entry, favorable_exit) / expected_loss
        result[f"net_mae_{horizon}m_r"] = -_net_per_unit(direction, entry, adverse_exit) / expected_loss
        result[f"net_close_{horizon}m_r"] = _net_per_unit(direction, entry, final_close) / expected_loss
        boundary_failed = (
            bool((segment["close"] < boundary).any())
            if direction > 0
            else bool((segment["close"] > boundary).any())
        )
        result[f"boundary_failed_by_{horizon}m"] = boundary_failed

    post_five = five.loc[(five.index > fill_time) & (five.index <= fill_time + timedelta(minutes=30))]
    reaccelerated = False
    reacceleration_time: str | None = None
    first_five: dict[str, Any] | None = None
    previous = None
    for timestamp, row in post_five.iterrows():
        if first_five is None:
            first_five = {
                "timestamp": timestamp.isoformat(),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "imbalance": float(row["imbalance"]),
                "volume_ratio": float(row["volume_ratio"]),
                "trade_ratio": float(row["trade_ratio"]),
            }
        if previous is not None:
            directional_body = direction * float(row["close"] - row["open"])
            directional_flow = direction * float(row["imbalance"])
            displaced = (
                float(row["close"]) > float(previous["high"])
                if direction > 0
                else float(row["close"]) < float(previous["low"])
            )
            located = (
                float(row["close_location"]) >= 0.62
                if direction > 0
                else float(row["close_location"]) <= 0.38
            )
            if (
                displaced
                and located
                and directional_body >= 0.15 * float(row["atr"])
                and directional_flow >= 0.05
                and float(row["volume_ratio"]) >= 0.9
            ):
                reaccelerated = True
                reacceleration_time = timestamp.isoformat()
                break
        previous = row
    result["first_complete_5m_after_fill"] = first_five
    result["directional_reacceleration_within_30m"] = reaccelerated
    result["reacceleration_time"] = reacceleration_time
    return result


def run(
    *,
    config_path: Path,
    evidence_dir: Path,
    output_path: Path,
    data_cache: Path,
) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    metrics = json.loads((evidence_dir / "metrics.json").read_text(encoding="utf-8"))
    intents = json.loads((evidence_dir / "trade_intents.json").read_text(encoding="utf-8"))["trade_intents"]
    skips = json.loads((evidence_dir / "skipped_setups.json").read_text(encoding="utf-8"))["skipped_setups"]
    outcomes = json.loads((evidence_dir / "position_outcomes.json").read_text(encoding="utf-8"))
    fills = pd.read_csv(evidence_dir / "fills.csv")
    positions = pd.read_csv(evidence_dir / "positions.csv")

    start = _parse_utc(str(metrics["window"]["start"]))
    end = _parse_utc(str(metrics["window"]["end"]))
    instrument = _build_instrument(config)
    bar_type = BarType.from_str(str(config["bar_type"]))
    loaded = load_official_binance_bars(
        symbol="BTCUSDT",
        interval="1m",
        load_start=start - timedelta(days=10),
        load_end=end + timedelta(hours=4, minutes=10),
        bar_type=bar_type,
        instrument=instrument,
        cache_dir=data_cache,
    )
    one = loaded.frame.copy()
    for column in (
        "open",
        "high",
        "low",
        "close",
        "volume",
        "trade_count",
        "taker_buy_volume",
    ):
        one[column] = pd.to_numeric(one[column], errors="coerce")
    one = one.dropna()
    five = aggregate_five_minute_bars(
        one,
        RangeFVGConfig.from_mapping(dict(config["pattern"])),
    )
    spread = five["high"] - five["low"]
    five["close_location"] = (five["close"] - five["low"]) / spread.replace(0, np.nan)

    fill_by_order = {
        str(row["client_order_id"]): row
        for _, row in fills.iterrows()
    }
    position_by_opening_order = {
        str(row["opening_order_id"]): row
        for _, row in positions.iterrows()
    }
    callback_by_scenario = {
        str(item["scenario_id"]): item for item in outcomes["strategy_callbacks"]
    }

    records: list[dict[str, Any]] = []
    for intent in intents:
        direction = 1 if intent["direction"] == "LONG" else -1
        entry_order_id = str(intent["entry_order_id"])
        signal_time = pd.to_datetime(int(intent["signal_time_ns"]), unit="ns", utc=True)
        entry = float(intent["estimated_entry"])
        stop = float(intent["structural_stop"])
        target = float(intent["external_target"])
        record: dict[str, Any] = {
            "scenario_id": intent["scenario_id"],
            "family": intent["scenario_family"],
            "direction": intent["direction"],
            "signal_time": signal_time.isoformat(),
            "boundary_source": intent["boundary_source"],
            "boundary_level": float(intent["boundary_level"]),
            "target_source": intent["external_target_source"],
            "entry": entry,
            "stop": stop,
            "target": target,
            "net_reward_risk": float(intent["net_reward_risk"]),
            "planned_stop_loss": float(intent["planned_stop_loss"]),
        }
        fill = fill_by_order.get(entry_order_id)
        if fill is None:
            expiry = signal_time + timedelta(minutes=int(config["pattern"]["entry_expiry_minutes"]))
            future = one.loc[(one.index > signal_time) & (one.index <= expiry)]
            if direction > 0:
                closest = float(future["low"].min()) if not future.empty else float("nan")
                distance = closest - entry
            else:
                closest = float(future["high"].max()) if not future.empty else float("nan")
                distance = entry - closest
            record.update(
                {
                    "filled": False,
                    "closest_price_during_entry_window": closest,
                    "signed_unfilled_distance": distance,
                }
            )
            records.append(record)
            continue

        fill_time = pd.to_datetime(fill["ts_event"], utc=True)
        fill_price = float(fill["last_px"])
        position = position_by_opening_order.get(entry_order_id)
        realized_pnl = _money(position["realized_pnl"]) if position is not None else None
        callback = callback_by_scenario.get(str(intent["scenario_id"]), {})
        record.update(
            {
                "filled": True,
                "fill_time": fill_time.isoformat(),
                "fill_delay_minutes": (fill_time - signal_time).total_seconds() / 60.0,
                "actual_fill_price": fill_price,
                "close_reason": callback.get("close_reason"),
                "realized_pnl": realized_pnl,
            }
        )
        record.update(
            _reaction_metrics(
                one,
                five,
                fill_time=fill_time,
                direction=direction,
                entry=fill_price,
                stop=stop,
                target=target,
                boundary=float(intent["boundary_level"]),
                expected_loss=float(intent["expected_loss_per_unit"]),
            )
        )
        records.append(record)

    filled = [item for item in records if item["filled"]]
    losing = [item for item in filled if float(item.get("realized_pnl", 0.0)) < 0]
    winning = [item for item in filled if float(item.get("realized_pnl", 0.0)) > 0]
    result = {
        "candidate": config["candidate"],
        "window": metrics["window"],
        "purpose": "post-run path decomposition only; no simulated performance evidence",
        "counts": {
            "detector_signals": metrics["detector_signals_in_window"],
            "cost_skipped": len(skips),
            "orders_submitted": len(intents),
            "entries_filled": len(filled),
            "entries_unfilled": len(records) - len(filled),
            "winning_fills": len(winning),
            "losing_fills": len(losing),
        },
        "dominant_path_findings": {
            "filled_without_directional_reacceleration_30m": sum(
                not bool(item["directional_reacceleration_within_30m"])
                for item in filled
            ),
            "losers_without_directional_reacceleration_30m": sum(
                not bool(item["directional_reacceleration_within_30m"])
                for item in losing
            ),
            "losers_with_boundary_failure_by_15m": sum(
                bool(item["boundary_failed_by_15m"])
                for item in losing
            ),
            "winners_reaching_external_target_240m": sum(
                item["first_touch_240m"] == "TARGET" for item in winning
            ),
        },
        "median_fill_delay_minutes": (
            float(np.median([item["fill_delay_minutes"] for item in filled]))
            if filled
            else None
        ),
        "median_net_mfe_60m_r_filled": (
            float(np.median([item["net_mfe_60m_r"] for item in filled]))
            if filled
            else None
        ),
        "median_net_mae_60m_r_filled": (
            float(np.median([item["net_mae_60m_r"] for item in filled]))
            if filled
            else None
        ),
        "records": records,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=True) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config_range_fvg.json")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-cache", type=Path, required=True)
    args = parser.parse_args()
    result = run(
        config_path=args.config.resolve(),
        evidence_dir=args.evidence.resolve(),
        output_path=args.output.resolve(),
        data_cache=args.data_cache.resolve(),
    )
    print(json.dumps({
        "counts": result["counts"],
        "dominant_path_findings": result["dominant_path_findings"],
        "median_fill_delay_minutes": result["median_fill_delay_minutes"],
        "median_net_mfe_60m_r_filled": result["median_net_mfe_60m_r_filled"],
        "median_net_mae_60m_r_filled": result["median_net_mae_60m_r_filled"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
