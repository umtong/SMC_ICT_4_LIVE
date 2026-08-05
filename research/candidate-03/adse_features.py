"""Causal market-state detectors for ADSE-v1."""
from __future__ import annotations

from collections import deque
from hashlib import sha256
from math import isfinite
from statistics import median

from adse_data import FIVE_MINUTES_NS, build_five_minute_bars
from adse_model import (
    AdseConfig,
    FiveMinuteState,
    MinuteBar,
    NS_PER_MINUTE,
    ScenarioSignal,
)


def build_atr(minutes: dict[int, MinuteBar], window: int) -> dict[int, float]:
    if window <= 0: raise ValueError("ATR window must be positive")
    history: deque[float] = deque(maxlen=window); output: dict[int, float] = {}
    previous_close: float | None = None
    for minute_ns, bar in sorted(minutes.items()):
        tr = bar.high - bar.low
        if previous_close is not None:
            tr = max(tr, abs(bar.high - previous_close), abs(bar.low - previous_close))
        history.append(tr)
        if len(history) == window: output[minute_ns] = sum(history) / window
        previous_close = bar.close
    return output


def build_states(
    futures_minutes: dict[int, MinuteBar],
    spot_minutes: dict[int, MinuteBar],
    open_interest: dict[int, float],
) -> list[FiveMinuteState]:
    futures_five = build_five_minute_bars(futures_minutes)
    spot_five = build_five_minute_bars(spot_minutes)
    boundaries = sorted(set(futures_five) & set(spot_five) & set(open_interest))
    if len(boundaries) < 15: raise ValueError("insufficient aligned five-minute history")
    output: list[FiveMinuteState] = []
    previous_boundary: int | None = None; previous_close: float | None = None; previous_oi: float | None = None
    for boundary in boundaries:
        oi = open_interest[boundary]
        if previous_boundary is None or boundary != previous_boundary + FIVE_MINUTES_NS:
            previous_close = None; previous_oi = None
        futures = futures_five[boundary]
        price_change = None if previous_close is None else (futures.close / previous_close - 1.0) * 10_000.0
        oi_change = None if previous_oi is None else (oi / previous_oi - 1.0) * 10_000.0
        output.append(FiveMinuteState(
            boundary, futures, spot_five[boundary], oi, price_change, oi_change,
        ))
        previous_boundary = boundary; previous_close = futures.close; previous_oi = oi
    return output


def build_regime_ratios(
    config: AdseConfig,
    futures_minutes: dict[int, MinuteBar],
    states: list[FiveMinuteState],
) -> dict[int, float]:
    """OI-turnover / price-volatility ratio using only observations before T.

    Numerator: median absolute five-minute OI change over the previous six hours.
    Denominator: median one-minute ATR in basis points over the previous six hours.
    Current OI change and current minute are excluded.
    """
    atr = build_atr(futures_minutes, config.atr_minutes)
    minute_keys = sorted(futures_minutes)
    atr_bps = {
        key: atr[key] / futures_minutes[key].close * 10_000.0
        for key in atr
        if futures_minutes[key].close > 0
    }
    ratios: dict[int, float] = {}
    oi_horizon = config.regime_oi_lookback_states * FIVE_MINUTES_NS
    atr_horizon = config.regime_atr_lookback_minutes * NS_PER_MINUTE

    for index, state in enumerate(states):
        boundary = state.boundary_ns
        oi_values = [
            abs(prior.open_interest_change_bps)
            for prior in states[max(0, index - config.regime_oi_lookback_states):index]
            if prior.open_interest_change_bps is not None
            and boundary - oi_horizon <= prior.boundary_ns < boundary
        ]
        if len(oi_values) < config.regime_oi_min_states: continue
        atr_values = [
            atr_bps[key]
            for key in minute_keys
            if boundary - atr_horizon <= key < boundary and key in atr_bps
        ]
        if len(atr_values) < config.regime_atr_min_minutes: continue
        denominator = median(atr_values)
        if denominator <= 0: continue
        ratio = median(oi_values) / denominator
        if isfinite(ratio): ratios[boundary] = ratio
    return ratios


