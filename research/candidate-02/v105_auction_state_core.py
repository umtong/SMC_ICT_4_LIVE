"""Candidate-02 v105: mutually exclusive external-auction continuation/reversal.

This module is only a causal signal-state compiler. NautilusTrader remains the
sole owner of orders, fills, costs, positions, liquidation and account NAV.

Narrative:
    already-known external liquidity -> first breach during meaningful activity
    -> either common spot/perpetual acceptance with a directional displacement
       imbalance inside the breach-to-acceptance impulse (continuation),
    -> or common return inside the old boundary followed by an opposite
       displacement imbalance which breaks the latest internal pivot confirmed
       before the breach (failed-auction reversal).
Ambiguous auctions are no-trade.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from v53_nt_core import CostConfig, RotationSignal
from v104_external_liquidity_core import (
    ActivationValidation,
    ConfirmedSwing,
    DisplacementEvent,
    ExternalLiquidityConfig,
    LiquidityLevel,
    ScenarioBuildResult,
    _cluster_breaches,
    _finite,
    _normalise_index,
    _normalise_timestamp,
    _select_natural_target,
    _session_label,
    _target_candidates,
    _true_range,
    _volatility_regime,
    _wick_breaches,
    build_liquidity_registry,
    build_state as _build_external_state,
    validate_activation,
)

UTC = "UTC"
NS_MINUTE = 60_000_000_000


@dataclass(frozen=True, slots=True)
class AuctionStateConfig(ExternalLiquidityConfig):
    failure_reentry_minutes: int = 6
    mss_lookback_minutes: int = 30
    mss_pivot_radius: int = 2

    def __post_init__(self) -> None:
        ExternalLiquidityConfig.__post_init__(self)
        if not 2 <= self.failure_reentry_minutes <= 15:
            raise ValueError("invalid failed-auction reentry horizon")
        if not 10 <= self.mss_lookback_minutes <= 120:
            raise ValueError("invalid MSS lookback")
        if self.mss_pivot_radius not in {1, 2, 3, 4}:
            raise ValueError("invalid MSS pivot radius")


@dataclass(frozen=True, slots=True)
class AuctionClassification:
    state: str
    reentry_position: int | None
    outside_close_count: int
    spot_acceptance_ratio: float
    basis_expansion_share: float


def build_state(features: pd.DataFrame, config: AuctionStateConfig) -> pd.DataFrame:
    return _build_external_state(features, config)


def _classify_common_auction(
    *,
    x: pd.DataFrame,
    previous: pd.Series,
    event_position: int,
    classification_end: int,
    boundary: float,
    direction: int,
    atr_value: float,
    config: AuctionStateConfig,
) -> AuctionClassification:
    """Classify one breach as accepted, failed, or ambiguous using closed bars.

    Any common spot/perpetual reentry inside the old boundary takes precedence
    over acceptance so the states cannot overlap. Acceptance requires the
    locked multi-close, spot participation and basis-quality contract.
    """
    if direction not in {-1, 1}:
        raise ValueError("direction must be -1 or +1")
    if event_position < 0 or classification_end < event_position or classification_end >= len(x):
        raise ValueError("invalid classification positions")
    segment = x.iloc[event_position : classification_end + 1]
    required = ("raw_close", "spot_close", "perp_spot_log_basis")
    if any(not _finite(row, required) for _, row in segment.iterrows()):
        return AuctionClassification("AMBIGUOUS", None, 0, math.nan, math.nan)

    pre_basis = float(previous["perp_spot_log_basis"])
    spot_boundary = boundary / math.exp(pre_basis)
    reentry_position: int | None = None
    for position in range(event_position, classification_end + 1):
        row = x.iloc[position]
        perp_inside = (
            float(row["raw_close"]) <= boundary
            if direction > 0
            else float(row["raw_close"]) >= boundary
        )
        spot_inside = (
            float(row["spot_close"]) <= spot_boundary
            if direction > 0
            else float(row["spot_close"]) >= spot_boundary
        )
        if perp_inside and spot_inside:
            reentry_position = position
            break
    if reentry_position is not None:
        return AuctionClassification("FAILED_AUCTION", reentry_position, 0, math.nan, math.nan)

    outside = (
        segment["raw_close"] > boundary
        if direction > 0
        else segment["raw_close"] < boundary
    )
    last = segment.iloc[-1]
    final_perp = float(last["raw_close"])
    final_spot = float(last["spot_close"])
    final_basis = float(last["perp_spot_log_basis"])
    final_outside_distance = direction * (final_perp - boundary)
    spot_outside_distance = direction * (final_spot - spot_boundary)
    perp_excess_fraction = max(direction * (final_perp / boundary - 1.0), 1e-12)
    spot_excess_fraction = direction * (final_spot / spot_boundary - 1.0)
    spot_ratio = spot_excess_fraction / perp_excess_fraction
    basis_share = max(direction * (final_basis - pre_basis), 0.0) / perp_excess_fraction
    accepted = (
        int(outside.sum()) >= config.minimum_outside_closes
        and final_outside_distance >= config.minimum_acceptance_atr * atr_value
        and spot_outside_distance > 0.0
        and spot_ratio >= config.minimum_spot_acceptance_ratio
        and basis_share <= config.maximum_basis_expansion_share
    )
    return AuctionClassification(
        "ACCEPTED_AUCTION" if accepted else "AMBIGUOUS",
        None,
        int(outside.sum()),
        float(spot_ratio),
        float(basis_share),
    )


def _find_directional_fvg(
    *,
    x: pd.DataFrame,
    first_position: int,
    last_position: int,
    boundary: float,
    direction: int,
    config: AuctionStateConfig,
    must_break: float | None = None,
) -> DisplacementEvent | None:
    """Find a causal directional displacement/FVG in an inclusive bar range."""
    if direction not in {-1, 1}:
        raise ValueError("direction must be -1 or +1")
    if last_position < first_position:
        return None
    for position in range(max(first_position, 2), min(last_position, len(x) - 1) + 1):
        row = x.iloc[position]
        fields = ("raw_open", "raw_high", "raw_low", "raw_close", "body", "body_threshold", "atr")
        if not _finite(row, fields):
            continue
        atr_value = float(row["atr"])
        if atr_value <= 0.0:
            continue
        body_floor = max(float(row["body_threshold"]), config.minimum_displacement_body_atr * atr_value)
        directional_body = direction * (float(row["raw_close"]) - float(row["raw_open"]))
        if directional_body <= 0.0 or float(row["body"]) < body_floor:
            continue
        if direction > 0 and float(row["raw_close"]) <= boundary:
            continue
        if direction < 0 and float(row["raw_close"]) >= boundary:
            continue
        if must_break is not None:
            broke = (
                float(row["raw_high"]) >= float(must_break)
                if direction > 0
                else float(row["raw_low"]) <= float(must_break)
            )
            if not broke:
                continue
        two_back = x.iloc[position - 2]
        minimum_gap = config.minimum_fvg_atr * atr_value
        if direction > 0:
            fvg_low, fvg_high = float(two_back["raw_high"]), float(row["raw_low"])
        else:
            fvg_low, fvg_high = float(row["raw_high"]), float(two_back["raw_low"])
        if not (math.isfinite(fvg_low) and math.isfinite(fvg_high)):
            continue
        if fvg_high - fvg_low < minimum_gap:
            continue
        return DisplacementEvent(
            position=position,
            fvg_low=fvg_low,
            fvg_high=fvg_high,
            body_atr=float(row["body"]) / max(atr_value, 1e-12),
        )
    return None


def _latest_confirmed_internal_pivot(
    *,
    x: pd.DataFrame,
    event_position: int,
    breakout_direction: int,
    config: AuctionStateConfig,
) -> tuple[float, int] | None:
    """Return the newest opposite-side pivot confirmed strictly before breach.

    An upside liquidity breach needs the latest confirmed swing low for a
    bearish MSS; a downside breach needs the latest confirmed swing high.
    """
    if breakout_direction not in {-1, 1}:
        raise ValueError("breakout_direction must be -1 or +1")
    radius = config.mss_pivot_radius
    first = max(radius, event_position - config.mss_lookback_minutes)
    last = event_position - radius - 1
    if last < first:
        return None
    values = (
        x["raw_low"].to_numpy(dtype=float)
        if breakout_direction > 0
        else x["raw_high"].to_numpy(dtype=float)
    )
    for pivot in range(last, first - 1, -1):
        confirmation = pivot + radius
        if confirmation >= event_position:
            continue
        value = float(values[pivot])
        if not math.isfinite(value):
            continue
        left = values[pivot - radius : pivot]
        right = values[pivot + 1 : pivot + radius + 1]
        if not (np.isfinite(left).all() and np.isfinite(right).all()):
            continue
        is_pivot = (
            value <= float(np.min(left)) and value <= float(np.min(right))
            and (value < float(np.min(left)) or value < float(np.min(right)))
            if breakout_direction > 0
            else value >= float(np.max(left)) and value >= float(np.max(right))
            and (value > float(np.max(left)) or value > float(np.max(right)))
        )
        if is_pivot:
            return value, int(x.index[confirmation].value)
    return None


def _candidate_target(
    *,
    levels: Sequence[LiquidityLevel],
    consumed: set[str],
    decision_ns: int,
    activation_ns: int,
    side: str,
    boundary: float,
    entry: float,
    stop: float,
    path_extreme: float,
    costs: CostConfig,
    config: AuctionStateConfig,
) -> tuple[LiquidityLevel, float, float] | None:
    candidates = _target_candidates(
        levels=levels,
        consumed=consumed,
        decision_ns=decision_ns,
        activation_ns=activation_ns,
        side=side,
        entry=entry,
        path_extreme=path_extreme,
    )
    return _select_natural_target(
        candidates=candidates,
        side=side,
        boundary=boundary,
        entry=entry,
        stop=stop,
        costs=costs,
        config=config,
    )


def build_scenario_result(
    *,
    state: pd.DataFrame,
    raw: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: AuctionStateConfig,
    costs: CostConfig,
) -> ScenarioBuildResult:
    start, end = _normalise_timestamp(evaluation_start), _normalise_timestamp(evaluation_end)
    if end <= start:
        raise ValueError("evaluation end must be after start")
    raw_view = _normalise_index(raw[["open", "high", "low", "close"]])
    x = state.join(
        raw_view.rename(columns={
            "open": "raw_open",
            "high": "raw_high",
            "low": "raw_low",
            "close": "raw_close",
        }),
        how="inner",
    )
    atr = _true_range(raw_view).rolling(
        config.atr_lookback_minutes,
        min_periods=max(30, config.atr_lookback_minutes // 2),
    ).median().shift(1)
    x["atr"] = atr.reindex(x.index)
    x["body"] = (x["raw_close"] - x["raw_open"]).abs()
    x["body_threshold"] = (
        x["body"].rolling(
            config.prior_window_minutes,
            min_periods=config.prior_minimum_minutes,
        ).quantile(config.displacement_body_quantile).shift(1)
    )
    levels = build_liquidity_registry(raw_view, atr=atr, config=config)
    diagnostics: Counter[str] = Counter()
    level_counts = Counter(level.family for level in levels)
    consumed: set[str] = set()
    index_ns = x.index.asi8
    index_ns_set = set(int(value) for value in index_ns)
    start_ns = int(start.value)

    for level in levels:
        if not (level.eligibility_ns < start_ns <= level.expiry_ns):
            continue
        left = int(np.searchsorted(index_ns, level.eligibility_ns, side="right"))
        right = int(np.searchsorted(index_ns, start_ns, side="left"))
        for position in range(left, right):
            row = x.iloc[position]
            atr_value = float(row["atr"])
            if math.isfinite(atr_value) and atr_value > 0.0 and _wick_breaches(
                level.side, level.price, row, atr_value, config.minimum_level_breach_atr
            ):
                consumed.add(level.level_id)
                diagnostics["PRE_EVALUATION_LEVEL_CONSUMED"] += 1
                break

    candidate_signals: list[RotationSignal] = []
    evaluation_positions = [
        int(x.index.get_loc(ts))
        for ts in x.loc[(x.index >= start) & (x.index < end)].index
    ]
    event_fields = (
        "raw_high", "raw_low", "raw_close", "atr",
        "aggressive_total_quote_1m", "turnover_threshold",
        "spot_close", "perp_spot_log_basis",
    )

    for event_position in evaluation_positions:
        if event_position < max(2, config.mss_pivot_radius + 1):
            diagnostics["EVENT_SKIPPED_INSUFFICIENT_HISTORY"] += 1
            continue
        event_ts = pd.Timestamp(x.index[event_position])
        event_ns = int(event_ts.value)
        event = x.iloc[event_position]
        previous = x.iloc[event_position - 1]
        if not _finite(event, event_fields) or not _finite(previous, ("raw_close", "perp_spot_log_basis")):
            diagnostics["EVENT_SKIPPED_NONFINITE"] += 1
            continue
        atr_value = float(event["atr"])
        if atr_value <= 0.0:
            diagnostics["EVENT_SKIPPED_INVALID_ATR"] += 1
            continue
        active = [
            level for level in levels
            if level.level_id not in consumed
            and level.eligibility_ns < event_ns <= level.expiry_ns
        ]
        previous_close = float(previous["raw_close"])
        upper = [
            level for level in active
            if level.side == "HIGH" and previous_close <= level.price
            and float(event["raw_high"]) >= level.price + config.minimum_level_breach_atr * atr_value
        ]
        lower = [
            level for level in active
            if level.side == "LOW" and previous_close >= level.price
            and float(event["raw_low"]) <= level.price - config.minimum_level_breach_atr * atr_value
        ]
        if not upper and not lower:
            diagnostics["EVENT_NO_FIRST_EXTERNAL_BREACH"] += 1
            continue
        consumed.update(level.level_id for level in upper + lower)
        diagnostics["EXTERNAL_LEVELS_CONSUMED"] += len(upper) + len(lower)
        if upper and lower:
            diagnostics["EVENT_BOTH_SIDES_CONSUMED_NO_TRADE"] += 1
            continue
        breakout_direction = 1 if upper else -1
        breached = upper or lower
        cluster = (
            _cluster_breaches(breached, config.level_merge_atr * atr_value)[-1]
            if breakout_direction > 0
            else _cluster_breaches(breached, config.level_merge_atr * atr_value)[0]
        )
        boundary = (
            max(level.price for level in cluster)
            if breakout_direction > 0
            else min(level.price for level in cluster)
        )
        event_extreme = float(event["raw_high"] if breakout_direction > 0 else event["raw_low"])
        event_extension = breakout_direction * (event_extreme - boundary)
        if event_extension > config.maximum_event_extension_atr * atr_value:
            diagnostics["EVENT_OVEREXTENDED_BEFORE_CONFIRMATION"] += 1
            continue
        if float(event["aggressive_total_quote_1m"]) < float(event["turnover_threshold"]):
            diagnostics["EVENT_BELOW_CAUSAL_TURNOVER_REGIME"] += 1
            continue

        classification_end = event_position + config.classification_minutes - 1
        if classification_end >= len(x) or pd.Timestamp(x.index[classification_end]) >= end:
            diagnostics["CLASSIFICATION_UNAVAILABLE"] += 1
            continue
        auction = _classify_common_auction(
            x=x,
            previous=previous,
            event_position=event_position,
            classification_end=classification_end,
            boundary=boundary,
            direction=breakout_direction,
            atr_value=atr_value,
            config=config,
        )
        if auction.state == "AMBIGUOUS":
            diagnostics["AMBIGUOUS_AUCTION_NO_TRADE"] += 1
            continue

        pivot: tuple[float, int] | None = None
        if auction.state == "ACCEPTED_AUCTION":
            trade_direction = breakout_direction
            displacement = _find_directional_fvg(
                x=x,
                first_position=event_position,
                last_position=classification_end,
                boundary=boundary,
                direction=trade_direction,
                config=config,
            )
            decision_position = classification_end
            state_name = "ACCEPTED_AUCTION_CONTINUATION"
            diagnostics["ACCEPTED_AUCTION_CLASSIFIED"] += 1
        else:
            pivot = _latest_confirmed_internal_pivot(
                x=x,
                event_position=event_position,
                breakout_direction=breakout_direction,
                config=config,
            )
            if pivot is None:
                diagnostics["FAILED_AUCTION_NO_PREBREACH_CONFIRMED_PIVOT"] += 1
                continue
            reentry = int(auction.reentry_position) if auction.reentry_position is not None else classification_end
            displacement_end = min(reentry + config.displacement_search_minutes, len(x) - 1)
            trade_direction = -breakout_direction
            displacement = _find_directional_fvg(
                x=x,
                first_position=max(reentry, event_position),
                last_position=displacement_end,
                boundary=boundary,
                direction=trade_direction,
                must_break=pivot[0],
                config=config,
            )
            decision_position = displacement.position if displacement is not None else displacement_end
            state_name = "FAILED_AUCTION_MSS_REVERSAL"
            diagnostics["FAILED_AUCTION_CLASSIFIED"] += 1
        if displacement is None:
            diagnostics[f"{auction.state}_DISPLACEMENT_FVG_MISSING"] += 1
            continue
        decision_position = max(decision_position, displacement.position)
        decision_ts = pd.Timestamp(x.index[decision_position])
        if decision_ts >= end:
            diagnostics["DECISION_OUTSIDE_EVALUATION"] += 1
            continue
        decision = x.iloc[decision_position]
        required_decision = (
            "raw_high", "raw_low", "raw_close", "spot_close",
            "signed_flow_ratio_1m", "spot_signed_flow_ratio_1m", "atr",
        )
        if not _finite(decision, required_decision):
            diagnostics["DECISION_NONFINITE"] += 1
            continue
        decision_atr = float(decision["atr"])
        if decision_atr <= 0.0:
            diagnostics["DECISION_INVALID_ATR"] += 1
            continue
        combined_flow = trade_direction * 0.5 * (
            float(decision["signed_flow_ratio_1m"])
            + float(decision["spot_signed_flow_ratio_1m"])
        )
        if combined_flow < config.minimum_retrace_flow_alignment:
            diagnostics["DECISION_STRONGLY_OPPOSED_BY_COMMON_FLOW"] += 1
            continue

        side = "BUY" if trade_direction > 0 else "SELL"
        entry = float(decision["raw_close"])
        old_range_invalidation = (
            boundary - config.invalidation_inside_atr * decision_atr
            if side == "BUY"
            else boundary + config.invalidation_inside_atr * decision_atr
        )
        if auction.state == "ACCEPTED_AUCTION":
            stop = (
                min(float(decision["raw_low"]) - config.stop_buffer_atr * decision_atr, old_range_invalidation)
                if side == "BUY"
                else max(float(decision["raw_high"]) + config.stop_buffer_atr * decision_atr, old_range_invalidation)
            )
        else:
            path_to_decision = x.iloc[event_position : decision_position + 1]
            stop = (
                min(float(path_to_decision["raw_low"].min()) - config.stop_buffer_atr * decision_atr, old_range_invalidation)
                if side == "BUY"
                else max(float(path_to_decision["raw_high"].max()) + config.stop_buffer_atr * decision_atr, old_range_invalidation)
            )
        geometry_ok = stop < boundary < entry if side == "BUY" else entry < boundary < stop
        if not geometry_ok:
            diagnostics["DECISION_STRUCTURE_GEOMETRY_INVALID"] += 1
            continue

        decision_ns = int(decision_ts.value)
        activation_ns = decision_ns + config.activation_delay_minutes * NS_MINUTE
        activation_ts = pd.Timestamp(activation_ns, unit="ns", tz=UTC)
        if activation_ts >= end or activation_ns not in index_ns_set:
            diagnostics["DELAYED_ACTIVATION_UNAVAILABLE"] += 1
            continue
        path = x.iloc[event_position : decision_position + 1]
        path_extreme = (
            float(path["raw_high"].max()) if side == "BUY" else float(path["raw_low"].min())
        )
        natural = _candidate_target(
            levels=levels,
            consumed=consumed,
            decision_ns=decision_ns,
            activation_ns=activation_ns,
            side=side,
            boundary=boundary,
            entry=entry,
            stop=stop,
            path_extreme=path_extreme,
            costs=costs,
            config=config,
        )
        if natural is None:
            diagnostics["NO_TRADABLE_NEAREST_EXTERNAL_TARGET"] += 1
            continue
        target, cost_after_rr, delivery_fraction = natural
        turnover_ratio = float(event["aggressive_total_quote_1m"]) / max(float(event["turnover_threshold"]), 1e-12)
        score = (
            max(displacement.body_atr, 0.0)
            * max(turnover_ratio, 1.0)
            * max(float(len(cluster)), 1.0)
            * (1.0 + max(combined_flow, 0.0))
            / (1.0 + max(auction.basis_expansion_share, 0.0) if math.isfinite(auction.basis_expansion_share) else 1.0)
        )
        details = {
            "state": state_name,
            "auction_state": auction.state,
            "liquidity_boundary": boundary,
            "liquidity_level_ids": sorted(level.level_id for level in cluster),
            "liquidity_families": sorted({level.family for level in cluster}),
            "liquidity_cluster_size": len(cluster),
            "breakout_direction": "UP" if breakout_direction > 0 else "DOWN",
            "trade_direction": "UP" if trade_direction > 0 else "DOWN",
            "event_close_utc": event_ts.isoformat(),
            "event_extension_atr": event_extension / max(atr_value, 1e-12),
            "classification_close_utc": pd.Timestamp(x.index[classification_end]).isoformat(),
            "outside_close_count": auction.outside_close_count,
            "spot_acceptance_ratio": auction.spot_acceptance_ratio,
            "basis_expansion_share": auction.basis_expansion_share,
            "reentry_close_utc": (
                None if auction.reentry_position is None
                else pd.Timestamp(x.index[auction.reentry_position]).isoformat()
            ),
            "prebreach_mss_pivot": None if pivot is None else pivot[0],
            "prebreach_mss_pivot_confirmation_ns": None if pivot is None else pivot[1],
            "displacement_close_utc": pd.Timestamp(x.index[displacement.position]).isoformat(),
            "displacement_body_atr": displacement.body_atr,
            "fvg_low": displacement.fvg_low,
            "fvg_high": displacement.fvg_high,
            "decision_close_utc": decision_ts.isoformat(),
            "activation_close_utc": activation_ts.isoformat(),
            "activation_delay_minutes": config.activation_delay_minutes,
            "combined_common_flow_alignment": combined_flow,
            "old_range_invalidation": old_range_invalidation,
            "structural_stop": stop,
            "decision_entry_reference": entry,
            "entry_to_decision_path_extreme": path_extreme,
            "minimum_target_cost_after_rr": config.minimum_target_cost_after_rr,
            "maximum_delivery_fraction": config.maximum_delivery_fraction,
            "activation_validation_costs": {
                name: str(getattr(costs, name)) for name in costs.__dataclass_fields__
            },
            "selected_nearest_external_target_id": target.level_id,
            "selected_nearest_external_target_family": target.family,
            "selected_nearest_external_target": target.price,
            "selected_target_eligibility_ns": target.eligibility_ns,
            "selected_target_expiry_ns": target.expiry_ns,
            "selected_target_known_by_decision": target.eligibility_ns <= decision_ns,
            "selected_target_active_at_activation": activation_ns <= target.expiry_ns,
            "selected_target_cost_after_rr_at_decision": cost_after_rr,
            "delivery_fraction_boundary_to_target": delivery_fraction,
            "target_skip_rule": "NEAREST_ONLY_NO_RR_SKIPPING",
            "session_diagnostic_only": _session_label(decision_ts),
            "volatility_regime_diagnostic_only": _volatility_regime(decision_atr, atr, decision_ts),
            "risk_multiplier_from_score": False,
            "causal_interpretation": (
                "the first traversal of already-known external liquidity was classified into one "
                "of two mutually exclusive common-market auction states; continuation required "
                "common acceptance plus same-direction displacement/FVG, while reversal required "
                "common reentry plus opposite displacement/FVG through a pivot confirmed before "
                "the breach; activation remained delayed by one completed minute"
            ),
        }
        candidate_signals.append(
            RotationSignal(
                scenario_id=f"v105-auction-{state_name.lower()}-{activation_ns}",
                observed_time_ns=activation_ns,
                side=side,
                entry_reference=entry,
                stop_price=stop,
                target_price=target.price,
                cost_after_reward_risk=cost_after_rr,
                score=float(score),
                max_hold_minutes=config.maximum_holding_minutes,
                source_feature_open_time_ns=event_ns - NS_MINUTE,
                source_feature_available_time_ns=activation_ns,
                source_max_market_time_ns=decision_ns,
                details=details,
            )
        )
        diagnostics[f"{auction.state}_QUALIFIED_BEFORE_GLOBAL_SCHEDULING"] += 1

    candidate_signals.sort(key=lambda signal: (signal.observed_time_ns, -signal.score, signal.scenario_id))
    selected: list[RotationSignal] = []
    seen_activation: set[int] = set()
    cooldown_until = -1
    for signal in candidate_signals:
        if signal.observed_time_ns in seen_activation:
            diagnostics["GLOBAL_SAME_MINUTE_SIGNAL_SUPPRESSED"] += 1
            continue
        if signal.observed_time_ns <= cooldown_until:
            diagnostics["GLOBAL_COOLDOWN_SIGNAL_SUPPRESSED"] += 1
            continue
        if signal.source_max_market_time_ns >= signal.observed_time_ns:
            raise AssertionError("v105 delayed activation causality failed")
        seen_activation.add(signal.observed_time_ns)
        selected.append(signal)
        cooldown_until = signal.observed_time_ns + config.cooldown_minutes * NS_MINUTE
    diagnostics["SCENARIO_SCHEDULED"] = len(selected)
    return ScenarioBuildResult(
        signals=tuple(selected),
        diagnostics=dict(sorted(diagnostics.items())),
        level_counts=dict(sorted(level_counts.items())),
    )


def build_rotation_signals(
    *,
    state: pd.DataFrame,
    raw: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: AuctionStateConfig,
    costs: CostConfig,
) -> list[RotationSignal]:
    return list(build_scenario_result(
        state=state,
        raw=raw,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
        config=config,
        costs=costs,
    ).signals)


__all__ = [
    "ActivationValidation",
    "AuctionClassification",
    "AuctionStateConfig",
    "ConfirmedSwing",
    "DisplacementEvent",
    "LiquidityLevel",
    "ScenarioBuildResult",
    "_classify_common_auction",
    "_find_directional_fvg",
    "_latest_confirmed_internal_pivot",
    "build_liquidity_registry",
    "build_rotation_signals",
    "build_scenario_result",
    "build_state",
    "validate_activation",
]
