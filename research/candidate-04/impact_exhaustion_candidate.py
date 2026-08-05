#!/usr/bin/env python3
"""Candidate-04 v10: event-time impact exhaustion after failed price discovery.

The state is not a generic overbought/oversold fade. It requires:

1. executed taker imbalance at or above its shifted past-only q80,
2. notional activity at or above its shifted past-only q90,
3. a 60-second price move at or above its shifted past-only q70,
4. high price-path efficiency, proving genuine displacement,
5. taker dominance below 80%, excluding one-sided liquidation cascades, and
6. a close through the shock minute's origin in the opposite direction within
   three fully observed minutes.

The rapid origin reclaim is a failed price-discovery event. Entry occurs at the
next minute open, invalidation is beyond the complete shock/reclaim extreme,
and the position seeks 2.0 net R. Existing cost, funding, risk sizing, same-bar
stop priority and global one-position contracts are reused unchanged.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, replace
import importlib.util
import json
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

_BASE_PATH = Path(__file__).with_name("swing_displacement_candidate.py")
_SPEC = importlib.util.spec_from_file_location("candidate04_impact_base", _BASE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load candidate foundation from {_BASE_PATH}")
base = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = base
_SPEC.loader.exec_module(base)

Config = base.Config
CandidateError = base.CandidateError
Intent = base.Intent
Trade = base.Trade


@dataclass(frozen=True, slots=True)
class ImpactParameters:
    flow_quantile: float
    maximum_absolute_flow: float
    notional_burst_quantile: float
    absolute_return_quantile: float
    minimum_efficiency_60s: float
    confirmation_minutes: int
    cooldown_minutes: int
    stop_buffer_atr: float
    quantile_window_minutes: int
    quantile_min_periods: int
    target_net_r: float
    maximum_hold_minutes: int

    @classmethod
    def load(cls, path: Path) -> "ImpactParameters":
        values = json.loads(path.read_text(encoding="utf-8"))
        result = cls(**values)
        result.validate()
        return result

    def validate(self) -> None:
        for name in (
            "flow_quantile",
            "notional_burst_quantile",
            "absolute_return_quantile",
        ):
            value = float(getattr(self, name))
            if not 0.0 < value < 1.0:
                raise CandidateError(f"{name} must be in (0, 1)")
        if not 0.0 < self.maximum_absolute_flow <= 1.0:
            raise CandidateError("maximum_absolute_flow must be in (0, 1]")
        if not 0.0 <= self.minimum_efficiency_60s <= 1.0:
            raise CandidateError("minimum_efficiency_60s must be in [0, 1]")
        if self.confirmation_minutes < 1 or self.cooldown_minutes < 1:
            raise CandidateError("confirmation and cooldown must be positive")
        if self.quantile_min_periods < 1:
            raise CandidateError("quantile_min_periods must be positive")
        if self.quantile_window_minutes < self.quantile_min_periods:
            raise CandidateError("quantile window must cover minimum periods")
        if self.target_net_r <= 0 or self.maximum_hold_minutes < 1:
            raise CandidateError("target and maximum hold must be positive")


def shifted_quantile(
    series: pd.Series,
    quantile: float,
    parameters: ImpactParameters,
) -> pd.Series:
    """Past-only rolling quantile; the current row never calibrates itself."""

    return (
        series.replace([np.inf, -np.inf], np.nan)
        .shift(1)
        .rolling(
            parameters.quantile_window_minutes,
            min_periods=parameters.quantile_min_periods,
        )
        .quantile(quantile)
    )


def impact_cutoffs(
    data: pd.DataFrame,
    parameters: ImpactParameters,
) -> dict[str, pd.Series]:
    return {
        "absolute_flow": shifted_quantile(
            data["flow_60s"].astype(float).abs(),
            parameters.flow_quantile,
            parameters,
        ),
        "notional_burst": shifted_quantile(
            data["notional_burst_xday_60s"].astype(float),
            parameters.notional_burst_quantile,
            parameters,
        ),
        "absolute_return": shifted_quantile(
            data["ret_60s_bps"].astype(float).abs(),
            parameters.absolute_return_quantile,
            parameters,
        ),
    }


def detect_impact_exhaustion_intents(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Config,
    parameters: ImpactParameters,
) -> tuple[list[Intent], list[dict[str, Any]]]:
    """Detect failed high-impact price discovery with next-minute execution."""

    cutoffs = impact_cutoffs(data, parameters)
    intents: list[Intent] = []
    diagnostics: list[dict[str, Any]] = []
    last_accepted_index = -10**12

    for index in range(parameters.quantile_min_periods, len(data) - 1):
        timestamp = data.index[index]
        if timestamp < evaluation_start or timestamp > evaluation_end:
            continue
        if index - last_accepted_index < parameters.cooldown_minutes:
            continue

        row = data.iloc[index]
        flow = float(row["flow_60s"])
        absolute_flow = abs(flow)
        burst = float(row["notional_burst_xday_60s"])
        efficiency = float(row["eff_60s"])
        signed_return = float(row["ret_60s_bps"])
        absolute_return = abs(signed_return)
        flow_floor = float(cutoffs["absolute_flow"].iloc[index])
        burst_floor = float(cutoffs["notional_burst"].iloc[index])
        return_floor = float(cutoffs["absolute_return"].iloc[index])

        finite = all(
            math.isfinite(value)
            for value in (
                flow,
                burst,
                efficiency,
                signed_return,
                flow_floor,
                burst_floor,
                return_floor,
            )
        )
        if not finite:
            continue
        if absolute_flow < flow_floor:
            continue
        if absolute_flow > parameters.maximum_absolute_flow:
            diagnostics.append(
                {
                    "time": timestamp,
                    "index": index,
                    "state": "CASCADE_DOMINANCE_REJECTED",
                    "absolute_flow": absolute_flow,
                    "maximum_absolute_flow": parameters.maximum_absolute_flow,
                },
            )
            continue
        if burst < burst_floor or efficiency < parameters.minimum_efficiency_60s:
            continue
        if absolute_return < return_floor or flow * signed_return <= 0.0:
            continue

        shock_side = 1 if signed_return > 0.0 else -1
        trade_side = -shock_side
        origin = float(row["open"])
        confirmation_index: int | None = None
        upper = min(
            index + parameters.confirmation_minutes,
            len(data) - 2,
        )
        for candidate_index in range(index + 1, upper + 1):
            close = float(data["close"].iloc[candidate_index])
            reclaimed = close > origin if trade_side == 1 else close < origin
            if reclaimed:
                confirmation_index = candidate_index
                break

        if confirmation_index is None:
            diagnostics.append(
                {
                    "time": timestamp,
                    "index": index,
                    "state": "IMPACT_ACCEPTED_NO_RECLAIM",
                    "shock_side": shock_side,
                    "absolute_flow": absolute_flow,
                    "notional_burst": burst,
                    "efficiency_60s": efficiency,
                    "absolute_return_bps": absolute_return,
                },
            )
            continue

        entry_index = confirmation_index + 1
        if entry_index >= len(data) or data.index[entry_index] > evaluation_end:
            continue
        segment = data.iloc[index : confirmation_index + 1]
        extreme = float(
            segment["low"].min()
            if trade_side == 1
            else segment["high"].max()
        )
        atr = float(row["atr"])
        if not math.isfinite(atr) or atr <= 0.0:
            continue
        stop_level = extreme - trade_side * parameters.stop_buffer_atr * atr
        details = {
            "shock_index": index,
            "confirmation_index": confirmation_index,
            "shock_side": shock_side,
            "origin": origin,
            "absolute_flow": absolute_flow,
            "flow_floor": flow_floor,
            "maximum_absolute_flow": parameters.maximum_absolute_flow,
            "notional_burst": burst,
            "notional_burst_floor": burst_floor,
            "efficiency_60s": efficiency,
            "absolute_return_bps": absolute_return,
            "absolute_return_floor_bps": return_floor,
            "confirmation_delay_minutes": confirmation_index - index,
            "cluster_events": 1,
        }
        intents.append(
            Intent(
                scenario="IMPACT_EXHAUSTION_FAILED_PRICE_DISCOVERY",
                side=trade_side,
                signal_index=confirmation_index,
                entry_index=entry_index,
                stop_level=stop_level,
                event_indices=(index, confirmation_index),
                details=details,
            ),
        )
        diagnostics.append(
            {
                "time": timestamp,
                "index": index,
                "state": "FAILED_PRICE_DISCOVERY_CONFIRMED",
                "side": trade_side,
                **details,
            },
        )
        last_accepted_index = index

    return intents, diagnostics


def run_candidate(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    base_config: Config,
    parameters: ImpactParameters,
) -> tuple[list[Trade], dict[str, Any], list[dict[str, Any]]]:
    """Reuse the audited execution/accounting path with only this scenario."""

    config = replace(
        base_config,
        target_net_r=parameters.target_net_r,
        trend_max_hold_minutes=parameters.maximum_hold_minutes,
    )
    original_swing = base.detect_swing_intents
    original_trend = base.detect_trend_intents

    def impact_detector(
        frame: pd.DataFrame,
        start: pd.Timestamp,
        end: pd.Timestamp,
        candidate_config: Config,
    ) -> tuple[list[Intent], list[dict[str, Any]]]:
        return detect_impact_exhaustion_intents(
            frame,
            start,
            end,
            candidate_config,
            parameters,
        )

    def empty_detector(
        frame: pd.DataFrame,
        start: pd.Timestamp,
        end: pd.Timestamp,
        candidate_config: Config,
    ) -> tuple[list[Intent], list[dict[str, Any]]]:
        del frame, start, end, candidate_config
        return [], []

    base.detect_swing_intents = impact_detector
    base.detect_trend_intents = empty_detector
    try:
        trades, metrics, diagnostics = base.run_candidate(
            data,
            evaluation_start,
            evaluation_end,
            config,
        )
    finally:
        base.detect_swing_intents = original_swing
        base.detect_trend_intents = original_trend
    metrics = dict(metrics)
    metrics["candidate"] = "candidate-04-v10-impact-exhaustion"
    metrics["impact_parameters"] = asdict(parameters)
    return trades, metrics, diagnostics


def write_outputs(
    output: Path,
    trades: list[Trade],
    metrics: dict[str, Any],
    diagnostics: list[dict[str, Any]],
    base_config_path: Path,
    strategy_config_path: Path,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
) -> None:
    base.write_outputs(
        output,
        trades,
        metrics,
        diagnostics,
        strategy_config_path,
        evaluation_start,
        evaluation_end,
    )
    run_path = output / "run.json"
    run = json.loads(run_path.read_text(encoding="utf-8"))
    run["candidate"] = "candidate-04-v10-impact-exhaustion"
    extra = dict(run.get("extra", {}))
    extra.update(
        {
            "candidate": "candidate-04-v10-impact-exhaustion",
            "base_config_path": str(base_config_path),
            "base_config_sha256": base.sha256_file(base_config_path),
            "strategy_config_path": str(strategy_config_path),
            "strategy_config_sha256": base.sha256_file(strategy_config_path),
        },
    )
    run["extra"] = extra
    run_path.write_text(
        json.dumps(base.serializable(run), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-config", type=Path, required=True)
    parser.add_argument("--strategy-config", type=Path, required=True)
    parser.add_argument("--rich-dir", type=Path, required=True)
    parser.add_argument("--kline-dir", type=Path, required=True)
    parser.add_argument("--evaluation-start", required=True)
    parser.add_argument("--evaluation-end", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--download-klines", action="store_true")
    args = parser.parse_args()

    config = Config.load(args.base_config)
    parameters = ImpactParameters.load(args.strategy_config)
    evaluation_start = pd.Timestamp(args.evaluation_start, tz="UTC")
    evaluation_end = (
        pd.Timestamp(args.evaluation_end, tz="UTC")
        + pd.Timedelta(hours=23, minutes=59)
    )
    if args.download_klines:
        kline_paths = base.ensure_klines(
            config.symbol,
            evaluation_start.date(),
            evaluation_end.date(),
            args.kline_dir,
        )
    else:
        kline_paths = sorted(args.kline_dir.glob(f"{config.symbol}-1m-*.zip"))
    rich = base.load_rich(args.rich_dir)
    klines = base.load_klines(kline_paths)
    required = pd.date_range(
        evaluation_start.normalize(),
        evaluation_end.normalize(),
        freq="1D",
    ).date
    present = set(klines.index.normalize().date)
    missing = [str(day) for day in required if day not in present]
    if missing:
        raise CandidateError(f"missing evaluation kline days: {missing}")

    data = base.prepare_data(rich, klines, config)
    trades, metrics, diagnostics = run_candidate(
        data,
        evaluation_start,
        evaluation_end,
        config,
        parameters,
    )
    write_outputs(
        args.output,
        trades,
        metrics,
        diagnostics,
        args.base_config,
        args.strategy_config,
        evaluation_start,
        evaluation_end,
    )
    print(json.dumps(base.serializable(metrics), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
