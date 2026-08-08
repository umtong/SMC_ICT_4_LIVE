#!/usr/bin/env python3
"""External Market-Profile Open-Rejection-Reverse study for Candidate 16 v11.

This family is independent from the project's sweep/L1 and cross-sectional
residual lineages.  It mechanizes a widely used Auction Market Theory policy:

    prior completed value distribution
      -> session opens outside value
      -> first 30 minutes fail to gain acceptance back inside
      -> second 30 minutes reject the outside auction and re-enter value
      -> no order on re-entry
      -> first later pullback toward the rejected value edge
      -> strictly later resumption toward prior POC with spot and taker flow
      -> stop beyond the rejected value edge / pullback extreme
      -> target the past-known prior POC

The volume-profile calculation reuses the core algorithm from the BSD-licensed
``bfolkens/py-market-profile`` project: close-price volume bins, a POC chosen
from the maximum-volume row, then adjacent-row expansion until 70% of volume is
inside value.  We adapt only the row size for four very different crypto prices
by using 100 deterministic equal-width rows over the prior UTC day's range.

The study uses checksum-verified Binance Vision spot and USD-M minute bars,
conservative stop-before-target ordering inside ambiguous one-minute bars, the
project's 20 bp round-trip cost screen, and one global active trade.  It creates
no exchange fills, account, portfolio or NAV.  Only a system that passes 2023
development and untouched 2024 unchanged may be promoted to NautilusTrader.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from v10_open_drive_study import HOLDOUT_YEAR
from v10_open_drive_study import DEVELOPMENT_YEAR
from v10_open_drive_study import GLOBAL_ENTRY_CLUSTER_MINUTES
from v10_open_drive_study import ROUND_TRIP_COST_RATE
from v10_open_drive_study import SYMBOLS
from v10_open_drive_study import _collapse_entry_clusters as _unused_open_drive_cluster
from v10_open_drive_study import load_symbol
from v10_open_drive_study import session_opens


PROFILE_ROWS = 100
VALUE_AREA_PCT = 0.70
FIRST_PERIOD_MINUTES = 30
SECOND_PERIOD_MINUTES = 30
PULLBACK_SEARCH_MINUTES = 15
MAX_PULLBACK_BARS = 5
MAX_HOLD_MINUTES = 60
MIN_TARGET_NET_R = 1.0


@dataclass(frozen=True, slots=True)
class ValueProfile:
    day: date
    low: float
    high: float
    row_width: float
    poc: float
    val: float
    vah: float
    total_quote_volume: float


@dataclass(frozen=True, slots=True)
class RejectionCandidate:
    symbol: str
    session: str
    session_open_ts: pd.Timestamp
    reentry_ts: pd.Timestamp
    entry_ts: pd.Timestamp
    side: int
    entry: float
    stop: float
    target: float
    target_source: str
    planned_loss_rate: float
    target_net_r: float
    profile_day: str
    profile_poc: float
    profile_val: float
    profile_vah: float
    profile_row_width: float
    open_price: float
    outside_extension: float
    reentry_price: float
    rejection_score: float


@dataclass(frozen=True, slots=True)
class ScoredRejection:
    candidate: RejectionCandidate
    exit_ts: pd.Timestamp
    exit_reason: str
    exit_price: float
    net_return: float
    net_r: float
    mfe: float
    mae: float


def calculate_value_profile(frame: pd.DataFrame, day: date) -> ValueProfile | None:
    if frame.empty or len(frame) < 1_400:
        return None
    closes = pd.to_numeric(frame["perp_close"], errors="coerce").to_numpy(float)
    volumes = pd.to_numeric(frame["perp_quote_volume"], errors="coerce").to_numpy(float)
    valid = np.isfinite(closes) & np.isfinite(volumes) & (closes > 0.0) & (volumes > 0.0)
    closes = closes[valid]
    volumes = volumes[valid]
    if len(closes) < 1_300:
        return None
    low = float(closes.min())
    high = float(closes.max())
    if not math.isfinite(low) or not math.isfinite(high) or high <= low:
        return None
    edges = np.linspace(low, high, PROFILE_ROWS + 1, dtype=float)
    row_width = float(edges[1] - edges[0])
    indices = np.searchsorted(edges, closes, side="right") - 1
    indices = np.clip(indices, 0, PROFILE_ROWS - 1)
    profile = np.bincount(indices, weights=volumes, minlength=PROFILE_ROWS).astype(float)
    total = float(profile.sum())
    if not math.isfinite(total) or total <= 0.0:
        return None

    max_volume = float(profile.max())
    max_indices = np.flatnonzero(np.isclose(profile, max_volume, rtol=0.0, atol=0.0))
    poc_index = int(max_indices[len(max_indices) // 2])
    target_volume = total * VALUE_AREA_PCT
    trial = float(profile[poc_index])
    low_index = poc_index
    high_index = poc_index
    while trial < target_volume:
        lower = low_index - 1 if low_index > 0 else None
        upper = high_index + 1 if high_index + 1 < PROFILE_ROWS else None
        if lower is None and upper is None:
            break
        lower_volume = float(profile[lower]) if lower is not None else -math.inf
        upper_volume = float(profile[upper]) if upper is not None else -math.inf
        if lower_volume > upper_volume:
            low_index = int(lower)
            trial += lower_volume
        else:
            high_index = int(upper)
            trial += upper_volume

    centers = (edges[:-1] + edges[1:]) / 2.0
    return ValueProfile(
        day=day,
        low=low,
        high=high,
        row_width=row_width,
        poc=float(centers[poc_index]),
        val=float(edges[low_index]),
        vah=float(edges[high_index + 1]),
        total_quote_volume=total,
    )


def build_profiles(panel: pd.DataFrame) -> dict[date, ValueProfile]:
    profiles: dict[date, ValueProfile] = {}
    timestamps = pd.to_datetime(panel["minute"], utc=True)
    for day, group in panel.groupby(timestamps.dt.date, sort=True):
        profile = calculate_value_profile(group, day)
        if profile is not None:
            profiles[day] = profile
    return profiles


def _window(panel: pd.DataFrame, start: pd.Timestamp, minutes: int) -> pd.DataFrame | None:
    expected = pd.date_range(start, periods=minutes, freq="min", tz="UTC").as_unit("ns")
    sample = panel.reindex(expected)
    if sample["perp_close"].isna().any() or sample["spot_close"].isna().any():
        return None
    return sample


def _inside_value(close: float, profile: ValueProfile) -> bool:
    return profile.val <= close <= profile.vah


def detect_candidate(
    *,
    symbol: str,
    panel: pd.DataFrame,
    profiles: dict[date, ValueProfile],
    session_name: str,
    session_ts: pd.Timestamp,
) -> RejectionCandidate | None:
    prior_day = (session_ts - pd.Timedelta(days=1)).date()
    profile = profiles.get(prior_day)
    if profile is None:
        return None
    opening = _window(panel, session_ts, FIRST_PERIOD_MINUTES + SECOND_PERIOD_MINUTES)
    if opening is None:
        return None
    open_price = float(opening.iloc[0]["perp_open"])
    if open_price > profile.vah:
        side = -1
        edge = profile.vah
    elif open_price < profile.val:
        side = 1
        edge = profile.val
    else:
        return None

    first = opening.iloc[:FIRST_PERIOD_MINUTES]
    second = opening.iloc[FIRST_PERIOD_MINUTES:]
    # The first period must remain accepted outside value by completed closes.
    first_outside = (
        bool((first["perp_close"] > profile.vah).all())
        if side < 0
        else bool((first["perp_close"] < profile.val).all())
    )
    if not first_outside:
        return None
    outside_extension = (
        float(first["perp_high"].max() - profile.vah)
        if side < 0
        else float(profile.val - first["perp_low"].min())
    )
    if not math.isfinite(outside_extension) or outside_extension <= 0.0:
        return None

    reentry_position: int | None = None
    for position, (_, row) in enumerate(second.iterrows()):
        close = float(row["perp_close"])
        if not _inside_value(close, profile):
            continue
        body = side * (close - float(row["perp_open"]))
        flow = float(row["perp_flow"])
        spot = float(row["spot_ret_1m"])
        if (
            body > 0.0
            and math.isfinite(flow)
            and side * flow > 0.0
            and math.isfinite(spot)
            and side * spot > 0.0
        ):
            reentry_position = FIRST_PERIOD_MINUTES + position
            break
    if reentry_position is None:
        return None

    reentry_row = opening.iloc[reentry_position]
    reentry_ts = pd.Timestamp(reentry_row.name)
    reentry_price = float(reentry_row["perp_close"])
    if side > 0 and not reentry_price < profile.poc:
        return None
    if side < 0 and not profile.poc < reentry_price:
        return None

    later = _window(
        panel,
        reentry_ts + pd.Timedelta(minutes=1),
        PULLBACK_SEARCH_MINUTES + MAX_PULLBACK_BARS + 1,
    )
    if later is None:
        return None
    pullback_start: int | None = None
    pullback_extreme = math.nan
    pullback_break = math.nan
    for position in range(PULLBACK_SEARCH_MINUTES):
        row = later.iloc[position]
        close = float(row["perp_close"])
        # A completed close back outside value cancels the rejected-auction thesis.
        if side < 0 and close > profile.vah:
            return None
        if side > 0 and close < profile.val:
            return None
        body = side * (close - float(row["perp_open"]))
        if pullback_start is None:
            if body >= 0.0:
                continue
            pullback_start = position
            pullback_extreme = (
                float(row["perp_low"]) if side > 0 else float(row["perp_high"])
            )
            pullback_break = (
                float(row["perp_high"]) if side > 0 else float(row["perp_low"])
            )
            continue

        elapsed = position - pullback_start
        if elapsed > MAX_PULLBACK_BARS:
            return None
        pullback = later.iloc[pullback_start:position]
        if side > 0:
            pullback_extreme = min(pullback_extreme, float(pullback["perp_low"].min()))
            pullback_break = max(pullback_break, float(pullback["perp_high"].max()))
            resumed = close > pullback_break
        else:
            pullback_extreme = max(pullback_extreme, float(pullback["perp_high"].max()))
            pullback_break = min(pullback_break, float(pullback["perp_low"].min()))
            resumed = close < pullback_break
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
        buffer = profile.row_width
        if side > 0:
            stop = min(profile.val - buffer, float(pullback_extreme) - buffer)
            target = profile.poc
            valid = stop < entry < target
        else:
            stop = max(profile.vah + buffer, float(pullback_extreme) + buffer)
            target = profile.poc
            valid = target < entry < stop
        if not valid:
            return None
        planned_loss_rate = side * (entry - stop) / entry + ROUND_TRIP_COST_RATE
        net_target_return = side * (target - entry) / entry - ROUND_TRIP_COST_RATE
        if planned_loss_rate <= 0.0 or net_target_return <= 0.0:
            return None
        target_net_r = net_target_return / planned_loss_rate
        if target_net_r + 1e-12 < MIN_TARGET_NET_R:
            return None
        score = (
            outside_extension / max(profile.row_width, 1e-12)
            * abs(reentry_price - edge) / max(profile.row_width, 1e-12)
        )
        return RejectionCandidate(
            symbol=symbol,
            session=session_name,
            session_open_ts=session_ts,
            reentry_ts=reentry_ts,
            entry_ts=pd.Timestamp(row.name),
            side=side,
            entry=entry,
            stop=stop,
            target=target,
            target_source="PRIOR_DAY_VOLUME_POC",
            planned_loss_rate=planned_loss_rate,
            target_net_r=target_net_r,
            profile_day=prior_day.isoformat(),
            profile_poc=profile.poc,
            profile_val=profile.val,
            profile_vah=profile.vah,
            profile_row_width=profile.row_width,
            open_price=open_price,
            outside_extension=outside_extension,
            reentry_price=reentry_price,
            rejection_score=score,
        )
    return None


def collapse_global_clusters(candidates: list[RejectionCandidate]) -> list[RejectionCandidate]:
    if not candidates:
        return []
    ordered = sorted(candidates, key=lambda item: item.entry_ts)
    clusters: list[list[RejectionCandidate]] = []
    current: list[RejectionCandidate] = []
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
                item.rejection_score,
                item.target_net_r,
                item.symbol,
            ),
        )
        for cluster in clusters
    ]


def score_candidate(
    candidate: RejectionCandidate,
    panel: pd.DataFrame,
) -> ScoredRejection | None:
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
            exit_reason = "POC_TARGET"
            exit_price = candidate.target
            exit_ts = pd.Timestamp(row.name)
            break
    net_return = (
        candidate.side * (exit_price - candidate.entry) / candidate.entry
        - ROUND_TRIP_COST_RATE
    )
    return ScoredRejection(
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
    profiles: dict[str, dict[date, ValueProfile]],
) -> tuple[list[RejectionCandidate], dict[str, int]]:
    candidates: list[RejectionCandidate] = []
    funnel = {
        "session_symbol_observations": 0,
        "complete_open_rejection_reverse_candidates": 0,
        "global_cluster_representatives": 0,
    }
    for session in session_opens(
        date(DEVELOPMENT_YEAR, 1, 1),
        date(HOLDOUT_YEAR, 12, 31),
    ):
        for symbol, panel in panels.items():
            funnel["session_symbol_observations"] += 1
            candidate = detect_candidate(
                symbol=symbol,
                panel=panel,
                profiles=profiles[symbol],
                session_name=session.name,
                session_ts=session.ts,
            )
            if candidate is not None:
                funnel["complete_open_rejection_reverse_candidates"] += 1
                candidates.append(candidate)
    selected = collapse_global_clusters(candidates)
    funnel["global_cluster_representatives"] = len(selected)
    return selected, funnel


def enforce_one_global_slot(
    candidates: list[RejectionCandidate],
    panels: dict[str, pd.DataFrame],
) -> tuple[list[ScoredRejection], int]:
    active_until: pd.Timestamp | None = None
    scored: list[ScoredRejection] = []
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


def records(scored: list[ScoredRejection]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for item in scored:
        candidate = item.candidate
        rows.append(
            {
                **candidate.__dict__,
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


def _profit_factor(values: pd.Series) -> float | None:
    gross_profit = float(values[values > 0.0].sum())
    gross_loss = float(-values[values < 0.0].sum())
    if gross_loss <= 0.0:
        return math.inf if gross_profit > 0.0 else None
    return gross_profit / gross_loss


def summarize(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "mean_net_r": 0.0,
            "median_net_r": 0.0,
            "profit_factor": 0.0,
            "median_mfe": 0.0,
            "median_mae": 0.0,
            "largest_winner_share": 1.0,
            "symbols_positive_mean_r": 0,
            "sessions_positive_mean_r": 0,
            "by_symbol": {},
            "by_session": {},
        }
    values = pd.to_numeric(frame["net_r"], errors="raise")
    by_symbol = {
        str(symbol): {
            "trades": int(len(group)),
            "wins": int((group["net_r"] > 0.0).sum()),
            "mean_net_r": float(group["net_r"].mean()),
        }
        for symbol, group in frame.groupby("symbol", sort=True)
    }
    by_session = {
        str(session): {
            "trades": int(len(group)),
            "wins": int((group["net_r"] > 0.0).sum()),
            "mean_net_r": float(group["net_r"].mean()),
        }
        for session, group in frame.groupby("session", sort=True)
    }
    factor = _profit_factor(values)
    winners = values[values > 0.0]
    return {
        "trades": int(len(frame)),
        "wins": int((values > 0.0).sum()),
        "losses": int((values < 0.0).sum()),
        "win_rate": float((values > 0.0).mean()),
        "mean_net_r": float(values.mean()),
        "median_net_r": float(values.median()),
        "profit_factor": None if factor is not None and math.isinf(factor) else factor,
        "profit_factor_infinite": bool(factor is not None and math.isinf(factor)),
        "median_mfe": float(frame["mfe"].median()),
        "median_mae": float(frame["mae"].median()),
        "active_days": int(pd.to_datetime(frame["entry_ts"], utc=True).dt.date.nunique()),
        "largest_winner_share": (
            float(winners.max() / winners.sum()) if not winners.empty else 1.0
        ),
        "symbols_positive_mean_r": sum(
            item["mean_net_r"] > 0.0 for item in by_symbol.values()
        ),
        "sessions_positive_mean_r": sum(
            item["mean_net_r"] > 0.0 for item in by_session.values()
        ),
        "by_symbol": by_symbol,
        "by_session": by_session,
        "exit_reasons": {
            str(key): int(value)
            for key, value in frame["exit_reason"].value_counts().sort_index().items()
        },
    }


def promotion_checks(summary: dict[str, Any]) -> dict[str, bool]:
    factor = summary.get("profit_factor")
    factor_pass = bool(summary.get("profit_factor_infinite")) or (
        factor is not None and float(factor) >= 1.5
    )
    return {
        "trades_at_least_20": int(summary.get("trades", 0)) >= 20,
        "wins_at_least_10": int(summary.get("wins", 0)) >= 10,
        "win_rate_at_least_50pct": float(summary.get("win_rate", 0.0)) >= 0.50,
        "mean_net_r_at_least_0_25": float(summary.get("mean_net_r", 0.0)) >= 0.25,
        "profit_factor_at_least_1_5": factor_pass,
        "median_mfe_covers_round_trip_cost": float(summary.get("median_mfe", 0.0)) >= ROUND_TRIP_COST_RATE,
        "positive_on_at_least_three_symbols": int(summary.get("symbols_positive_mean_r", 0)) >= 3,
        "positive_on_at_least_two_sessions": int(summary.get("sessions_positive_mean_r", 0)) >= 2,
        "largest_winner_share_at_most_35pct": float(summary.get("largest_winner_share", 1.0)) <= 0.35,
    }


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def run(cache: Path, output: Path) -> dict[str, Any]:
    panels = {symbol: load_symbol(symbol, cache) for symbol in SYMBOLS}
    profiles = {symbol: build_profiles(panel.reset_index(drop=True)) for symbol, panel in panels.items()}
    candidates, funnel = discover(panels, profiles)
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
        decision = "PROMOTE_OPEN_REJECTION_REVERSE_TO_NAUTILUS_CONTINUOUS_ACCOUNT"
    elif development_pass:
        decision = "DISCARD_OPEN_REJECTION_REVERSE_AFTER_UNTOUCHED_2024_FAILURE"
    else:
        decision = "DISCARD_OPEN_REJECTION_REVERSE_AFTER_2023_DEVELOPMENT_FAILURE"

    output.mkdir(parents=True, exist_ok=True)
    development.to_csv(output / "development_trades.csv", index=False)
    if holdout_opened:
        holdout.to_csv(output / "holdout_trades.csv", index=False)
    result = {
        "schema": "candidate-16-v11-open-rejection-reverse-study-v1",
        "role": "mechanism and geometry study; no fills, account, portfolio, or NAV claim",
        "external_reuse": {
            "profile_algorithm": "bfolkens/py-market-profile BSD-3-Clause",
            "adaptation": (
                "100 equal-width prior-day close-price quote-volume rows; "
                "POC max row and 70% adjacent expansion"
            ),
            "trade_policy": (
                "Open-Rejection-Reverse: open outside prior value, first 30m outside, "
                "second 30m re-entry, first later pullback and resumption, target POC"
            ),
        },
        "data": {
            "source": "checksum-verified Binance Vision spot and USD-M 1m monthly klines",
            "symbols": list(SYMBOLS),
            "years": [DEVELOPMENT_YEAR, HOLDOUT_YEAR],
        },
        "profile_contract": {
            "utc_prior_day_only": True,
            "rows": PROFILE_ROWS,
            "value_area_pct": VALUE_AREA_PCT,
            "profile_price_input": "completed one-minute close",
            "profile_volume_input": "completed one-minute quote volume",
        },
        "scenario_contract": {
            "first_period_minutes": FIRST_PERIOD_MINUTES,
            "second_period_minutes": SECOND_PERIOD_MINUTES,
            "no_order_on_reentry": True,
            "max_pullback_bars": MAX_PULLBACK_BARS,
            "target": "prior completed UTC-day POC",
            "stop": "beyond rejected VA edge and later pullback extreme",
            "round_trip_cost_rate": ROUND_TRIP_COST_RATE,
            "minimum_target_net_r": MIN_TARGET_NET_R,
            "same_bar_stop_before_target": True,
            "max_hold_minutes": MAX_HOLD_MINUTES,
            "global_entry_or_position_slot": 1,
        },
        "profile_counts": {symbol: len(value) for symbol, value in profiles.items()},
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
