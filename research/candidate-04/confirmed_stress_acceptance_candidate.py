#!/usr/bin/env python3
"""Candidate-04 v9: confirm ambiguous stress transitions with auction acceptance.

V8 fixed noisy normal-basis inventory displacement, but its first untouched week
contained only negative-basis stress entries. Two ambiguities remained:

* simultaneous OI-tail and executed-notional-tail events can be liquidation
  climax rather than persistent inventory transfer;
* one close beyond a rejected sweep extreme proves failure of the rejection,
  but not persistence of price acceptance.

V9 preserves every single-mechanism stress inventory branch. It requires the
existing v8 five-minute acceptance state only when both stress mechanisms fire
simultaneously, and for every reversal-failure continuation. Reversal-failure
acceptance additionally requires aligned terminal flow/close location and a
non-climactic terminal minute. No risk, cost, target or sizing rule changes.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
from pathlib import Path
import sys
from typing import Any

import pandas as pd

_BASE_PATH = Path(__file__).with_name("mesoscale_acceptance_candidate.py")
_SPEC = importlib.util.spec_from_file_location("candidate04_v8_base", _BASE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load v8 base candidate from {_BASE_PATH}")
v8 = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = v8
_SPEC.loader.exec_module(v8)

Config = v8.Config
CandidateError = v8.CandidateError
Intent = v8.Intent
Trade = v8.Trade


def acceptance_state(
    data: pd.DataFrame,
    index: int,
    side: int,
    config: Config,
    *,
    require_terminal_confirmation: bool,
) -> dict[str, Any]:
    """Evaluate v8 mesoscale acceptance at one close-confirmed signal."""

    efficiency, cutoff, raw_return = v8.mesoscale_acceptance_series(data, config)
    mesoscale_return = side * float(raw_return.iloc[index])
    mesoscale_efficiency = float(efficiency.iloc[index])
    mesoscale_cutoff = float(cutoff.iloc[index])
    finite = all(
        math.isfinite(value)
        for value in (mesoscale_return, mesoscale_efficiency, mesoscale_cutoff)
    )
    mesoscale_acceptance = (
        finite
        and mesoscale_return > 0.0
        and mesoscale_efficiency >= mesoscale_cutoff
    )

    row = data.iloc[index]
    terminal_efficiency = float(row["eff_60s"])
    terminal_flow = side * float(row["flow_60s"])
    terminal_close_location = v8.v7.v6.v5.directional_close_location(row, side)
    terminal_confirmation = (
        math.isfinite(terminal_efficiency)
        and math.isfinite(terminal_flow)
        and math.isfinite(terminal_close_location)
        and terminal_efficiency <= config.trend_efficiency_60s_max
        and terminal_flow >= config.trend_flow_60s
        and terminal_close_location >= config.trend_close_location
    )
    passed = mesoscale_acceptance and (
        terminal_confirmation if require_terminal_confirmation else True
    )
    return {
        "mesoscale_return": mesoscale_return,
        "mesoscale_efficiency": mesoscale_efficiency,
        "mesoscale_efficiency_cutoff": mesoscale_cutoff,
        "mesoscale_acceptance": mesoscale_acceptance,
        "terminal_efficiency_60s": terminal_efficiency,
        "terminal_flow_60s": terminal_flow,
        "terminal_close_location": terminal_close_location,
        "terminal_confirmation": terminal_confirmation,
        "confirmed_stress_acceptance": passed,
    }


def filter_dual_mechanism_inventory_intents(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Config,
) -> tuple[list[Intent], list[dict[str, Any]]]:
    """Require acceptance only when OI-tail and execution-tail fire together."""

    original = filter_dual_mechanism_inventory_intents.original_detector
    base_intents, base_diagnostics = original(
        data,
        evaluation_start,
        evaluation_end,
        config,
    )
    diagnostics_by_index = {
        int(item.get("signal_index", -1)): dict(item)
        for item in base_diagnostics
    }
    accepted: list[Intent] = []
    diagnostics: list[dict[str, Any]] = []
    seen: set[int] = set()

    for parent in base_intents:
        index = int(parent.signal_index)
        side = int(parent.side)
        oi_transfer = bool(parent.details.get("oi_transfer", False))
        execution_shock = bool(parent.details.get("execution_shock", False))
        dual_mechanism = oi_transfer and execution_shock
        state = acceptance_state(
            data,
            index,
            side,
            config,
            require_terminal_confirmation=False,
        )
        passed = (not dual_mechanism) or bool(
            state["confirmed_stress_acceptance"],
        )
        details = {
            **parent.details,
            **state,
            "dual_stress_mechanism": dual_mechanism,
            "dual_mechanism_acceptance_required": dual_mechanism,
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
        diagnostic = diagnostics_by_index.get(
            index,
            {"time": data.index[index], "signal_index": index, "side": side},
        )
        diagnostics.append(
            {
                **diagnostic,
                **details,
                "passed": passed,
            },
        )
        seen.add(index)

    for item in base_diagnostics:
        index = int(item.get("signal_index", -1))
        if index not in seen:
            diagnostics.append(dict(item))

    return (
        v8.v7.v6.cluster_intents(accepted, config.parent_cluster_minutes),
        diagnostics,
    )


filter_dual_mechanism_inventory_intents.original_detector = (
    v8.v7.detect_stress_inventory_transfer_intents
)


def filter_reversal_failure_intents(
    data: pd.DataFrame,
    swing_intents: list[Intent],
    config: Config,
) -> tuple[list[Intent], list[dict[str, Any]]]:
    """Require persistent auction acceptance after a rejected sweep fails."""

    original = filter_reversal_failure_intents.original_detector
    base_intents, base_diagnostics = original(data, swing_intents, config)
    diagnostic_by_trigger = {
        int(item["trigger_index"]): dict(item)
        for item in base_diagnostics
        if item.get("trigger_index") is not None
    }
    accepted: list[Intent] = []
    diagnostics: list[dict[str, Any]] = []
    seen: set[int] = set()

    for parent in base_intents:
        index = int(parent.signal_index)
        side = int(parent.side)
        state = acceptance_state(
            data,
            index,
            side,
            config,
            require_terminal_confirmation=True,
        )
        details = {**parent.details, **state}
        if state["confirmed_stress_acceptance"]:
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
        diagnostic = diagnostic_by_trigger.get(
            index,
            {"time": data.index[index], "trigger_index": index, "side": side},
        )
        diagnostics.append(
            {
                **diagnostic,
                **details,
                "acceptance_filter_passed": bool(
                    state["confirmed_stress_acceptance"],
                ),
            },
        )
        seen.add(index)

    for item in base_diagnostics:
        trigger = item.get("trigger_index")
        index = int(trigger) if trigger is not None else -1
        if index not in seen:
            diagnostics.append(dict(item))

    return (
        v8.v7.v6.cluster_intents(accepted, config.parent_cluster_minutes),
        diagnostics,
    )


filter_reversal_failure_intents.original_detector = (
    v8.v7.v6.detect_stress_failure_intents
)


def run_candidate(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Config,
) -> tuple[list[Trade], dict[str, Any], list[dict[str, Any]]]:
    original_transfer = v8.v7.detect_stress_inventory_transfer_intents
    original_failure = v8.v7.v6.detect_stress_failure_intents
    filter_dual_mechanism_inventory_intents.original_detector = original_transfer
    filter_reversal_failure_intents.original_detector = original_failure
    v8.v7.detect_stress_inventory_transfer_intents = (
        filter_dual_mechanism_inventory_intents
    )
    v8.v7.v6.detect_stress_failure_intents = filter_reversal_failure_intents
    try:
        return v8.run_candidate(data, evaluation_start, evaluation_end, config)
    finally:
        v8.v7.detect_stress_inventory_transfer_intents = original_transfer
        v8.v7.v6.detect_stress_failure_intents = original_failure


def write_outputs(
    output: Path,
    trades: list[Trade],
    metrics: dict[str, Any],
    diagnostics: list[dict[str, Any]],
    config_path: Path,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
) -> None:
    v8.write_outputs(
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
    run["candidate"] = "candidate-04-v9-confirmed-stress-acceptance"
    extra = dict(run.get("extra", {}))
    extra.update(
        {
            "candidate": "candidate-04-v9-confirmed-stress-acceptance",
            "dual_mechanism_requires_acceptance": True,
            "reversal_failure_requires_terminal_acceptance": True,
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
        kline_paths = v8.v7.v6.v5.ensure_klines(
            config.symbol,
            evaluation_start.date(),
            evaluation_end.date(),
            args.kline_dir,
        )
    else:
        kline_paths = sorted(args.kline_dir.glob(f"{config.symbol}-1m-*.zip"))
    rich = v8.v7.v6.v5.load_rich(args.rich_dir)
    klines = v8.v7.v6.v5.load_klines(kline_paths)
    required = pd.date_range(
        evaluation_start.normalize(),
        evaluation_end.normalize(),
        freq="1D",
    ).date
    present = set(klines.index.normalize().date)
    missing = [str(day) for day in required if day not in present]
    if missing:
        raise CandidateError(f"missing evaluation kline days: {missing}")

    data = v8.v7.v6.v5.prepare_data(rich, klines, config)
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
    print(json.dumps(v8.v7.v6.v5.serializable(metrics), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
