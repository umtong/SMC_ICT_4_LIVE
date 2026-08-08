#!/usr/bin/env python3
"""External NR7 next-day range-expansion study for Candidate 16 v17.

Toby Crabel's NR7 family treats the narrowest completed daily range of the last
seven sessions as stored energy for the next range expansion.  This candidate
uses that external context to generalize the positive-but-sparse v12 Initial
Balance observation without weakening any trigger:

    completed UTC day is the narrowest normalized high-low range of the last 7
      -> no order; the next UTC day is the only eligible auction
      -> the first physical contact with either prior-day boundary is final
      -> contact must close outside on >=1.5x prior-20m volume with spot and
         perpetual taker flow aligned
      -> no order on the breakout
      -> the first later touch of the broken boundary must close outside
      -> no order on the retest
      -> a strictly later bar breaks the defended retest with aligned spot/flow
      -> entry, retest-extreme invalidation and one-prior-day-range projection
         all belong to the new extension leg

Both boundaries are consumed by the first contact: the system cannot reject a
bad high breakout and later select a favorable low breakout from the same NR7
state.  A two-sided first-contact bar is unresolved/no-trade.  The measured
objective is fixed by the completed NR7 day, not an arbitrary R multiple.

The study uses checksum-verified Binance Vision spot and USD-M minute bars,
stop-before-target ordering inside ambiguous minutes, 20 bp round-trip cost,
and one global active trade.  It creates no fills, account, portfolio or NAV.
Unchanged 2024 is opened only after every frozen 2023 promotion check passes.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date, timedelta
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from v10_open_drive_study import DEVELOPMENT_YEAR
from v10_open_drive_study import GLOBAL_ENTRY_CLUSTER_MINUTES
from v10_open_drive_study import HOLDOUT_YEAR
from v10_open_drive_study import ROUND_TRIP_COST_RATE
from v10_open_drive_study import SYMBOLS
from v10_open_drive_study import load_symbol
from v10_open_drive_study import promotion_checks
from v10_open_drive_study import summarize


NR_LOOKBACK_DAYS = 7
ENTRY_VOLUME_LOOKBACK = 20
ENTRY_VOLUME_MULTIPLIER = 1.5
RETEST_SEARCH_MINUTES = 20
RESUMPTION_SEARCH_MINUTES = 5
MAX_HOLD_MINUTES = 180
MIN_TARGET_NET_R = 1.0


@dataclass(frozen=True, slots=True)
class DailyRangeState:
    day: date
    open: float
    high: float
    low: float
    close: float
    normalized_range: float
    nr7: bool


@dataclass(frozen=True, slots=True)
class NR7Candidate:
    symbol: str
    nr7_day: str
    contact_ts: pd.Timestamp
    retest_ts: pd.Timestamp
    entry_ts: pd.Timestamp
    side: int
    entry: float
    stop: float
    target: float
    target_source: str
    planned_loss_rate: float
    target_net_r: float
    nr7_open: float
    nr7_high: float
    nr7_low: float
    nr7_close: float
    nr7_range: float
    nr7_normalized_range: float
    contact_volume_ratio: float
    contact_flow: float
    contact_spot_return: float
    expansion_score: float


@dataclass(frozen=True, slots=True)
class ScoredNR7:
    candidate: NR7Candidate
    exit_ts: pd.Timestamp
    exit_reason: str
    exit_price: float
    net_return: float
    net_r: float
    mfe: float
    mae: float


def build_daily_states(panel: pd.DataFrame) -> dict[date, DailyRangeState]:
    source = panel.copy()
    source.index = pd.to_datetime(source.index, utc=True).astype("datetime64[ns, UTC]")
    grouped = source.resample("1D", label="left", closed="left")
    daily = pd.DataFrame(
        {
            "open": grouped["perp_open"].first(),
            "high": grouped["perp_high"].max(),
            "low": grouped["perp_low"].min(),
            "close": grouped["perp_close"].last(),
            "minute_count": grouped["perp_close"].count(),
        },
    )
    daily = daily[daily["minute_count"] == 1_440].copy()
    daily["normalized_range"] = (
        (daily["high"] - daily["low"])
        / daily["open"].replace(0.0, np.nan)
    )
    trailing_min = daily["normalized_range"].rolling(
        NR_LOOKBACK_DAYS,
        min_periods=NR_LOOKBACK_DAYS,
    ).min()
    # Exact equality is deterministic because the current value participates in
    # a fixed seven-observation minimum; ties remain valid NR7 states.
    daily["nr7"] = daily["normalized_range"].eq(trailing_min)
    result: dict[date, DailyRangeState] = {}
    for timestamp, row in daily.iterrows():
        values = [
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
            float(row["normalized_range"]),
        ]
        if not all(math.isfinite(value) and value > 0.0 for value in values):
            continue
        day = pd.Timestamp(timestamp).date()
        result[day] = DailyRangeState(
            day=day,
            open=values[0],
            high=values[1],
            low=values[2],
            close=values[3],
            normalized_range=values[4],
            nr7=bool(row["nr7"]),
        )
    return result


def _day_frame(panel: pd.DataFrame, day: date) -> pd.DataFrame:
    start = pd.Timestamp(day, tz="UTC").as_unit("ns")
    expected = pd.date_range(start, periods=1_440, freq="min", tz="UTC").as_unit("ns")
    frame = panel.reindex(expected)
    if frame["perp_close"].isna().any() or frame["spot_close"].isna().any():
        return pd.DataFrame()
    return frame


def detect_candidate(
    *,
    symbol: str,
    state: DailyRangeState,
    next_day: pd.DataFrame,
) -> NR7Candidate | None:
    if not state.nr7 or next_day.empty:
        return None
    volume_mean = (
        pd.to_numeric(next_day["perp_quote_volume"], errors="coerce")
        .rolling(ENTRY_VOLUME_LOOKBACK, min_periods=ENTRY_VOLUME_LOOKBACK)
        .mean()
        .shift(1)
    )
    previous_close = pd.to_numeric(next_day["perp_close"], errors="coerce").shift(1)
    contact_position: int | None = None
    side = 0
    contact_volume_ratio = math.nan
    contact_flow = math.nan
    contact_spot = math.nan
    for position in range(ENTRY_VOLUME_LOOKBACK, len(next_day)):
        row = next_day.iloc[position]
        prev = float(previous_close.iloc[position])
        high = float(row["perp_high"])
        low = float(row["perp_low"])
        close = float(row["perp_close"])
        high_contact = prev <= state.high and high >= state.high
        low_contact = prev >= state.low and low <= state.low
        if high_contact and low_contact:
            return None
        if not high_contact and not low_contact:
            continue
        candidate_side = 1 if high_contact else -1
        # The first physical contact consumes both boundaries whether it succeeds
        # or fails, preventing later direction selection inside the same state.
        accepted = close > state.high if candidate_side > 0 else close < state.low
        prior = float(volume_mean.iloc[position])
        volume = float(row["perp_quote_volume"])
        volume_ratio = volume / max(prior, 1e-12)
        flow = float(row["perp_flow"])
        spot = float(row["spot_ret_1m"])
        body = candidate_side * (close - float(row["perp_open"]))
        if not (
            accepted
            and body > 0.0
            and math.isfinite(volume_ratio)
            and volume_ratio >= ENTRY_VOLUME_MULTIPLIER
            and math.isfinite(flow)
            and candidate_side * flow > 0.0
            and math.isfinite(spot)
            and candidate_side * spot > 0.0
        ):
            return None
        contact_position = position
        side = candidate_side
        contact_volume_ratio = volume_ratio
        contact_flow = flow
        contact_spot = spot
        break
    if contact_position is None or side == 0:
        return None

    edge = state.high if side > 0 else state.low
    day_range = state.high - state.low
    target = edge + side * day_range
    contact_row = next_day.iloc[contact_position]
    if (side > 0 and float(contact_row["perp_high"]) >= target) or (
        side < 0 and float(contact_row["perp_low"]) <= target
    ):
        return None

    retest_position: int | None = None
    retest_row: pd.Series | None = None
    search_end = min(
        contact_position + 1 + RETEST_SEARCH_MINUTES,
        len(next_day) - RESUMPTION_SEARCH_MINUTES,
    )
    for position in range(contact_position + 1, search_end):
        row = next_day.iloc[position]
        high = float(row["perp_high"])
        low = float(row["perp_low"])
        close = float(row["perp_close"])
        if (side > 0 and high >= target) or (side < 0 and low <= target):
            return None
        failed = close <= edge if side > 0 else close >= edge
        if failed:
            return None
        touched = low <= edge if side > 0 else high >= edge
        if not touched:
            continue
        retest_position = position
        retest_row = row
        break
    if retest_position is None or retest_row is None:
        return None

    retest_extreme = (
        float(retest_row["perp_low"])
        if side > 0
        else float(retest_row["perp_high"])
    )
    retest_break = (
        float(retest_row["perp_high"])
        if side > 0
        else float(retest_row["perp_low"])
    )
    for position in range(
        retest_position + 1,
        min(retest_position + 1 + RESUMPTION_SEARCH_MINUTES, len(next_day)),
    ):
        row = next_day.iloc[position]
        high = float(row["perp_high"])
        low = float(row["perp_low"])
        close = float(row["perp_close"])
        if (side > 0 and low <= retest_extreme) or (side < 0 and high >= retest_extreme):
            return None
        if (side > 0 and high >= target) or (side < 0 and low <= target):
            return None
        resumed = close > retest_break if side > 0 else close < retest_break
        body = side * (close - float(row["perp_open"]))
        flow = float(row["perp_flow"])
        spot = float(row["spot_ret_1m"])
        if not (
            resumed
            and body > 0.0
            and math.isfinite(flow)
            and side * flow > 0.0
            and math.isfinite(spot)
            and side * spot > 0.0
        ):
            continue
        entry = close
        stop = retest_extreme
        if (side > 0 and not stop < entry < target) or (
            side < 0 and not target < entry < stop
        ):
            return None
        planned_loss_rate = side * (entry - stop) / entry + ROUND_TRIP_COST_RATE
        net_target_return = side * (target - entry) / entry - ROUND_TRIP_COST_RATE
        if planned_loss_rate <= 0.0 or net_target_return <= 0.0:
            return None
        target_net_r = net_target_return / planned_loss_rate
        if target_net_r + 1e-12 < MIN_TARGET_NET_R:
            return None
        score = (
            contact_volume_ratio
            * state.normalized_range
            * target_net_r
        )
        return NR7Candidate(
            symbol=symbol,
            nr7_day=state.day.isoformat(),
            contact_ts=pd.Timestamp(contact_row.name),
            retest_ts=pd.Timestamp(retest_row.name),
            entry_ts=pd.Timestamp(row.name),
            side=side,
            entry=entry,
            stop=stop,
            target=target,
            target_source="ONE_PRIOR_NR7_DAY_RANGE_EXTENSION",
            planned_loss_rate=planned_loss_rate,
            target_net_r=target_net_r,
            nr7_open=state.open,
            nr7_high=state.high,
            nr7_low=state.low,
            nr7_close=state.close,
            nr7_range=day_range,
            nr7_normalized_range=state.normalized_range,
            contact_volume_ratio=contact_volume_ratio,
            contact_flow=contact_flow,
            contact_spot_return=contact_spot,
            expansion_score=score,
        )
    return None


def collapse_global_clusters(candidates: list[NR7Candidate]) -> list[NR7Candidate]:
    if not candidates:
        return []
    ordered = sorted(candidates, key=lambda item: item.entry_ts)
    clusters: list[list[NR7Candidate]] = []
    current: list[NR7Candidate] = []
    anchor: pd.Timestamp | None = None
    for candidate in ordered:
        if (
            anchor is None
            or candidate.entry_ts - anchor > pd.Timedelta(minutes=GLOBAL_ENTRY_CLUSTER_MINUTES)
        ):
            if current:
                clusters.append(current)
            current = [candidate]
            anchor = candidate.entry_ts
        else:
            current.append(candidate)
    if current:
        clusters.append(current)
    return [
        max(
            cluster,
            key=lambda item: (
                item.expansion_score,
                item.target_net_r,
                item.symbol,
            ),
        )
        for cluster in clusters
    ]


def _minute_window(panel: pd.DataFrame, start: pd.Timestamp, minutes: int) -> pd.DataFrame | None:
    expected = pd.date_range(start, periods=minutes, freq="min", tz="UTC").as_unit("ns")
    sample = panel.reindex(expected)
    if sample["perp_close"].isna().any():
        return None
    return sample


def score_candidate(candidate: NR7Candidate, panel: pd.DataFrame) -> ScoredNR7 | None:
    future = _minute_window(
        panel,
        candidate.entry_ts + pd.Timedelta(minutes=1),
        MAX_HOLD_MINUTES,
    )
    if future is None:
        return None
    exit_reason = "TIME_EXIT"
    exit_price = float(future.iloc[-1]["perp_close"])
    exit_ts = pd.Timestamp(future.iloc[-1].name)
    mfe = 0.0
    mae = 0.0
    for _, row in future.iterrows():
        high = float(row["perp_high"])
        low = float(row["perp_low"])
        if candidate.side > 0:
            mfe = max(mfe, high / candidate.entry - 1.0)
            mae = min(mae, low / candidate.entry - 1.0)
            stop_hit = low <= candidate.stop
            target_hit = high >= candidate.target
        else:
            mfe = max(mfe, 1.0 - low / candidate.entry)
            mae = min(mae, 1.0 - high / candidate.entry)
            stop_hit = high >= candidate.stop
            target_hit = low <= candidate.target
        if stop_hit:
            exit_reason = "STOP"
            exit_price = candidate.stop
            exit_ts = pd.Timestamp(row.name)
            break
        if target_hit:
            exit_reason = "NR7_MEASURED_TARGET"
            exit_price = candidate.target
            exit_ts = pd.Timestamp(row.name)
            break
    net_return = (
        candidate.side * (exit_price - candidate.entry) / candidate.entry
        - ROUND_TRIP_COST_RATE
    )
    return ScoredNR7(
        candidate=candidate,
        exit_ts=exit_ts,
        exit_reason=exit_reason,
        exit_price=exit_price,
        net_return=net_return,
        net_r=net_return / candidate.planned_loss_rate,
        mfe=mfe,
        mae=mae,
    )


def discover(
    panels: dict[str, pd.DataFrame],
    states: dict[str, dict[date, DailyRangeState]],
) -> tuple[list[NR7Candidate], dict[str, int]]:
    candidates: list[NR7Candidate] = []
    funnel = {
        "complete_daily_states": 0,
        "nr7_states": 0,
        "complete_next_days": 0,
        "complete_nr7_candidates": 0,
        "global_cluster_representatives": 0,
    }
    for symbol, state_map in states.items():
        panel = panels[symbol]
        funnel["complete_daily_states"] += len(state_map)
        for day, state in state_map.items():
            if not state.nr7:
                continue
            funnel["nr7_states"] += 1
            next_day = _day_frame(panel, day + timedelta(days=1))
            if next_day.empty:
                continue
            funnel["complete_next_days"] += 1
            candidate = detect_candidate(
                symbol=symbol,
                state=state,
                next_day=next_day,
            )
            if candidate is not None:
                funnel["complete_nr7_candidates"] += 1
                candidates.append(candidate)
    selected = collapse_global_clusters(candidates)
    funnel["global_cluster_representatives"] = len(selected)
    return selected, funnel


def enforce_one_global_slot(
    candidates: list[NR7Candidate],
    panels: dict[str, pd.DataFrame],
) -> tuple[list[ScoredNR7], int]:
    active_until: pd.Timestamp | None = None
    scored: list[ScoredNR7] = []
    conflicts = 0
    for candidate in sorted(candidates, key=lambda item: item.entry_ts):
        if active_until is not None and candidate.entry_ts <= active_until:
            conflicts += 1
            continue
        result = score_candidate(candidate, panels[candidate.symbol])
        if result is None:
            continue
        scored.append(result)
        active_until = result.exit_ts
    return scored, conflicts


def utc_block(timestamp: pd.Timestamp) -> str:
    hour = timestamp.tz_convert("UTC").hour
    if hour < 8:
        return "ASIA_0000_0759_UTC"
    if hour < 13:
        return "EUROPE_0800_1259_UTC"
    if hour < 21:
        return "NEW_YORK_1300_2059_UTC"
    return "LATE_2100_2359_UTC"


def records(scored: list[ScoredNR7]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for item in scored:
        rows.append(
            {
                **asdict(item.candidate),
                "session": utc_block(item.candidate.entry_ts),
                "exit_ts": item.exit_ts,
                "exit_reason": item.exit_reason,
                "exit_price": item.exit_price,
                "net_return": item.net_return,
                "net_r": item.net_r,
                "mfe": item.mfe,
                "mae": item.mae,
            },
        )
    return pd.DataFrame(rows)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def run(cache: Path, output: Path) -> dict[str, Any]:
    panels = {symbol: load_symbol(symbol, cache) for symbol in SYMBOLS}
    states = {symbol: build_daily_states(panel) for symbol, panel in panels.items()}
    candidates, funnel = discover(panels, states)
    scored, conflicts = enforce_one_global_slot(candidates, panels)
    frame = records(scored)
    if frame.empty:
        development = frame
        holdout = frame
    else:
        years = pd.to_datetime(frame["entry_ts"], utc=True).dt.year
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
        decision = "PROMOTE_NR7_RANGE_EXTENSION_TO_NAUTILUS_CONTINUOUS_ACCOUNT"
    elif development_pass:
        decision = "DISCARD_NR7_RANGE_EXTENSION_AFTER_UNTOUCHED_2024_FAILURE"
    else:
        decision = "DISCARD_NR7_RANGE_EXTENSION_AFTER_2023_DEVELOPMENT_FAILURE"

    output.mkdir(parents=True, exist_ok=True)
    development.to_csv(output / "development_trades.csv", index=False)
    if holdout_opened:
        holdout.to_csv(output / "holdout_trades.csv", index=False)
    result = {
        "schema": "candidate-16-v17-nr7-range-expansion-study-v1",
        "role": "mechanism and geometry study; no fills, account, portfolio, or NAV claim",
        "external_policy": {
            "family": "Toby Crabel NR7 next-day range expansion",
            "context": (
                "completed UTC day normalized range equals minimum of fixed last seven days"
            ),
            "interaction": (
                "first next-day contact with either boundary; close outside on >=1.5x "
                "prior20m volume with spot and taker flow aligned"
            ),
            "transition": (
                "first later boundary retest closes outside; strictly later flow/spot-aligned "
                "break of defended retest"
            ),
            "objective": "one complete prior NR7 day range projected from broken boundary",
        },
        "data": {
            "source": "checksum-verified Binance Vision spot and USD-M 1m monthly klines",
            "symbols": list(SYMBOLS),
            "years": [DEVELOPMENT_YEAR, HOLDOUT_YEAR],
        },
        "scenario_contract": {
            "nr_lookback_days": NR_LOOKBACK_DAYS,
            "first_contact_consumes_both_boundaries": True,
            "two_sided_first_contact_unresolved": True,
            "no_order_on_breakout": True,
            "first_retest_is_final": True,
            "no_order_on_retest": True,
            "minimum_target_net_r": MIN_TARGET_NET_R,
            "round_trip_cost_rate": ROUND_TRIP_COST_RATE,
            "same_bar_stop_before_target": True,
            "max_hold_minutes": MAX_HOLD_MINUTES,
            "global_entry_or_position_slot": 1,
        },
        "daily_state_counts": {symbol: len(value) for symbol, value in states.items()},
        "funnel": funnel,
        "global_slot_conflicts_skipped": conflicts,
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
