#!/usr/bin/env python3
"""Persist causal paths around the frozen v9 liquidation episodes.

This is a diagnostic, not a strategy and not a promotion claim.  The v9 event
contract is reused unchanged.  The only new output is a compact event-time
panel around each independent episode so trade geometry can be reasoned about
instead of discarding a potentially useful family from one fixed-horizon gate.

All path columns at t <= current row use only observations complete by that
minute.  Future rows are retained only as labelled outcomes for offline causal
analysis; they are never fed back into event selection.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

import v9_tardis_liquidation_study as base
# Installs the canonical UTC-nanosecond readers and safe decluster sentinel.
import v9_tardis_liquidation_study_v3 as compatibility  # noqa: F401


START_MONTH = base.START_MONTH
END_MONTH = base.END_MONTH
WINDOW_BEFORE_MINUTES = 15
WINDOW_AFTER_MINUTES = 120


def _obtain_all(cache: Path) -> tuple[list[date], dict[tuple[str, date], dict[str, Path]]]:
    days = base._months(START_MONTH, END_MONTH)
    requests = [(symbol, day) for day in days for symbol in base.SYMBOLS]
    obtained: dict[tuple[str, date], dict[str, Path]] = {}
    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = {
            executor.submit(base.obtain_day, symbol, day, cache): (symbol, day)
            for symbol, day in requests
        }
        for future in as_completed(futures):
            obtained[futures[future]] = future.result()
    return days, obtained


def _build_panel(days: list[date], obtained: dict[tuple[str, date], dict[str, Path]]) -> pd.DataFrame:
    panels: list[pd.DataFrame] = []
    for day in days:
        for symbol in base.SYMBOLS:
            panels.append(base.build_day(symbol, day, obtained[(symbol, day)]))
    result = pd.concat(panels, ignore_index=True)
    result["minute"] = pd.to_datetime(result["minute"], utc=True).astype("datetime64[ns, UTC]")
    return base.apply_causal_event_thresholds(result)


def _safe_log_ratio(numerator: pd.Series, denominator: float) -> pd.Series:
    values = pd.to_numeric(numerator, errors="coerce")
    if not math.isfinite(denominator) or denominator <= 0.0:
        return pd.Series(np.nan, index=values.index, dtype=float)
    return np.log(values / denominator)


def extract_paths(panel: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    paths: list[pd.DataFrame] = []
    grouped = {
        (str(symbol), str(day)): group.sort_values("minute", kind="stable").copy()
        for (symbol, day), group in panel.groupby(["symbol", "sample_day"], sort=False)
    }
    for event_id, event in events.reset_index(drop=True).iterrows():
        symbol = str(event["symbol"])
        sample_day = str(event["sample_day"])
        group = grouped[(symbol, sample_day)]
        event_minute = pd.Timestamp(event["minute"])
        lower = event_minute - pd.Timedelta(minutes=WINDOW_BEFORE_MINUTES)
        upper = event_minute + pd.Timedelta(minutes=WINDOW_AFTER_MINUTES)
        path = group[(group["minute"] >= lower) & (group["minute"] <= upper)].copy()
        if path.empty:
            continue

        event_direction = int(event["event_direction"])
        reversal_side = -event_direction
        event_close = float(event["perp_close"])
        event_open = float(event["perp_open"])
        event_high = float(event["perp_high"])
        event_low = float(event["perp_low"])
        event_mid = 0.5 * (event_high + event_low)
        event_spot_close = float(event["spot_close"])
        event_oi = float(event["open_interest"])
        event_perp_basis = float(event["perp_spot_basis"])
        event_mark_basis = float(event["mark_index_basis"])

        path["event_id"] = int(event_id)
        path["event_minute"] = event_minute
        path["event_regime"] = str(event["regime"])
        path["event_direction"] = event_direction
        path["reversal_side"] = reversal_side
        path["t_min"] = (
            (path["minute"] - event_minute) / pd.Timedelta(minutes=1)
        ).astype(int)
        path["event_close"] = event_close
        path["event_open"] = event_open
        path["event_high"] = event_high
        path["event_low"] = event_low
        path["event_mid"] = event_mid
        path["event_vwap_4h"] = float(event["rolling_vwap_4h"])
        path["event_oi_change_15m"] = float(event["oi_change_15m"])
        path["event_futures_lead_return"] = float(event["futures_lead_return"])
        path["event_perp_basis_z_directional"] = float(event["perp_basis_z_directional"])
        path["event_mark_basis_z_directional"] = float(event["mark_basis_z_directional"])
        path["event_liq_share_of_volume"] = float(event["liq_share_of_perp_volume"])
        path["event_cluster_symbol_count"] = int(event["cluster_symbol_count"])
        path["event_cluster_symbols"] = str(event["cluster_symbols"])

        perp_log = _safe_log_ratio(path["perp_close"], event_close)
        spot_log = _safe_log_ratio(path["spot_close"], event_spot_close)
        path["continuation_return_from_event"] = event_direction * perp_log
        path["reversal_return_from_event"] = reversal_side * perp_log
        path["reversal_spot_return_from_event"] = reversal_side * spot_log
        path["reversal_bar_return"] = reversal_side * pd.to_numeric(
            path["perp_ret_1m"], errors="coerce"
        )
        path["reversal_spot_bar_return"] = reversal_side * pd.to_numeric(
            path["spot_ret_1m"], errors="coerce"
        )

        buy_share = pd.to_numeric(path["perp_taker_buy_quote"], errors="coerce") / pd.to_numeric(
            path["perp_quote_volume"], errors="coerce"
        ).replace(0.0, np.nan)
        path["reversal_taker_imbalance"] = reversal_side * (2.0 * buy_share - 1.0)
        path["reversal_close_vs_event_mid"] = reversal_side * (
            pd.to_numeric(path["perp_close"], errors="coerce") - event_mid
        ) / event_close
        path["reversal_close_vs_event_open"] = reversal_side * (
            pd.to_numeric(path["perp_close"], errors="coerce") - event_open
        ) / event_close
        path["reversal_close_vs_event_close"] = reversal_side * (
            pd.to_numeric(path["perp_close"], errors="coerce") - event_close
        ) / event_close

        high_extension = pd.to_numeric(path["perp_high"], errors="coerce") / event_high - 1.0
        low_extension = event_low / pd.to_numeric(path["perp_low"], errors="coerce") - 1.0
        path["continuation_extreme_extension"] = np.where(
            event_direction > 0,
            high_extension,
            low_extension,
        )
        high_reversal = pd.to_numeric(path["perp_high"], errors="coerce") / event_close - 1.0
        low_reversal = event_close / pd.to_numeric(path["perp_low"], errors="coerce") - 1.0
        path["reversal_intrabar_excursion"] = np.where(
            reversal_side > 0,
            high_reversal,
            low_reversal,
        )

        path["perp_basis_contraction_for_reversal"] = reversal_side * (
            pd.to_numeric(path["perp_spot_basis"], errors="coerce") - event_perp_basis
        )
        path["mark_basis_contraction_for_reversal"] = reversal_side * (
            pd.to_numeric(path["mark_index_basis"], errors="coerce") - event_mark_basis
        )
        if math.isfinite(event_oi) and event_oi > 0.0:
            path["oi_change_from_event"] = (
                pd.to_numeric(path["open_interest"], errors="coerce") / event_oi - 1.0
            )
        else:
            path["oi_change_from_event"] = np.nan

        close = pd.to_numeric(path["perp_close"], errors="coerce")
        for horizon in (2, 3, 5):
            path[f"reversal_return_{horizon}m_trailing"] = reversal_side * np.log(
                close / close.shift(horizon)
            )
            path[f"reversal_flow_{horizon}m_mean"] = path["reversal_taker_imbalance"].rolling(
                horizon, min_periods=horizon
            ).mean()

        output_columns = [
            "event_id", "event_minute", "minute", "t_min", "symbol", "sample_day",
            "event_regime", "event_direction", "reversal_side",
            "event_open", "event_high", "event_low", "event_close", "event_mid",
            "event_vwap_4h", "event_oi_change_15m", "event_futures_lead_return",
            "event_perp_basis_z_directional", "event_mark_basis_z_directional",
            "event_liq_share_of_volume", "event_cluster_symbol_count", "event_cluster_symbols",
            "perp_open", "perp_high", "perp_low", "perp_close", "perp_quote_volume",
            "perp_taker_buy_quote", "spot_open", "spot_high", "spot_low", "spot_close",
            "spot_quote_volume", "open_interest", "perp_spot_basis", "mark_index_basis",
            "rolling_vwap_4h", "long_liq_notional", "short_liq_notional",
            "continuation_return_from_event", "reversal_return_from_event",
            "reversal_spot_return_from_event", "reversal_bar_return",
            "reversal_spot_bar_return", "reversal_taker_imbalance",
            "reversal_close_vs_event_mid", "reversal_close_vs_event_open",
            "reversal_close_vs_event_close", "continuation_extreme_extension",
            "reversal_intrabar_excursion", "perp_basis_contraction_for_reversal",
            "mark_basis_contraction_for_reversal", "oi_change_from_event",
            "reversal_return_2m_trailing", "reversal_return_3m_trailing",
            "reversal_return_5m_trailing", "reversal_flow_2m_mean",
            "reversal_flow_3m_mean", "reversal_flow_5m_mean",
        ]
        paths.append(path[output_columns])
    if not paths:
        raise base.StudyError("no event-time paths were extracted")
    return pd.concat(paths, ignore_index=True)


def run(cache: Path, output: Path) -> dict[str, object]:
    days, obtained = _obtain_all(cache)
    panel = _build_panel(days, obtained)
    events = base.classify_and_score(panel)
    if events.empty:
        raise base.StudyError("frozen v9 event contract produced no independent episodes")
    events = events.sort_values(["minute", "symbol"], kind="stable").reset_index(drop=True)
    paths = extract_paths(panel, events)
    output.mkdir(parents=True, exist_ok=True)
    events.to_csv(output / "events.csv", index=False)
    paths.to_csv(output / "paths.csv", index=False)

    forced = events[events["regime"].isin({
        "FORCED_BASIS_DISLOCATION", "FORCED_OI_DERIVATIVES_LEAD",
    })]
    result = {
        "schema": "candidate-16-v10-liquidation-path-diagnostic-v1",
        "role": "development-data path diagnostic; no strategy, fills, PnL, account, or NAV claim",
        "event_contract_reused_unchanged": True,
        "source_event_schema": "candidate-16-v9-tardis-liquidation-study-v1",
        "sample_contract": {
            "start_month": START_MONTH,
            "end_month": END_MONTH,
            "calendar_days": len(days),
            "symbol_days": len(days) * len(base.SYMBOLS),
        },
        "window": {
            "before_minutes": WINDOW_BEFORE_MINUTES,
            "after_minutes": WINDOW_AFTER_MINUTES,
        },
        "global_independent_events": int(len(events)),
        "forced_events": int(len(forced)),
        "path_rows": int(len(paths)),
        "path_rows_t0_or_later": int((paths["t_min"] >= 0).sum()),
        "regime_counts": {
            str(key): int(value)
            for key, value in events["regime"].value_counts().sort_index().items()
        },
        "diagnostic_intent": (
            "separate event state from a strictly later target-owned reversal transition, "
            "then freeze a minimal transition before any untouched-period evaluation"
        ),
    }
    base.write_json(output / "diagnostic.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.cache.resolve(), args.output.resolve())
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
