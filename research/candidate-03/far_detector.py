"""Causal detector for aggressive-flow absorption away from equilibrium."""
from __future__ import annotations

from collections import deque
from hashlib import sha256
from math import sqrt
from statistics import median

from far_model import AbsorptionSignal, Direction, FarConfig, FeatureSnapshot, MinuteBar


class FlowAbsorptionDetector:
    """Compute observable facts; do not make entry or exit decisions here."""

    def __init__(self, config: FarConfig) -> None:
        self.config = config
        self._notional_history: deque[float] = deque(maxlen=config.activity_baseline_minutes)
        self._true_ranges: deque[float] = deque(maxlen=config.atr_window_minutes)
        self._weighted_history: deque[tuple[float, float, float]] = deque(
            maxlen=config.equilibrium_window_minutes
        )
        self._sum_volume = 0.0
        self._sum_price_volume = 0.0
        self._sum_price2_volume = 0.0
        self._previous_close: float | None = None
        self._equilibrium_side = 0
        self._excursion_start_minute = -1
        self._excursion_minutes = 0
        self.minutes_seen = 0
        self.qualified_signals = 0

    def observe(self, bar: MinuteBar) -> AbsorptionSignal | None:
        # The current minute is intentionally excluded from the activity
        # baseline: the detector asks whether current activity is exceptional
        # relative to observations available before this minute.
        activity_baseline = (
            median(self._notional_history)
            if len(self._notional_history) >= self.config.activity_min_history_minutes
            else None
        )

        previous_close = self._previous_close
        true_range = bar.high - bar.low
        if previous_close is not None:
            true_range = max(true_range, abs(bar.high - previous_close), abs(bar.low - previous_close))
        self._true_ranges.append(true_range)
        atr = (
            sum(self._true_ranges) / self.config.atr_window_minutes
            if len(self._true_ranges) == self.config.atr_window_minutes
            else None
        )

        typical_price = (bar.high + bar.low + bar.close) / 3.0
        if len(self._weighted_history) == self.config.equilibrium_window_minutes:
            old_volume, old_pv, old_p2v = self._weighted_history[0]
            self._sum_volume -= old_volume
            self._sum_price_volume -= old_pv
            self._sum_price2_volume -= old_p2v
        price_volume = typical_price * bar.volume
        price2_volume = typical_price * typical_price * bar.volume
        self._weighted_history.append((bar.volume, price_volume, price2_volume))
        self._sum_volume += bar.volume
        self._sum_price_volume += price_volume
        self._sum_price2_volume += price2_volume

        equilibrium = sigma = z_score = None
        if (
            len(self._weighted_history) == self.config.equilibrium_window_minutes
            and self._sum_volume > 0
        ):
            equilibrium = self._sum_price_volume / self._sum_volume
            variance = max(
                0.0,
                self._sum_price2_volume / self._sum_volume - equilibrium * equilibrium,
            )
            sigma = sqrt(variance)
            if sigma > 0:
                z_score = (bar.close - equilibrium) / sigma

        side = 0 if z_score is None or z_score == 0 else (1 if z_score > 0 else -1)
        if side == 0:
            self._equilibrium_side = 0
            self._excursion_start_minute = bar.minute_index
            self._excursion_minutes = 0
        elif side == self._equilibrium_side:
            self._excursion_minutes += 1
        else:
            self._equilibrium_side = side
            self._excursion_start_minute = bar.minute_index
            self._excursion_minutes = 1

        flow = bar.signed_notional / bar.notional if bar.notional > 0 else 0.0
        activity_ratio = (
            bar.notional / activity_baseline
            if activity_baseline is not None and activity_baseline > 0
            else None
        )
        return_bps = (bar.close / bar.open - 1.0) * 10_000.0
        close_location = (
            (bar.close - bar.low) / (bar.high - bar.low) if bar.high > bar.low else 0.5
        )
        flow_sign = 1 if flow > 0 else (-1 if flow < 0 else 0)
        directional_progress = flow_sign * return_bps
        rejection_location = 1.0 - close_location if flow_sign > 0 else close_location

        signal: AbsorptionSignal | None = None
        if (
            atr is not None
            and activity_ratio is not None
            and z_score is not None
            and flow_sign != 0
            and abs(flow) >= self.config.flow_imbalance_min
            and activity_ratio >= self.config.activity_ratio_min
            and abs(z_score) >= self.config.equilibrium_z_min
            and flow_sign * z_score > 0
            and directional_progress <= self.config.directional_progress_max_bps
            and rejection_location >= self.config.rejection_location_min
        ):
            snapshot = FeatureSnapshot(
                observed_time_ns=bar.observed_time_ns,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                atr=atr,
                flow_imbalance=flow,
                activity_ratio=activity_ratio,
                equilibrium_price=equilibrium,
                equilibrium_sigma=sigma,
                equilibrium_z=z_score,
                equilibrium_side=side,
                equilibrium_excursion_minutes=self._excursion_minutes,
                equilibrium_excursion_start_minute=self._excursion_start_minute,
                return_bps=return_bps,
                directional_progress_bps=directional_progress,
                close_location=close_location,
                rejection_location=rejection_location,
                aggregate_trade_count=bar.aggregate_trade_count,
                notional=bar.notional,
            )
            scenario_id = "FAR2-" + sha256(
                f"{bar.observed_time_ns}|{flow_sign}|{bar.close:.12g}".encode("utf-8")
            ).hexdigest()[:16]
            signal = AbsorptionSignal(
                scenario_id=scenario_id,
                direction=Direction.SHORT if flow_sign > 0 else Direction.LONG,
                snapshot=snapshot,
            )
            self.qualified_signals += 1

        self._notional_history.append(bar.notional)
        self._previous_close = bar.close
        self.minutes_seen += 1
        return signal


def snapshot_details(signal: AbsorptionSignal) -> dict[str, float | int | str]:
    snapshot = signal.snapshot
    return {
        "direction": signal.direction.value,
        "flow_imbalance": snapshot.flow_imbalance,
        "activity_ratio": snapshot.activity_ratio,
        "equilibrium_price": snapshot.equilibrium_price,
        "equilibrium_sigma": snapshot.equilibrium_sigma,
        "equilibrium_z": snapshot.equilibrium_z,
        "equilibrium_side": snapshot.equilibrium_side,
        "equilibrium_excursion_minutes": snapshot.equilibrium_excursion_minutes,
        "equilibrium_excursion_start_minute": snapshot.equilibrium_excursion_start_minute,
        "return_bps": snapshot.return_bps,
        "directional_progress_bps": snapshot.directional_progress_bps,
        "close_location": snapshot.close_location,
        "rejection_location": snapshot.rejection_location,
        "signal_open": snapshot.open,
        "signal_high": snapshot.high,
        "signal_low": snapshot.low,
        "signal_close": snapshot.close,
        "signal_atr": snapshot.atr,
        "notional": snapshot.notional,
        "aggregate_trade_count": snapshot.aggregate_trade_count,
    }
