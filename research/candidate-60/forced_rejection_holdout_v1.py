#!/usr/bin/env python3
"""Frozen untouched test of a forced-deleveraging rejection transition.

The event contract is reused byte-for-byte from Candidate 16 v9/v10.  This file
adds one pre-registered transition and never searches thresholds on the
untouched interval.

Primary causal sequence
-----------------------
1. A globally de-clustered liquidation episode is classified *at event time* as
   FORCED_BASIS_DISLOCATION or FORCED_OI_DERIVATIVES_LEAD by the frozen v9
   contract.
2. During completed minutes t=1..3 after that event, the first minute must show
   all of:
      - perpetual close reaccepted past the event bar midpoint in reversal side;
      - spot close crossed the event spot close in reversal side;
      - perp/spot basis contracted from its event value in reversal side;
      - open interest is below the event-time level.
3. Entry observation is the next completed minute's open.  The primary labelled
   outcome is the reversal-side log return to the close of the 15th bar after
   entry, minus the frozen 20 bp round-trip diagnostic cost.

This is an untouched signal-mechanism test, not a NautilusTrader fill/account or
NAV claim.  A production strategy requires a separately validated invalidation,
risk sizing, execution and integrated-account test.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

FORCED_REGIMES = frozenset({
    "FORCED_BASIS_DISLOCATION",
    "FORCED_OI_DERIVATIVES_LEAD",
})
MAX_CONFIRM_MINUTE = 3
PRIMARY_HOLD_MINUTES = 15
ROUND_TRIP_COST_RATE = 0.0020
SOURCE_COMMIT = "d35fe7c3556a387933103e18d491ab56d2f37c18"
RULE_ID = "C60_FORCED_REJECTION_T3_SPOT_BASIS_OI_V1"


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _sha256_path(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _obtain_all(base: Any, start_month: str, end_month: str, cache: Path):
    days: list[date] = base._months(start_month, end_month)
    requests = [(symbol, day) for day in days for symbol in base.SYMBOLS]
    obtained: dict[tuple[str, date], dict[str, Path]] = {}
    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = {
            executor.submit(base.obtain_day, symbol, day, cache): (symbol, day)
            for symbol, day in requests
        }
        for future in as_completed(futures):
            key = futures[future]
            obtained[key] = future.result()
            print(f"OBTAINED {key[0]} {key[1]}", flush=True)
    return days, obtained


def _build_panel(base: Any, days, obtained) -> pd.DataFrame:
    panels: list[pd.DataFrame] = []
    for day in days:
        for symbol in base.SYMBOLS:
            panels.append(base.build_day(symbol, day, obtained[(symbol, day)]))
    panel = pd.concat(panels, ignore_index=True)
    panel["minute"] = pd.to_datetime(panel["minute"], utc=True).astype(
        "datetime64[ns, UTC]"
    )
    return base.apply_causal_event_thresholds(panel)


def _robust(values: pd.Series) -> dict[str, float | int | None]:
    array = pd.to_numeric(values, errors="coerce").dropna().to_numpy(float)
    if len(array) == 0:
        return {
            "n": 0,
            "mean": None,
            "median": None,
            "positive_rate": None,
            "trim_best_mean": None,
            "trim_worst_mean": None,
            "largest_absolute_share": None,
            "minimum": None,
            "maximum": None,
        }
    absolute_sum = float(np.abs(array).sum())
    return {
        "n": int(len(array)),
        "mean": float(array.mean()),
        "median": float(np.median(array)),
        "positive_rate": float((array > 0.0).mean()),
        "trim_best_mean": (
            float(np.delete(array, int(np.argmax(array))).mean())
            if len(array) > 1
            else None
        ),
        "trim_worst_mean": (
            float(np.delete(array, int(np.argmin(array))).mean())
            if len(array) > 1
            else None
        ),
        "largest_absolute_share": (
            float(np.max(np.abs(array)) / absolute_sum)
            if absolute_sum > 0.0
            else None
        ),
        "minimum": float(array.min()),
        "maximum": float(array.max()),
    }


def select_transitions(paths: pd.DataFrame) -> pd.DataFrame:
    required = {
        "event_id", "event_minute", "minute", "t_min", "symbol", "sample_day",
        "event_regime", "event_direction", "reversal_side", "event_open",
        "event_high", "event_low", "event_close", "event_mid", "event_vwap_4h",
        "perp_open", "perp_high", "perp_low", "perp_close", "spot_close",
        "open_interest", "reversal_spot_return_from_event",
        "reversal_close_vs_event_mid", "perp_basis_contraction_for_reversal",
        "oi_change_from_event",
    }
    missing = sorted(required - set(paths.columns))
    if missing:
        raise ValueError(f"path schema missing required fields: {missing}")

    rows: list[dict[str, Any]] = []
    paths = paths.sort_values(["event_id", "t_min"], kind="stable")
    for event_id, group in paths.groupby("event_id", sort=True):
        group = group.sort_values("t_min", kind="stable").reset_index(drop=True)
        positions = {int(value): index for index, value in enumerate(group["t_min"])}
        eligible = group[
            group["t_min"].between(1, MAX_CONFIRM_MINUTE)
            & group["reversal_close_vs_event_mid"].gt(0.0)
            & group["reversal_spot_return_from_event"].gt(0.0)
            & group["perp_basis_contraction_for_reversal"].gt(0.0)
            & group["oi_change_from_event"].lt(0.0)
        ]
        if eligible.empty:
            continue
        confirmation = eligible.iloc[0]
        confirmation_t = int(confirmation["t_min"])
        entry_index = positions.get(confirmation_t + 1)
        exit_index = positions.get(confirmation_t + PRIMARY_HOLD_MINUTES)
        if entry_index is None or exit_index is None:
            continue
        entry = group.iloc[entry_index]
        exit_row = group.iloc[exit_index]
        side = int(confirmation["reversal_side"])
        entry_price = float(entry["perp_open"])
        exit_price = float(exit_row["perp_close"])
        gross = side * math.log(exit_price / entry_price)

        window = group[
            group["t_min"].between(
                confirmation_t + 1,
                confirmation_t + PRIMARY_HOLD_MINUTES,
            )
        ]
        if side > 0:
            mfe = float(np.log(window["perp_high"] / entry_price).max())
            mae = float(np.log(window["perp_low"] / entry_price).min())
        else:
            mfe = float(np.log(entry_price / window["perp_low"]).max())
            mae = float(np.log(entry_price / window["perp_high"]).min())

        event_range = float(confirmation["event_high"] - confirmation["event_low"])
        pre_confirmation = group[group["t_min"].between(0, confirmation_t)]
        if side > 0:
            emergency_stop = (
                float(pre_confirmation["perp_low"].min()) - 2.0 * event_range
            ) * (1.0 - 0.0002)
            emergency_hit = bool((window["perp_low"] <= emergency_stop).any())
        else:
            emergency_stop = (
                float(pre_confirmation["perp_high"].max()) + 2.0 * event_range
            ) * (1.0 + 0.0002)
            emergency_hit = bool((window["perp_high"] >= emergency_stop).any())
        emergency_risk = abs(math.log(emergency_stop / entry_price))

        rows.append({
            "rule_id": RULE_ID,
            "event_id": int(event_id),
            "event_minute": str(confirmation["event_minute"]),
            "sample_day": str(confirmation["sample_day"]),
            "symbol": str(confirmation["symbol"]),
            "event_regime": str(confirmation["event_regime"]),
            "regime_group": (
                "FORCED"
                if str(confirmation["event_regime"]) in FORCED_REGIMES
                else str(confirmation["event_regime"])
            ),
            "event_direction": int(confirmation["event_direction"]),
            "side": side,
            "confirmation_t": confirmation_t,
            "entry_t": confirmation_t + 1,
            "exit_t": confirmation_t + PRIMARY_HOLD_MINUTES,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "gross_log_return": gross,
            "primary_net_log_return": gross - ROUND_TRIP_COST_RATE,
            "exact_opposite_net_log_return": -gross - ROUND_TRIP_COST_RATE,
            "mfe_15m": mfe,
            "mae_15m": mae,
            "event_range_fraction": event_range / float(confirmation["event_close"]),
            "emergency_stop": emergency_stop,
            "emergency_risk_fraction": emergency_risk,
            "emergency_hit_before_primary_exit": int(emergency_hit),
            "primary_net_r_at_emergency_risk": (
                (gross - ROUND_TRIP_COST_RATE) / emergency_risk
                if emergency_risk > 0.0
                else None
            ),
            "reversal_close_vs_event_mid": float(
                confirmation["reversal_close_vs_event_mid"]
            ),
            "reversal_spot_return_from_event": float(
                confirmation["reversal_spot_return_from_event"]
            ),
            "perp_basis_contraction_for_reversal": float(
                confirmation["perp_basis_contraction_for_reversal"]
            ),
            "oi_change_from_event": float(confirmation["oi_change_from_event"]),
        })
    return pd.DataFrame(rows)


def summarize(
    *,
    transitions: pd.DataFrame,
    events: pd.DataFrame,
    start_month: str,
    end_month: str,
    sample_days: int,
    source_hashes: dict[str, str],
) -> dict[str, Any]:
    groups: dict[str, Any] = {}
    if not transitions.empty:
        for name, group in transitions.groupby("regime_group", sort=True):
            groups[str(name)] = {
                "events": int(len(group)),
                "calendar_days_with_transition": int(group["sample_day"].nunique()),
                "symbols": int(group["symbol"].nunique()),
                "confirmation_t_median": float(group["confirmation_t"].median()),
                "gross": _robust(group["gross_log_return"]),
                "primary_cost_after": _robust(group["primary_net_log_return"]),
                "exact_opposite_cost_after": _robust(
                    group["exact_opposite_net_log_return"]
                ),
                "primary_net_r_at_emergency_risk": _robust(
                    group["primary_net_r_at_emergency_risk"]
                ),
                "emergency_hits": int(
                    group["emergency_hit_before_primary_exit"].sum()
                ),
                "by_symbol": {
                    str(symbol): {
                        "n": int(len(current)),
                        "mean_primary_net": float(
                            current["primary_net_log_return"].mean()
                        ),
                        "median_primary_net": float(
                            current["primary_net_log_return"].median()
                        ),
                    }
                    for symbol, current in group.groupby("symbol", sort=True)
                },
                "by_year": {
                    str(year): {
                        "n": int(len(current)),
                        "mean_primary_net": float(
                            current["primary_net_log_return"].mean()
                        ),
                        "median_primary_net": float(
                            current["primary_net_log_return"].median()
                        ),
                    }
                    for year, current in group.assign(
                        year=pd.to_datetime(group["sample_day"]).dt.year
                    ).groupby("year", sort=True)
                },
            }

    event_counts = {
        str(key): int(value)
        for key, value in events["regime"].value_counts().sort_index().items()
    }
    return {
        "schema": "candidate-60-forced-rejection-holdout-v1",
        "role": (
            "untouched causal signal-mechanism evaluation; no NautilusTrader "
            "fills, account, integrated NAV, or production-readiness claim"
        ),
        "source_commit": SOURCE_COMMIT,
        "source_hashes": source_hashes,
        "sample_contract": {
            "start_month": start_month,
            "end_month": end_month,
            "calendar_days": int(sample_days),
            "symbol_days": int(sample_days * 4),
            "first_calendar_day_of_each_month": True,
        },
        "frozen_rule": {
            "rule_id": RULE_ID,
            "event_contract": "candidate-16-v9-tardis-liquidation-study-v1",
            "primary_regimes": sorted(FORCED_REGIMES),
            "confirmation_window_completed_minutes": [1, MAX_CONFIRM_MINUTE],
            "first_transition_only": True,
            "conditions_all_strict": [
                "reversal_close_vs_event_mid > 0",
                "reversal_spot_return_from_event > 0",
                "perp_basis_contraction_for_reversal > 0",
                "oi_change_from_event < 0",
            ],
            "entry_observation": "next minute open",
            "primary_exit_observation": (
                f"close at confirmation_t + {PRIMARY_HOLD_MINUTES}"
            ),
            "round_trip_diagnostic_cost_rate": ROUND_TRIP_COST_RATE,
            "thresholds_searched_on_untouched_data": 0,
            "rule_changed_after_untouched_results": False,
        },
        "global_independent_events": int(len(events)),
        "event_regime_counts": event_counts,
        "transitions_all_regimes": int(len(transitions)),
        "groups": groups,
        "interpretation_contract": {
            "positive_primary_result_is_not_sufficient_for_promotion": True,
            "required_next_step_if_mechanism_survives": (
                "freeze executable invalidation and management, then evaluate "
                "through NautilusTrader in one four-symbol continuous account"
            ),
            "structural_failure_signals": [
                "cost-after mean and median fail together",
                "trim-best mean is non-positive",
                "exact opposite is not materially worse",
                "result is concentrated in one symbol or one observation",
                "transition is not more discriminating than unresolved controls",
            ],
        },
    }


def run_prebuilt(paths_input: Path, events_input: Path, output: Path) -> dict[str, Any]:
    paths = pd.read_csv(paths_input)
    events = pd.read_csv(events_input)
    transitions = select_transitions(paths)
    output.mkdir(parents=True, exist_ok=True)
    transitions.to_csv(output / "transitions.csv", index=False)
    summary = summarize(
        transitions=transitions,
        events=events,
        start_month="PREBUILT",
        end_month="PREBUILT",
        sample_days=int(events["sample_day"].nunique()),
        source_hashes={
            "paths": _sha256_path(paths_input),
            "events": _sha256_path(events_input),
        },
    )
    _write_json(output / "summary.json", summary)
    return summary


def run_downloaded(
    *,
    start_month: str,
    end_month: str,
    cache: Path,
    output: Path,
) -> dict[str, Any]:
    import v9_tardis_liquidation_study as base
    import v9_tardis_liquidation_study_v3 as compatibility  # noqa: F401
    import v10_liquidation_path_diagnostic as path_source

    days, obtained = _obtain_all(base, start_month, end_month, cache)
    panel = _build_panel(base, days, obtained)
    events = base.classify_and_score(panel)
    if events.empty:
        raise base.StudyError("frozen v9 event contract produced no independent events")
    events = events.sort_values(["minute", "symbol"], kind="stable").reset_index(drop=True)
    paths = path_source.extract_paths(panel, events)
    transitions = select_transitions(paths)

    output.mkdir(parents=True, exist_ok=True)
    events.to_csv(output / "events.csv", index=False)
    transitions.to_csv(output / "transitions.csv", index=False)
    source_hashes = {
        name: _sha256_path(Path(module.__file__).resolve())
        for name, module in {
            "v9_tardis_liquidation_study.py": base,
            "v10_liquidation_path_diagnostic.py": path_source,
        }.items()
    }
    summary = summarize(
        transitions=transitions,
        events=events,
        start_month=start_month,
        end_month=end_month,
        sample_days=len(days),
        source_hashes=source_hashes,
    )
    _write_json(output / "summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-month", default="2024-01")
    parser.add_argument("--end-month", default="2025-12")
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--paths-input", type=Path)
    parser.add_argument("--events-input", type=Path)
    args = parser.parse_args()

    if args.paths_input or args.events_input:
        if not args.paths_input or not args.events_input:
            raise SystemExit("--paths-input and --events-input must be supplied together")
        result = run_prebuilt(
            args.paths_input.resolve(),
            args.events_input.resolve(),
            args.output.resolve(),
        )
    else:
        if args.cache is None:
            raise SystemExit("--cache is required for downloaded evaluation")
        result = run_downloaded(
            start_month=args.start_month,
            end_month=args.end_month,
            cache=args.cache.resolve(),
            output=args.output.resolve(),
        )
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
