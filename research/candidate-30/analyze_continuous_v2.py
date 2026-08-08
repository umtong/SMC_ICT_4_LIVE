#!/usr/bin/env python3
"""Pandas-resolution-safe entry point for the Candidate 30 study.

Pandas 3 can preserve second, millisecond or microsecond resolution in a
DatetimeIndex, so ``DatetimeIndex.asi8`` is not a portable nanosecond contract.
The study compares every clock through ``Timestamp.value`` instead.  No alpha,
state, route, cost, horizon or promotion rule differs from the pre-registration
in :mod:`analyze_continuous`.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd

import analyze_continuous as _base

_ORIGINAL_SUMMARY = _base._summary


def _safe_quantile(values: np.ndarray, quantile: float) -> float:
    finite = values[np.isfinite(values)]
    return float(np.quantile(finite, quantile)) if finite.size else float("nan")


def _daily_prior_thresholds(
    *,
    times: pd.DatetimeIndex,
    series: dict[str, np.ndarray],
) -> dict[str, np.ndarray]:
    """Use prior UTC days only, with all search keys in explicit Unix ns."""
    result = {
        "shock_return_cut": np.full(len(times), np.nan),
        "shock_quote_cut": np.full(len(times), np.nan),
        "oi_expand_cut": np.full(len(times), np.nan),
        "premium_abs_cut": np.full(len(times), np.nan),
        "account_median": np.full(len(times), np.nan),
        "oi_clear_cut": np.full(len(times), np.nan),
    }
    values_ns = np.fromiter(
        (pd.Timestamp(value).value for value in times),
        dtype=np.int64,
        count=len(times),
    )
    if values_ns.size == 0 or np.any(np.diff(values_ns) <= 0):
        raise _base.StudyError("threshold clock must be non-empty, unique and monotonic")

    first_day = pd.Timestamp(times[0]).floor("D")
    last_day = pd.Timestamp(times[-1]).floor("D")
    for day in pd.date_range(first_day, last_day, freq="1D"):
        day = pd.Timestamp(day)
        day_start_ns = int(day.value)
        next_day_ns = int((day + pd.Timedelta(days=1)).value)
        current_start = int(np.searchsorted(values_ns, day_start_ns, side="left"))
        current_end = int(np.searchsorted(values_ns, next_day_ns, side="left"))
        history_start_ns = int(
            (day - pd.Timedelta(days=_base.HISTORY_DAYS)).value,
        )
        history_start = int(
            np.searchsorted(values_ns, history_start_ns, side="left"),
        )
        history_end = current_start
        observed_days = (
            day.date() - pd.Timestamp(times[history_start]).date()
        ).days if history_end > history_start else 0
        if observed_days < _base.MIN_HISTORY_DAYS or history_end <= history_start:
            continue
        window = slice(history_start, history_end)
        result["shock_return_cut"][current_start:current_end] = _safe_quantile(
            series["abs_ret_1m_bps"][window],
            0.99,
        )
        result["shock_quote_cut"][current_start:current_end] = _safe_quantile(
            series["quote_volume"][window],
            0.95,
        )
        result["oi_expand_cut"][current_start:current_end] = max(
            0.0,
            _safe_quantile(series["oi_change_4h"][window], 0.75),
        )
        result["premium_abs_cut"][current_start:current_end] = _safe_quantile(
            np.abs(series["premium_index"][window]),
            0.85,
        )
        result["account_median"][current_start:current_end] = _safe_quantile(
            series["account_ratio"][window],
            0.50,
        )
        result["oi_clear_cut"][current_start:current_end] = min(
            0.0,
            _safe_quantile(series["oi_change_15m"][window], 0.10),
        )
    return result


def _summary(frame: pd.DataFrame) -> dict[str, object]:
    """Keep JSON evidence finite when a small group happens to have no loss."""
    result = _ORIGINAL_SUMMARY(frame)
    profit_factor = float(result.get("profit_factor", 0.0))
    if not math.isfinite(profit_factor):
        result["profit_factor"] = 1_000_000.0
        result["profit_factor_is_lossless_sample"] = True
    else:
        result["profit_factor_is_lossless_sample"] = False
    return result


_base._daily_prior_thresholds = _daily_prior_thresholds
_base._summary = _summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--symbol", default="BTCUSDT")
    args = parser.parse_args()
    _base.run(args.input_root, args.output, args.symbol)


if __name__ == "__main__":
    main()
