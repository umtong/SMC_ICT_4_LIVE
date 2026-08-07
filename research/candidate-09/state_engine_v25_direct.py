"""Candidate 09 v25: spot-led liquidation-dislocation auction reversion.

A five-minute open-interest reduction is not treated as a direction by itself.  The
candidate first asks whether the Binance USD-M perpetual moved materially farther than
the completed Binance spot auction.  That perpetual/spot dislocation is interpreted
as a derivatives-specific forced-flow distortion only when price, aggregate taker flow,
participation and OI agree.  It then waits for the dislocation to contract and for a
completed spot-led reversal, perpetual counterflow and basis contraction before entering
toward the frozen pre-shock source-auction equilibrium.  All observations are completed and available before use.

Exact controls remove OI, remove only the perpetual/spot dislocation admission test, or
remove only the spot-lead component of post-shock reversal confirmation.  No optimizer or parameter search is
present.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, fields
from hashlib import sha256
from math import isfinite
from statistics import median
from typing import Any, Mapping, Sequence

from state_engine_v18_direct import (
    MINUTE_NS,
    DiagnosticEvent,
    EngineConfig as V18EngineConfig,
    EngineResult,
    RiskSizing,
    Signal,
    risk_based_quantity,
)


@dataclass(frozen=True, slots=True)
class FlowBar:
    ts_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    taker_buy_volume: float
    trade_count: int
    spot_open: float | None = None
    spot_high: float | None = None
    spot_low: float | None = None
    spot_close: float | None = None
    metric_observed_ns: int | None = None
    open_interest: float | None = None
    open_interest_value: float | None = None
    metric_taker_ratio: float | None = None
    top_trader_account_ratio: float | None = None
    top_trader_position_ratio: float | None = None
    global_account_ratio: float | None = None

    def __post_init__(self) -> None:
        values = (self.open, self.high, self.low, self.close, self.volume, self.taker_buy_volume)
        if self.ts_ns < 0 or any(not isfinite(value) for value in values):
            raise ValueError("bar contains an invalid timestamp or non-finite value")
        if self.low > min(self.open, self.close, self.high) or self.high < max(self.open, self.close, self.low):
            raise ValueError("bar OHLC values are inconsistent")
        if self.volume < 0.0 or not 0.0 <= self.taker_buy_volume <= self.volume + 1e-9:
            raise ValueError("bar volume is inconsistent")
        if self.trade_count < 0:
            raise ValueError("trade_count must be non-negative")
        spot_values = (self.spot_open, self.spot_high, self.spot_low, self.spot_close)
        if any(value is not None for value in spot_values):
            if any(value is None or not isfinite(value) or value <= 0.0 for value in spot_values):
                raise ValueError("spot OHLC must be complete, finite and positive")
            assert self.spot_open is not None and self.spot_high is not None
            assert self.spot_low is not None and self.spot_close is not None
            if self.spot_low > min(self.spot_open, self.spot_close, self.spot_high):
                raise ValueError("spot OHLC values are inconsistent")
            if self.spot_high < max(self.spot_open, self.spot_close, self.spot_low):
                raise ValueError("spot OHLC values are inconsistent")
        metric_values = (
            self.open_interest,
            self.open_interest_value,
            self.metric_taker_ratio,
            self.top_trader_account_ratio,
            self.top_trader_position_ratio,
            self.global_account_ratio,
        )
        if self.metric_observed_ns is None:
            if any(value is not None for value in metric_values):
                raise ValueError("metric values require metric_observed_ns")
        else:
            if self.metric_observed_ns < 0 or self.metric_observed_ns > self.ts_ns:
                raise ValueError("metric observation must already be available to the bar")
            if self.open_interest is None or self.open_interest <= 0.0:
                raise ValueError("metric observation requires positive open interest")
            for value in metric_values:
                if value is not None and (not isfinite(value) or value < 0.0):
                    raise ValueError("metric value is invalid")
            if self.metric_taker_ratio is not None and self.metric_taker_ratio <= 0.0:
                raise ValueError("metric taker ratio must be positive when present")

    @property
    def signed_flow(self) -> float:
        return 2.0 * self.taker_buy_volume - self.volume

    @property
    def flow_imbalance(self) -> float:
        return self.signed_flow / self.volume if self.volume > 0.0 else 0.0

    @property
    def has_spot(self) -> bool:
        return self.spot_close is not None

    @property
    def basis_fraction(self) -> float | None:
        if self.spot_close is None or self.spot_close <= 0.0:
            return None
        return self.close / self.spot_close - 1.0


@dataclass(frozen=True, slots=True)
class EngineConfig(V18EngineConfig):
    metrics_interval_minutes: int = 5
    oi_change_lookback_samples: int = 24
    minimum_oi_change_multiple: float = 2.0
    minimum_absolute_oi_change_fraction: float = 0.0005
    minimum_impulse_atr: float = 0.50
    basis_lookback_bars: int = 240
    gap_lookback_samples: int = 48
    basis_dislocation_multiple: float = 3.0
    return_gap_multiple: float = 2.0
    minimum_absolute_basis_fraction: float = 0.00015
    basis_reclaim_fraction: float = 0.25
    confirmation_timeout_bars: int = 4
    require_oi: bool = True
    require_spot_gap: bool = True
    require_spot_lead: bool = True

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any], *, ablation: str = "baseline") -> "EngineConfig":
        allowed = {"baseline", "no-oi", "no-spot-gap", "no-spot-lead"}
        if ablation not in allowed:
            raise ValueError(f"unknown ablation: {ablation}")
        base = V18EngineConfig.from_mapping(payload, ablation="baseline")
        inherited = {item.name: getattr(base, item.name) for item in fields(V18EngineConfig)}
        positioning = payload["positioning"]
        dislocation = payload["dislocation"]
        return cls(
            **inherited,
            metrics_interval_minutes=int(positioning["metrics_interval_minutes"]),
            oi_change_lookback_samples=int(positioning["oi_change_lookback_samples"]),
            minimum_oi_change_multiple=float(positioning["minimum_oi_change_multiple"]),
            minimum_absolute_oi_change_fraction=float(positioning["minimum_absolute_oi_change_fraction"]),
            minimum_impulse_atr=float(positioning["minimum_impulse_atr"]),
            basis_lookback_bars=int(dislocation["basis_lookback_bars"]),
            gap_lookback_samples=int(dislocation["gap_lookback_samples"]),
            basis_dislocation_multiple=float(dislocation["basis_dislocation_multiple"]),
            return_gap_multiple=float(dislocation["return_gap_multiple"]),
            minimum_absolute_basis_fraction=float(dislocation["minimum_absolute_basis_fraction"]),
            basis_reclaim_fraction=float(dislocation["basis_reclaim_fraction"]),
            confirmation_timeout_bars=int(dislocation["confirmation_timeout_bars"]),
            require_oi=ablation != "no-oi",
            require_spot_gap=ablation != "no-spot-gap",
            require_spot_lead=ablation != "no-spot-lead",
        )


@dataclass(frozen=True, slots=True)
class SourceAuction:
    start_ns: int
    end_ns: int
    high: float
    low: float
    equilibrium: float
    width: float


@dataclass(slots=True)
class _PendingDislocation:
    scenario_id: str
    direction: str
    detected_index: int
    metric_observed_ns: int
    metric_source_ns: int
    source: SourceAuction
    pulse_high: float
    pulse_low: float
    spot_pulse_high: float
    spot_pulse_low: float
    fair_basis: float
    initial_basis_dislocation: float
    basis_scale: float
    pulse_return_gap: float
    gap_scale: float
    oi_change_fraction: float
    oi_change_multiple: float
    oi_scale: float
    pulse_flow_imbalance: float
    impulse_atr: float
    volume_ratio: float
    frozen_atr: float
    frozen_spot_atr: float
    observed_high: float
    observed_low: float


class LiquidityStateEngine:
    """OI-qualified perpetual/spot dislocation followed by spot-led auction reversion."""

    config: EngineConfig

    def __init__(self, config: EngineConfig):
        if config.metrics_interval_minutes <= 0:
            raise ValueError("metrics interval must be positive")
        if config.oi_change_lookback_samples < 8:
            raise ValueError("OI lookback must contain enough prior completed samples")
        if config.basis_lookback_bars < 60 or config.gap_lookback_samples < 12:
            raise ValueError("dislocation lookbacks are too short")
        if not 0.0 < config.basis_reclaim_fraction < 1.0:
            raise ValueError("basis reclaim fraction must be in (0, 1)")
        self.config = config
        history_size = max(
            1024,
            config.basis_lookback_bars + config.metrics_interval_minutes + 64,
            config.gap_lookback_samples * config.metrics_interval_minutes + 64,
        )
        self._bars: deque[FlowBar] = deque(maxlen=history_size)
        self._true_ranges: deque[float] = deque(maxlen=config.atr_period)
        self._volumes: deque[float] = deque(maxlen=config.volume_period)
        self._metric_changes: deque[float] = deque(maxlen=config.oi_change_lookback_samples)
        self._last_metric_observed_ns: int | None = None
        self._last_open_interest: float | None = None
        self._pending: _PendingDislocation | None = None
        self._index = -1
        self._last_timestamp = -1
        self._atr = 0.0
        self._volume_median = 0.0
        self._cooldown = 0

    @property
    def atr(self) -> float:
        return self._atr

    @property
    def active_pools(self) -> tuple[_PendingDislocation, ...]:
        return (self._pending,) if self._pending is not None else ()

    def on_bar(self, bar: FlowBar) -> EngineResult:
        if bar.ts_ns <= self._last_timestamp:
            raise ValueError("bars must be strictly increasing by observation timestamp")
        self._last_timestamp = bar.ts_ns
        self._index += 1
        previous_close = self._bars[-1].close if self._bars else bar.close
        true_range = max(bar.high - bar.low, abs(bar.high - previous_close), abs(bar.low - previous_close))
        self._true_ranges.append(true_range)
        self._atr = sum(self._true_ranges) / len(self._true_ranges)
        self._volume_median = median(self._volumes) if self._volumes else max(bar.volume, 1e-12)
        self._bars.append(bar)

        events: list[DiagnosticEvent] = []
        signal: Signal | None = None
        if self._cooldown > 0:
            self._cooldown -= 1
        elif self._pending is not None:
            signal = self._advance_pending(bar, events)

        candidate = self._observe_new_metric(bar)
        if self._cooldown == 0 and self._pending is None and candidate is not None:
            events.append(self._pulse_event(candidate, bar))
            self._pending = candidate

        self._volumes.append(bar.volume)
        return EngineResult(tuple(events), signal)

    def _observe_new_metric(self, bar: FlowBar) -> _PendingDislocation | None:
        observed_ns = bar.metric_observed_ns
        oi = bar.open_interest
        if observed_ns is None or oi is None:
            return None
        if self._last_metric_observed_ns is not None and observed_ns <= self._last_metric_observed_ns:
            return None
        previous_oi = self._last_open_interest
        self._last_metric_observed_ns = observed_ns
        self._last_open_interest = oi
        if previous_oi is None or previous_oi <= 0.0:
            return None
        change = (oi - previous_oi) / previous_oi
        prior_changes = list(self._metric_changes)
        scale = median(abs(value) for value in prior_changes) if prior_changes else 0.0
        self._metric_changes.append(change)
        minimum_history = max(8, self.config.oi_change_lookback_samples // 2)
        if len(prior_changes) < minimum_history or self._atr <= 0.0:
            return None
        scale = max(scale, 1e-8)
        threshold = max(
            self.config.minimum_absolute_oi_change_fraction,
            self.config.minimum_oi_change_multiple * scale,
        )
        if self.config.require_oi and change > -threshold:
            return None
        return self._build_dislocation(bar, change=change, scale=scale)

    def _build_dislocation(self, bar: FlowBar, *, change: float, scale: float) -> _PendingDislocation | None:
        assert bar.metric_observed_ns is not None
        metric_source_ns = bar.metric_observed_ns - MINUTE_NS
        completed = [item for item in self._bars if item.ts_ns <= metric_source_ns and item.has_spot]
        interval = self.config.metrics_interval_minutes
        minimum_history = max(
            interval + 15 + self.config.basis_lookback_bars,
            interval * (self.config.gap_lookback_samples + 2),
        )
        if len(completed) < minimum_history:
            return None
        pulse_bars = completed[-interval:]
        source_bars = completed[-interval - 15 : -interval]
        historical = completed[:-interval]
        if len(pulse_bars) != interval or len(source_bars) != 15:
            return None
        source = self._source_auction(source_bars)
        if source.width <= 0.0:
            return None
        # Freeze all volatility normalization before the positioning pulse.  Using
        # ``self._atr`` here would let the pulse and metric-availability bars inflate
        # their own displacement, confirmation and invalidation scales.  The frozen
        # scale is causal and represents the auction state that existed before the
        # suspected forced-flow event began.
        frozen_atr = self._futures_atr(historical, self.config.atr_period)
        frozen_spot_atr = self._spot_atr(historical, self.config.atr_period)
        if frozen_atr <= 0.0 or frozen_spot_atr <= 0.0:
            return None
        pulse_open = pulse_bars[0].open
        pulse_close = pulse_bars[-1].close
        spot_open = pulse_bars[0].spot_open
        spot_close = pulse_bars[-1].spot_close
        if spot_open is None or spot_close is None:
            return None
        pulse_high = max(item.high for item in pulse_bars)
        pulse_low = min(item.low for item in pulse_bars)
        spot_pulse_high = max(float(item.spot_high) for item in pulse_bars if item.spot_high is not None)
        spot_pulse_low = min(float(item.spot_low) for item in pulse_bars if item.spot_low is not None)
        impulse = pulse_close - pulse_open
        impulse_atr = abs(impulse) / max(frozen_atr, 1e-12)
        if impulse_atr < self.config.minimum_impulse_atr:
            return None
        pulse_volume = sum(item.volume for item in pulse_bars)
        pulse_flow = sum(item.signed_flow for item in pulse_bars) / max(pulse_volume, 1e-12)
        prior_sums = self._nonoverlapping_volume_sums(historical, interval, limit=12)
        reference_volume = median(prior_sums) if prior_sums else interval * max(self._volume_median, 1e-12)
        volume_ratio = pulse_volume / max(reference_volume, 1e-12)
        if volume_ratio < self.config.minimum_volume_ratio:
            return None

        basis_history = [item.basis_fraction for item in historical[-self.config.basis_lookback_bars:]]
        basis_values = [float(value) for value in basis_history if value is not None]
        if len(basis_values) < self.config.basis_lookback_bars // 2:
            return None
        fair_basis = median(basis_values)
        basis_scale = median(abs(value - fair_basis) for value in basis_values)
        basis_scale = max(basis_scale, 1e-8)
        current_basis = pulse_close / spot_close - 1.0
        basis_dislocation = current_basis - fair_basis

        gap_history = self._nonoverlapping_return_gaps(
            historical,
            interval,
            limit=self.config.gap_lookback_samples,
        )
        if len(gap_history) < self.config.gap_lookback_samples // 2:
            return None
        gap_center = median(gap_history)
        gap_scale = median(abs(value - gap_center) for value in gap_history)
        gap_scale = max(gap_scale, 1e-8)
        futures_return = pulse_close / pulse_open - 1.0
        spot_return = spot_close / spot_open - 1.0
        pulse_gap = futures_return - spot_return - gap_center

        basis_threshold = max(
            self.config.minimum_absolute_basis_fraction,
            self.config.basis_dislocation_multiple * basis_scale,
        )
        gap_threshold = self.config.return_gap_multiple * gap_scale
        buffer = self.config.acceptance_buffer_atr * frozen_atr
        if (
            impulse > 0.0
            and pulse_close >= source.high + buffer
            and pulse_flow >= self.config.directional_imbalance
        ):
            direction = "UP"
            spot_gap_ok = basis_dislocation >= basis_threshold and pulse_gap >= gap_threshold
        elif (
            impulse < 0.0
            and pulse_close <= source.low - buffer
            and pulse_flow <= -self.config.directional_imbalance
        ):
            direction = "DOWN"
            spot_gap_ok = basis_dislocation <= -basis_threshold and pulse_gap <= -gap_threshold
        else:
            return None
        if self.config.require_spot_gap and not spot_gap_ok:
            return None

        scenario_id = sha256(
            f"{bar.metric_observed_ns}|{direction}|{fair_basis:.12f}|{basis_dislocation:.12f}|{change:.12f}".encode()
        ).hexdigest()[:20]
        return _PendingDislocation(
            scenario_id=f"spot-dislocation-{scenario_id}",
            direction=direction,
            detected_index=self._index,
            metric_observed_ns=bar.metric_observed_ns,
            metric_source_ns=metric_source_ns,
            source=source,
            pulse_high=pulse_high,
            pulse_low=pulse_low,
            spot_pulse_high=spot_pulse_high,
            spot_pulse_low=spot_pulse_low,
            fair_basis=fair_basis,
            initial_basis_dislocation=basis_dislocation,
            basis_scale=basis_scale,
            pulse_return_gap=pulse_gap,
            gap_scale=gap_scale,
            oi_change_fraction=change,
            oi_change_multiple=abs(change) / max(scale, 1e-12),
            oi_scale=scale,
            pulse_flow_imbalance=pulse_flow,
            impulse_atr=impulse_atr,
            volume_ratio=volume_ratio,
            frozen_atr=frozen_atr,
            frozen_spot_atr=frozen_spot_atr,
            observed_high=max(pulse_high, bar.high),
            observed_low=min(pulse_low, bar.low),
        )

    @staticmethod
    def _source_auction(bars: Sequence[FlowBar]) -> SourceAuction:
        high = max(item.high for item in bars)
        low = min(item.low for item in bars)
        total_volume = sum(item.volume for item in bars)
        equilibrium = (
            sum(((item.high + item.low + item.close) / 3.0) * item.volume for item in bars) / total_volume
            if total_volume > 0.0
            else (high + low) / 2.0
        )
        return SourceAuction(
            start_ns=bars[0].ts_ns - MINUTE_NS,
            end_ns=bars[-1].ts_ns,
            high=high,
            low=low,
            equilibrium=equilibrium,
            width=high - low,
        )

    @staticmethod
    def _nonoverlapping_volume_sums(bars: Sequence[FlowBar], interval: int, *, limit: int) -> list[float]:
        values: list[float] = []
        end = len(bars)
        while end >= interval and len(values) < limit:
            start = end - interval
            values.append(sum(item.volume for item in bars[start:end]))
            end = start
        return values

    @staticmethod
    def _nonoverlapping_return_gaps(bars: Sequence[FlowBar], interval: int, *, limit: int) -> list[float]:
        values: list[float] = []
        end = len(bars)
        while end >= interval and len(values) < limit:
            start = end - interval
            block = bars[start:end]
            first, last = block[0], block[-1]
            if first.spot_open is not None and last.spot_close is not None:
                futures_return = last.close / first.open - 1.0
                spot_return = last.spot_close / first.spot_open - 1.0
                values.append(futures_return - spot_return)
            end = start
        return values

    @staticmethod
    def _futures_atr(bars: Sequence[FlowBar], period: int) -> float:
        if period <= 0 or len(bars) < period + 1:
            return 0.0
        window = bars[-(period + 1):]
        ranges = [
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
            for previous, current in zip(window[:-1], window[1:], strict=True)
        ]
        return sum(ranges) / len(ranges) if ranges else 0.0

    @staticmethod
    def _spot_atr(bars: Sequence[FlowBar], period: int) -> float:
        eligible = [item for item in bars if item.has_spot]
        if period <= 0 or len(eligible) < period + 1:
            return 0.0
        window = eligible[-(period + 1):]
        ranges: list[float] = []
        for previous, current in zip(window[:-1], window[1:], strict=True):
            assert previous.spot_close is not None
            assert current.spot_high is not None and current.spot_low is not None
            ranges.append(max(
                current.spot_high - current.spot_low,
                abs(current.spot_high - previous.spot_close),
                abs(current.spot_low - previous.spot_close),
            ))
        return sum(ranges) / len(ranges) if ranges else 0.0

    def _advance_pending(self, bar: FlowBar, events: list[DiagnosticEvent]) -> Signal | None:
        pending = self._pending
        assert pending is not None
        if self._index <= pending.detected_index:
            return None
        pending.observed_high = max(pending.observed_high, bar.high)
        pending.observed_low = min(pending.observed_low, bar.low)
        elapsed = self._index - pending.detected_index
        if self._source_target_reached(pending, bar):
            self._expire(pending, bar, "SOURCE_AUCTION_EQUILIBRIUM_REACHED_BEFORE_ENTRY", events)
            return None
        if self._spot_led_reversal_confirmed(pending, bar):
            signal, reason = self._build_signal(
                pending,
                bar,
                confirmation="basis-contraction-plus-spot-lead-plus-perpetual-counterflow",
            )
            return self._finish(pending, bar, signal, reason, events)
        if elapsed >= self.config.confirmation_timeout_bars:
            self._expire(pending, bar, "SPOT_LED_REVERSAL_DID_NOT_CONFIRM_IN_TIME", events)
        return None

    @staticmethod
    def _source_target(pending: _PendingDislocation) -> float:
        return pending.source.equilibrium

    def _source_target_reached(self, pending: _PendingDislocation, bar: FlowBar) -> bool:
        target = self._source_target(pending)
        return bar.high >= target if pending.direction == "DOWN" else bar.low <= target

    def _spot_led_reversal_confirmed(self, pending: _PendingDislocation, bar: FlowBar) -> bool:
        if bar.spot_close is None or len(self._bars) < 2:
            return False
        previous = self._bars[-2]
        if not previous.has_spot:
            return False
        assert previous.spot_high is not None and previous.spot_low is not None
        assert bar.spot_open is not None and bar.spot_high is not None and bar.spot_low is not None
        current = bar.close / bar.spot_close - 1.0 - pending.fair_basis
        initial = pending.initial_basis_dislocation
        contracted = abs(current) <= (1.0 - self.config.basis_reclaim_fraction) * abs(initial)
        toward_fair = current > initial if pending.direction == "DOWN" else current < initial
        futures_body_atr = abs(bar.close - bar.open) / max(pending.frozen_atr, 1e-12)
        spot_body_atr = abs(bar.spot_close - bar.spot_open) / max(pending.frozen_spot_atr, 1e-12)
        minimum_body = self.config.minimum_resolution_displacement_atr
        if pending.direction == "DOWN":
            perpetual_counterflow = bar.close > self._bars[-2].high and bar.close > bar.open
            flow_shift = bar.flow_imbalance >= self.config.directional_imbalance
            spot_lead = bar.spot_close > previous.spot_high and bar.spot_close > bar.spot_open
            spot_not_extending = bar.spot_low >= pending.spot_pulse_low - 0.10 * pending.frozen_spot_atr
            futures_not_extending = bar.low >= pending.pulse_low - 0.10 * pending.frozen_atr
        else:
            perpetual_counterflow = bar.close < self._bars[-2].low and bar.close < bar.open
            flow_shift = bar.flow_imbalance <= -self.config.directional_imbalance
            spot_lead = bar.spot_close < previous.spot_low and bar.spot_close < bar.spot_open
            spot_not_extending = bar.spot_high <= pending.spot_pulse_high + 0.10 * pending.frozen_spot_atr
            futures_not_extending = bar.high <= pending.pulse_high + 0.10 * pending.frozen_atr
        spot_lead_ok = spot_lead if self.config.require_spot_lead else True
        return (
            contracted
            and toward_fair
            and perpetual_counterflow
            and flow_shift
            and spot_lead_ok
            and spot_not_extending
            and futures_not_extending
            and futures_body_atr >= minimum_body
            and (spot_body_atr >= minimum_body if self.config.require_spot_lead else True)
        )

    def _build_signal(
        self,
        pending: _PendingDislocation,
        bar: FlowBar,
        *,
        confirmation: str,
    ) -> tuple[Signal | None, str]:
        target = self._source_target(pending)
        entry = bar.close
        # The invalidation buffer uses the same frozen pre-pulse volatility scale as
        # admission and confirmation.  The stop still lies beyond every observed
        # perpetual extreme, so this does not weaken the scenario invalidation; it
        # prevents the liquidation shock from mechanically widening its own stop.
        atr = max(pending.frozen_atr, 1e-12)
        if pending.direction == "DOWN":
            side = "BUY"
            stop = pending.observed_low - self.config.stop_buffer_atr * atr
            geometry_ok = stop < entry < target
        else:
            side = "SELL"
            stop = pending.observed_high + self.config.stop_buffer_atr * atr
            geometry_ok = target < entry < stop
        if not geometry_ok:
            return None, "SPOT_LED_AUCTION_REVERSION_HAS_INVALID_GEOMETRY"
        cost = self.config.composite_cost_per_fill
        net_risk = abs(entry - stop) + entry * cost + stop * cost
        net_reward = abs(target - entry) - entry * cost - target * cost
        if net_reward <= 0.0:
            return None, "SPOT_LED_AUCTION_REVERSION_HAS_NONPOSITIVE_REWARD_AFTER_COST"
        ratio = net_reward / max(net_risk, 1e-12)
        if ratio < self.config.minimum_net_reward_to_risk:
            return None, "SPOT_LED_AUCTION_REVERSION_NET_REWARD_TO_RISK_BELOW_GATE"
        return Signal(
            scenario_id=pending.scenario_id,
            branch="REVERSAL",
            side=side,
            observed_time_ns=bar.ts_ns,
            entry_reference=entry,
            stop_price=stop,
            target_price=target,
            net_reward_to_risk=ratio,
            reason_code="SPOT_LED_LIQUIDATION_AUCTION_REVERSION",
            details={
                "pulse_direction": pending.direction,
                "require_oi": self.config.require_oi,
                "require_spot_gap": self.config.require_spot_gap,
                "require_spot_lead": self.config.require_spot_lead,
                "oi_change_fraction": pending.oi_change_fraction,
                "oi_change_multiple": pending.oi_change_multiple,
                "oi_scale": pending.oi_scale,
                "pulse_flow_imbalance": pending.pulse_flow_imbalance,
                "impulse_atr": pending.impulse_atr,
                "pulse_volume_ratio": pending.volume_ratio,
                "frozen_atr": pending.frozen_atr,
                "frozen_spot_atr": pending.frozen_spot_atr,
                "fair_basis": pending.fair_basis,
                "initial_basis_dislocation": pending.initial_basis_dislocation,
                "basis_scale": pending.basis_scale,
                "pulse_return_gap": pending.pulse_return_gap,
                "gap_scale": pending.gap_scale,
                "source_high": pending.source.high,
                "source_low": pending.source.low,
                "pulse_high": pending.pulse_high,
                "pulse_low": pending.pulse_low,
                "source_auction_equilibrium_target": target,
                "confirmation": confirmation,
                "atr": atr,
            },
        ), "ENTERABLE"

    def _pulse_event(self, pending: _PendingDislocation, bar: FlowBar) -> DiagnosticEvent:
        if not self.config.require_oi:
            reason = "SPOT_DISLOCATION_WITHOUT_OI_CONTROL"
        elif not self.config.require_spot_gap:
            reason = "ABNORMAL_OI_DROP_WITHOUT_SPOT_GAP_CONTROL"
        else:
            reason = "ABNORMAL_OI_DROP_WITH_PERPETUAL_SPOT_DISLOCATION"
        return self._event(
            pending,
            bar,
            "SPOT_LED_LIQUIDATION_DISLOCATION_CONFIRMED",
            "OBSERVING",
            "SPOT_LED_REVERSAL_PENDING",
            reason,
        )

    def _event(
        self,
        pending: _PendingDislocation,
        bar: FlowBar,
        event_type: str,
        previous_state: str,
        next_state: str,
        reason_code: str,
        extra: Mapping[str, Any] | None = None,
    ) -> DiagnosticEvent:
        details: dict[str, Any] = {
            "pulse_direction": pending.direction,
            "require_oi": self.config.require_oi,
            "require_spot_gap": self.config.require_spot_gap,
            "require_spot_lead": self.config.require_spot_lead,
            "metric_observed_ns": pending.metric_observed_ns,
            "metric_source_ns": pending.metric_source_ns,
            "oi_change_fraction": pending.oi_change_fraction,
            "oi_change_multiple": pending.oi_change_multiple,
            "oi_scale": pending.oi_scale,
            "pulse_flow_imbalance": pending.pulse_flow_imbalance,
            "impulse_atr": pending.impulse_atr,
            "pulse_volume_ratio": pending.volume_ratio,
            "frozen_atr": pending.frozen_atr,
            "frozen_spot_atr": pending.frozen_spot_atr,
            "fair_basis": pending.fair_basis,
            "initial_basis_dislocation": pending.initial_basis_dislocation,
            "basis_scale": pending.basis_scale,
            "pulse_return_gap": pending.pulse_return_gap,
            "gap_scale": pending.gap_scale,
            "source_high": pending.source.high,
            "source_low": pending.source.low,
            "pulse_high": pending.pulse_high,
            "pulse_low": pending.pulse_low,
            "spot_pulse_high": pending.spot_pulse_high,
            "spot_pulse_low": pending.spot_pulse_low,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "spot_close": bar.spot_close,
            "basis_fraction": bar.basis_fraction,
            "flow_imbalance": bar.flow_imbalance,
            "atr": self._atr,
        }
        if extra:
            details.update(extra)
        return DiagnosticEvent(
            scenario_id=pending.scenario_id,
            event_type=event_type,
            event_time_ns=bar.ts_ns,
            observed_time_ns=bar.ts_ns,
            previous_state=previous_state,
            next_state=next_state,
            reason_code=reason_code,
            reference_price=bar.close,
            details=details,
        )

    def _finish(
        self,
        pending: _PendingDislocation,
        bar: FlowBar,
        signal: Signal | None,
        reason: str,
        events: list[DiagnosticEvent],
    ) -> Signal | None:
        events.append(self._event(
            pending,
            bar,
            "SPOT_LED_AUCTION_REVERSION_CONFIRMED" if signal is not None else "SPOT_LED_AUCTION_REVERSION_UNTRADEABLE",
            "SPOT_LED_REVERSAL_PENDING",
            "ENTERABLE" if signal is not None else "NO_TRADE",
            signal.reason_code if signal is not None else reason,
            {"net_reward_to_risk": signal.net_reward_to_risk if signal is not None else None},
        ))
        self._pending = None
        self._cooldown = self.config.cooldown_bars
        return signal

    def _expire(
        self,
        pending: _PendingDislocation,
        bar: FlowBar,
        reason: str,
        events: list[DiagnosticEvent],
    ) -> None:
        events.append(self._event(
            pending,
            bar,
            "SPOT_LED_AUCTION_REVERSION_EXPIRED",
            "SPOT_LED_REVERSAL_PENDING",
            "NO_TRADE",
            reason,
        ))
        self._pending = None
        self._cooldown = self.config.cooldown_bars


__all__ = [
    "MINUTE_NS",
    "DiagnosticEvent",
    "EngineConfig",
    "EngineResult",
    "FlowBar",
    "LiquidityStateEngine",
    "RiskSizing",
    "Signal",
    "risk_based_quantity",
]