def detect_signals(
    config: AdseConfig,
    futures_minutes: dict[int, MinuteBar],
    states: list[FiveMinuteState],
    evaluation_start_ns: int,
    evaluation_end_ns: int,
) -> list[ScenarioSignal]:
    config.validate()
    atr = build_atr(futures_minutes, config.atr_minutes)
    ratios = build_regime_ratios(config, futures_minutes, states)
    signals: list[ScenarioSignal] = []

    # Low-to-transitional OI-turnover regime: liquidation propagation.
    for index in range(13, len(states)):
        continuation = states[index]; ignition = states[index - 1]; anchor = states[index - 13]
        confirmation = continuation.boundary_ns
        if not evaluation_start_ns <= confirmation < evaluation_end_ns: continue
        if confirmation - anchor.boundary_ns != 13 * FIVE_MINUTES_NS: continue
        if ignition.boundary_ns + FIVE_MINUTES_NS != confirmation: continue
        ratio = ratios.get(confirmation)
        if ratio is None or ratio >= config.lcpt_regime_ratio_max: continue
        if ignition.futures_return_bps is None or ignition.open_interest_change_bps is None: continue
        if continuation.futures_return_bps is None or continuation.open_interest_change_bps is None: continue
        ignition_return = ignition.futures_return_bps
        if ignition_return == 0: continue
        direction = 1 if ignition_return > 0 else -1
        ignition_oi_drop = -ignition.open_interest_change_bps
        continuation_oi_drop = -continuation.open_interest_change_bps
        continuation_return = direction * continuation.futures_return_bps
        extension = direction * (ignition.futures.close / anchor.futures.close - 1.0) * 10_000.0
        if abs(ignition_return) < config.ignition_price_shock_bps: continue
        if ignition_oi_drop < config.ignition_oi_drop_bps: continue
        if direction * ignition.futures.flow <= config.ignition_futures_flow_min: continue
        if direction * ignition.spot.flow < config.ignition_spot_flow_min: continue
        if continuation_return <= 0: continue
        if direction * continuation.futures.flow <= config.continuation_futures_flow_min: continue
        if direction * continuation.spot.flow < config.continuation_spot_flow_min: continue
        if continuation_oi_drop < config.continuation_oi_drop_bps: continue
        if extension > config.extension_through_ignition_max_bps: continue
        atr_key = confirmation - NS_PER_MINUTE; signal_atr = atr.get(atr_key)
        if signal_atr is None or signal_atr <= 0: continue
        cascade_high = max(ignition.futures.high, continuation.futures.high)
        cascade_low = min(ignition.futures.low, continuation.futures.low)
        stop = (
            cascade_low - config.lcpt_stop_buffer_atr * signal_atr
            if direction > 0
            else cascade_high + config.lcpt_stop_buffer_atr * signal_atr
        )
        scenario_id = "ADSE-L-" + sha256(
            f"{confirmation}|{direction}|{cascade_low:.12g}|{cascade_high:.12g}".encode(),
        ).hexdigest()[:16]
        signals.append(ScenarioSignal(
            scenario_id=scenario_id,
            scenario_kind="LCPT",
            direction=direction,
            hypothesis_time_ns=ignition.boundary_ns,
            confirmation_time_ns=confirmation,
            stop_trigger_price=stop,
            atr=signal_atr,
            regime_ratio=ratio,
            buffer_direction_required=False,
            exit_profile=config.lcpt_exit,
            features={
                "ignition_return_bps": direction * ignition_return,
                "ignition_oi_drop_bps": ignition_oi_drop,
                "continuation_return_bps": continuation_return,
                "continuation_oi_drop_bps": continuation_oi_drop,
                "ignition_futures_flow": direction * ignition.futures.flow,
                "ignition_spot_flow": direction * ignition.spot.flow,
                "continuation_futures_flow": direction * continuation.futures.flow,
                "continuation_spot_flow": direction * continuation.spot.flow,
                "extension_through_ignition_bps": extension,
                "cascade_high": cascade_high,
                "cascade_low": cascade_low,
            },
        ))

    # Transitional-to-high OI-turnover regime: deleveraging drift with pullback/reacceleration.
    trend_states = config.tpr_trend_minutes // 5
    for index in range(trend_states + 1, len(states)):
        resumption = states[index]; pullback = states[index - 1]; anchor = states[index - 1 - trend_states]
        confirmation = resumption.boundary_ns
        if not evaluation_start_ns <= confirmation < evaluation_end_ns: continue
        if confirmation - anchor.boundary_ns != (trend_states + 1) * FIVE_MINUTES_NS: continue
        ratio = ratios.get(confirmation)
        if ratio is None or ratio < config.tpr_regime_ratio_min: continue
        if pullback.futures_return_bps is None or resumption.futures_return_bps is None: continue
        trend = (pullback.futures.close / anchor.futures.close - 1.0) * 10_000.0
        if trend == 0: continue
        direction = 1 if trend > 0 else -1; magnitude = abs(trend)
        if not config.tpr_trend_min_bps <= magnitude <= config.tpr_trend_max_bps: continue
        pullback_return = direction * pullback.futures_return_bps
        resumption_return = direction * resumption.futures_return_bps
        if pullback_return > -config.tpr_pullback_min_bps: continue
        if resumption_return < config.tpr_resumption_min_bps: continue
        if direction * pullback.futures.flow > config.tpr_pullback_futures_flow_max: continue
        if direction * resumption.futures.flow <= config.tpr_resumption_futures_flow_min: continue
        if direction * resumption.spot.flow < config.tpr_resumption_spot_flow_min: continue
        if direction * (resumption.futures.close - pullback.futures.open) <= 0: continue
        atr_key = confirmation - NS_PER_MINUTE; signal_atr = atr.get(atr_key)
        if signal_atr is None or signal_atr <= 0: continue
        stop = (
            pullback.futures.low - config.tpr_stop_buffer_atr * signal_atr
            if direction > 0
            else pullback.futures.high + config.tpr_stop_buffer_atr * signal_atr
        )
        scenario_id = "ADSE-T-" + sha256(
            f"{confirmation}|{direction}|{stop:.12g}|{trend:.12g}".encode(),
        ).hexdigest()[:16]
        signals.append(ScenarioSignal(
            scenario_id=scenario_id,
            scenario_kind="TPR",
            direction=direction,
            hypothesis_time_ns=pullback.boundary_ns,
            confirmation_time_ns=confirmation,
            stop_trigger_price=stop,
            atr=signal_atr,
            regime_ratio=ratio,
            buffer_direction_required=True,
            exit_profile=config.tpr_exit,
            features={
                "trend_return_bps": direction * trend,
                "pullback_return_bps": -pullback_return,
                "resumption_return_bps": resumption_return,
                "pullback_futures_flow": direction * pullback.futures.flow,
                "pullback_spot_flow": direction * pullback.spot.flow,
                "resumption_futures_flow": direction * resumption.futures.flow,
                "resumption_spot_flow": direction * resumption.spot.flow,
                "pullback_high": pullback.futures.high,
                "pullback_low": pullback.futures.low,
                "buffer_reference_price": resumption.futures.close,
            },
        ))

    midpoint = (config.lcpt_regime_ratio_max + config.tpr_regime_ratio_min) / 2.0
    def ordering(signal: ScenarioSignal) -> tuple[int, int, str]:
        affinity = (
            signal.scenario_kind == "LCPT" and signal.regime_ratio < midpoint
        ) or (
            signal.scenario_kind == "TPR" and signal.regime_ratio >= midpoint
        )
        return signal.confirmation_time_ns, 0 if affinity else 1, signal.scenario_kind
    return sorted(signals, key=ordering)
