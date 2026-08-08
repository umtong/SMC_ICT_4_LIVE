#!/usr/bin/env python3
"""External prior-day HVN-LVN-HVN fast-lane study for Candidate 16 v16.

The policy is mined from volume-profile/Auction Market Theory playbooks rather
than inherited project patterns:

    prior completed UTC-day profile
      -> two accepted high-volume clusters separated by a thin low-volume gap
      -> the gap is fresh; only its first next-day contact may create a state
      -> a volume/spot/taker-flow backed close enters the gap
      -> no order on entry into the LVN
      -> the first later retest of the entry edge must close inside the gap
      -> no order on the retest
      -> a strictly later bar breaks the defended retest with spot and flow
      -> enter the new traversal leg
      -> stop beyond the defended edge/retest extreme
      -> target the near boundary of the opposite prior-day HVN

The volume profile follows the reusable close-price/quote-volume binning used by
``bfolkens/py-market-profile``.  Public node playbooks contribute fixed node
semantics: HVN clusters are top-quartile volume with at least three contiguous
rows; an LVN trough contains one to three bottom-quartile rows between the two
HVNs, and the complete intervening gap is below median volume.  There is no
post-result threshold search.

Checksum-verified Binance Vision spot and USD-M minute bars are used.  Same-bar
ambiguity is resolved stop before target, 20 bp round-trip costs are deducted,
and only one global active trade is allowed.  This is a mechanism/geometry study
only; no exchange fills, account, portfolio or NAV are created.  Unchanged 2024
is opened only after all frozen 2023 promotion checks pass.
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


PROFILE_ROWS = 100
HVN_QUANTILE = 0.75
LVN_QUANTILE = 0.25
MIN_HVN_ROWS = 3
MIN_LVN_ROWS = 1
MAX_LVN_ROWS = 3
MAX_GAP_ROWS = 8
ENTRY_VOLUME_MULTIPLIER = 1.5
ENTRY_VOLUME_LOOKBACK = 20
RETEST_SEARCH_MINUTES = 8
RESUMPTION_SEARCH_MINUTES = 4
MAX_HOLD_MINUTES = 60
MIN_TARGET_NET_R = 1.0


@dataclass(frozen=True, slots=True)
class NodeCluster:
    start: int
    end: int

    @property
    def rows(self) -> int:
        return self.end - self.start + 1


@dataclass(frozen=True, slots=True)
class FastLane:
    profile_day: date
    lower_hvn: NodeCluster
    upper_hvn: NodeCluster
    gap_start: int
    gap_end: int
    lvn_start: int
    lvn_end: int
    lower_hvn_target: float
    lower_entry_edge: float
    upper_entry_edge: float
    upper_hvn_target: float
    row_width: float
    gap_rows: int
    lvn_rows: int
    gap_volume_ratio_to_median: float


@dataclass(frozen=True, slots=True)
class DailyNodeProfile:
    day: date
    low: float
    high: float
    row_width: float
    edges: tuple[float, ...]
    volumes: tuple[float, ...]
    lanes: tuple[FastLane, ...]


@dataclass(frozen=True, slots=True)
class LaneCandidate:
    symbol: str
    profile_day: str
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
    lower_entry_edge: float
    upper_entry_edge: float
    lower_hvn_target: float
    upper_hvn_target: float
    row_width: float
    gap_rows: int
    lvn_rows: int
    contact_volume_ratio: float
    contact_flow: float
    contact_spot_return: float
    traversal_score: float


@dataclass(frozen=True, slots=True)
class ScoredLane:
    candidate: LaneCandidate
    exit_ts: pd.Timestamp
    exit_reason: str
    exit_price: float
    net_return: float
    net_r: float
    mfe: float
    mae: float


def _clusters(mask: np.ndarray) -> list[NodeCluster]:
    clusters: list[NodeCluster] = []
    start: int | None = None
    for index, value in enumerate(mask.tolist() + [False]):
        if value and start is None:
            start = index
        elif not value and start is not None:
            clusters.append(NodeCluster(start, index - 1))
            start = None
    return clusters


def calculate_profile(frame: pd.DataFrame, day: date) -> DailyNodeProfile | None:
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
    positive = profile[profile > 0.0]
    if len(positive) < PROFILE_ROWS // 2:
        return None
    hvn_threshold = float(np.quantile(positive, HVN_QUANTILE))
    lvn_threshold = float(np.quantile(positive, LVN_QUANTILE))
    median_volume = float(np.median(positive))
    hvn_clusters = [cluster for cluster in _clusters(profile >= hvn_threshold) if cluster.rows >= MIN_HVN_ROWS]
    lvn_clusters = [
        cluster
        for cluster in _clusters((profile > 0.0) & (profile <= lvn_threshold))
        if MIN_LVN_ROWS <= cluster.rows <= MAX_LVN_ROWS
    ]
    lanes: list[FastLane] = []
    for lvn in lvn_clusters:
        lower_candidates = [cluster for cluster in hvn_clusters if cluster.end < lvn.start]
        upper_candidates = [cluster for cluster in hvn_clusters if cluster.start > lvn.end]
        if not lower_candidates or not upper_candidates:
            continue
        lower = max(lower_candidates, key=lambda cluster: cluster.end)
        upper = min(upper_candidates, key=lambda cluster: cluster.start)
        gap_start = lower.end + 1
        gap_end = upper.start - 1
        gap_rows = gap_end - gap_start + 1
        if gap_rows < 1 or gap_rows > MAX_GAP_ROWS:
            continue
        gap_volume = profile[gap_start : gap_end + 1]
        if gap_volume.size == 0 or float(gap_volume.max()) > median_volume:
            continue
        if not (gap_start <= lvn.start <= lvn.end <= gap_end):
            continue
        lanes.append(
            FastLane(
                profile_day=day,
                lower_hvn=lower,
                upper_hvn=upper,
                gap_start=gap_start,
                gap_end=gap_end,
                lvn_start=lvn.start,
                lvn_end=lvn.end,
                lower_hvn_target=float(edges[lower.end + 1]),
                lower_entry_edge=float(edges[gap_start]),
                upper_entry_edge=float(edges[gap_end + 1]),
                upper_hvn_target=float(edges[upper.start]),
                row_width=row_width,
                gap_rows=gap_rows,
                lvn_rows=lvn.rows,
                gap_volume_ratio_to_median=float(gap_volume.mean() / max(median_volume, 1e-12)),
            ),
        )
    if not lanes:
        return DailyNodeProfile(
            day=day,
            low=low,
            high=high,
            row_width=row_width,
            edges=tuple(float(value) for value in edges),
            volumes=tuple(float(value) for value in profile),
            lanes=(),
        )
    # Duplicate troughs can map to the same HVN gap.  Keep the thinnest trough.
    unique: dict[tuple[int, int], FastLane] = {}
    for lane in lanes:
        key = (lane.gap_start, lane.gap_end)
        incumbent = unique.get(key)
        if incumbent is None or lane.gap_volume_ratio_to_median < incumbent.gap_volume_ratio_to_median:
            unique[key] = lane
    return DailyNodeProfile(
        day=day,
        low=low,
        high=high,
        row_width=row_width,
        edges=tuple(float(value) for value in edges),
        volumes=tuple(float(value) for value in profile),
        lanes=tuple(unique[key] for key in sorted(unique)),
    )


def build_profiles(panel: pd.DataFrame) -> dict[date, DailyNodeProfile]:
    timestamps = pd.to_datetime(panel["minute"], utc=True)
    profiles: dict[date, DailyNodeProfile] = {}
    for day, group in panel.groupby(timestamps.dt.date, sort=True):
        profile = calculate_profile(group, day)
        if profile is not None:
            profiles[day] = profile
    return profiles


def _day_frame(panel: pd.DataFrame, day: date) -> pd.DataFrame:
    start = pd.Timestamp(day, tz="UTC").as_unit("ns")
    expected = pd.date_range(start, periods=1_440, freq="min", tz="UTC").as_unit("ns")
    frame = panel.reindex(expected)
    if frame["perp_close"].isna().any() or frame["spot_close"].isna().any():
        return pd.DataFrame()
    return frame


def detect_lane_candidate(
    *,
    symbol: str,
    frame: pd.DataFrame,
    lane: FastLane,
) -> LaneCandidate | None:
    if frame.empty:
        return None
    close = pd.to_numeric(frame["perp_close"], errors="coerce")
    prior_volume = (
        pd.to_numeric(frame["perp_quote_volume"], errors="coerce")
        .rolling(ENTRY_VOLUME_LOOKBACK, min_periods=ENTRY_VOLUME_LOOKBACK)
        .mean()
        .shift(1)
    )
    previous_close = close.shift(1)
    contact_position: int | None = None
    side = 0
    for position in range(ENTRY_VOLUME_LOOKBACK, len(frame)):
        row = frame.iloc[position]
        prev = float(previous_close.iloc[position])
        high = float(row["perp_high"])
        low = float(row["perp_low"])
        current_close = float(row["perp_close"])
        long_contact = prev < lane.lower_entry_edge and high >= lane.lower_entry_edge
        short_contact = prev > lane.upper_entry_edge and low <= lane.upper_entry_edge
        if not long_contact and not short_contact:
            continue
        # First physical contact consumes the fresh lane whether accepted or rejected.
        candidate_side = 1 if long_contact else -1
        inside = (
            lane.lower_entry_edge < current_close < lane.upper_hvn_target
            if candidate_side > 0
            else lane.lower_hvn_target < current_close < lane.upper_entry_edge
        )
        volume_mean = float(prior_volume.iloc[position])
        volume = float(row["perp_quote_volume"])
        volume_ratio = volume / max(volume_mean, 1e-12)
        flow = float(row["perp_flow"])
        spot = float(row["spot_ret_1m"])
        body = candidate_side * (current_close - float(row["perp_open"]))
        if not (
            inside
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

    edge = lane.lower_entry_edge if side > 0 else lane.upper_entry_edge
    target = lane.upper_hvn_target if side > 0 else lane.lower_hvn_target
    retest_position: int | None = None
    retest_row: pd.Series | None = None
    search_end = min(contact_position + 1 + RETEST_SEARCH_MINUTES, len(frame) - RESUMPTION_SEARCH_MINUTES)
    for position in range(contact_position + 1, search_end):
        row = frame.iloc[position]
        high = float(row["perp_high"])
        low = float(row["perp_low"])
        current_close = float(row["perp_close"])
        if (side > 0 and high >= target) or (side < 0 and low <= target):
            return None
        outside = current_close <= edge if side > 0 else current_close >= edge
        if outside:
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
        min(retest_position + 1 + RESUMPTION_SEARCH_MINUTES, len(frame)),
    ):
        row = frame.iloc[position]
        high = float(row["perp_high"])
        low = float(row["perp_low"])
        current_close = float(row["perp_close"])
        if (side > 0 and low <= retest_extreme) or (side < 0 and high >= retest_extreme):
            return None
        if (side > 0 and high >= target) or (side < 0 and low <= target):
            return None
        resumed = current_close > retest_break if side > 0 else current_close < retest_break
        body = side * (current_close - float(row["perp_open"]))
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
        entry = current_close
        stop = (
            min(edge - lane.row_width, retest_extreme - lane.row_width)
            if side > 0
            else max(edge + lane.row_width, retest_extreme + lane.row_width)
        )
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
            * lane.gap_rows
            / max(lane.gap_volume_ratio_to_median, 1e-12)
            * target_net_r
        )
        return LaneCandidate(
            symbol=symbol,
            profile_day=lane.profile_day.isoformat(),
            contact_ts=pd.Timestamp(frame.iloc[contact_position].name),
            retest_ts=pd.Timestamp(retest_row.name),
            entry_ts=pd.Timestamp(row.name),
            side=side,
            entry=entry,
            stop=stop,
            target=target,
            target_source=(
                "OPPOSITE_PRIOR_DAY_HVN_LOWER_BOUNDARY"
                if side > 0
                else "OPPOSITE_PRIOR_DAY_HVN_UPPER_BOUNDARY"
            ),
            planned_loss_rate=planned_loss_rate,
            target_net_r=target_net_r,
            lower_entry_edge=lane.lower_entry_edge,
            upper_entry_edge=lane.upper_entry_edge,
            lower_hvn_target=lane.lower_hvn_target,
            upper_hvn_target=lane.upper_hvn_target,
            row_width=lane.row_width,
            gap_rows=lane.gap_rows,
            lvn_rows=lane.lvn_rows,
            contact_volume_ratio=contact_volume_ratio,
            contact_flow=contact_flow,
            contact_spot_return=contact_spot,
            traversal_score=score,
        )
    return None


def discover(
    panels: dict[str, pd.DataFrame],
    profiles: dict[str, dict[date, DailyNodeProfile]],
) -> tuple[list[LaneCandidate], dict[str, int]]:
    candidates: list[LaneCandidate] = []
    funnel = {
        "profile_days": 0,
        "profile_lanes": 0,
        "next_day_complete_lanes": 0,
        "complete_fast_lane_candidates": 0,
        "global_cluster_representatives": 0,
    }
    for symbol, profile_map in profiles.items():
        panel = panels[symbol]
        for profile_day, profile in profile_map.items():
            funnel["profile_days"] += 1
            funnel["profile_lanes"] += len(profile.lanes)
            next_day = profile_day + timedelta(days=1)
            frame = _day_frame(panel, next_day)
            if frame.empty:
                continue
            for lane in profile.lanes:
                funnel["next_day_complete_lanes"] += 1
                candidate = detect_lane_candidate(
                    symbol=symbol,
                    frame=frame,
                    lane=lane,
                )
                if candidate is not None:
                    funnel["complete_fast_lane_candidates"] += 1
                    candidates.append(candidate)
    selected = collapse_global_clusters(candidates)
    funnel["global_cluster_representatives"] = len(selected)
    return selected, funnel


def collapse_global_clusters(candidates: list[LaneCandidate]) -> list[LaneCandidate]:
    if not candidates:
        return []
    ordered = sorted(candidates, key=lambda item: item.entry_ts)
    clusters: list[list[LaneCandidate]] = []
    current: list[LaneCandidate] = []
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
                item.traversal_score,
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


def score_candidate(candidate: LaneCandidate, panel: pd.DataFrame) -> ScoredLane | None:
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
            exit_reason = "OPPOSITE_HVN_TARGET"
            exit_price = candidate.target
            exit_ts = pd.Timestamp(row.name)
            break
    net_return = (
        candidate.side * (exit_price - candidate.entry) / candidate.entry
        - ROUND_TRIP_COST_RATE
    )
    return ScoredLane(
        candidate=candidate,
        exit_ts=exit_ts,
        exit_reason=exit_reason,
        exit_price=exit_price,
        net_return=net_return,
        net_r=net_return / candidate.planned_loss_rate,
        mfe=mfe,
        mae=mae,
    )


def enforce_one_global_slot(
    candidates: list[LaneCandidate],
    panels: dict[str, pd.DataFrame],
) -> tuple[list[ScoredLane], int]:
    active_until: pd.Timestamp | None = None
    scored: list[ScoredLane] = []
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


def records(scored: list[ScoredLane]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for item in scored:
        hour = item.candidate.entry_ts.tz_convert("UTC").hour
        if hour < 8:
            session = "ASIA_0000_0759_UTC"
        elif hour < 13:
            session = "EUROPE_0800_1259_UTC"
        elif hour < 21:
            session = "NEW_YORK_1300_2059_UTC"
        else:
            session = "LATE_2100_2359_UTC"
        rows.append(
            {
                **asdict(item.candidate),
                "session": session,
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
    profiles = {
        symbol: build_profiles(panel.reset_index(drop=True))
        for symbol, panel in panels.items()
    }
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
        decision = "PROMOTE_LVN_FAST_LANE_TO_NAUTILUS_CONTINUOUS_ACCOUNT"
    elif development_pass:
        decision = "DISCARD_LVN_FAST_LANE_AFTER_UNTOUCHED_2024_FAILURE"
    else:
        decision = "DISCARD_LVN_FAST_LANE_AFTER_2023_DEVELOPMENT_FAILURE"

    output.mkdir(parents=True, exist_ok=True)
    development.to_csv(output / "development_trades.csv", index=False)
    if holdout_opened:
        holdout.to_csv(output / "holdout_trades.csv", index=False)
    result = {
        "schema": "candidate-16-v16-lvn-fast-lane-study-v1",
        "role": "mechanism and geometry study; no fills, account, portfolio, or NAV claim",
        "external_policy": {
            "family": "prior-day Volume Profile HVN-LVN-HVN fast lane",
            "reused_profile_algorithm": "bfolkens/py-market-profile close-price volume binning",
            "node_contract": {
                "profile_rows": PROFILE_ROWS,
                "hvn_quantile": HVN_QUANTILE,
                "minimum_hvn_rows": MIN_HVN_ROWS,
                "lvn_quantile": LVN_QUANTILE,
                "lvn_rows": [MIN_LVN_ROWS, MAX_LVN_ROWS],
                "maximum_complete_gap_rows": MAX_GAP_ROWS,
                "complete_gap_volume_below_profile_median": True,
            },
            "interaction": (
                "fresh next-day first physical contact; volume>=1.5x prior20m, "
                "flow and spot aligned, completed close inside gap"
            ),
            "transition": (
                "first later edge retest closes inside; strictly later flow/spot-aligned "
                "break of defended retest"
            ),
            "objective": "near boundary of opposite prior-day HVN",
        },
        "data": {
            "source": "checksum-verified Binance Vision spot and USD-M 1m monthly klines",
            "symbols": list(SYMBOLS),
            "years": [DEVELOPMENT_YEAR, HOLDOUT_YEAR],
        },
        "scenario_contract": {
            "fresh_lane_first_contact_is_final": True,
            "no_order_on_gap_entry": True,
            "first_retest_is_final": True,
            "no_order_on_retest": True,
            "minimum_target_net_r": MIN_TARGET_NET_R,
            "round_trip_cost_rate": ROUND_TRIP_COST_RATE,
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
