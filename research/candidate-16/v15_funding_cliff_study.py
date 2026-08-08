#!/usr/bin/env python3
"""External funding-cliff state-router study for Candidate 16 v15.

Practitioner funding-window playbooks make two apparently contradictory claims:
extreme positive funding is a crowded-long fade, yet a bearish pre-settlement
sweep can exhaust that unwind and rebound immediately after payment.  Rather
than choosing one story after seeing results, this study predefines two mutually
exclusive states at each completed Binance funding timestamp:

1. PRE_SETTLEMENT_UNWIND_EXHAUSTED
   * absolute funding is above its shifted trailing 95th percentile;
   * perpetual basis has the same sign as funding;
   * the prior 30 minutes moved against the funding sign;
   * the perpetual led spot in that unwind direction.
   Policy to test: after the realized funding is public, trade back in the
   funding-sign direction.

2. CROWDED_FUNDING_FADE
   * the same extreme funding and aligned basis exist;
   * the completed pre-window did not qualify as derivatives-led unwind.
   Policy to test: trade opposite the funding-sign direction.

Realized funding is never used before settlement.  The descriptive entry clock
is the next minute open, after the settlement event.  Simultaneous four-asset
funding events are one causal episode and only the largest standardized funding
extreme is retained.  Outcomes are measured over 30, 60 and 120 minutes after
20 bp round-trip costs.

The study reuses checksum-verified Binance Vision fundingRate, spot and USD-M
minute archives.  It creates no fills, stop/target, account, portfolio or NAV.
A state must pass unchanged 2023 and untouched 2024 before a complete later-leg
NautilusTrader scenario is built.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import io
import json
import math
from pathlib import Path
import re
from typing import Any
import zipfile

import numpy as np
import pandas as pd

from v9_liquidation_event_study import Archive
from v9_liquidation_event_study import StudyError
from v9_liquidation_event_study import _timestamp_ms
from v9_liquidation_event_study import download_verified
from v10_open_drive_study import DEVELOPMENT_YEAR
from v10_open_drive_study import HOLDOUT_YEAR
from v10_open_drive_study import ROUND_TRIP_COST_RATE
from v10_open_drive_study import SYMBOLS
from v10_open_drive_study import load_symbol


TRAILING_EVENTS = 90
EXTREME_QUANTILE = 0.95
PRE_WINDOW_MINUTES = 30
HORIZONS = (30, 60, 120)


@dataclass(frozen=True, slots=True)
class FundingEvent:
    symbol: str
    funding_ts: pd.Timestamp
    entry_ts: pd.Timestamp
    funding_rate: float
    funding_abs_threshold: float
    funding_extreme_ratio: float
    funding_sign: int
    perp_spot_basis: float
    pre_perp_return: float
    pre_spot_return: float
    pre_unwind_return: float
    pre_futures_lead: float
    state: str
    policy_side: int
    return_30m: float
    return_60m: float
    return_120m: float
    net_return_30m: float
    net_return_60m: float
    net_return_120m: float
    mfe_120m: float
    mae_120m: float


def _normalize_name(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")


def read_funding(path: Path, symbol: str) -> pd.DataFrame:
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(names) != 1:
            raise StudyError(f"expected one funding CSV in {path}, got {names}")
        raw = archive.read(names[0])
    frame = pd.read_csv(io.BytesIO(raw), low_memory=False)
    if frame.empty:
        return pd.DataFrame(columns=["funding_ts", "funding_rate"])
    frame.columns = [_normalize_name(column) for column in frame.columns]
    time_column = next(
        (
            candidate
            for candidate in ("calc_time", "funding_time", "fundingtime", "time")
            if candidate in frame.columns
        ),
        None,
    )
    rate_column = next(
        (
            candidate
            for candidate in (
                "last_funding_rate",
                "funding_rate",
                "fundingrate",
                "rate",
            )
            if candidate in frame.columns
        ),
        None,
    )
    if time_column is None:
        raise StudyError(f"cannot resolve funding timestamp in {path}: {list(frame.columns)}")
    if rate_column is None:
        numeric_candidates = [
            column
            for column in frame.columns
            if column != time_column
            and pd.to_numeric(frame[column], errors="coerce").notna().mean() > 0.90
        ]
        if not numeric_candidates:
            raise StudyError(f"cannot resolve funding rate in {path}: {list(frame.columns)}")
        rate_column = numeric_candidates[-1]
    timestamp_ms = _timestamp_ms(frame[time_column])
    result = pd.DataFrame(
        {
            "funding_ts": pd.to_datetime(
                timestamp_ms,
                unit="ms",
                utc=True,
                errors="coerce",
            ).dt.floor("min").astype("datetime64[ns, UTC]"),
            "funding_rate": pd.to_numeric(frame[rate_column], errors="coerce"),
        },
    ).dropna()
    result = result.sort_values("funding_ts", kind="stable").drop_duplicates(
        "funding_ts",
        keep="last",
    )
    result["symbol"] = symbol
    return result.reset_index(drop=True)


def load_funding(symbol: str, cache: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for period in pd.period_range(
        f"{DEVELOPMENT_YEAR}-01",
        f"{HOLDOUT_YEAR}-12",
        freq="M",
    ):
        label = str(period)
        archive = Archive("um", "monthly", "fundingRate", symbol, label)
        path = download_verified(archive, cache / symbol / "funding")
        frames.append(read_funding(path, symbol))
    result = pd.concat(frames, ignore_index=True).sort_values(
        "funding_ts",
        kind="stable",
    )
    if result["funding_ts"].duplicated().any():
        raise StudyError(f"duplicated funding events: {symbol}")
    result["abs_rate"] = result["funding_rate"].abs()
    result["abs_threshold"] = (
        result["abs_rate"]
        .rolling(TRAILING_EVENTS, min_periods=TRAILING_EVENTS)
        .quantile(EXTREME_QUANTILE)
        .shift(1)
    )
    result["extreme_ratio"] = result["abs_rate"] / result["abs_threshold"].replace(0.0, np.nan)
    return result.reset_index(drop=True)


def _window(
    panel: pd.DataFrame,
    start: pd.Timestamp,
    minutes: int,
) -> pd.DataFrame | None:
    expected = pd.date_range(start, periods=minutes, freq="min", tz="UTC").as_unit("ns")
    sample = panel.reindex(expected)
    if sample["perp_close"].isna().any() or sample["spot_close"].isna().any():
        return None
    return sample


def detect_symbol_events(
    symbol: str,
    panel: pd.DataFrame,
    funding: pd.DataFrame,
) -> list[FundingEvent]:
    events: list[FundingEvent] = []
    eligible = funding[
        funding["abs_threshold"].gt(0.0)
        & funding["abs_rate"].ge(funding["abs_threshold"])
        & funding["funding_rate"].ne(0.0)
    ]
    for row in eligible.itertuples(index=False):
        funding_ts = pd.Timestamp(row.funding_ts)
        funding_sign = 1 if float(row.funding_rate) > 0.0 else -1
        pre = _window(
            panel,
            funding_ts - pd.Timedelta(minutes=PRE_WINDOW_MINUTES),
            PRE_WINDOW_MINUTES,
        )
        if pre is None:
            continue
        pre_perp_open = float(pre.iloc[0]["perp_open"])
        pre_perp_close = float(pre.iloc[-1]["perp_close"])
        pre_spot_open = float(pre.iloc[0]["spot_open"])
        pre_spot_close = float(pre.iloc[-1]["spot_close"])
        if min(pre_perp_open, pre_perp_close, pre_spot_open, pre_spot_close) <= 0.0:
            continue
        pre_perp_return = math.log(pre_perp_close / pre_perp_open)
        pre_spot_return = math.log(pre_spot_close / pre_spot_open)
        basis = pre_perp_close / pre_spot_close - 1.0
        if funding_sign * basis <= 0.0:
            continue
        unwind_side = -funding_sign
        pre_unwind = unwind_side * pre_perp_return
        pre_futures_lead = unwind_side * (pre_perp_return - pre_spot_return)
        if pre_unwind > 0.0 and pre_futures_lead > 0.0:
            state = "PRE_SETTLEMENT_UNWIND_EXHAUSTED"
            policy_side = funding_sign
        else:
            state = "CROWDED_FUNDING_FADE"
            policy_side = -funding_sign

        entry_ts = funding_ts + pd.Timedelta(minutes=1)
        entry_row = panel.reindex([entry_ts])
        if entry_row["perp_open"].isna().any():
            continue
        entry = float(entry_row.iloc[0]["perp_open"])
        if entry <= 0.0:
            continue
        returns: dict[int, float] = {}
        complete = True
        for horizon in HORIZONS:
            target_ts = entry_ts + pd.Timedelta(minutes=horizon - 1)
            target_row = panel.reindex([target_ts])
            if target_row["perp_close"].isna().any():
                complete = False
                break
            close = float(target_row.iloc[0]["perp_close"])
            returns[horizon] = policy_side * math.log(close / entry)
        if not complete:
            continue
        path = _window(panel, entry_ts, max(HORIZONS))
        if path is None:
            continue
        if policy_side > 0:
            mfe = float(path["perp_high"].max() / entry - 1.0)
            mae = float(path["perp_low"].min() / entry - 1.0)
        else:
            mfe = float(1.0 - path["perp_low"].min() / entry)
            mae = float(1.0 - path["perp_high"].max() / entry)
        events.append(
            FundingEvent(
                symbol=symbol,
                funding_ts=funding_ts,
                entry_ts=entry_ts,
                funding_rate=float(row.funding_rate),
                funding_abs_threshold=float(row.abs_threshold),
                funding_extreme_ratio=float(row.extreme_ratio),
                funding_sign=funding_sign,
                perp_spot_basis=basis,
                pre_perp_return=pre_perp_return,
                pre_spot_return=pre_spot_return,
                pre_unwind_return=pre_unwind,
                pre_futures_lead=pre_futures_lead,
                state=state,
                policy_side=policy_side,
                return_30m=returns[30],
                return_60m=returns[60],
                return_120m=returns[120],
                net_return_30m=returns[30] - ROUND_TRIP_COST_RATE,
                net_return_60m=returns[60] - ROUND_TRIP_COST_RATE,
                net_return_120m=returns[120] - ROUND_TRIP_COST_RATE,
                mfe_120m=mfe,
                mae_120m=mae,
            ),
        )
    return events


def collapse_simultaneous_events(events: list[FundingEvent]) -> list[FundingEvent]:
    by_time: dict[pd.Timestamp, list[FundingEvent]] = {}
    for event in events:
        by_time.setdefault(event.funding_ts, []).append(event)
    selected = [
        max(
            group,
            key=lambda item: (
                item.funding_extreme_ratio,
                abs(item.funding_rate),
                item.symbol,
            ),
        )
        for _, group in sorted(by_time.items())
    ]
    return sorted(selected, key=lambda item: item.funding_ts)


def records(events: list[FundingEvent]) -> pd.DataFrame:
    return pd.DataFrame([asdict(event) for event in events])


def summarize(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "events": 0,
            "cost_after_positive_rate_60m": 0.0,
            "mean_return_60m": 0.0,
            "median_return_60m": 0.0,
            "mean_net_return_60m": 0.0,
            "median_net_return_60m": 0.0,
            "median_mfe_120m": 0.0,
            "median_mae_120m": 0.0,
            "symbols_positive_mean_net_60m": 0,
            "largest_event_share_of_positive_net_60m": 1.0,
            "by_symbol": {},
        }
    by_symbol = {
        str(symbol): {
            "events": int(len(group)),
            "mean_net_return_60m": float(group["net_return_60m"].mean()),
            "cost_after_positive_rate_60m": float(
                (group["net_return_60m"] > 0.0).mean(),
            ),
        }
        for symbol, group in frame.groupby("symbol", sort=True)
    }
    positive = frame.loc[frame["net_return_60m"] > 0.0, "net_return_60m"]
    result: dict[str, Any] = {
        "events": int(len(frame)),
        "cost_after_positive_rate_60m": float((frame["net_return_60m"] > 0.0).mean()),
        "mean_return_60m": float(frame["return_60m"].mean()),
        "median_return_60m": float(frame["return_60m"].median()),
        "mean_net_return_60m": float(frame["net_return_60m"].mean()),
        "median_net_return_60m": float(frame["net_return_60m"].median()),
        "median_mfe_120m": float(frame["mfe_120m"].median()),
        "median_mae_120m": float(frame["mae_120m"].median()),
        "symbols_positive_mean_net_60m": sum(
            item["mean_net_return_60m"] > 0.0 for item in by_symbol.values()
        ),
        "largest_event_share_of_positive_net_60m": (
            float(positive.max() / positive.sum()) if not positive.empty else 1.0
        ),
        "by_symbol": by_symbol,
    }
    for horizon in HORIZONS:
        result[f"mean_return_{horizon}m"] = float(frame[f"return_{horizon}m"].mean())
        result[f"median_return_{horizon}m"] = float(frame[f"return_{horizon}m"].median())
        result[f"cost_after_positive_rate_{horizon}m"] = float(
            (frame[f"net_return_{horizon}m"] > 0.0).mean(),
        )
    return result


def promotion_checks(summary: dict[str, Any]) -> dict[str, bool]:
    return {
        "independent_events_at_least_20": int(summary.get("events", 0)) >= 20,
        "cost_after_positive_rate_60m_at_least_55pct": (
            float(summary.get("cost_after_positive_rate_60m", 0.0)) >= 0.55
        ),
        "median_60m_move_covers_round_trip_cost": (
            float(summary.get("median_return_60m", 0.0)) >= ROUND_TRIP_COST_RATE
        ),
        "mean_net_return_60m_positive": (
            float(summary.get("mean_net_return_60m", 0.0)) > 0.0
        ),
        "median_mfe_120m_covers_round_trip_cost": (
            float(summary.get("median_mfe_120m", 0.0)) >= ROUND_TRIP_COST_RATE
        ),
        "positive_mean_net_on_at_least_three_symbols": (
            int(summary.get("symbols_positive_mean_net_60m", 0)) >= 3
        ),
        "largest_positive_event_share_at_most_35pct": (
            float(summary.get("largest_event_share_of_positive_net_60m", 1.0))
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
    funding = {symbol: load_funding(symbol, cache) for symbol in SYMBOLS}
    raw_events = [
        event
        for symbol in SYMBOLS
        for event in detect_symbol_events(symbol, panels[symbol], funding[symbol])
    ]
    selected = collapse_simultaneous_events(raw_events)
    frame = records(selected)
    if frame.empty:
        development = frame
        holdout = frame
    else:
        years = pd.to_datetime(frame["funding_ts"], utc=True).dt.year
        development = frame[years == DEVELOPMENT_YEAR].copy()
        holdout = frame[years == HOLDOUT_YEAR].copy()

    state_results: dict[str, Any] = {}
    any_development_pass = False
    for state in ("PRE_SETTLEMENT_UNWIND_EXHAUSTED", "CROWDED_FUNDING_FADE"):
        development_state = development[development["state"] == state].copy() if not development.empty else development
        development_summary = summarize(development_state)
        development_checks = promotion_checks(development_summary)
        development_pass = all(development_checks.values())
        any_development_pass = any_development_pass or development_pass
        if development_pass:
            holdout_state = holdout[holdout["state"] == state].copy() if not holdout.empty else holdout
            holdout_summary = summarize(holdout_state)
            holdout_checks = promotion_checks(holdout_summary)
            holdout_pass = all(holdout_checks.values())
        else:
            holdout_summary = None
            holdout_checks = None
            holdout_pass = False
        state_results[state] = {
            "development": {
                "summary": development_summary,
                "checks": development_checks,
                "passed": development_pass,
            },
            "holdout": {
                "opened": development_pass,
                "summary": holdout_summary,
                "checks": holdout_checks,
                "passed": holdout_pass,
            },
            "promote": development_pass and holdout_pass,
        }

    promoted = [state for state, result in state_results.items() if result["promote"]]
    if len(promoted) == 2:
        decision = "PROMOTE_TWO_STATE_FUNDING_ROUTER_TO_LATER_LEG_NAUTILUS_SCENARIO"
    elif len(promoted) == 1:
        decision = f"PROMOTE_{promoted[0]}_ONLY_TO_LATER_LEG_NAUTILUS_SCENARIO"
    elif any_development_pass:
        decision = "DISCARD_FUNDING_ROUTER_AFTER_UNTOUCHED_2024_FAILURE"
    else:
        decision = "DISCARD_FUNDING_ROUTER_AFTER_2023_MECHANISM_FAILURE"

    output.mkdir(parents=True, exist_ok=True)
    development.to_csv(output / "development_events.csv", index=False)
    if any_development_pass:
        holdout.to_csv(output / "holdout_events.csv", index=False)
    result = {
        "schema": "candidate-16-v15-funding-cliff-study-v1",
        "role": "external mechanism study; no fills, stop/target, account, portfolio, or NAV claim",
        "external_policy": {
            "family": "perpetual funding settlement crowding and unwind",
            "settlement_clock": "realized funding at event; descriptive entry at next minute open",
            "extreme": (
                f"absolute funding >= shifted trailing {EXTREME_QUANTILE:.0%} "
                f"quantile of prior {TRAILING_EVENTS} events"
            ),
            "states": {
                "PRE_SETTLEMENT_UNWIND_EXHAUSTED": (
                    "prior 30m moved against funding sign and perpetual led spot; "
                    "test rebound in funding-sign direction"
                ),
                "CROWDED_FUNDING_FADE": (
                    "extreme aligned funding/basis without derivatives-led unwind; "
                    "test opposite funding-sign direction"
                ),
            },
        },
        "data": {
            "source": "checksum-verified Binance Vision fundingRate, spot and USD-M 1m archives",
            "symbols": list(SYMBOLS),
            "years": [DEVELOPMENT_YEAR, HOLDOUT_YEAR],
        },
        "cost": {"round_trip_rate": ROUND_TRIP_COST_RATE},
        "funding_rows": {symbol: int(len(value)) for symbol, value in funding.items()},
        "raw_symbol_events": len(raw_events),
        "independent_global_events": len(selected),
        "state_counts": (
            {
                str(key): int(value)
                for key, value in frame["state"].value_counts().sort_index().items()
            }
            if not frame.empty
            else {}
        ),
        "states": state_results,
        "promoted_states": promoted,
        "promote": bool(promoted),
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
