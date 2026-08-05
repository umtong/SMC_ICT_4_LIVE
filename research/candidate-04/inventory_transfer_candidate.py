#!/usr/bin/env python3
"""Candidate-04 v7: basis-regime inventory-transfer state machine.

The v6 basis branch remains unchanged. In a negative-basis deleveraging regime,
continuation may be caused by either of two independent inventory mechanisms:

* a side-adjusted open-interest shock (long: OI creation; short: OI liquidation),
* an extreme executed-notional burst while the base displacement state holds.

Requiring both mechanisms simultaneously made valid episodes sparse. The OR is
not a score or risk multiplier: each branch is a complete causal confirmation
of inventory transfer. All thresholds are shifted and past-only.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any

import pandas as pd

_BASE_PATH = Path(__file__).with_name("regime_transition_candidate.py")
_SPEC = importlib.util.spec_from_file_location("candidate04_v6_base", _BASE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load v6 base candidate from {_BASE_PATH}")
v6 = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = v6
_SPEC.loader.exec_module(v6)

Config = v6.Config
CandidateError = v6.CandidateError
Intent = v6.Intent
Trade = v6.Trade


def detect_stress_inventory_transfer_intents(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Config,
) -> tuple[list[Intent], list[dict[str, Any]]]:
    """Select stress-regime displacement via OI transfer OR execution shock.

    The underlying v5 trend detector has already required 240-minute
    displacement, a 15-minute pullback, a fixed five-minute structure break,
    directional body/flow/close location, absorption, low efficiency and
    non-accelerating terminal flow. This function only diagnoses the inventory
    mechanism which confirms that displacement.
    """

    base_intents, _ = v6.detect_trend_intents(
        data,
        evaluation_start,
        evaluation_end,
        config,
    )
    window = config.stress_inventory_quantile_window_minutes
    minimum = config.stress_inventory_quantile_min_periods
    quantile = config.stress_inventory_quantile
    raw_oi = data["oi_change_xday_15m"]
    burst_series = data["notional_burst_xday_60s"]
    burst_threshold = (
        burst_series.shift(1)
        .rolling(window, min_periods=minimum)
        .quantile(quantile)
    )
    directional_oi_threshold = {
        side: (
            (side * raw_oi).shift(1)
            .rolling(window, min_periods=minimum)
            .quantile(quantile)
        )
        for side in (-1, 1)
    }

    intents: list[Intent] = []
    diagnostics: list[dict[str, Any]] = []
    for parent in base_intents:
        index = parent.signal_index
        regime = v6.basis_regime(data, index, config)
        if regime >= config.basis_stress_threshold_bps:
            continue

        side = parent.side
        directional_oi = side * float(raw_oi.iloc[index])
        oi_cutoff = max(
            0.0,
            float(directional_oi_threshold[side].iloc[index]),
        )
        burst = float(burst_series.iloc[index])
        burst_cutoff = max(
            config.trend_notional_burst_60s,
            float(burst_threshold.iloc[index]),
        )
        oi_transfer = directional_oi > oi_cutoff
        execution_shock = burst > burst_cutoff
        passed = oi_transfer or execution_shock

        details = {
            **parent.details,
            "basis_regime_bps": regime,
            "directional_oi_change_15m": directional_oi,
            "directional_oi_quantile_cutoff": oi_cutoff,
            "notional_burst_60s": burst,
            "notional_quantile_cutoff": burst_cutoff,
            "oi_transfer": oi_transfer,
            "execution_shock": execution_shock,
        }
        if passed:
            intents.append(
                Intent(
                    scenario="STRESS_INVENTORY_SHOCK_DISPLACEMENT",
                    side=side,
                    signal_index=parent.signal_index,
                    entry_index=parent.entry_index,
                    stop_level=parent.stop_level,
                    event_indices=parent.event_indices,
                    details=details,
                ),
            )
        diagnostics.append(
            {
                "time": data.index[index],
                "signal_index": index,
                "side": side,
                **details,
                "passed": passed,
            },
        )

    return (
        v6.cluster_intents(intents, config.parent_cluster_minutes),
        diagnostics,
    )


def run_candidate(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Config,
) -> tuple[list[Trade], dict[str, Any], list[dict[str, Any]]]:
    original = v6.detect_stress_inventory_shock_intents
    v6.detect_stress_inventory_shock_intents = (
        detect_stress_inventory_transfer_intents
    )
    try:
        return v6.run_candidate(data, evaluation_start, evaluation_end, config)
    finally:
        v6.detect_stress_inventory_shock_intents = original


def write_outputs(
    output: Path,
    trades: list[Trade],
    metrics: dict[str, Any],
    diagnostics: list[dict[str, Any]],
    config_path: Path,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
) -> None:
    v6.write_outputs(
        output,
        trades,
        metrics,
        diagnostics,
        config_path,
        evaluation_start,
        evaluation_end,
    )
    run_path = output / "run.json"
    run = json.loads(run_path.read_text(encoding="utf-8"))
    run["candidate"] = "candidate-04-v7-inventory-transfer"
    extra = dict(run.get("extra", {}))
    extra["candidate"] = "candidate-04-v7-inventory-transfer"
    run["extra"] = extra
    run_path.write_text(
        json.dumps(run, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--rich-dir", type=Path, required=True)
    parser.add_argument("--kline-dir", type=Path, required=True)
    parser.add_argument("--evaluation-start", required=True)
    parser.add_argument("--evaluation-end", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--download-klines", action="store_true")
    args = parser.parse_args()

    config = Config.load(args.config)
    evaluation_start = pd.Timestamp(args.evaluation_start, tz="UTC")
    evaluation_end = (
        pd.Timestamp(args.evaluation_end, tz="UTC")
        + pd.Timedelta(hours=23, minutes=59)
    )
    if args.download_klines:
        kline_paths = v6.v5.ensure_klines(
            config.symbol,
            evaluation_start.date(),
            evaluation_end.date(),
            args.kline_dir,
        )
    else:
        kline_paths = sorted(
            args.kline_dir.glob(f"{config.symbol}-1m-*.zip"),
        )
    rich = v6.v5.load_rich(args.rich_dir)
    klines = v6.v5.load_klines(kline_paths)
    required = pd.date_range(
        evaluation_start.normalize(),
        evaluation_end.normalize(),
        freq="1D",
    ).date
    present = set(klines.index.normalize().date)
    missing = [str(day) for day in required if day not in present]
    if missing:
        raise CandidateError(f"missing evaluation kline days: {missing}")

    data = v6.v5.prepare_data(rich, klines, config)
    trades, metrics, diagnostics = run_candidate(
        data,
        evaluation_start,
        evaluation_end,
        config,
    )
    write_outputs(
        args.output,
        trades,
        metrics,
        diagnostics,
        args.config,
        evaluation_start,
        evaluation_end,
    )
    print(json.dumps(v6.v5.serializable(metrics), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
