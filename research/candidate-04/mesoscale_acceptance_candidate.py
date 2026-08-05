#!/usr/bin/env python3
"""Candidate-04 v8: mesoscale auction acceptance before inventory continuation.

V7 showed that non-contracting open interest plus a one-minute displacement is
not sufficient: it also admits isolated impulses inside a noisy auction. V8
keeps the complete v7 basis-regime state machine, but requires normal-basis
inventory continuation to demonstrate price acceptance across two scales:

* the last five minutes must have moved in the intended direction, and
* five-minute net/path efficiency must be at or above its past-only rolling
  65th percentile.

The existing v7 one-minute efficiency ceiling remains in force. Together these
conditions express a causal shape: efficient multi-minute acceptance with a
non-climactic terminal minute. Negative-basis stress branches are unchanged.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

_BASE_PATH = Path(__file__).with_name("inventory_transfer_candidate.py")
_SPEC = importlib.util.spec_from_file_location("candidate04_v7_base", _BASE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load v7 base candidate from {_BASE_PATH}")
v7 = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = v7
_SPEC.loader.exec_module(v7)

Config = v7.Config
CandidateError = v7.CandidateError
Intent = v7.Intent
Trade = v7.Trade

ACCEPTANCE_QUANTILE = 0.65


def mesoscale_acceptance_series(
    data: pd.DataFrame,
    config: Config,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Return directional-neutral efficiency, past cutoff and raw return.

    A row is knowable only after its minute closes. The quantile is shifted one
    row, therefore the current observation never calibrates its own cutoff.
    """

    minutes = int(config.trend_structure_minutes)
    if minutes < 2:
        raise CandidateError("mesoscale acceptance requires at least two minutes")
    close = data["close"].astype(float)
    one_minute_path = close.pct_change(fill_method=None).abs()
    raw_return = close.pct_change(minutes, fill_method=None)
    efficiency = (
        raw_return.abs()
        / one_minute_path.rolling(minutes, min_periods=minutes).sum().replace(0.0, np.nan)
    )
    cutoff = (
        efficiency.shift(1)
        .rolling(
            int(config.stress_inventory_quantile_window_minutes),
            min_periods=int(config.stress_inventory_quantile_min_periods),
        )
        .quantile(ACCEPTANCE_QUANTILE)
    )
    return efficiency, cutoff, raw_return


def math_is_finite(value: float) -> bool:
    return bool(np.isfinite(value))


def detect_mesoscale_inventory_intents(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Config,
) -> tuple[list[Intent], list[dict[str, Any]]]:
    """Filter only normal-basis inventory intents by five-minute acceptance."""

    original = detect_mesoscale_inventory_intents.original_detector
    base_intents, base_diagnostics = original(
        data,
        evaluation_start,
        evaluation_end,
        config,
    )
    efficiency, cutoff, raw_return = mesoscale_acceptance_series(data, config)

    accepted: list[Intent] = []
    diagnostics: list[dict[str, Any]] = []
    diagnostic_by_index = {
        int(item.get("index", -1)): dict(item) for item in base_diagnostics
    }
    for parent in base_intents:
        index = int(parent.signal_index)
        regime = float(v7.v6.basis_regime(data, index, config))
        side = int(parent.side)
        current_efficiency = float(efficiency.iloc[index])
        current_cutoff = float(cutoff.iloc[index])
        directional_return = side * float(raw_return.iloc[index])
        normal_regime = regime >= config.basis_stress_threshold_bps
        acceptance = (
            math_is_finite(current_efficiency)
            and math_is_finite(current_cutoff)
            and directional_return > 0.0
            and current_efficiency >= current_cutoff
        )
        passed = (not normal_regime) or acceptance
        details = {
            **parent.details,
            "basis_regime_bps": regime,
            "mesoscale_return": directional_return,
            "mesoscale_efficiency": current_efficiency,
            "mesoscale_efficiency_cutoff": current_cutoff,
            "mesoscale_acceptance": acceptance,
        }
        if passed:
            accepted.append(
                Intent(
                    scenario=parent.scenario,
                    side=side,
                    signal_index=parent.signal_index,
                    entry_index=parent.entry_index,
                    stop_level=parent.stop_level,
                    event_indices=parent.event_indices,
                    details=details,
                ),
            )
        diagnostic = diagnostic_by_index.get(
            index,
            {"index": index, "time": data.index[index]},
        )
        diagnostics.append(
            {
                **diagnostic,
                **details,
                "mesoscale_filter_passed": passed,
            },
        )

    return accepted, diagnostics


detect_mesoscale_inventory_intents.original_detector = v7.v6.detect_trend_intents


def run_candidate(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Config,
) -> tuple[list[Trade], dict[str, Any], list[dict[str, Any]]]:
    original = v7.v6.detect_trend_intents
    detect_mesoscale_inventory_intents.original_detector = original
    v7.v6.detect_trend_intents = detect_mesoscale_inventory_intents
    try:
        return v7.run_candidate(data, evaluation_start, evaluation_end, config)
    finally:
        v7.v6.detect_trend_intents = original


def write_outputs(
    output: Path,
    trades: list[Trade],
    metrics: dict[str, Any],
    diagnostics: list[dict[str, Any]],
    config_path: Path,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
) -> None:
    v7.write_outputs(
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
    run["candidate"] = "candidate-04-v8-mesoscale-acceptance"
    extra = dict(run.get("extra", {}))
    extra.update(
        {
            "candidate": "candidate-04-v8-mesoscale-acceptance",
            "acceptance_quantile": ACCEPTANCE_QUANTILE,
        },
    )
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
        kline_paths = v7.v6.v5.ensure_klines(
            config.symbol,
            evaluation_start.date(),
            evaluation_end.date(),
            args.kline_dir,
        )
    else:
        kline_paths = sorted(args.kline_dir.glob(f"{config.symbol}-1m-*.zip"))
    rich = v7.v6.v5.load_rich(args.rich_dir)
    klines = v7.v6.v5.load_klines(kline_paths)
    required = pd.date_range(
        evaluation_start.normalize(),
        evaluation_end.normalize(),
        freq="1D",
    ).date
    present = set(klines.index.normalize().date)
    missing = [str(day) for day in required if day not in present]
    if missing:
        raise CandidateError(f"missing evaluation kline days: {missing}")

    data = v7.v6.v5.prepare_data(rich, klines, config)
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
    print(json.dumps(v7.v6.v5.serializable(metrics), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
