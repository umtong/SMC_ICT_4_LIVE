#!/usr/bin/env python3
"""Enumerate causal first-return arming decisions from departure until first return.

Every semantic departure exists before future return. The strategy may WAIT, arm a
proximal/midpoint first-return limit after any completed commitment bar, or reject the
episode. Future bars only label each immutable arm-time plan. Once filled, exits are
the declared TP or SL; unfilled orders cancel only when the causal opportunity ends.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

import numpy as np
import pandas as pd

import departure_first_return_harvest_fixed as fixed

core = fixed.core
EPS = 1e-12
MAX_ARM_MINUTES = 24


@dataclass(frozen=True, slots=True)
class ArmLabel:
    fill_state: str
    outcome: str
    fill_index: int | None
    fill_time_ns: int | None
    resolution_index: int | None
    resolution_time_ns: int | None
    order_terminal_index: int
    order_terminal_time_ns: int
    entry_wait_minutes: float | None
    holding_minutes: float | None
    actual_entry: float | None
    actual_target_net_r: float | None
    actual_stop_net_r: float | None
    actual_gross_rr: float | None
    net_r: float | None
    mfe_r: float | None
    mae_r: float | None = None


def _sign(side: str) -> float:
    return 1.0 if side == "LONG" else -1.0


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _empty_label(state: str, data: pd.DataFrame, terminal: int) -> ArmLabel:
    return ArmLabel(
        state, "UNFILLED", None, None, None, None, terminal,
        int(data.index[terminal].value), None, None, None, None, None, None,
        None, None, None,
    )


def _copy_label(label: Any) -> ArmLabel:
    values = {name: getattr(label, name, None) for name in ArmLabel.__dataclass_fields__}
    return ArmLabel(**values)


def _arm_metrics(data: pd.DataFrame, candidate: Any, arm: int, entry: float, stop: float) -> dict[str, float]:
    setup = candidate.setup
    departure = int(candidate.departure_index)
    side = str(setup.side)
    sign = _sign(side)
    atr = max(core._atr_price(data, departure), EPS)
    risk = max(abs(float(entry) - float(stop)), EPS)
    segment = data.iloc[departure:arm + 1]
    close = segment.close.to_numpy(float)
    progress = sign * (close[-1] - float(entry))
    travel = float(np.abs(np.diff(close)).sum()) if len(close) > 1 else 0.0
    max_excursion = (
        float(segment.high.max()) - float(entry)
        if side == "LONG" else float(entry) - float(segment.low.min())
    )
    outside = segment.close > float(setup.upper) if side == "LONG" else segment.close < float(setup.lower)
    quote = pd.to_numeric(segment.get("quote_volume", pd.Series(0.0, index=segment.index)), errors="coerce").fillna(0.0)
    if "signed_quote_flow" in segment:
        signed = pd.to_numeric(segment.signed_quote_flow, errors="coerce").fillna(0.0)
    elif "delta_share" in segment:
        signed = pd.to_numeric(segment.delta_share, errors="coerce").fillna(0.0) * quote
    else:
        signed = pd.Series(0.0, index=segment.index)
    prior_quote = pd.to_numeric(
        data.quote_volume.iloc[max(0, departure - 60):departure], errors="coerce"
    ).median() if "quote_volume" in data else float("nan")
    arm_row = data.iloc[arm]
    return {
        "arm_minutes": float(arm - departure),
        "arm_progress_r": progress / risk,
        "arm_progress_atr": progress / atr,
        "arm_max_excursion_r": max_excursion / risk,
        "arm_max_excursion_atr": max_excursion / atr,
        "arm_path_efficiency": sign * (close[-1] - close[0]) / max(travel, EPS),
        "arm_outside_close_count": float(outside.sum()),
        "arm_outside_close_ratio": float(outside.mean()),
        "arm_flow_share_signed": sign * float(signed.sum()) / max(float(quote.sum()), EPS),
        "arm_activity_ratio": float(quote.mean()) / max(_finite(prior_quote, float(quote.mean())), EPS),
        "arm_basis_change_3m_signed_bps": sign * _finite(arm_row.get("basis_change_3m_bps")),
        "arm_mark_basis_change_3m_signed_bps": sign * _finite(arm_row.get("mark_basis_change_3m_bps")),
        "arm_oi_log_change": _finite(arm_row.get("metric_oi_log_change_1")),
        "arm_index_return_5m_signed": sign * _finite(arm_row.get("index_return_5m")),
        "arm_factor_return_5m_signed": sign * _finite(arm_row.get("common_return_5m"), _finite(arm_row.get("factor_return"))),
        "arm_breadth_signed": sign * _finite(arm_row.get("common_breadth"), _finite(arm_row.get("breadth"))),
    }


def _pre_arm_alive(data, candidate, arm, entry, stop, target, tick) -> bool:
    setup, departure, side = candidate.setup, int(candidate.departure_index), str(candidate.setup.side)
    for position in range(departure + 1, arm + 1):
        row = data.iloc[position]
        invalidated = float(row.low) <= stop if side == "LONG" else float(row.high) >= stop
        target_spent = float(row.high) >= target if side == "LONG" else float(row.low) <= target
        traded = float(row.low) <= entry - core.LIMIT_TRADE_THROUGH_TICKS * tick if side == "LONG" else float(row.high) >= entry + core.LIMIT_TRADE_THROUGH_TICKS * tick
        overlaps = float(row.low) <= float(setup.upper) and float(row.high) >= float(setup.lower)
        if invalidated or target_spent or traded or overlaps:
            return False
    return True


def label_from_arm(data, candidate, arm, entry, stop, target, tick) -> ArmLabel:
    setup, side = candidate.setup, str(candidate.setup.side)
    expiry = min(len(data) - 1, core._pending_expiry(candidate, candidate.source))
    if arm >= expiry or not _pre_arm_alive(data, candidate, arm, entry, stop, target, tick):
        return _empty_label("ARM_NOT_AVAILABLE", data, min(max(arm, 0), len(data)-1))
    touch_index = None
    for position in range(arm + 1, expiry + 1):
        row = data.iloc[position]
        invalidated = float(row.low) <= stop if side == "LONG" else float(row.high) >= stop
        target_spent = float(row.high) >= target if side == "LONG" else float(row.low) <= target
        traded = float(row.low) <= entry - core.LIMIT_TRADE_THROUGH_TICKS * tick if side == "LONG" else float(row.high) >= entry + core.LIMIT_TRADE_THROUGH_TICKS * tick
        overlaps = float(row.low) <= float(setup.upper) and float(row.high) >= float(setup.lower)
        if traded:
            if invalidated or target_spent:
                return _copy_label(core._same_bar_stop_label(data, position, arm, entry, stop, target, side, tick))
            return _copy_label(core._resolve_after_fill(data, position, arm, entry, stop, target, side, tick))
        if invalidated:
            return _empty_label("CANCELED_PRE_FILL_INVALIDATED", data, position)
        if target_spent:
            return _empty_label("CANCELED_PRE_FILL_TARGET_SPENT", data, position)
        if touch_index is None and overlaps:
            touch_index = position
        elif touch_index is not None:
            close_away = float(row.close) >= float(setup.upper) if side == "LONG" else float(row.close) <= float(setup.lower)
            if close_away or position - touch_index > core.MAX_RESPONSE_BARS:
                return _empty_label("CANCELED_FIRST_RETURN_PASSED", data, position)
    return _empty_label("EXPIRED_UNFILLED", data, expiry)


def _arm_positions(data, candidate, source, entry, stop, target, tick):
    departure = int(candidate.departure_index)
    expiry = min(len(data) - 1, core._pending_expiry(candidate, source), departure + MAX_ARM_MINUTES)
    side, sign = str(candidate.setup.side), _sign(str(candidate.setup.side))
    best = sign * float(data.iloc[departure].close)
    milestones = {1, 2, 3, 5, 8, 12, 18, 24}
    for arm in range(departure + 1, expiry + 1):
        if not _pre_arm_alive(data, candidate, arm, entry, stop, target, tick):
            break
        value = sign * float(data.iloc[arm].close)
        new_extreme = value > best + tick
        if new_extreme:
            best = value
        if new_extreme or (arm - departure) in milestones:
            yield arm


def generate_symbol(symbol, data, levels, metadata, trading_start):
    tick = core.CONTRACTS[symbol].tick_size
    start_ns = int(pd.Timestamp(trading_start, tz="UTC").value)
    sources = core.direction_sources(levels, metadata, minimum_timeframe=core.MINIMUM_SOURCE_TIMEFRAME)
    records, active_until, seen = [], {"HIGH": -1, "LOW": -1}, set()
    counts = {"semantic_sources": len(sources), "source_interactions": 0, "causal_departures": 0, "arm_states": 0, "plans": 0}
    for source in sources:
        interaction = int(source.first_penetration_index)
        if interaction >= len(data) or int(data.index[interaction].value) < start_ns or interaction <= active_until[source.side]:
            continue
        clock = (interaction, source.side)
        if clock in seen:
            continue
        peers = [level for level in sources if level.side == source.side and int(level.first_penetration_index) == interaction]
        owner = max(peers, key=lambda level: (metadata[level.level_id].semantic_weight, level.timeframe_minutes, level.defense_count, level.strength_ratio))
        seen.add(clock)
        if owner.level_id != source.level_id:
            continue
        counts["source_interactions"] += 1
        candidates = core._departure_candidates(data, owner, tick)
        if not candidates:
            continue
        candidate = candidates[0]
        active_until[source.side] = max(active_until[source.side], candidate.departure_index)
        counts["causal_departures"] += 1
        object.__setattr__(candidate, "source", owner)
        stop = core._causal_stop(data, candidate, owner, tick)
        event_ns = int(data.index[candidate.setup.interaction_index].value)
        episode_id = f"S6:{symbol}:{event_ns}:{core._stable_id(owner.level_id)}"
        for entry_name, entry in core._entry_variants(data, candidate, tick):
            if not (stop < entry if candidate.setup.side == "LONG" else stop > entry):
                continue
            obstacle, route_features = core.v4._first_obstacle(data, levels, metadata, candidate.departure_index, entry, candidate.setup.side, tick)
            if obstacle is None:
                continue
            risk = abs(entry - stop)
            for gross_rr in core.RR_VARIANTS:
                target = entry + _sign(candidate.setup.side) * gross_rr * risk
                route_clear = target <= obstacle.order_price + tick if candidate.setup.side == "LONG" else target >= obstacle.order_price - tick
                if not route_clear:
                    continue
                economics = core._raw_economics(candidate.setup.side, entry, stop, target, tick)
                if economics is None or economics["target_net_r"] <= 0.0:
                    continue
                base_features = core._plan_features(data, levels, metadata, owner, candidate, obstacle, route_features, entry, stop)
                for arm in _arm_positions(data, candidate, owner, entry, stop, target, tick):
                    label = label_from_arm(data, candidate, arm, entry, stop, target, tick)
                    if label.fill_state == "ARM_NOT_AVAILABLE":
                        continue
                    state_id = f"S6STATE:{symbol}:{event_ns}:{candidate.event_meta['narrative_branch']}:{arm}:{core._stable_id(owner.level_id,candidate.setup.setup_kind)}"
                    action_id = f"{episode_id}:{arm}:{entry_name}:{gross_rr:.2f}:{obstacle.kind}"
                    records.append({
                        "action_id": action_id, "state_id": state_id, "episode_id": episode_id,
                        "symbol": symbol, "side": candidate.setup.side, "family": candidate.event_meta["narrative_branch"],
                        "departure_time_ns": int(data.index[candidate.departure_index].value), "order_time_ns": int(data.index[arm].value), "arm_index": int(arm),
                        "entry_geometry": entry_name, "entry": float(entry), "stop": float(stop), "target": float(target), "gross_rr": float(gross_rr),
                        "risk_bps": risk / max(abs(entry), EPS) * 10000.0, "route_kind": obstacle.kind, "route_price": float(obstacle.order_price),
                        "route_rr": abs(float(obstacle.order_price)-entry)/max(risk,EPS), "planned_target_net_r": float(economics["target_net_r"]),
                        **base_features, **_arm_metrics(data, candidate, arm, entry, stop), **asdict(label),
                    })
                    counts["plans"] += 1
    frame = pd.DataFrame(records)
    counts["arm_states"] = int(frame.state_id.nunique()) if not frame.empty else 0
    return frame, counts


core.generate_symbol = generate_symbol

if __name__ == "__main__":
    core.main()
