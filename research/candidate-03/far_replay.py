"""Causal event-time coordinator for the FAR detector and portfolio."""
from __future__ import annotations

from typing import Any, Callable, Iterable

from far_detector import FlowAbsorptionDetector, snapshot_details
from far_metrics import build_metrics
from far_model import AbsorptionSignal, FarConfig, MinuteBar, ScenarioState
from far_portfolio import FarPortfolio

Emit = Callable[..., None]
NS_PER_MINUTE = 60_000_000_000


class FarReplay:
    def __init__(self, config: FarConfig, emit: Emit) -> None:
        config.validate()
        self.config = config
        self.emit = emit
        self.detector = FlowAbsorptionDetector(config)
        self.portfolio = FarPortfolio(config, emit)
        self.pending: AbsorptionSignal | None = None
        self.current_bar: MinuteBar | None = None
        self.last_trade_values: tuple[int, float, int] | None = None
        self.last_accepted_signal_ns = -10**30
        self.qualified_signals = 0
        self.blocked_by_position = 0
        self.blocked_by_cooldown = 0
        self.pending_without_entry = 0

    def run(
        self,
        trades: Iterable[tuple[int, float, float, int, int]],
        start_ns: int,
        end_ns: int,
    ) -> dict[str, Any]:
        first_event_time_ns = -1
        for aggregate_id, price, quantity, event_time_ns, aggressor_sign in trades:
            if first_event_time_ns < 0:
                first_event_time_ns = event_time_ns
            if event_time_ns >= end_ns:
                break
            self.last_trade_values = (aggregate_id, price, event_time_ns)
            minute_index = event_time_ns // NS_PER_MINUTE
            if self.current_bar is None:
                self.current_bar = MinuteBar.from_values(
                    minute_index, price, quantity, event_time_ns, aggressor_sign
                )
            elif minute_index != self.current_bar.minute_index:
                if minute_index != self.current_bar.minute_index + 1:
                    raise ValueError(
                        f"missing aggregate-trade minute: {self.current_bar.minute_index} -> {minute_index}"
                    )
                completed = self.current_bar
                self.current_bar = MinuteBar.from_values(
                    minute_index, price, quantity, event_time_ns, aggressor_sign
                )
                self._on_minute(completed, start_ns, end_ns)
            else:
                self.current_bar.add_values(price, quantity, event_time_ns, aggressor_sign)

            if self.pending is not None and event_time_ns > self.pending.snapshot.observed_time_ns:
                self.portfolio.open(self.pending, aggregate_id, price, event_time_ns)
                self.pending = None
            elif self.portfolio.position is not None:
                self.portfolio.process(aggregate_id, price, event_time_ns)

        if first_event_time_ns < 0:
            raise ValueError("aggregate-trade stream is empty")
        required_minute = (start_ns - self.config.warmup_minutes * NS_PER_MINUTE) // NS_PER_MINUTE
        if first_event_time_ns // NS_PER_MINUTE > required_minute:
            raise ValueError("insufficient causal warm-up before evaluation week")
        if self.current_bar is not None:
            self._on_minute(self.current_bar, start_ns, end_ns)
        if self.pending is not None:
            self.pending_without_entry += 1
            self.pending = None
        if self.portfolio.position is not None:
            if self.last_trade_values is None:
                raise RuntimeError("position exists without a last observed trade")
            self.portfolio.force_close(*self.last_trade_values)
        counters = {
            "qualified_signals": self.qualified_signals,
            "blocked_by_position": self.blocked_by_position,
            "blocked_by_cooldown": self.blocked_by_cooldown,
            "pending_without_entry": self.pending_without_entry,
        }
        return build_metrics(
            self.config,
            self.portfolio.nav,
            self.portfolio.max_drawdown,
            self.portfolio.trades,
            counters,
            start_ns,
            end_ns,
        )

    def _on_minute(self, bar: MinuteBar, start_ns: int, end_ns: int) -> None:
        signal = self.detector.observe(bar)
        if signal is None or not (start_ns <= signal.snapshot.observed_time_ns < end_ns):
            return
        self.qualified_signals += 1
        if self.portfolio.position is not None or self.pending is not None:
            self.blocked_by_position += 1
            return
        elapsed = signal.snapshot.observed_time_ns - self.last_accepted_signal_ns
        if elapsed < self.config.episode_cooldown_minutes * NS_PER_MINUTE:
            self.blocked_by_cooldown += 1
            return
        self.last_accepted_signal_ns = signal.snapshot.observed_time_ns
        self.emit(
            scenario_id=signal.scenario_id,
            event_type="FLOW_CHASE_DETECTED",
            event_time_ns=signal.snapshot.observed_time_ns,
            observed_time_ns=signal.snapshot.observed_time_ns,
            previous_state=ScenarioState.IDLE.value,
            next_state=ScenarioState.STRETCHED_CHASE.value,
            reason_code="AGGRESSIVE_FLOW_ALIGNED_WITH_EQUILIBRIUM_STRETCH",
            reference_price=signal.snapshot.close,
            details=snapshot_details(signal),
        )
        self.emit(
            scenario_id=signal.scenario_id,
            event_type="ABSORPTION_CONFIRMED",
            event_time_ns=signal.snapshot.observed_time_ns,
            observed_time_ns=signal.snapshot.observed_time_ns,
            previous_state=ScenarioState.STRETCHED_CHASE.value,
            next_state=ScenarioState.ENTRY_PENDING.value,
            reason_code="HIGH_ACTIVITY_WITHOUT_DIRECTIONAL_PRICE_PROGRESS",
            reference_price=signal.snapshot.close,
            details={
                "direction": signal.direction.value,
                "directional_progress_bps": signal.snapshot.directional_progress_bps,
                "rejection_location": signal.snapshot.rejection_location,
            },
        )
        self.pending = signal
