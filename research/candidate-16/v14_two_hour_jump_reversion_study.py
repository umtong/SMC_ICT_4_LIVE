#!/usr/bin/env python3
"""External two-hour jump mean-reversion replication for Candidate 16 v14.

De Nicola (2021) reports unusually strong negative first-order autocorrelation
for Bitcoin at one-, two- and four-hour horizons.  The two-hour rule takes the
opposite direction for the next complete two-hour period after a completed
large jump.  Its reported four-sigma gross mean is about 0.74%, large enough to
justify a modern cost-aware replication before inventing another pattern.

Only the minimum causal project adaptations are made:

* fixed non-overlapping UTC two-hour bars;
* jump threshold = four times a shifted trailing 30-day standard deviation,
  never whole-sample volatility;
* BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT are evaluated symmetrically;
* simultaneous cross-asset jumps form one causal episode and only the largest
  standardized jump is retained;
* the opposite hypothetical position is held for exactly the next completed
  two-hour period;
* 20 bp round-trip costs are deducted;
* 2024 is not opened unless unchanged 2023 clears fixed economic and
  diversification checks.

This module creates no exchange fills, stop/target, account, portfolio or NAV.
A passing mechanism must next be converted into a complete later-leg scenario
and validated in NautilusTrader with current-NAV 3% risk and one global slot.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from v10_open_drive_study import DEVELOPMENT_YEAR
from v10_open_drive_study import HOLDOUT_YEAR
from v10_open_drive_study import ROUND_TRIP_COST_RATE
from v10_open_drive_study import SYMBOLS
from v10_open_drive_study import load_symbol


BAR_HOURS = 2
TRAILING_DAYS = 30
TRAILING_BARS = TRAILING_DAYS * (24 // BAR_HOURS)
JUMP_SIGMA = 4.0
NEXT_PERIOD_HOURS = 2


@dataclass(frozen=True, slots=True)
class JumpEvent:
    symbol: str
    bar_start_ts: pd.Timestamp
    bar_end_ts: pd.Timestamp
    next_start_ts: pd.Timestamp
    next_end_ts: pd.Timestamp
    jump_return: float
    prior_sigma: float
    jump_z: float
    event_direction: int
    next_return: float
    reversal_gross_return: float
    reversal_net_return: float
    next_period_mfe: float
    next_period_mae: float


def aggregate_two_hour(panel: pd.DataFrame) -> pd.DataFrame:
    source = panel.copy()
    source.index = pd.to_datetime(source.index, utc=True).astype("datetime64[ns, UTC]")
    grouped = source.resample(
        f"{BAR_HOURS}h",
        label="left",
        closed="left",
        origin="start_day",
    )
    bars = pd.DataFrame(
        {
            "open": grouped["perp_open"].first(),
            "high": grouped["perp_high"].max(),
            "low": grouped["perp_low"].min(),
            "close": grouped["perp_close"].last(),
            "quote_volume": grouped["perp_quote_volume"].sum(),
            "minute_count": grouped["perp_close"].count(),
        },
    )
    bars = bars[bars["minute_count"] == BAR_HOURS * 60].copy()
    bars.index = bars.index.as_unit("ns")
    bars["bar_start_ts"] = bars.index
    bars["bar_end_ts"] = (
        bars.index
        + pd.Timedelta(hours=BAR_HOURS)
        - pd.Timedelta(minutes=1)
    ).as_unit("ns")
    bars["log_return"] = np.log(bars["close"] / bars["open"])
    # Shift excludes the current jump from the volatility baseline that judges it.
    bars["prior_sigma"] = (
        bars["log_return"]
        .rolling(TRAILING_BARS, min_periods=TRAILING_BARS)
        .std(ddof=0)
        .shift(1)
    )
    bars["jump_z"] = (
        bars["log_return"].abs()
        / bars["prior_sigma"].replace(0.0, np.nan)
    )
    bars["next_log_return"] = bars["log_return"].shift(-1)
    bars["next_start_ts"] = bars["bar_start_ts"].shift(-1)
    bars["next_end_ts"] = bars["bar_end_ts"].shift(-1)
    return bars


def _next_path(
    panel: pd.DataFrame,
    start: pd.Timestamp,
) -> pd.DataFrame | None:
    expected = pd.date_range(
        start,
        periods=NEXT_PERIOD_HOURS * 60,
        freq="min",
        tz="UTC",
    ).as_unit("ns")
    sample = panel.reindex(expected)
    if sample["perp_close"].isna().any():
        return None
    return sample


def detect_symbol_events(
    symbol: str,
    panel: pd.DataFrame,
    bars: pd.DataFrame,
) -> list[JumpEvent]:
    events: list[JumpEvent] = []
    eligible = bars[
        bars["prior_sigma"].gt(0.0)
        & bars["jump_z"].ge(JUMP_SIGMA)
        & bars["next_log_return"].notna()
        & bars["next_start_ts"].notna()
    ]
    for _, row in eligible.iterrows():
        jump_return = float(row["log_return"])
        direction = 1 if jump_return > 0.0 else -1
        next_return = float(row["next_log_return"])
        gross = -direction * next_return
        net = gross - ROUND_TRIP_COST_RATE
        next_start = pd.Timestamp(row["next_start_ts"])
        path = _next_path(panel, next_start)
        if path is None:
            continue
        entry = float(path.iloc[0]["perp_open"])
        if not math.isfinite(entry) or entry <= 0.0:
            continue
        reversal_side = -direction
        if reversal_side > 0:
            mfe = float(path["perp_high"].max() / entry - 1.0)
            mae = float(path["perp_low"].min() / entry - 1.0)
        else:
            mfe = float(1.0 - path["perp_low"].min() / entry)
            mae = float(1.0 - path["perp_high"].max() / entry)
        events.append(
            JumpEvent(
                symbol=symbol,
                bar_start_ts=pd.Timestamp(row["bar_start_ts"]),
                bar_end_ts=pd.Timestamp(row["bar_end_ts"]),
                next_start_ts=next_start,
                next_end_ts=pd.Timestamp(row["next_end_ts"]),
                jump_return=jump_return,
                prior_sigma=float(row["prior_sigma"]),
                jump_z=float(row["jump_z"]),
                event_direction=direction,
                next_return=next_return,
                reversal_gross_return=gross,
                reversal_net_return=net,
                next_period_mfe=mfe,
                next_period_mae=mae,
            ),
        )
    return events


def collapse_simultaneous_events(events: list[JumpEvent]) -> list[JumpEvent]:
    by_time: dict[pd.Timestamp, list[JumpEvent]] = {}
    for event in events:
        by_time.setdefault(event.bar_end_ts, []).append(event)
    selected = [
        max(
            group,
            key=lambda item: (
                item.jump_z,
                abs(item.jump_return),
                item.symbol,
            ),
        )
        for _, group in sorted(by_time.items())
    ]
    return sorted(selected, key=lambda item: item.bar_end_ts)


def records(events: list[JumpEvent]) -> pd.DataFrame:
    return pd.DataFrame([asdict(event) for event in events])


def summarize(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "events": 0,
            "gross_positive_rate": 0.0,
            "cost_after_positive_rate": 0.0,
            "mean_gross_return": 0.0,
            "median_gross_return": 0.0,
            "mean_net_return": 0.0,
            "median_net_return": 0.0,
            "median_mfe": 0.0,
            "median_mae": 0.0,
            "symbols_positive_mean_net": 0,
            "largest_event_share_of_positive_net": 1.0,
            "by_symbol": {},
        }
    gross = pd.to_numeric(frame["reversal_gross_return"], errors="raise")
    net = pd.to_numeric(frame["reversal_net_return"], errors="raise")
    by_symbol = {
        str(symbol): {
            "events": int(len(group)),
            "mean_gross_return": float(group["reversal_gross_return"].mean()),
            "mean_net_return": float(group["reversal_net_return"].mean()),
            "cost_after_positive_rate": float(
                (group["reversal_net_return"] > 0.0).mean(),
            ),
        }
        for symbol, group in frame.groupby("symbol", sort=True)
    }
    positive = net[net > 0.0]
    return {
        "events": int(len(frame)),
        "gross_positive_rate": float((gross > 0.0).mean()),
        "cost_after_positive_rate": float((net > 0.0).mean()),
        "mean_gross_return": float(gross.mean()),
        "median_gross_return": float(gross.median()),
        "mean_net_return": float(net.mean()),
        "median_net_return": float(net.median()),
        "median_mfe": float(frame["next_period_mfe"].median()),
        "median_mae": float(frame["next_period_mae"].median()),
        "symbols_positive_mean_net": sum(
            item["mean_net_return"] > 0.0 for item in by_symbol.values()
        ),
        "largest_event_share_of_positive_net": (
            float(positive.max() / positive.sum())
            if not positive.empty
            else 1.0
        ),
        "by_symbol": by_symbol,
    }


def promotion_checks(summary: dict[str, Any]) -> dict[str, bool]:
    return {
        "independent_events_at_least_20": int(summary.get("events", 0)) >= 20,
        "gross_positive_rate_at_least_55pct": (
            float(summary.get("gross_positive_rate", 0.0)) >= 0.55
        ),
        "cost_after_positive_rate_at_least_50pct": (
            float(summary.get("cost_after_positive_rate", 0.0)) >= 0.50
        ),
        "mean_gross_return_covers_round_trip_cost": (
            float(summary.get("mean_gross_return", 0.0))
            >= ROUND_TRIP_COST_RATE
        ),
        "mean_net_return_positive": float(summary.get("mean_net_return", 0.0)) > 0.0,
        "median_mfe_covers_round_trip_cost": (
            float(summary.get("median_mfe", 0.0)) >= ROUND_TRIP_COST_RATE
        ),
        "positive_mean_net_on_at_least_three_symbols": (
            int(summary.get("symbols_positive_mean_net", 0)) >= 3
        ),
        "largest_positive_event_share_at_most_35pct": (
            float(summary.get("largest_event_share_of_positive_net", 1.0))
            <= 0.35
        ),
    }


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def run(cache: Path, output: Path) -> dict[str, Any]:
    panels = {symbol: load_symbol(symbol, cache) for symbol in SYMBOLS}
    bars = {symbol: aggregate_two_hour(panel) for symbol, panel in panels.items()}
    raw_events = [
        event
        for symbol in SYMBOLS
        for event in detect_symbol_events(symbol, panels[symbol], bars[symbol])
    ]
    selected = collapse_simultaneous_events(raw_events)
    frame = records(selected)
    if frame.empty:
        development = frame
        holdout = frame
    else:
        years = pd.to_datetime(frame["bar_end_ts"], utc=True).dt.year
        development = frame[years == DEVELOPMENT_YEAR].copy()
        holdout = frame[years == HOLDOUT_YEAR].copy()
    development_summary = summarize(development)
    development_checks = promotion_checks(development_summary)
    development_pass = all(development_checks.values())
    if development_pass:
        holdout_opened = True
        holdout_summary = summarize(holdout)
        holdout_checks = promotion_checks(holdout_summary)
        holdout_pass = all(holdout_checks.values())
    else:
        holdout_opened = False
        holdout_summary = None
        holdout_checks = None
        holdout_pass = False

    if development_pass and holdout_pass:
        decision = "PROMOTE_TWO_HOUR_JUMP_REVERSION_TO_LATER_LEG_NAUTILUS_SCENARIO"
    elif development_pass:
        decision = "DISCARD_TWO_HOUR_JUMP_REVERSION_AFTER_UNTOUCHED_2024_FAILURE"
    else:
        decision = "DISCARD_TWO_HOUR_JUMP_REVERSION_AFTER_2023_REPLICATION_FAILURE"

    output.mkdir(parents=True, exist_ok=True)
    development.to_csv(output / "development_events.csv", index=False)
    if holdout_opened:
        holdout.to_csv(output / "holdout_events.csv", index=False)
    result = {
        "schema": "candidate-16-v14-two-hour-jump-reversion-study-v1",
        "role": (
            "external mechanism replication; no fills, stop/target, account, "
            "portfolio, or NAV claim"
        ),
        "external_policy": {
            "source": "De Nicola (2021), On the Intraday Behavior of Bitcoin",
            "timeframe_hours": BAR_HOURS,
            "jump_threshold_sigma": JUMP_SIGMA,
            "position": "opposite completed jump direction",
            "holding_period_hours": NEXT_PERIOD_HOURS,
            "paper_reported_gross_mean_at_4sigma": 0.0074,
        },
        "causal_adaptations": {
            "volatility_baseline": (
                f"shifted trailing {TRAILING_DAYS}-day standard deviation of "
                "non-overlapping two-hour returns"
            ),
            "simultaneous_cross_asset_policy": (
                "one causal episode; largest standardized jump only"
            ),
            "whole_sample_statistics_forbidden": True,
        },
        "data": {
            "source": (
                "checksum-verified Binance Vision spot and USD-M 1m monthly klines"
            ),
            "symbols": list(SYMBOLS),
            "years": [DEVELOPMENT_YEAR, HOLDOUT_YEAR],
        },
        "cost": {
            "round_trip_rate": ROUND_TRIP_COST_RATE,
            "meaning": "7.5 bps fee plus 2.5 bps adverse slippage per side",
        },
        "two_hour_rows": {symbol: int(len(value)) for symbol, value in bars.items()},
        "raw_symbol_events": len(raw_events),
        "independent_global_events": len(selected),
        "development": {
            "period": "2023-01-01 through 2023-12-31",
            "summary": development_summary,
            "checks": development_checks,
            "passed": development_pass,
        },
        "holdout": {
            "period": "2024-01-01 through 2024-12-31",
            "opened": holdout_opened,
            "summary": holdout_summary,
            "checks": holdout_checks,
            "passed": holdout_pass,
        },
        "promote": development_pass and holdout_pass,
        "decision": decision,
    }
    write_json(output / "study.json", result)
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
