#!/usr/bin/env python3
"""Compile causal rich-state scenario signals for NautilusTrader.

This module is a pattern/scenario compiler, not a backtest engine. It never
matches orders, fills positions, computes PnL, sizes risk or updates NAV. It
uses only completed bars and rich observations whose exchange timestamps are
available by the signal close, then emits timestamped intents for a separate
NautilusTrader Strategy.

V22 corrects two structural conflations found in the prior activity router:

* an executed-notional tail is not interchangeable with persistent inventory;
  execution-shock-only and dual-tail events are excluded from continuation;
* one close beyond a rejected sweep is not sufficient acceptance; stress
  failed-auction continuation must pass the existing five-minute acceptance,
  aligned terminal flow/close-location and non-climactic terminal minute.

Routes are mutually exclusive:

* normal median basis: failed-auction resumption and mesoscale inventory
  displacement;
* high-activity negative basis: confirmed failed-auction continuation, fresh
  inventory creation, or orderly OI contraction, with no execution shock;
* low-activity negative basis: failed price discovery after an impact shock.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from dataclasses import dataclass
import importlib.util
import json
import math
from pathlib import Path
import sys
from typing import Any

import pandas as pd

import nt_backtest


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_ROOT = Path(__file__).parent
v9 = _load_module(
    "candidate04_v22_v9",
    _ROOT / "confirmed_stress_acceptance_candidate.py",
)
v10 = _load_module(
    "candidate04_v22_v10",
    _ROOT / "impact_exhaustion_candidate.py",
)
base = v9.v8.v7.v6.v5
Config = v9.Config
Intent = v9.Intent


@dataclass(frozen=True, slots=True)
class RouterParameters:
    activity_lookback_minutes: int
    low_activity_path_bps_max: float
    impact_requires_negative_basis: bool

    @classmethod
    def load(cls, path: Path) -> "RouterParameters":
        result = cls(**json.loads(path.read_text(encoding="utf-8")))
        if result.activity_lookback_minutes < 60:
            raise ValueError("activity lookback must be at least 60 minutes")
        if result.low_activity_path_bps_max <= 0.0:
            raise ValueError("low-activity path ceiling must be positive")
        return result


def _serializable(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _serializable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_serializable(item) for item in value]
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return number if math.isfinite(number) else None


def auction_path_bps(data: pd.DataFrame, parameters: RouterParameters) -> pd.Series:
    path = data["close"].astype(float).pct_change(fill_method=None).abs()
    return (
        path.rolling(
            parameters.activity_lookback_minutes,
            min_periods=parameters.activity_lookback_minutes,
        ).sum()
        * 10_000.0
    )


def _copy_intent(
    parent: Intent,
    *,
    scenario: str,
    details: dict[str, Any],
) -> Intent:
    return Intent(
        scenario=scenario,
        side=parent.side,
        signal_index=parent.signal_index,
        entry_index=parent.entry_index,
        stop_level=parent.stop_level,
        event_indices=parent.event_indices,
        details=details,
    )


def collect_signals(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Config,
    impact_parameters: Any,
    router: RouterParameters,
) -> tuple[list[Intent], dict[str, Any]]:
    """Collect mutually exclusive causal scenario intents without execution."""

    activity = auction_path_bps(data, router)
    trade_basis = data["trade_index_basis_bps"].astype(float)

    swing, _ = v9.v8.v7.v6.detect_swing_intents(
        data,
        evaluation_start,
        evaluation_end,
        config,
    )
    v9.v8.detect_mesoscale_inventory_intents.original_detector = (
        v9.v8.v7.v6.detect_trend_intents
    )
    trend, _ = v9.v8.detect_mesoscale_inventory_intents(
        data,
        evaluation_start,
        evaluation_end,
        config,
    )

    # V9 persistence confirmation for every negative-basis rejected-sweep
    # failure. This is a complete acceptance state, not a score.
    v9.filter_reversal_failure_intents.original_detector = (
        v9.v8.v7.v6.detect_stress_failure_intents
    )
    stress_failure, _ = v9.filter_reversal_failure_intents(
        data,
        swing,
        config,
    )

    impact, _ = v10.detect_impact_exhaustion_intents(
        data,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
    )

    oi = data["oi_change_xday_15m"].astype(float)
    burst = data["notional_burst_xday_60s"].astype(float)
    window = config.stress_inventory_quantile_window_minutes
    minimum = config.stress_inventory_quantile_min_periods
    quantile = config.stress_inventory_quantile
    positive_oi_cutoff = (
        oi.shift(1).rolling(window, min_periods=minimum).quantile(quantile)
    )
    negative_oi_cutoff = (
        oi.shift(1).rolling(window, min_periods=minimum).quantile(1.0 - quantile)
    )
    burst_cutoff = (
        burst.shift(1).rolling(window, min_periods=minimum).quantile(quantile)
    )

    routed: list[Intent] = []
    counts: dict[str, int] = {
        "normal_failed_auction": 0,
        "normal_inventory": 0,
        "confirmed_stress_failure": 0,
        "fresh_inventory": 0,
        "orderly_oi_contraction": 0,
        "impact_exhaustion": 0,
        "excluded_execution_shock": 0,
        "excluded_dual_tail": 0,
    }

    for parent in swing:
        index = int(parent.signal_index)
        regime = v9.v8.v7.v6.basis_regime(data, index, config)
        if regime < config.basis_stress_threshold_bps:
            continue
        details = {
            **parent.details,
            "auction_route": "NORMAL_BASIS",
            "basis_regime_bps": regime,
            "trade_index_basis_bps": float(trade_basis.iloc[index]),
            "auction_path_240m_bps": float(activity.iloc[index]),
            "compiler": "candidate-04-v22",
        }
        routed.append(
            _copy_intent(
                parent,
                scenario="NORMAL_FAILED_AUCTION_RESUMPTION",
                details=details,
            ),
        )
        counts["normal_failed_auction"] += 1

    for parent in trend:
        index = int(parent.signal_index)
        regime = v9.v8.v7.v6.basis_regime(data, index, config)
        current_basis = float(trade_basis.iloc[index])
        current_activity = float(activity.iloc[index])
        if regime >= config.basis_stress_threshold_bps:
            details = {
                **parent.details,
                "auction_route": "NORMAL_BASIS",
                "basis_regime_bps": regime,
                "trade_index_basis_bps": current_basis,
                "auction_path_240m_bps": current_activity,
                "compiler": "candidate-04-v22",
            }
            routed.append(
                _copy_intent(
                    parent,
                    scenario="NORMAL_INVENTORY_DISPLACEMENT",
                    details=details,
                ),
            )
            counts["normal_inventory"] += 1
            continue

        if not (
            math.isfinite(current_activity)
            and current_activity >= router.low_activity_path_bps_max
            and current_basis < 0.0
        ):
            continue

        raw_oi = float(oi.iloc[index])
        positive_cutoff = max(0.0, float(positive_oi_cutoff.iloc[index]))
        negative_cutoff = min(0.0, float(negative_oi_cutoff.iloc[index]))
        current_burst = float(burst.iloc[index])
        current_burst_cutoff = max(
            config.trend_notional_burst_60s,
            float(burst_cutoff.iloc[index]),
        )
        execution_shock = current_burst > current_burst_cutoff
        fresh_inventory = raw_oi > positive_cutoff
        orderly_contraction = raw_oi < negative_cutoff
        if execution_shock:
            if fresh_inventory or orderly_contraction:
                counts["excluded_dual_tail"] += 1
            else:
                counts["excluded_execution_shock"] += 1
            continue
        if not (fresh_inventory or orderly_contraction):
            continue

        scenario = (
            "FRESH_INVENTORY_ACCEPTANCE"
            if fresh_inventory
            else "ORDERLY_OI_CONTRACTION_CONTINUATION"
        )
        details = {
            **parent.details,
            "auction_route": "HIGH_ACTIVITY_NEGATIVE_BASIS",
            "basis_regime_bps": regime,
            "trade_index_basis_bps": current_basis,
            "auction_path_240m_bps": current_activity,
            "raw_oi_change_15m": raw_oi,
            "positive_oi_tail_cutoff": positive_cutoff,
            "negative_oi_tail_cutoff": negative_cutoff,
            "notional_burst_60s": current_burst,
            "notional_tail_cutoff": current_burst_cutoff,
            "execution_shock": False,
            "inventory_mechanism": (
                "OPEN_INTEREST_CREATION"
                if fresh_inventory
                else "OPEN_INTEREST_CONTRACTION"
            ),
            "compiler": "candidate-04-v22",
        }
        routed.append(_copy_intent(parent, scenario=scenario, details=details))
        counts[
            "fresh_inventory" if fresh_inventory else "orderly_oi_contraction"
        ] += 1

    for parent in stress_failure:
        index = int(parent.signal_index)
        current_activity = float(activity.iloc[index])
        current_basis = float(trade_basis.iloc[index])
        if not (
            math.isfinite(current_activity)
            and current_activity >= router.low_activity_path_bps_max
            and current_basis < 0.0
        ):
            continue
        details = {
            **parent.details,
            "auction_route": "HIGH_ACTIVITY_NEGATIVE_BASIS",
            "trade_index_basis_bps": current_basis,
            "auction_path_240m_bps": current_activity,
            "compiler": "candidate-04-v22",
        }
        routed.append(
            _copy_intent(
                parent,
                scenario="CONFIRMED_STRESS_FAILED_AUCTION_CONTINUATION",
                details=details,
            ),
        )
        counts["confirmed_stress_failure"] += 1

    for parent in impact:
        index = int(parent.signal_index)
        current_activity = float(activity.iloc[index])
        current_basis = float(trade_basis.iloc[index])
        passed = (
            math.isfinite(current_activity)
            and current_activity < router.low_activity_path_bps_max
            and (
                not router.impact_requires_negative_basis
                or current_basis < 0.0
            )
        )
        if not passed:
            continue
        details = {
            **parent.details,
            "auction_route": "LOW_ACTIVITY_NEGATIVE_BASIS",
            "trade_index_basis_bps": current_basis,
            "auction_path_240m_bps": current_activity,
            "compiler": "candidate-04-v22",
        }
        routed.append(
            _copy_intent(
                parent,
                scenario="LIQUIDATION_FAILED_PRICE_DISCOVERY",
                details=details,
            ),
        )
        counts["impact_exhaustion"] += 1

    priority = {
        "NORMAL_FAILED_AUCTION_RESUMPTION": 0,
        "LIQUIDATION_FAILED_PRICE_DISCOVERY": 1,
        "CONFIRMED_STRESS_FAILED_AUCTION_CONTINUATION": 2,
        "FRESH_INVENTORY_ACCEPTANCE": 3,
        "ORDERLY_OI_CONTRACTION_CONTINUATION": 4,
        "NORMAL_INVENTORY_DISPLACEMENT": 5,
    }
    routed.sort(
        key=lambda item: (
            int(item.signal_index),
            priority.get(item.scenario, 99),
        ),
    )

    # One scenario decision per completed bar. Position overlap is intentionally
    # left to the Nautilus strategy/portfolio, not pre-simulated here.
    unique: list[Intent] = []
    seen_signal_indices: set[int] = set()
    for intent in routed:
        index = int(intent.signal_index)
        if index in seen_signal_indices:
            continue
        seen_signal_indices.add(index)
        unique.append(intent)

    return unique, {
        "raw_routed_signals": len(routed),
        "unique_signal_bars": len(unique),
        "route_counts": counts,
        "router_parameters": asdict(router),
    }


def _load_data(
    rich_dir: Path,
    kline_dir: Path,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Config,
    *,
    download_klines: bool,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if download_klines:
        kline_paths = base.ensure_klines(
            config.symbol,
            evaluation_start.date(),
            evaluation_end.date(),
            kline_dir,
        )
    else:
        kline_paths = sorted(kline_dir.glob(f"{config.symbol}-1m-*.zip"))
    rich = base.load_rich(rich_dir)
    klines = base.load_klines(kline_paths)
    required = pd.date_range(
        evaluation_start.normalize(),
        evaluation_end.normalize(),
        freq="1D",
    ).date
    present = set(klines.index.normalize().date)
    missing = [str(day) for day in required if day not in present]
    if missing:
        raise RuntimeError(f"missing evaluation kline days: {missing}")
    data = base.prepare_data(rich, klines, config)

    # Build the exact close-time index used by the Nautilus runner from the same
    # checksummed archives. Positional equality is verified before timestamps
    # are assigned to signals.
    nt_frames = [nt_backtest.read_daily_kline(path) for path in kline_paths]
    nt_frame = pd.concat(nt_frames).sort_index()
    if len(nt_frame) != len(data):
        raise RuntimeError(
            f"Nautilus/compiler row mismatch: nt={len(nt_frame)} data={len(data)}",
        )
    return data, nt_frame


def write_signals(
    output: Path,
    intents: list[Intent],
    summary: dict[str, Any],
    data: pd.DataFrame,
    nt_frame: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for intent in intents:
        index = int(intent.signal_index)
        signal_time = data.index[index]
        if not evaluation_start <= signal_time <= evaluation_end:
            continue
        observe_time = nt_frame.index[index]
        rows.append(
            {
                "scenario": intent.scenario,
                "side": int(intent.side),
                "signal_index": index,
                "signal_time": signal_time.isoformat(),
                "observe_time": observe_time.isoformat(),
                "observe_time_ns": int(observe_time.value),
                "stop_level": float(intent.stop_level),
                "event_indices": [int(value) for value in intent.event_indices],
                "details": _serializable(intent.details),
            },
        )
    (output / "signals.json").write_text(
        json.dumps(rows, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    flat_rows = [
        {
            key: value
            for key, value in row.items()
            if key not in {"details", "event_indices"}
        }
        for row in rows
    ]
    pd.DataFrame(flat_rows).to_csv(output / "signals.csv", index=False)
    summary = {**summary, "written_signals": len(rows)}
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-config", type=Path, required=True)
    parser.add_argument("--impact-config", type=Path, required=True)
    parser.add_argument("--router-config", type=Path, required=True)
    parser.add_argument("--rich-dir", type=Path, required=True)
    parser.add_argument("--kline-dir", type=Path, required=True)
    parser.add_argument("--evaluation-start", required=True)
    parser.add_argument("--evaluation-end", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--download-klines", action="store_true")
    args = parser.parse_args()

    config = Config.load(args.base_config)
    impact_parameters = v10.ImpactParameters.load(args.impact_config)
    router = RouterParameters.load(args.router_config)
    evaluation_start = pd.Timestamp(args.evaluation_start, tz="UTC")
    evaluation_end = (
        pd.Timestamp(args.evaluation_end, tz="UTC")
        + pd.Timedelta(hours=23, minutes=59)
    )
    data, nt_frame = _load_data(
        args.rich_dir,
        args.kline_dir,
        evaluation_start,
        evaluation_end,
        config,
        download_klines=args.download_klines,
    )
    intents, summary = collect_signals(
        data,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
        router,
    )
    write_signals(
        args.output,
        intents,
        summary,
        data,
        nt_frame,
        evaluation_start,
        evaluation_end,
    )


if __name__ == "__main__":
    main()
