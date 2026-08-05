"Causal LCPT detectors: completed-minute facts first, scenario signal second."

from __future__ import annotations

from collections import deque
from hashlib import sha256
from math import copysign, isfinite

from lcpt_data import build_five_minute_bars
from lcpt_model import (
    CascadeSignal,
    FiveMinuteState,
    LcptConfig,
    MinuteBar,
    NS_PER_MINUTE,
)


FIVE_MINUTES_NS = 5 * NS_PER_MINUTE


def build_atr(minutes: dict[int, MinuteBar], window: int) -> dict[int, float]:
    if window <= 0:
        raise ValueError("ATR window must be positive")
    output: dict[int, float] = {}
    history: deque[float] = deque(maxlen=window)
    previous_close: float | None = None

    for minute_ns, bar in sorted(minutes.items()):
        true_range = bar.high - bar.low
        if previous_close is not None:
            true_range = max(
                true_range,
                abs(bar.high - previous_close),
                abs(bar.low - previous_close),
            )
        history.append(true_range)
        if len(history) == window:
            output[minute_ns] = sum(history) / window
        previous_close = bar.close
    return output


def build_states(
    futures_minutes: dict[int, MinuteBar],
    spot_minutes: dict[int, MinuteBar],
    open_interest: dict[int, float],
) -> list[FiveMinuteState]:
    futures_five = build_five_minute_bars(futures_minutes)
    spot_five = build_five_minute_bars(spot_minutes)
    boundaries = sorted(set(futures_five) & set(spot_five))
    if len(boundaries) < 15:
        raise ValueError("insufficient aligned five-minute history")

    states: list[FiveMinuteState] = []
    previous_close: float | None = None
    previous_oi: float | None = None
    previous_boundary: int | None = None

    for boundary in boundaries:
        oi = open_interest.get(boundary)
        if oi is None or oi <= 0:
            # Explicit zero/missing OI snapshots are provider outages, not
            # liquidation events. Reset return continuity across the gap.
            previous_close = None
            previous_oi = None
            previous_boundary = None
            continue
        if previous_boundary is not None and boundary != previous_boundary + FIVE_MINUTES_NS:
            previous_close = None
            previous_oi = None
        futures = futures_five[boundary]
        spot = spot_five[boundary]
        futures_return_bps = (
            0.0 if previous_close is None else (futures.close / previous_close - 1.0) * 10_000.0
        )
        oi_change_bps = (
            0.0 if previous_oi is None else (oi / previous_oi - 1.0) * 10_000.0
        )
        states.append(
            FiveMinuteState(
                boundary_ns=boundary,
                futures=futures,
                spot=spot,
                open_interest=oi,
                futures_return_bps=futures_return_bps,
                open_interest_change_bps=oi_change_bps,
            ),
        )
        previous_close = futures.close
        previous_oi = oi
        previous_boundary = boundary
    return states


def detect_cascade_signals(
    config: LcptConfig,
    futures_minutes: dict[int, MinuteBar],
    states: list[FiveMinuteState],
    evaluation_start_ns: int,
    evaluation_end_ns: int,
) -> list[CascadeSignal]:
    """Detect liquidation-cascade propagation without future-confirmed pivots.

    At confirmation boundary ``T``:
    - ``states[i - 1]`` is ignition over [T-10m, T-5m);
    - ``states[i]`` is continuation over [T-5m, T);
    - the extension filter is the 60-minute move ending at ignition close,
      ``close[i-1] / close[i-13]``. It therefore includes ignition but excludes
      the continuation interval. This is an event-age filter known before
      continuation confirmation, not a post-event trend filter.
    """
    atr = build_atr(futures_minutes, config.atr_minutes)
    signals: list[CascadeSignal] = []

    for index in range(13, len(states)):
        continuation = states[index]
        ignition = states[index - 1]
        extension_anchor = states[index - 13]
        confirmation_ns = continuation.boundary_ns
        if confirmation_ns - extension_anchor.boundary_ns != 13 * FIVE_MINUTES_NS:
            # The 60-minute extension and two-stage cascade require an
            # uninterrupted 65-minute observation chain.
            continue
        if not evaluation_start_ns <= confirmation_ns < evaluation_end_ns:
            continue
        if ignition.boundary_ns + FIVE_MINUTES_NS != confirmation_ns:
            raise ValueError("ignition and continuation are not adjacent")

        ignition_return = ignition.futures_return_bps
        if ignition_return == 0:
            continue
        direction = 1 if ignition_return > 0 else -1

        ignition_oi_drop = -ignition.open_interest_change_bps
        continuation_oi_drop = -continuation.open_interest_change_bps
        extension = (
            ignition.futures.close / extension_anchor.futures.close - 1.0
        ) * 10_000.0
        directed_extension = direction * extension

        if abs(ignition_return) < config.ignition_price_shock_bps:
            continue
        if ignition_oi_drop < config.ignition_oi_drop_bps:
            continue
        if direction * ignition.futures.flow <= config.ignition_futures_flow_min:
            continue
        if direction * ignition.spot.flow < config.ignition_spot_flow_min:
            continue
        if direction * continuation.futures_return_bps <= 0:
            continue
        if direction * continuation.futures.flow <= config.continuation_futures_flow_min:
            continue
        if direction * continuation.spot.flow < config.continuation_spot_flow_min:
            continue
        if continuation_oi_drop < config.continuation_oi_drop_bps:
            continue
        if directed_extension > config.extension_through_ignition_max_bps:
            continue

        atr_minute = confirmation_ns - NS_PER_MINUTE
        signal_atr = atr.get(atr_minute)
        if signal_atr is None or not isfinite(signal_atr) or signal_atr <= 0:
            continue

        cascade_high = max(ignition.futures.high, continuation.futures.high)
        cascade_low = min(ignition.futures.low, continuation.futures.low)
        stop = (
            cascade_low - config.stop_buffer_atr * signal_atr
            if direction > 0
            else cascade_high + config.stop_buffer_atr * signal_atr
        )
        scenario_id = "LCPT-" + sha256(
            (
                f"{confirmation_ns}|{direction}|{cascade_low:.12g}|"
                f"{cascade_high:.12g}|{continuation_oi_drop:.12g}"
            ).encode("utf-8"),
        ).hexdigest()[:16]
        signals.append(
            CascadeSignal(
                scenario_id=scenario_id,
                direction=direction,
                ignition_time_ns=ignition.boundary_ns,
                confirmation_time_ns=confirmation_ns,
                cascade_high=cascade_high,
                cascade_low=cascade_low,
                atr=signal_atr,
                stop_trigger_price=stop,
                ignition_return_bps=ignition_return,
                ignition_oi_drop_bps=ignition_oi_drop,
                continuation_return_bps=continuation.futures_return_bps,
                continuation_oi_drop_bps=continuation_oi_drop,
                ignition_futures_flow=direction * ignition.futures.flow,
                ignition_spot_flow=direction * ignition.spot.flow,
                continuation_futures_flow=direction * continuation.futures.flow,
                continuation_spot_flow=direction * continuation.spot.flow,
                extension_through_ignition_bps=directed_extension,
            ),
        )

    return signals
