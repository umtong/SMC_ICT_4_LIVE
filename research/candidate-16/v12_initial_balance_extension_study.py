#!/usr/bin/env python3
"""External Initial-Balance range-extension study for Candidate 16 v12.

The failed v10 Open Drive tried to monetize the first ten minutes and exposed
less gross movement than the project's round-trip cost.  This candidate does
not loosen v10.  It implements a different Auction Market Theory policy mined
from Initial Balance traders:

    first completed session hour forms a narrow, rotational balance
      -> no order inside the balance
      -> first later initiative closes beyond the IB with spot, flow and volume
      -> no order on the breakout
      -> the first later touch of the broken IB edge must close outside
      -> no order on the retest
      -> a strictly later bar breaks the defended retest with spot and flow
      -> entry/stop/1x-IB projection all belong to this new extension leg

The balance definition is causal and portable: the current normalized IB range
must be at or below the shifted 25th percentile of the prior 40 completed
same-session ranges for that symbol, while the first-hour net path consumes at
most 35% of its high-low range.  The first retest is final; a failed first touch
cannot be skipped for a favorable later touch.

The study reuses checksum-verified Binance Vision spot/perpetual data and the
session/DST loader from v10.  It uses conservative stop-before-target ordering,
20 bp round-trip cost, and one global active trade.  It creates no fills,
account, portfolio or NAV.  2024 remains unopened unless unchanged 2023
mechanism and geometry pass.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date
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
from v10_open_drive_study import session_opens
from v10_open_drive_study import summarize


INITIAL_BALANCE_MINUTES = 60
MIN_HISTORY = 40
NARROW_QUANTILE = 0.25
MAX_ROTATIONAL_EFFICIENCY = 0.35
BREAKOUT_SEARCH_MINUTES = 30
RETEST_SEARCH_MINUTES = 20
RESUMPTION_SEARCH_MINUTES = 5
MAX_HOLD_MINUTES = 120
MIN_TARGET_NET_R = 1.0


@dataclass(slots=True)
class BalanceHistory:
    normalized_ranges: list[float]

    def ready(self) -> bool:
        return len(self.normalized_ranges) >= MIN_HISTORY

    def narrow_threshold(self) -> float:
        return float(np.quantile(self.normalized_ranges, NARROW_QUANTILE))

    def observe(self, value: float) -> None:
        if math.isfinite(value) and value > 0.0:
            self.normalized_ranges.append(float(value))


@dataclass(frozen=True, slots=True)
class BalanceCandidate:
    symbol: str
    session: str
    session_open_ts: pd.Timestamp
    breakout_ts: pd.Timestamp
    retest_ts: pd.Timestamp
    entry_ts: pd.Timestamp
    side: int
    entry: float
    stop: float
    target: float
    target_source: str
    planned_loss_rate: float
    target_net_r: float
    ib_high: float
    ib_low: float
    ib_range: float
    ib_normalized_range: float
    ib_narrow_threshold: float
    ib_efficiency: float
    breakout_close: float
    breakout_volume_ratio: float
    expansion_score: float


@dataclass(frozen=True, slots=True)
class ScoredBalance:
    candidate: BalanceCandidate
    exit_ts: pd.Timestamp
    exit_reason: str
    exit_price: float
    net_return: float
    net_r: float
    mfe: float
    mae: float


def _window(panel: pd.DataFrame, start: pd.Timestamp, minutes: int) -> pd.DataFrame | None:
    expected = pd.date_range(start, periods=minutes, freq="min", tz="UTC").as_unit("ns")
    sample = panel.reindex(expected)
    if sample["perp_close"].isna().any() or sample["spot_close"].isna().any():
        return None
    return sample


def detect_candidate(
    *,
    symbol: str,
    panel: pd.DataFrame,
    session_name: str,
    session_ts: pd.Timestamp,
    history: BalanceHistory,
) -> BalanceCandidate | None:
    initial = _window(panel, session_ts, INITIAL_BALANCE_MINUTES)
    if initial is None:
        return None
    open_price = float(initial.iloc[0]["perp_open"])
    close_price = float(initial.iloc[-1]["perp_close"])
    ib_high = float(initial["perp_high"].max())
    ib_low = float(initial["perp_low"].min())
    ib_range = ib_high - ib_low
    if min(open_price, close_price, ib_low) <= 0.0 or not math.isfinite(ib_range) or ib_range <= 0.0:
        return None
    normalized_range = ib_range / open_price
    threshold_ready = history.ready()
    narrow_threshold = history.narrow_threshold() if threshold_ready else math.nan
    history.observe(normalized_range)
    if not threshold_ready:
        return None
    efficiency = abs(close_price - open_price) / ib_range
    if normalized_range > narrow_threshold or efficiency > MAX_ROTATIONAL_EFFICIENCY:
        return None

    initiative = _window(
        panel,
        session_ts + pd.Timedelta(minutes=INITIAL_BALANCE_MINUTES),
        BREAKOUT_SEARCH_MINUTES + RETEST_SEARCH_MINUTES + RESUMPTION_SEARCH_MINUTES + 2,
    )
    if initiative is None:
        return None
    ib_volume_median = float(initial["perp_quote_volume"].median())
    breakout_position: int | None = None
    side = 0
    breakout_volume_ratio = math.nan
    for position in range(BREAKOUT_SEARCH_MINUTES):
        row = initiative.iloc[position]
        close = float(row["perp_close"])
        if close > ib_high:
            candidate_side = 1
        elif close < ib_low:
            candidate_side = -1
        else:
            continue
        body = candidate_side * (close - float(row["perp_open"]))
        flow = float(row["perp_flow"])
        spot = float(row["spot_ret_1m"])
        volume = float(row["perp_quote_volume"])
        volume_ratio = volume / max(ib_volume_median, 1e-12)
        if (
            body > 0.0
            and math.isfinite(flow)
            and candidate_side * flow > 0.0
            and math.isfinite(spot)
            and candidate_side * spot > 0.0
            and math.isfinite(volume_ratio)
            and volume_ratio > 1.0
        ):
            breakout_position = position
            side = candidate_side
            breakout_volume_ratio = volume_ratio
            break
    if breakout_position is None or side == 0:
        return None

    breakout_row = initiative.iloc[breakout_position]
    breakout_ts = pd.Timestamp(breakout_row.name)
    breakout_close = float(breakout_row["perp_close"])
    edge = ib_high if side > 0 else ib_low
    target = edge + side * ib_range
    if (side > 0 and breakout_close >= target) or (side < 0 and breakout_close <= target):
        return None

    first_touch_position: int | None = None
    first_touch_row: pd.Series | None = None
    search_start = breakout_position + 1
    search_end = min(
        search_start + RETEST_SEARCH_MINUTES,
        len(initiative) - RESUMPTION_SEARCH_MINUTES - 1,
    )
    for position in range(search_start, search_end):
        row = initiative.iloc[position]
        high = float(row["perp_high"])
        low = float(row["perp_low"])
        close = float(row["perp_close"])
        # Objective consumed before entry: missed trade, never chase.
        if (side > 0 and high >= target) or (side < 0 and low <= target):
            return None
        touched = low <= edge if side > 0 else high >= edge
        if not touched:
            # A completed close fully back inside before a touch invalidates the extension.
            if (side > 0 and close < edge) or (side < 0 and close > edge):
                return None
            continue
        first_touch_position = position
        first_touch_row = row
        defended = close > edge if side > 0 else close < edge
        if not defended:
            return None
        break
    if first_touch_position is None or first_touch_row is None:
        return None

    retest_ts = pd.Timestamp(first_touch_row.name)
    retest_extreme = (
        float(first_touch_row["perp_low"])
        if side > 0
        else float(first_touch_row["perp_high"])
    )
    retest_break = (
        float(first_touch_row["perp_high"])
        if side > 0
        else float(first_touch_row["perp_low"])
    )
    for position in range(
        first_touch_position + 1,
        min(first_touch_position + 1 + RESUMPTION_SEARCH_MINUTES, len(initiative)),
    ):
        row = initiative.iloc[position]
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
        expansion_score = (
            narrow_threshold / max(normalized_range, 1e-12)
            * breakout_volume_ratio
            * abs(breakout_close - edge) / max(ib_range, 1e-12)
        )
        return BalanceCandidate(
            symbol=symbol,
            session=session_name,
            session_open_ts=session_ts,
            breakout_ts=breakout_ts,
            retest_ts=retest_ts,
            entry_ts=pd.Timestamp(row.name),
            side=side,
            entry=entry,
            stop=stop,
            target=target,
            target_source="ONE_INITIAL_BALANCE_RANGE_EXTENSION",
            planned_loss_rate=planned_loss_rate,
            target_net_r=target_net_r,
            ib_high=ib_high,
            ib_low=ib_low,
            ib_range=ib_range,
            ib_normalized_range=normalized_range,
            ib_narrow_threshold=narrow_threshold,
            ib_efficiency=efficiency,
            breakout_close=breakout_close,
            breakout_volume_ratio=breakout_volume_ratio,
            expansion_score=expansion_score,
        )
    return None


def collapse_global_clusters(candidates: list[BalanceCandidate]) -> list[BalanceCandidate]:
    if not candidates:
        return []
    ordered = sorted(candidates, key=lambda item: item.entry_ts)
    clusters: list[list[BalanceCandidate]] = []
    current: list[BalanceCandidate] = []
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


def score_candidate(candidate: BalanceCandidate, panel: pd.DataFrame) -> ScoredBalance | None:
    future = _window(
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
            exit_reason = "IB_PROJECTION_TARGET"
            exit_price = candidate.target
            exit_ts = pd.Timestamp(row.name)
            break
    net_return = (
        candidate.side * (exit_price - candidate.entry) / candidate.entry
        - ROUND_TRIP_COST_RATE
    )
    return ScoredBalance(
        candidate=candidate,
        exit_ts=exit_ts,
        exit_reason=exit_reason,
        exit_price=exit_price,
        net_return=net_return,
        net_r=net_return / candidate.planned_loss_rate,
        mfe=mfe,
        mae=mae,
    )


def discover(panels: dict[str, pd.DataFrame]) -> tuple[list[BalanceCandidate], dict[str, int]]:
    histories: dict[tuple[str, str], BalanceHistory] = defaultdict(
        lambda: BalanceHistory([]),
    )
    candidates: list[BalanceCandidate] = []
    funnel = {
        "session_symbol_observations": 0,
        "threshold_ready_observations": 0,
        "complete_initial_balance_candidates": 0,
        "global_cluster_representatives": 0,
    }
    for session in session_opens(
        date(DEVELOPMENT_YEAR, 1, 1),
        date(HOLDOUT_YEAR, 12, 31),
    ):
        for symbol, panel in panels.items():
            history = histories[(symbol, session.name)]
            ready = history.ready()
            funnel["session_symbol_observations"] += 1
            candidate = detect_candidate(
                symbol=symbol,
                panel=panel,
                session_name=session.name,
                session_ts=session.ts,
                history=history,
            )
            if ready:
                funnel["threshold_ready_observations"] += 1
            if candidate is not None:
                funnel["complete_initial_balance_candidates"] += 1
                candidates.append(candidate)
    selected = collapse_global_clusters(candidates)
    funnel["global_cluster_representatives"] = len(selected)
    return selected, funnel


def enforce_one_global_slot(
    candidates: list[BalanceCandidate],
    panels: dict[str, pd.DataFrame],
) -> tuple[list[ScoredBalance], int]:
    active_until: pd.Timestamp | None = None
    scored: list[ScoredBalance] = []
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


def records(scored: list[ScoredBalance]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for item in scored:
        rows.append(
            {
                **asdict(item.candidate),
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
    candidates, funnel = discover(panels)
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
        decision = "PROMOTE_INITIAL_BALANCE_EXTENSION_TO_NAUTILUS_CONTINUOUS_ACCOUNT"
    elif development_pass:
        decision = "DISCARD_INITIAL_BALANCE_EXTENSION_AFTER_UNTOUCHED_2024_FAILURE"
    else:
        decision = "DISCARD_INITIAL_BALANCE_EXTENSION_AFTER_2023_DEVELOPMENT_FAILURE"

    output.mkdir(parents=True, exist_ok=True)
    development.to_csv(output / "development_trades.csv", index=False)
    if holdout_opened:
        holdout.to_csv(output / "holdout_trades.csv", index=False)
    result = {
        "schema": "candidate-16-v12-initial-balance-extension-study-v1",
        "role": "mechanism and geometry study; no fills, account, portfolio, or NAV claim",
        "external_policy": {
            "family": "Auction Market Theory / Initial Balance range extension",
            "context": (
                "first-hour normalized range <= shifted prior same-session 25th percentile "
                "and path efficiency <=35%"
            ),
            "state": (
                "first later close outside IB with above-IB-median volume, spot and taker flow"
            ),
            "transition": (
                "first touch of broken IB edge closes outside; strictly later bar breaks "
                "the retest with spot and taker flow"
            ),
            "invalidation": "first retest fails or later-leg retest extreme trades before entry",
            "objective": "one complete Initial Balance range projected from broken edge",
        },
        "data": {
            "source": "checksum-verified Binance Vision spot and USD-M 1m monthly klines",
            "symbols": list(SYMBOLS),
            "years": [DEVELOPMENT_YEAR, HOLDOUT_YEAR],
        },
        "scenario_contract": {
            "initial_balance_minutes": INITIAL_BALANCE_MINUTES,
            "minimum_prior_same_session_samples": MIN_HISTORY,
            "narrow_range_quantile": NARROW_QUANTILE,
            "max_rotational_efficiency": MAX_ROTATIONAL_EFFICIENCY,
            "no_order_on_breakout": True,
            "first_retest_is_final": True,
            "no_order_on_retest": True,
            "minimum_target_net_r": MIN_TARGET_NET_R,
            "round_trip_cost_rate": ROUND_TRIP_COST_RATE,
            "same_bar_stop_before_target": True,
            "max_hold_minutes": MAX_HOLD_MINUTES,
            "global_entry_or_position_slot": 1,
        },
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
