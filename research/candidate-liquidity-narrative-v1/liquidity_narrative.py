"""Hierarchical liquidity narrative with price-volume event ownership.

The policy is not an OB, FVG, trend-line or fake-out strategy. It models one
causal market story:

    accumulated external liquidity -> acquisition/manipulation
    -> price-volume ownership transfer -> scale-matched external objective
    -> internal-liquidity pullback -> first completed response -> redelivery

Small external pools lying inside an active higher-scale route are treated as
low-resistance objectives and are not faded. OB/FVG overlap is used only to
refine price location. Reversal ownership changes only at the active objective,
or after the active narrative has already invalidated. Future bars are used
only by the offline first-passage labeler and by reporting an already elapsed
narrative resolution when later events are processed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta
import json
import math
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

import hierarchical_liquidity_bpr as base
import hierarchical_liquidity_bpr_v2 as route
from auction_episode_research import (
    ActionSpec,
    CONTRACTS,
    _economics,
    _stable_id,
    _time_ns,
    label_action,
)
from derivatives_dislocation import prepare_market_state
from liquidity_displacement import _add_common_state
from market_data import load_range as load_reference_range
from metrics_state import load_range_metrics


POLICY = (
    "LIQUIDITY_NARRATIVE:HIGHER_SCALE_EXTERNAL_LIQUIDITY_ACQUISITION_WITH_"
    "PRICE_VOLUME_EVENT_THEN_COMPLETED_CONTROL_TRANSFER_OWNS_DIRECTION_TO_A_"
    "SCALE_MATCHED_EXTERNAL_OBJECTIVE;LOWER_SCALE_EXTERNAL_POOLS_INSIDE_THE_"
    "ROUTE_ARE_LOW_RESISTANCE;FRESH_INTERNAL_OB_FVG_LOCATION_PLUS_FIRST_"
    "COMPLETED_RETURN_RESPONSE_OWNS_ENTRY;ACTIVE_OBJECTIVE_OR_INVALIDATION_"
    "MUST_RESOLVE_BEFORE_OPPOSITE_NARRATIVE"
)
ANCHOR_TIMEFRAMES = (60, 240, 720, 1440, 10080)
MAX_SHIFT_MINUTES = 30
MAX_NARRATIVE_MINUTES = {
    60: 12 * 60,
    240: 2 * 24 * 60,
    720: 4 * 24 * 60,
    1440: 8 * 24 * 60,
    10080: 14 * 24 * 60,
}
MAX_HOLD_MINUTES = 360
MIN_INTERNAL_HISTORY = 8
CONTROL_LOOKBACK_MINUTES = 15
ZONE_GROUP_MINUTES = 4
EPS = 1e-12


@dataclass(frozen=True, slots=True)
class AnchorCandidate:
    narrative_id: str
    symbol: str
    side: str
    source: base.LiquidityLevel
    target: base.LiquidityLevel
    setup: base.Setup
    interaction_index: int
    reclaim_index: int
    shift_index: int
    event_extreme: float
    event_mechanism: str
    event_features: dict[str, float]
    target_index: int | None
    invalidation_index: int | None
    end_index: int


@dataclass(frozen=True, slots=True)
class InternalZone:
    zone_id: str
    kind: str
    confirmation_index: int
    lower: float
    upper: float
    gap: base.Gap
    ob_lower: float | None
    ob_upper: float | None
    equilibrium: float
    route_progress: float
    control_price: float


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _sign(side: str) -> float:
    return 1.0 if side == "LONG" else -1.0


def _resample_ohlc(data: pd.DataFrame, minutes: int) -> pd.DataFrame:
    return data[["open", "high", "low", "close"]].resample(
        f"{minutes}min",
        label="right",
        closed="right",
        origin="epoch",
    ).agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()


def _confirmed_structure_state(data: pd.DataFrame, minutes: int, span: int = 2) -> pd.DataFrame:
    """Return causal HH/HL, LL/LH or balanced state on the one-minute index."""
    bars = _resample_ohlc(data, minutes)
    highs = bars.high.to_numpy(float)
    lows = bars.low.to_numpy(float)
    events: list[tuple[pd.Timestamp, str, float]] = []
    if len(bars) >= 2 * span + 1:
        for center in range(span, len(bars) - span):
            observed = center + span
            window_h = highs[center - span : center + span + 1]
            window_l = lows[center - span : center + span + 1]
            if highs[center] == np.nanmax(window_h) and int(np.sum(window_h == highs[center])) == 1:
                events.append((bars.index[observed], "HIGH", float(highs[center])))
            if lows[center] == np.nanmin(window_l) and int(np.sum(window_l == lows[center])) == 1:
                events.append((bars.index[observed], "LOW", float(lows[center])))
    events.sort(key=lambda item: (item[0], item[1]))

    state_events: dict[pd.Timestamp, dict[str, float]] = {}
    recent_highs: list[float] = []
    recent_lows: list[float] = []
    state = 0.0
    for timestamp, kind, price in events:
        if kind == "HIGH":
            recent_highs.append(price)
            recent_highs = recent_highs[-2:]
        else:
            recent_lows.append(price)
            recent_lows = recent_lows[-2:]
        if len(recent_highs) >= 2 and len(recent_lows) >= 2:
            if recent_highs[-1] > recent_highs[-2] and recent_lows[-1] > recent_lows[-2]:
                state = 1.0
            elif recent_highs[-1] < recent_highs[-2] and recent_lows[-1] < recent_lows[-2]:
                state = -1.0
            else:
                state = 0.0
        state_events[timestamp] = {
            "state": state,
            "last_high": recent_highs[-1] if recent_highs else float("nan"),
            "last_low": recent_lows[-1] if recent_lows else float("nan"),
        }

    if not state_events:
        return pd.DataFrame(
            {"state": 0.0, "last_high": np.nan, "last_low": np.nan},
            index=data.index,
        )
    event_frame = pd.DataFrame.from_dict(state_events, orient="index").sort_index()
    return event_frame.reindex(data.index, method="ffill").fillna({"state": 0.0})


def add_structure_state(data: pd.DataFrame) -> pd.DataFrame:
    output = data.copy()
    for minutes in (5, 15, 60):
        state = _confirmed_structure_state(output, minutes)
        output[f"structure_{minutes}m"] = state.state.astype(float)
        output[f"structure_{minutes}m_last_high"] = state.last_high.astype(float)
        output[f"structure_{minutes}m_last_low"] = state.last_low.astype(float)
    return output


def _event_process(
    data: pd.DataFrame,
    source: base.LiquidityLevel,
    setup: base.Setup,
) -> tuple[str | None, dict[str, float]]:
    """Classify the acquisition from contemporaneous price-volume mechanics."""
    side = setup.side
    outward = -_sign(side)
    interaction = setup.interaction_index
    reclaim = setup.reclaim_index
    event = data.iloc[interaction : reclaim + 1]
    if event.empty:
        return None, {}
    quote = float(event.quote_volume.sum())
    signed = float(event.signed_quote.sum())
    outward_delta_share = outward * signed / max(quote, EPS)
    activity_peak = _finite(event.activity_ratio.max(), 0.0)
    activity_median = _finite(event.activity_ratio.median(), 0.0)
    range_peak = _finite(event.range_ratio.max(), 0.0)
    impact_median = _finite(event.impact_per_activity.median(), 0.0)
    source_width = max(source.upper - source.lower, EPS)
    penetration = (
        max(0.0, source.lower - setup.event_extreme)
        if source.side == "LOW"
        else max(0.0, setup.event_extreme - source.upper)
    )
    penetration_widths = penetration / source_width
    row = data.iloc[interaction]
    oi_change = _finite(row.get("metric_oi_log_change_1"), 0.0)
    basis_outward = outward * _finite(row.get("basis_change_3m_bps"), 0.0)
    mark_outward = outward * _finite(row.get("mark_basis_change_3m_bps"), 0.0)

    mechanism: str | None
    if oi_change < 0.0 and (basis_outward > 0.0 or mark_outward > 0.0):
        mechanism = "FORCED_DISLOCATION_SWEEP"
    elif outward_delta_share > 0.0 and activity_peak >= 1.0:
        mechanism = "AGGRESSOR_LIQUIDITY_SWEEP"
    elif activity_peak >= 1.0 and penetration_widths >= 1.0 and impact_median <= 1.0:
        mechanism = "HIGH_EFFORT_ABSORBED_STOP_RUN"
    else:
        mechanism = None
    features = {
        "event_outward_delta_share": outward_delta_share,
        "event_activity_peak": activity_peak,
        "event_activity_median": activity_median,
        "event_range_peak": range_peak,
        "event_impact_per_activity_median": impact_median,
        "event_penetration_widths": penetration_widths,
        "event_oi_change_1": oi_change,
        "event_basis_change_3m_outward": basis_outward,
        "event_mark_basis_change_3m_outward": mark_outward,
    }
    return mechanism, features


def _control_transfer_index(data: pd.DataFrame, setup: base.Setup, tick: float) -> int | None:
    """Require the event to become a completed ownership transfer, not just reclaim."""
    sign = _sign(setup.side)
    end = min(len(data), setup.confirmation_index + MAX_SHIFT_MINUTES + 1)
    for index in range(max(setup.confirmation_index, setup.reclaim_index + 1), end):
        row = data.iloc[index]
        crossed = (
            float(row.close) > setup.pre_event_control + tick
            if setup.side == "LONG"
            else float(row.close) < setup.pre_event_control - tick
        )
        directional_body = sign * float(row.close - row.open) > 0.0
        five_state = sign * _finite(row.get("structure_5m"), 0.0)
        fifteen_state = sign * _finite(row.get("structure_15m"), 0.0)
        if crossed and directional_body and five_state >= 0.0 and fifteen_state >= 0.0:
            return index
    return None


def _first_hit_index(
    data: pd.DataFrame,
    start: int,
    side: str,
    target: float,
    invalidation: float,
    hard_end: int,
) -> tuple[int | None, int | None, int]:
    target_index: int | None = None
    invalidation_index: int | None = None
    for index in range(start + 1, min(len(data), hard_end + 1)):
        row = data.iloc[index]
        if side == "LONG":
            target_hit = float(row.high) >= target
            invalidated = float(row.low) <= invalidation
        else:
            target_hit = float(row.low) <= target
            invalidated = float(row.high) >= invalidation
        if target_hit:
            target_index = index
        if invalidated:
            invalidation_index = index
        if target_hit or invalidated:
            return target_index, invalidation_index, index
    return target_index, invalidation_index, min(len(data) - 1, hard_end)


def _build_anchor_candidates(
    symbol: str,
    data: pd.DataFrame,
    levels: Sequence[base.LiquidityLevel],
    tick: float,
    trading_start: date,
) -> tuple[list[AnchorCandidate], dict[str, int]]:
    start_ns = int(pd.Timestamp(trading_start, tz="UTC").value)
    candidates: list[AnchorCandidate] = []
    counts = {
        "source_events": 0,
        "manipulations": 0,
        "price_volume_events": 0,
        "setups": 0,
        "control_transfers": 0,
        "targets": 0,
    }
    for source in levels:
        if source.timeframe_minutes not in ANCHOR_TIMEFRAMES:
            continue
        interaction = source.first_penetration_index
        if interaction is None or interaction >= len(data) or _time_ns(data.index, interaction) < start_ns:
            continue
        counts["source_events"] += 1
        manipulation = base._detect_manipulation(data, interaction, source)
        if manipulation is None:
            continue
        counts["manipulations"] += 1
        reclaim, extreme = manipulation
        setup = base._detect_setup(data, interaction, reclaim, extreme, source, tick)
        if setup is None:
            continue
        counts["setups"] += 1
        mechanism, event_features = _event_process(data, source, setup)
        if mechanism is None:
            continue
        counts["price_volume_events"] += 1
        shift = _control_transfer_index(data, setup, tick)
        if shift is None:
            continue
        counts["control_transfers"] += 1
        entry_reference = float(data.iloc[shift].close)
        target = base._target_level(levels, source, setup.side, shift, entry_reference)
        if target is None:
            continue
        counts["targets"] += 1
        max_minutes = MAX_NARRATIVE_MINUTES.get(source.timeframe_minutes, 12 * 60)
        hard_end = min(len(data) - 1, shift + max_minutes)
        target_index, invalidation_index, end_index = _first_hit_index(
            data,
            shift,
            setup.side,
            target.price,
            setup.event_extreme,
            hard_end,
        )
        narrative_id = (
            f"LN:{symbol}:{_time_ns(data.index, shift)}:"
            f"{_stable_id(source.level_id, target.level_id, setup.side)}"
        )
        candidates.append(
            AnchorCandidate(
                narrative_id=narrative_id,
                symbol=symbol,
                side=setup.side,
                source=source,
                target=target,
                setup=setup,
                interaction_index=interaction,
                reclaim_index=reclaim,
                shift_index=shift,
                event_extreme=setup.event_extreme,
                event_mechanism=mechanism,
                event_features=event_features,
                target_index=target_index,
                invalidation_index=invalidation_index,
                end_index=end_index,
            )
        )
    candidates.sort(key=lambda item: (item.shift_index, -item.source.timeframe_minutes, item.narrative_id))
    return candidates, counts


def _source_is_active_objective(candidate: AnchorCandidate, active: AnchorCandidate) -> bool:
    if candidate.source.level_id == active.target.level_id:
        return True
    overlap = candidate.source.lower <= active.target.upper and candidate.source.upper >= active.target.lower
    return overlap and candidate.source.timeframe_minutes >= active.source.timeframe_minutes


def _select_narratives(candidates: Sequence[AnchorCandidate]) -> tuple[list[AnchorCandidate], dict[str, int]]:
    """Apply one directional story per symbol without consulting later outcomes."""
    accepted: list[AnchorCandidate] = []
    active: AnchorCandidate | None = None
    stats = {
        "accepted": 0,
        "same_direction_subordinate": 0,
        "counterflow_inside_route": 0,
        "stronger_same_direction_refresh": 0,
    }
    for candidate in candidates:
        if active is not None and active.end_index < candidate.shift_index:
            active = None
        if active is None:
            accepted.append(candidate)
            active = candidate
            stats["accepted"] += 1
            continue
        if candidate.side == active.side:
            if candidate.source.timeframe_minutes > active.source.timeframe_minutes:
                accepted.append(candidate)
                active = candidate
                stats["accepted"] += 1
                stats["stronger_same_direction_refresh"] += 1
            else:
                stats["same_direction_subordinate"] += 1
            continue
        if _source_is_active_objective(candidate, active):
            accepted.append(candidate)
            active = candidate
            stats["accepted"] += 1
        else:
            stats["counterflow_inside_route"] += 1
    return accepted, stats


def _last_opposite_body(
    data: pd.DataFrame,
    index: int,
    side: str,
    lookback: int = 6,
) -> tuple[float, float] | None:
    sign = _sign(side)
    for position in range(index - 1, max(-1, index - lookback - 1), -1):
        if position < 0:
            break
        row = data.iloc[position]
        if sign * float(row.close - row.open) < 0.0:
            return min(float(row.open), float(row.close)), max(float(row.open), float(row.close))
    return None


def _internal_zones(data: pd.DataFrame, anchor: AnchorCandidate, tick: float) -> list[InternalZone]:
    side = anchor.side
    sign = _sign(side)
    zones: list[InternalZone] = []
    group: list[InternalZone] = []
    route_slice = data.iloc[anchor.shift_index : anchor.end_index + 1]
    if route_slice.empty:
        return []

    def flush_group() -> None:
        nonlocal group
        if not group:
            return
        group.sort(
            key=lambda item: (
                item.kind != "OB_FVG",
                -item.gap.middle_body_ratio,
                -item.gap.middle_range_ratio,
                item.confirmation_index,
            )
        )
        zones.append(group[0])
        group = []

    last_group_index: int | None = None
    for index in range(max(anchor.shift_index + 2, 2), anchor.end_index + 1):
        gap = base._gap_at(data, index, tick)
        if gap is None or gap.side != side:
            continue
        history_start = max(anchor.shift_index, index - CONTROL_LOOKBACK_MINUTES)
        prior = data.iloc[history_start : index - 1]
        if len(prior) < MIN_INTERNAL_HISTORY:
            continue
        control = float(prior.high.max()) if side == "LONG" else float(prior.low.min())
        close = float(data.iloc[index].close)
        control_broken = close > control + tick if side == "LONG" else close < control - tick
        if not control_broken:
            continue
        route_frame = data.iloc[anchor.shift_index : index + 1]
        route_extreme = float(route_frame.high.max()) if side == "LONG" else float(route_frame.low.min())
        equilibrium = 0.5 * (anchor.event_extreme + route_extreme)
        gap_mid = 0.5 * (gap.lower + gap.upper)
        in_value = gap_mid <= equilibrium + tick if side == "LONG" else gap_mid >= equilibrium - tick
        if not in_value:
            continue
        if sign * _finite(data.iloc[index].get("structure_15m"), 0.0) < 0.0:
            continue
        body = _last_opposite_body(data, index, side)
        lower, upper = gap.lower, gap.upper
        kind = "FVG"
        ob_lower: float | None = None
        ob_upper: float | None = None
        if body is not None:
            ob_lower, ob_upper = body
            overlap_lower = max(gap.lower, ob_lower)
            overlap_upper = min(gap.upper, ob_upper)
            if overlap_upper > overlap_lower + tick:
                lower, upper = overlap_lower, overlap_upper
                kind = "OB_FVG"
        route_total = abs(anchor.target.price - anchor.event_extreme)
        route_progress = sign * (close - anchor.event_extreme) / max(route_total, EPS)
        zone = InternalZone(
            zone_id=(
                f"{anchor.narrative_id}:Z:{index}:"
                f"{_stable_id(kind, lower, upper)}"
            ),
            kind=kind,
            confirmation_index=index,
            lower=float(lower),
            upper=float(upper),
            gap=gap,
            ob_lower=ob_lower,
            ob_upper=ob_upper,
            equilibrium=equilibrium,
            route_progress=route_progress,
            control_price=control,
        )
        if last_group_index is None or index - last_group_index <= ZONE_GROUP_MINUTES:
            group.append(zone)
        else:
            flush_group()
            group.append(zone)
        last_group_index = index
    flush_group()
    return zones


def _action_features(
    data: pd.DataFrame,
    anchor: AnchorCandidate,
    emission_index: int,
    entry: float,
    stop: float,
    target: float,
    response: dict[str, Any],
    action_family: str,
    zone: InternalZone | None,
    economics: dict[str, float],
) -> dict[str, Any]:
    row = data.iloc[emission_index]
    sign = _sign(anchor.side)
    features: dict[str, Any] = {
        **economics,
        **anchor.event_features,
        "action_family": action_family,
        "event_mechanism": anchor.event_mechanism,
        "anchor_setup_kind": anchor.setup.setup_kind,
        "response_kind": response["response_kind"],
        "anchor_source_scale_minutes": anchor.source.timeframe_minutes,
        "anchor_source_strength_ratio": anchor.source.strength_ratio,
        "anchor_source_defense_count": anchor.source.defense_count,
        "anchor_target_scale_minutes": anchor.target.timeframe_minutes,
        "anchor_target_strength_ratio": anchor.target.strength_ratio,
        "anchor_target_defense_count": anchor.target.defense_count,
        "anchor_interaction_to_shift_minutes": float(anchor.shift_index - anchor.interaction_index),
        "anchor_reclaim_to_shift_minutes": float(anchor.shift_index - anchor.reclaim_index),
        "structure_5m_signed": sign * _finite(row.get("structure_5m"), 0.0),
        "structure_15m_signed": sign * _finite(row.get("structure_15m"), 0.0),
        "structure_60m_signed": sign * _finite(row.get("structure_60m"), 0.0),
        "decision_delta_share_signed": sign * _finite(row.get("delta_share"), 0.0),
        "decision_activity_ratio": _finite(row.get("activity_ratio"), 0.0),
        "decision_impact_per_activity": _finite(row.get("impact_per_activity"), 0.0),
        "decision_basis_bps_signed": sign * _finite(row.get("basis_bps"), 0.0),
        "decision_index_return_5m_signed": sign * _finite(row.get("index_return_5m"), 0.0),
        "decision_futures_return_5m_signed": sign * _finite(row.get("futures_return_5m"), 0.0),
        "entry_to_target_route_fraction": abs(entry - anchor.event_extreme) / max(abs(anchor.target.price - anchor.event_extreme), EPS),
        "local_stop_to_anchor_extreme_ratio": abs(stop - entry) / max(abs(anchor.event_extreme - entry), EPS),
        **{
            key: value
            for key, value in response.items()
            if key not in {"departure_index", "touch_index", "response_index", "retest_extreme", "response_kind"}
        },
        "diagnostic_event_time_ns": _time_ns(data.index, anchor.interaction_index),
        "diagnostic_reclaim_time_ns": _time_ns(data.index, anchor.reclaim_index),
        "diagnostic_shift_time_ns": _time_ns(data.index, anchor.shift_index),
        "diagnostic_first_return_time_ns": _time_ns(data.index, int(response["touch_index"])),
        "diagnostic_response_time_ns": _time_ns(data.index, emission_index),
        "diagnostic_event_extreme": anchor.event_extreme,
        "diagnostic_retest_extreme": float(response["retest_extreme"]),
        "diagnostic_zone_lower": zone.lower if zone is not None else anchor.setup.lower,
        "diagnostic_zone_upper": zone.upper if zone is not None else anchor.setup.upper,
        "diagnostic_target_level_id": anchor.target.level_id,
    }
    if zone is not None:
        features.update(
            {
                "zone_kind": zone.kind,
                "zone_confirmation_index": zone.confirmation_index,
                "zone_width_bps": (zone.upper - zone.lower) / max(abs(entry), EPS) * 10_000.0,
                "zone_equilibrium_distance_bps_signed": sign * (zone.equilibrium - 0.5 * (zone.lower + zone.upper)) / max(abs(entry), EPS) * 10_000.0,
                "zone_route_progress": zone.route_progress,
                "zone_control_distance_bps": abs(zone.control_price - 0.5 * (zone.lower + zone.upper)) / max(abs(entry), EPS) * 10_000.0,
                "zone_ob_overlap": float(zone.kind == "OB_FVG"),
                "zone_gap_body_ratio": zone.gap.middle_body_ratio,
                "zone_gap_range_ratio": zone.gap.middle_range_ratio,
                "zone_gap_activity_ratio": zone.gap.middle_activity_ratio,
                "zone_gap_delta_signed": zone.gap.middle_delta_signed,
            }
        )
    else:
        features.update(
            {
                "zone_kind": anchor.setup.setup_kind,
                "zone_ob_overlap": 0.0,
                "zone_route_progress": 0.0,
                "zone_gap_body_ratio": anchor.setup.directional_gap.middle_body_ratio,
                "zone_gap_range_ratio": anchor.setup.directional_gap.middle_range_ratio,
                "zone_gap_activity_ratio": anchor.setup.directional_gap.middle_activity_ratio,
                "zone_gap_delta_signed": anchor.setup.directional_gap.middle_delta_signed,
            }
        )
    for minutes in (1, 3, 5, 15, 30, 60):
        features[f"common_return_{minutes}m_signed"] = sign * _finite(row.get(f"common_return_{minutes}m"), 0.0)
        features[f"residual_return_{minutes}m_signed"] = sign * _finite(row.get(f"residual_return_{minutes}m"), 0.0)
        features[f"common_breadth_{minutes}m_signed"] = sign * _finite(row.get(f"common_breadth_{minutes}m"), 0.0)
    for column in row.index:
        if str(column).startswith("metric_"):
            features[str(column)] = _finite(row[column], 0.0)
    return features


def _make_action(
    symbol: str,
    data: pd.DataFrame,
    anchor: AnchorCandidate,
    response: dict[str, Any],
    tick: float,
    action_family: str,
    zone: InternalZone | None,
) -> ActionSpec | None:
    emission_index = int(response["response_index"])
    if emission_index >= anchor.end_index:
        return None
    entry = float(data.iloc[emission_index].close)
    buffer = max(2.0 * tick, 0.05 * _finite(data.iloc[emission_index].get("prior_range_1m"), tick))
    if action_family == "ANCHOR_REVERSAL":
        stop_reference = anchor.event_extreme
    else:
        stop_reference = float(response["retest_extreme"])
    stop = stop_reference - buffer if anchor.side == "LONG" else stop_reference + buffer
    target = anchor.target.price
    if anchor.side == "LONG" and not (stop < entry - tick and target > entry + tick):
        return None
    if anchor.side == "SHORT" and not (stop > entry + tick and target < entry - tick):
        return None
    economics = _economics(
        side=anchor.side,
        entry=entry,
        stop=stop,
        target=target,
        tick_size=tick,
        entry_style="MARKET",
    )
    if not economics or economics["gross_rr"] < 1.0 or economics["target_net_r"] <= 0.0 or economics["stop_net_r"] >= 0.0:
        return None
    zone_identity = zone.zone_id if zone is not None else f"ANCHOR:{anchor.setup.setup_kind}"
    action_id = f"{anchor.narrative_id}:{action_family}:{_stable_id(zone_identity, emission_index)}"
    features = _action_features(
        data,
        anchor,
        emission_index,
        entry,
        stop,
        target,
        response,
        action_family,
        zone,
        economics,
    )
    source = anchor.source
    target_level = anchor.target
    return ActionSpec(
        action_id=action_id,
        episode_id=zone_identity,
        symbol=symbol,
        event_type="LIQUIDITY_NARRATIVE",
        decision_stage=f"{action_family}_{features['zone_kind']}_FIRST_RETURN_RESPONSE",
        side=anchor.side,
        emission_index=emission_index,
        emission_time_ns=_time_ns(data.index, emission_index),
        entry_style="MARKET",
        entry=entry,
        stop=stop,
        target=target,
        entry_expiry_minutes=1,
        source_level_id=source.level_id,
        source_kind=source.source_kind,
        source_timeframe_minutes=source.timeframe_minutes,
        source_span=source.span,
        source_price=source.price,
        source_lower=source.lower,
        source_upper=source.upper,
        source_strength_ratio=source.strength_ratio,
        source_defense_count=source.defense_count,
        source_age_minutes=(
            _time_ns(data.index, emission_index) - source.observed_time_ns
        ) / 60_000_000_000.0,
        objective_id=target_level.level_id,
        objective_kind=target_level.source_kind,
        objective_timeframe_minutes=target_level.timeframe_minutes,
        objective_strength_ratio=target_level.strength_ratio,
        interaction_time_ns=_time_ns(data.index, anchor.interaction_index),
        feature_values=features,
    )


def _generate_anchor_actions(
    symbol: str,
    data: pd.DataFrame,
    anchor: AnchorCandidate,
    tick: float,
) -> list[ActionSpec]:
    actions: list[ActionSpec] = []
    shifted_setup = replace(anchor.setup, confirmation_index=anchor.shift_index)
    response = base._first_return_response(data, shifted_setup, tick)
    if response is not None and int(response["response_index"]) < anchor.end_index:
        action = _make_action(symbol, data, anchor, response, tick, "ANCHOR_REVERSAL", None)
        if action is not None:
            actions.append(action)

    for zone in _internal_zones(data, anchor, tick):
        setup = base.Setup(
            setup_kind=zone.kind,
            side=anchor.side,
            interaction_index=anchor.interaction_index,
            reclaim_index=anchor.reclaim_index,
            event_extreme=anchor.event_extreme,
            confirmation_index=zone.confirmation_index,
            lower=zone.lower,
            upper=zone.upper,
            manipulation_gap=None,
            directional_gap=zone.gap,
            pre_event_control=zone.control_price,
        )
        response = base._first_return_response(data, setup, tick)
        if response is None:
            continue
        response_index = int(response["response_index"])
        if response_index >= anchor.end_index:
            continue
        response_row = data.iloc[response_index]
        if _sign(anchor.side) * _finite(response_row.get("structure_5m"), 0.0) < 0.0:
            continue
        action = _make_action(symbol, data, anchor, response, tick, "NARRATIVE_REDELIVERY", zone)
        if action is not None:
            actions.append(action)
    actions.sort(key=lambda item: (item.emission_index, item.action_id))
    return actions


def _causal_priority(row: pd.Series) -> tuple[Any, ...]:
    mechanism = {
        "FORCED_DISLOCATION_SWEEP": 3,
        "AGGRESSOR_LIQUIDITY_SWEEP": 2,
        "HIGH_EFFORT_ABSORBED_STOP_RUN": 1,
    }.get(str(row.event_mechanism), 0)
    family = 1 if str(row.action_family) == "ANCHOR_REVERSAL" else 0
    confluence = 1 if str(row.zone_kind) == "OB_FVG" else 0
    alignment = int(_finite(row.structure_5m_signed, 0.0) >= 0.0) + int(
        _finite(row.structure_15m_signed, 0.0) >= 0.0
    )
    return (
        int(row.source_timeframe_minutes),
        family,
        confluence,
        mechanism,
        alignment,
        int(row.source_defense_count),
        -_finite(row.post_cost_break_even_probability, 1.0),
        str(row.action_id),
    )


def route_continuous_account(actions: pd.DataFrame, risk_fraction: float = 0.03) -> tuple[pd.DataFrame, dict[str, Any]]:
    if actions.empty:
        return actions.copy(), {
            "closed_trades": 0,
            "ending_nav": 1.0,
            "return": 0.0,
            "win_rate": None,
            "mean_net_r": None,
            "maximum_drawdown": 0.0,
        }
    resolved_names = {
        "TARGET_FIRST",
        "STOP_FIRST",
        "AMBIGUOUS_FILL_TARGET_SAME_MINUTE",
        "AMBIGUOUS_SAME_MINUTE",
    }
    candidates = actions[
        actions.outcome.isin(resolved_names)
        & actions.resolution_time_ns.notna()
        & actions.net_r.notna()
    ].copy()
    candidates = candidates.sort_values(["emission_time_ns", "symbol", "action_id"])
    selected: list[pd.Series] = []
    busy_until = -1
    for emission_time, group in candidates.groupby("emission_time_ns", sort=True):
        if int(emission_time) < busy_until:
            continue
        eligible = [row for _, row in group.iterrows()]
        eligible.sort(key=_causal_priority, reverse=True)
        chosen = eligible[0]
        selected.append(chosen)
        busy_until = int(chosen.resolution_time_ns)
    ledger = pd.DataFrame(selected).reset_index(drop=True) if selected else candidates.iloc[0:0].copy()
    nav = 1.0
    peak = 1.0
    max_drawdown = 0.0
    nav_before: list[float] = []
    nav_after: list[float] = []
    account_returns: list[float] = []
    for _, row in ledger.iterrows():
        nav_before.append(nav)
        trade_return = risk_fraction * float(row.net_r)
        nav = max(0.0, nav * (1.0 + trade_return))
        nav_after.append(nav)
        account_returns.append(trade_return)
        peak = max(peak, nav)
        max_drawdown = max(max_drawdown, 1.0 - nav / max(peak, EPS))
    if not ledger.empty:
        ledger["nav_before"] = nav_before
        ledger["account_return"] = account_returns
        ledger["nav_after"] = nav_after
    summary = {
        "closed_trades": int(len(ledger)),
        "ending_nav": float(nav),
        "return": float(nav - 1.0),
        "wins": int((ledger.outcome == "TARGET_FIRST").sum()) if not ledger.empty else 0,
        "win_rate": float((ledger.outcome == "TARGET_FIRST").mean()) if not ledger.empty else None,
        "mean_net_r": float(ledger.net_r.mean()) if not ledger.empty else None,
        "median_gross_rr": float(ledger.gross_rr.median()) if not ledger.empty else None,
        "median_holding_minutes": float(ledger.holding_minutes.median()) if not ledger.empty else None,
        "maximum_drawdown": float(max_drawdown),
        "risk_fraction": risk_fraction,
    }
    return ledger, summary


def generate_symbol(
    symbol: str,
    data: pd.DataFrame,
    raw: pd.DataFrame,
    levels: Sequence[base.LiquidityLevel],
    trading_start: date,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    tick = CONTRACTS[symbol].tick_size
    candidates, funnel = _build_anchor_candidates(symbol, data, levels, tick, trading_start)
    narratives, routing = _select_narratives(candidates)
    records: list[dict[str, Any]] = []
    for anchor in narratives:
        for action in _generate_anchor_actions(symbol, data, anchor, tick):
            label = label_action(data, action, tick)
            if label.holding_minutes is not None and label.holding_minutes > MAX_HOLD_MINUTES:
                continue
            records.append(
                {
                    **{key: value for key, value in asdict(action).items() if key != "feature_values"},
                    **action.feature_values,
                    **asdict(label),
                    "narrative_id": anchor.narrative_id,
                    "narrative_end_index": anchor.end_index,
                    "narrative_target_index": anchor.target_index,
                    "narrative_invalidation_index": anchor.invalidation_index,
                }
            )
    actions = pd.DataFrame(records)
    if not actions.empty and actions.action_id.duplicated().any():
        raise RuntimeError(f"duplicate liquidity narrative action {symbol}")
    return actions, {
        "symbol": symbol,
        "bars": int(len(data)),
        "levels": int(len(levels)),
        "anchor_funnel": funnel,
        "anchor_routing": routing,
        "narratives": int(len(narratives)),
        "actions": int(len(actions)),
        "outcomes": actions.outcome.value_counts().to_dict() if not actions.empty else {},
    }


def run_research(
    *,
    start: date,
    end: date,
    warmup_days: int,
    symbols: Sequence[str],
    cache: Path,
    output: Path,
) -> dict[str, Any]:
    from data_re1_flow import load_range_flow

    output.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    load_start = start - timedelta(days=warmup_days)
    raw_by_symbol: dict[str, pd.DataFrame] = {}
    prepared: dict[str, pd.DataFrame] = {}
    levels_by_symbol: dict[str, list[base.LiquidityLevel]] = {}
    for symbol in symbols:
        tick = CONTRACTS[symbol].tick_size
        raw = load_range_flow(symbol, load_start, end, cache)
        raw_by_symbol[symbol] = raw
        index_price = load_reference_range("indexPriceKlines", symbol, load_start, end, cache)
        mark_price = load_reference_range("markPriceKlines", symbol, load_start, end, cache)
        metrics = load_range_metrics(symbol, load_start, end, cache)
        data = prepare_market_state(raw, index_price, mark_price, metrics, tick)
        prepared[symbol] = data
        levels_by_symbol[symbol] = route.detect_levels_v2(symbol, data, raw, tick)
    prepared = _add_common_state(prepared)
    prepared = {symbol: add_structure_state(frame) for symbol, frame in prepared.items()}

    frames: list[pd.DataFrame] = []
    by_symbol: dict[str, Any] = {}
    for symbol in symbols:
        actions, symbol_summary = generate_symbol(
            symbol,
            prepared[symbol],
            raw_by_symbol[symbol],
            levels_by_symbol[symbol],
            start,
        )
        by_symbol[symbol] = symbol_summary
        if not actions.empty:
            actions.to_csv(output / f"{symbol}_liquidity_narrative_actions.csv", index=False)
            frames.append(actions)
    combined = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    combined.to_csv(output / "liquidity_narrative_actions.csv", index=False)
    account, account_summary = route_continuous_account(combined)
    account.to_csv(output / "continuous_account_trades.csv", index=False)
    resolved = (
        combined[
            combined.outcome.isin(
                [
                    "TARGET_FIRST",
                    "STOP_FIRST",
                    "AMBIGUOUS_FILL_TARGET_SAME_MINUTE",
                    "AMBIGUOUS_SAME_MINUTE",
                ]
            )
        ]
        if not combined.empty
        else combined
    )
    summary = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "warmup_days": warmup_days,
        "symbols": list(symbols),
        "actions": int(len(combined)),
        "resolved_actions": int(len(resolved)),
        "action_win_rate": float((resolved.outcome == "TARGET_FIRST").mean()) if not resolved.empty else None,
        "action_mean_net_r": float(resolved.net_r.mean()) if not resolved.empty else None,
        "continuous_account": account_summary,
        "by_symbol": by_symbol,
        "policy": POLICY,
        "future_information_in_features": False,
        "future_information_in_labels_only": True,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


__all__ = [
    "POLICY",
    "add_structure_state",
    "generate_symbol",
    "route_continuous_account",
    "run_research",
]
