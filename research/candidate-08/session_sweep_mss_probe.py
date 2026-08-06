"""Completed-session liquidity sweep -> one-minute MSS/FVG diagnostic.

The scenario uses only completed UTC session ranges: Asia 00:00-08:00 is traded during
08:00-13:00, and London 08:00-13:00 is traded during 13:00-17:00. A boundary sweep must close back
inside, occur in prior-day premium/discount, avoid reversal against an efficient 60-minute auction,
and be followed by a separate one-minute MSS displacement with an aligned FVG. The opposite
completed session boundary is the only target. This is a causal diagnostic, not a backtest engine.
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
from run import _parse_utc  # noqa: E402


FEE_RATE = 0.0006


def _enrich(frame: pd.DataFrame) -> pd.DataFrame:
    data = ltf._features(frame)
    day_key = data.index.floor("D")
    days = data.groupby(day_key, sort=True).agg(
        day_high=("high", "max"),
        day_low=("low", "min"),
    )
    days["previous_day_high"] = days["day_high"].shift(1)
    days["previous_day_low"] = days["day_low"].shift(1)
    days["previous_day_mid"] = (days["previous_day_high"] + days["previous_day_low"]) / 2.0
    for column in ("previous_day_high", "previous_day_low", "previous_day_mid"):
        data[column] = day_key.map(days[column])

    hour = data.index.hour
    asia = data.loc[(hour >= 0) & (hour < 8)].groupby(day_key[(hour >= 0) & (hour < 8)]).agg(
        asia_high=("high", "max"),
        asia_low=("low", "min"),
    )
    london = data.loc[(hour >= 8) & (hour < 13)].groupby(day_key[(hour >= 8) & (hour < 13)]).agg(
        london_high=("high", "max"),
        london_low=("low", "min"),
    )
    data["asia_high"] = day_key.map(asia["asia_high"])
    data["asia_low"] = day_key.map(asia["asia_low"])
    data["london_high"] = day_key.map(london["london_high"])
    data["london_low"] = day_key.map(london["london_low"])

    movement = data["close"].diff()
    path = movement.abs().shift(1).rolling(60, min_periods=45).sum()
    data["efficiency_60m"] = (
        data["close"].shift(1) - data["close"].shift(61)
    ).abs() / path.replace(0, np.nan)
    data["direction_60m"] = np.sign(data["close"].shift(1) - data["close"].shift(61))
    return data


def _session_context(timestamp: pd.Timestamp, row: pd.Series) -> tuple[str, float, float, pd.Timestamp] | None:
    hour = int(timestamp.hour)
    day = timestamp.floor("D")
    if 8 <= hour < 13:
        return "ASIA_TO_LONDON", float(row["asia_high"]), float(row["asia_low"]), day + timedelta(hours=13)
    if 13 <= hour < 17:
        return "LONDON_TO_NEW_YORK", float(row["london_high"]), float(row["london_low"]), day + timedelta(hours=17)
    return None


def _sweep_side(
    row: pd.Series,
    previous_close: float,
    *,
    range_high: float,
    range_low: float,
) -> int:
    atr = float(row["atr1m"])
    if not np.isfinite(atr) or atr <= 0:
        return 0
    body = max(abs(float(row["close"] - row["open"])), 0.01 * atr)
    high_wick = float(row["high"] - max(row["open"], row["close"]))
    low_wick = float(min(row["open"], row["close"]) - row["low"])
    high_sweep = (
        previous_close <= range_high
        and float(row["high"]) >= range_high + 0.05 * atr
        and float(row["close"]) <= range_high - 0.01 * atr
        and high_wick / body >= 0.50
        and float(row["close_location1m"]) <= 0.48
    )
    low_sweep = (
        previous_close >= range_low
        and float(row["low"]) <= range_low - 0.05 * atr
        and float(row["close"]) >= range_low + 0.01 * atr
        and low_wick / body >= 0.50
        and float(row["close_location1m"]) >= 0.52
    )
    if high_sweep and low_sweep:
        return 0
    return -1 if high_sweep else (1 if low_sweep else 0)


def _context_allows(row: pd.Series, direction: int, sweep_extreme: float) -> bool:
    midpoint = float(row["previous_day_mid"])
    efficiency = float(row["efficiency_60m"])
    auction_direction = int(np.sign(float(row["direction_60m"])))
    if not np.isfinite(midpoint):
        return False
    premium_discount = sweep_extreme > midpoint if direction < 0 else sweep_extreme < midpoint
    if not premium_discount:
        return False
    if np.isfinite(efficiency) and efficiency >= 0.35 and auction_direction == -direction:
        return False
    return True


def _mss_fvg(data: pd.DataFrame, position: int, direction: int) -> bool:
    if position < 3:
        return False
    row = data.iloc[position]
    prior = data.iloc[position - 3 : position]
    two_back = data.iloc[position - 2]
    atr = float(row["atr1m"])
    values = (
        atr,
        float(row["imbalance1m"]),
        float(row["volume_ratio1m"]),
        float(row["trade_ratio1m"]),
        float(row["close_location1m"]),
    )
    if not all(np.isfinite(value) for value in values) or atr <= 0:
        return False
    directional_body = direction * float(row["close"] - row["open"])
    directional_flow = direction * float(row["imbalance1m"])
    if direction > 0:
        break_structure = float(row["close"]) > float(prior["high"].max())
        fvg = float(row["low"]) > float(two_back["high"]) + 0.02 * atr
        located = float(row["close_location1m"]) >= 0.65
    else:
        break_structure = float(row["close"]) < float(prior["low"].min())
        fvg = float(row["high"]) < float(two_back["low"]) - 0.02 * atr
        located = float(row["close_location1m"]) <= 0.35
    return (
        break_structure
        and fvg
        and located
        and directional_body >= 0.35 * atr
        and directional_flow >= 0.08
        and float(row["volume_ratio1m"]) >= 1.10
        and float(row["trade_ratio1m"]) >= 1.05
    )


def _net(direction: int, entry: float, exit_price: float, tick: float) -> float:
    return direction * (exit_price - entry) - FEE_RATE * (entry + exit_price) - 2.0 * tick


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


def _detect(
    data: pd.DataFrame,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    symbol: str,
    window_name: str,
    tick: float,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    records: list[dict[str, Any]] = []
    diagnostics: Counter[str] = Counter()
    consumed: set[tuple[str, str, int]] = set()
    evaluation = np.flatnonzero((data.index >= start) & (data.index < end))
    for raw_position in evaluation:
        position = int(raw_position)
        if position < 1:
            continue
        timestamp = data.index[position]
        row = data.iloc[position]
        context = _session_context(timestamp, row)
        if context is None or not all(np.isfinite(value) for value in context[1:3]):
            continue
        session_name, range_high, range_low, session_end = context
        previous_close = float(data.iloc[position - 1]["close"])
        sweep = _sweep_side(row, previous_close, range_high=range_high, range_low=range_low)
        if sweep == 0:
            continue
        boundary_name = "HIGH" if sweep < 0 else "LOW"
        key = (timestamp.strftime("%Y-%m-%d"), session_name, -1 if sweep < 0 else 1)
        if key in consumed:
            diagnostics["BOUNDARY_ALREADY_CONSUMED"] += 1
            continue
        consumed.add(key)
        sweep_extreme = float(row["high"] if sweep < 0 else row["low"])
        if not _context_allows(row, sweep, sweep_extreme):
            diagnostics["CONTEXT_BLOCKED"] += 1
            continue

        trigger_position: int | None = None
        extreme = sweep_extreme
        trigger_end = min(len(data), position + 1 + 12)
        for candidate in range(position + 1, trigger_end):
            candidate_row = data.iloc[candidate]
            candidate_time = data.index[candidate]
            if candidate_time >= session_end:
                break
            extreme = (
                max(extreme, float(candidate_row["high"]))
                if sweep < 0
                else min(extreme, float(candidate_row["low"]))
            )
            if (
                (sweep < 0 and float(candidate_row["close"]) > range_high + 0.10 * float(candidate_row["atr1m"]))
                or (sweep > 0 and float(candidate_row["close"]) < range_low - 0.10 * float(candidate_row["atr1m"]))
            ):
                diagnostics["RECLAIM_FAILED_BEFORE_MSS"] += 1
                break
            if _mss_fvg(data, candidate, sweep):
                trigger_position = candidate
                break
        if trigger_position is None:
            diagnostics["NO_SEPARATE_MSS_FVG"] += 1
            continue

        trigger = data.iloc[trigger_position]
        trigger_time = data.index[trigger_position]
        entry = float(trigger["close"]) + sweep * tick
        buffer = 0.05 * float(trigger["atr1m"])
        minimum = 0.25 * float(trigger["atr1m"])
        if sweep > 0:
            stop = min(extreme - buffer, entry - minimum)
            target = range_high
            valid = stop < entry < target
        else:
            stop = max(extreme + buffer, entry + minimum)
            target = range_low
            valid = target < entry < stop
        if not valid:
            diagnostics["INVALID_OPPOSITE_BOUNDARY_GEOMETRY"] += 1
            continue
        expected_loss = -_net(sweep, entry, stop, tick)
        expected_gain = _net(sweep, entry, target, tick)
        net_rr = expected_gain / expected_loss if expected_loss > 0 else float("-inf")
        if expected_gain <= 0 or net_rr < 1.20:
            diagnostics["INSUFFICIENT_COST_AFTER_OPPOSITE_BOUNDARY"] += 1
            continue

        horizon_end = min(session_end, trigger_time + timedelta(minutes=180))
        future = data.loc[(data.index > trigger_time) & (data.index <= horizon_end)]
        outcome, outcome_time = _first_touch(
            future,
            direction=sweep,
            stop=stop,
            target=target,
        )
        if outcome == "TARGET":
            proxy = net_rr
        elif outcome in ("STOP", "STOP_AMBIGUOUS_FIRST"):
            proxy = -1.0
        elif future.empty:
            proxy = 0.0
        else:
            proxy = _net(sweep, entry, float(future.iloc[-1]["close"]), tick) / expected_loss
        records.append(
            {
                "symbol": symbol,
                "window": window_name,
                "session": session_name,
                "swept_boundary": boundary_name,
                "direction": "LONG" if sweep > 0 else "SHORT",
                "sweep_time": timestamp.isoformat(),
                "trigger_time": trigger_time.isoformat(),
                "entry": entry,
                "stop": stop,
                "target": target,
                "net_reward_risk": net_rr,
                "outcome": outcome,
                "outcome_time": outcome_time,
                "net_r_proxy": proxy,
                "sweep_extreme": sweep_extreme,
                "previous_day_mid": float(row["previous_day_mid"]),
                "efficiency_60m": float(row["efficiency_60m"]),
                "direction_60m": float(row["direction_60m"]),
                "trigger_imbalance": float(trigger["imbalance1m"]),
                "trigger_volume_ratio": float(trigger["volume_ratio1m"]),
                "trigger_trade_ratio": float(trigger["trade_ratio1m"]),
            }
        )
        diagnostics["TRADEABLE_SCENARIO"] += 1
    return records, diagnostics


def _summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    proxies = np.asarray([record["net_r_proxy"] for record in records], dtype=float)
    return {
        "trades": len(records),
        "outcomes": dict(sorted(Counter(record["outcome"] for record in records).items())),
        "total_net_r_proxy": float(proxies.sum()) if proxies.size else 0.0,
        "mean_net_r_proxy": float(proxies.mean()) if proxies.size else 0.0,
        "median_net_r_proxy": float(np.median(proxies)) if proxies.size else 0.0,
        "positive_share": float((proxies > 0).mean()) if proxies.size else 0.0,
        "median_net_rr": float(np.median([record["net_reward_risk"] for record in records])) if records else 0.0,
    }


def run(config_path: Path, output: Path, data_cache: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    windows = list(config["suites"]["screen"])
    output.mkdir(parents=True, exist_ok=True)
    all_records: list[dict[str, Any]] = []
    detailed: dict[str, Any] = {}
    for symbol, asset_config in ASSETS.items():
        asset_payload: dict[str, Any] = {}
        for window in windows:
            start = _parse_utc(str(window["start"]))
            end = _parse_utc(str(window["end"]))
            frame, sources, quality = _load_frame(
                symbol=symbol,
                load_start=start - timedelta(days=3),
                load_end=end + timedelta(hours=1),
                cache_dir=data_cache,
            )
            data = _enrich(frame)
            records, diagnostics = _detect(
                data,
                start=start,
                end=end,
                symbol=symbol,
                window_name=window["name"],
                tick=float(asset_config["tick"]),
            )
            all_records.extend(records)
            payload = {
                "symbol": symbol,
                "window": window,
                "diagnostics": dict(sorted(diagnostics.items())),
                "summary": _summary(records),
                "records": records,
                "data_quality": quality,
                "source_files": [asdict(source) for source in sources],
            }
            asset_payload[window["name"]] = payload
            destination = output / symbol / f"{window['name']}.json"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        detailed[symbol] = asset_payload

    by_asset = {
        symbol: _summary([
            record
            for window in detailed[symbol].values()
            for record in window["records"]
        ])
        for symbol in ASSETS
    }
    by_window = {
        window["name"]: _summary([
            record
            for symbol in ASSETS
            for record in detailed[symbol][window["name"]]["records"]
        ])
        for window in windows
    }
    result = {
        "candidate": "candidate-08-session-sweep-mss-probe",
        "purpose": "causal session-auction diagnostic; not NautilusTrader execution evidence",
        "session_contract": {
            "asia_range": "00:00-08:00 UTC, observed only after 08:00",
            "london_execution": "08:00-13:00 UTC, target opposite Asia boundary",
            "london_range": "08:00-13:00 UTC, observed only after 13:00",
            "new_york_execution": "13:00-17:00 UTC, target opposite London boundary",
        },
        "by_asset": by_asset,
        "by_window": by_window,
        "combined": _summary(all_records),
        "records": sorted(all_records, key=lambda item: (item["trigger_time"], item["symbol"])),
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
        "by_asset": result["by_asset"],
        "by_window": result["by_window"],
        "combined": result["combined"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
