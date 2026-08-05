"""Causal event-time coordinator for FAR-v2."""
from __future__ import annotations

from collections import deque
from typing import Any, Callable, Iterable

from far_detector import FlowAbsorptionDetector, snapshot_details
from far_metrics import build_metrics
from far_model import ChochSetup, Direction, FarConfig, MinuteBar, ScenarioState
from far_portfolio import FarPortfolio

Emit = Callable[..., None]
NS_PER_MINUTE = 60_000_000_000


class FarReplay:
    """Replay exact aggregate trades without seeing beyond each decision time."""

    def __init__(self, config: FarConfig, emit: Emit) -> None:
        config.validate()
        self.config = config
        self.emit = emit
        self.detector = FlowAbsorptionDetector(config)
        self.portfolio = FarPortfolio(config, emit)
        self.pending: ChochSetup | None = None
        self.current_bar: MinuteBar | None = None
        self.completed_bars: deque[MinuteBar] = deque(maxlen=config.choch_lookback_minutes)
        self.last_trade_values: tuple[int, float, int] | None = None
        self.last_attempt_time_ns = -10**30
        self.attempted_excursions: set[tuple[int, int]] = set()
        self.counters: dict[str, int] = {
            "qualified_signals": 0,
            "stale_excursion_signals": 0,
            "blocked_by_position": 0,
            "blocked_by_cooldown": 0,
            "blocked_by_excursion": 0,
            "insufficient_structure_history": 0,
            "choch_armed": 0,
            "choch_confirmed": 0,
            "choch_invalidated": 0,
            "choch_expired": 0,
            "pending_at_end": 0,
        }

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

            if self.pending is not None and event_time_ns > self.pending.signal.snapshot.observed_time_ns:
                self._process_pending(aggregate_id, price, event_time_ns)
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
            self.counters["pending_at_end"] += 1
            self._terminate_pending(
                state=ScenarioState.EXPIRED,
                reason="RUN_ENDED_BEFORE_CHOCH",
                event_time_ns=end_ns - 1,
                reference_price=self.current_bar.close if self.current_bar is not None else None,
            )
        if self.portfolio.position is not None:
            if self.last_trade_values is None:
                raise RuntimeError("position exists without a last observed trade")
            self.portfolio.force_close(*self.last_trade_values)
        return build_metrics(
            self.config,
            self.portfolio.nav,
            self.portfolio.max_drawdown,
            self.portfolio.trades,
            dict(self.counters),
            start_ns,
            end_ns,
        )

    def _on_minute(self, bar: MinuteBar, start_ns: int, end_ns: int) -> None:
        signal = self.detector.observe(bar)
        prior_structure = tuple(self.completed_bars)
        if signal is not None and start_ns <= signal.snapshot.observed_time_ns < end_ns:
            self._consider_signal(signal, prior_structure)
        self.completed_bars.append(bar)

    def _consider_signal(self, signal: Any, prior_structure: tuple[MinuteBar, ...]) -> None:
        snapshot = signal.snapshot
        self.counters["qualified_signals"] += 1
        self.emit(
            scenario_id=signal.scenario_id,
            event_type="FLOW_CHASE_DETECTED",
            event_time_ns=snapshot.observed_time_ns,
            observed_time_ns=snapshot.observed_time_ns,
            previous_state=ScenarioState.IDLE.value,
            next_state=ScenarioState.STRETCHED_CHASE.value,
            reason_code="AGGRESSIVE_FLOW_ALIGNED_WITH_EQUILIBRIUM_STRETCH",
            reference_price=snapshot.close,
            details=snapshot_details(signal),
        )
        self.emit(
            scenario_id=signal.scenario_id,
            event_type="ABSORPTION_OBSERVED",
            event_time_ns=snapshot.observed_time_ns,
            observed_time_ns=snapshot.observed_time_ns,
            previous_state=ScenarioState.STRETCHED_CHASE.value,
            next_state=ScenarioState.ABSORPTION_OBSERVED.value,
            reason_code="HIGH_ACTIVITY_WITHOUT_DIRECTIONAL_PRICE_PROGRESS",
            reference_price=snapshot.close,
            details={
                "direction": signal.direction.value,
                "directional_progress_bps": snapshot.directional_progress_bps,
                "rejection_location": snapshot.rejection_location,
            },
        )

        if snapshot.equilibrium_excursion_minutes > self.config.equilibrium_excursion_max_minutes:
            self.counters["stale_excursion_signals"] += 1
            self._reject_signal(signal, "EQUILIBRIUM_EXCURSION_TOO_OLD")
            return
        if self.portfolio.position is not None or self.pending is not None:
            self.counters["blocked_by_position"] += 1
            self._reject_signal(signal, "SINGLE_SLOT_OCCUPIED")
            return
        elapsed = snapshot.observed_time_ns - self.last_attempt_time_ns
        if elapsed < self.config.episode_cooldown_minutes * NS_PER_MINUTE:
            self.counters["blocked_by_cooldown"] += 1
            self._reject_signal(signal, "INDEPENDENT_EPISODE_COOLDOWN")
            return
        if self.config.one_attempt_per_excursion and signal.excursion_id in self.attempted_excursions:
            self.counters["blocked_by_excursion"] += 1
            self._reject_signal(signal, "EXCURSION_ALREADY_ATTEMPTED")
            return
        if len(prior_structure) < self.config.choch_lookback_minutes:
            self.counters["insufficient_structure_history"] += 1
            self._reject_signal(signal, "INSUFFICIENT_PRIOR_STRUCTURE")
            return

        if signal.direction is Direction.LONG:
            confirmation = max(bar.high for bar in prior_structure)
            invalidation = snapshot.low - self.config.stop_buffer_atr * snapshot.atr
        else:
            confirmation = min(bar.low for bar in prior_structure)
            invalidation = snapshot.high + self.config.stop_buffer_atr * snapshot.atr
        expires = snapshot.observed_time_ns + self.config.choch_wait_minutes * NS_PER_MINUTE
        self.pending = ChochSetup(signal, confirmation, invalidation, expires)
        self.last_attempt_time_ns = snapshot.observed_time_ns
        self.attempted_excursions.add(signal.excursion_id)
        self.counters["choch_armed"] += 1
        self.emit(
            scenario_id=signal.scenario_id,
            event_type="CHOCH_ARMED",
            event_time_ns=snapshot.observed_time_ns,
            observed_time_ns=snapshot.observed_time_ns,
            previous_state=ScenarioState.ABSORPTION_OBSERVED.value,
            next_state=ScenarioState.CHOCH_PENDING.value,
            reason_code="PRIOR_TEN_MINUTE_STRUCTURE_IS_CONFIRMATION_BOUNDARY",
            reference_price=confirmation,
            details={
                "direction": signal.direction.value,
                "confirmation_price": confirmation,
                "invalidation_price": invalidation,
                "expires_time_ns": expires,
                "lookback_minutes": self.config.choch_lookback_minutes,
                "excursion_id": list(signal.excursion_id),
            },
        )

    def _process_pending(self, aggregate_id: int, price: float, event_time_ns: int) -> None:
        setup = self.pending
        if setup is None:
            return
        direction = setup.signal.direction
        invalidated = (
            price <= setup.invalidation_price
            if direction is Direction.LONG
            else price >= setup.invalidation_price
        )
        confirmed = (
            price >= setup.confirmation_price
            if direction is Direction.LONG
            else price <= setup.confirmation_price
        )
        # Adverse evidence and expiry are evaluated before confirmation. This is
        # conservative when exact order cannot rescue a setup after its thesis
        # has already failed or its decision window has ended.
        if invalidated:
            self.counters["choch_invalidated"] += 1
            self._terminate_pending(
                state=ScenarioState.INVALIDATED,
                reason="SIGNAL_EXTREME_INVALIDATED_BEFORE_CHOCH",
                event_time_ns=event_time_ns,
                reference_price=price,
            )
            return
        if event_time_ns > setup.expires_time_ns:
            self.counters["choch_expired"] += 1
            self._terminate_pending(
                state=ScenarioState.EXPIRED,
                reason="CHOCH_NOT_CONFIRMED_IN_TIME",
                event_time_ns=event_time_ns,
                reference_price=price,
            )
            return
        if not confirmed:
            return
        self.counters["choch_confirmed"] += 1
        self.emit(
            scenario_id=setup.signal.scenario_id,
            event_type="CHOCH_CONFIRMED",
            event_time_ns=event_time_ns,
            observed_time_ns=event_time_ns,
            previous_state=ScenarioState.CHOCH_PENDING.value,
            next_state=ScenarioState.ENTRY_PENDING.value,
            reason_code="FIRST_AGGREGATE_TRADE_THROUGH_PRIOR_STRUCTURE",
            reference_price=price,
            details={
                "aggregate_trade_id": aggregate_id,
                "confirmation_price": setup.confirmation_price,
                "observed_trade_price": price,
            },
        )
        signal = setup.signal
        self.pending = None
        self.portfolio.open(signal, aggregate_id, price, event_time_ns)

    def _reject_signal(self, signal: Any, reason: str) -> None:
        self.emit(
            scenario_id=signal.scenario_id,
            event_type="SCENARIO_TERMINATED",
            event_time_ns=signal.snapshot.observed_time_ns,
            observed_time_ns=signal.snapshot.observed_time_ns,
            previous_state=ScenarioState.ABSORPTION_OBSERVED.value,
            next_state=ScenarioState.EXPIRED.value,
            reason_code=reason,
            reference_price=signal.snapshot.close,
            details={},
        )

    def _terminate_pending(
        self,
        *,
        state: ScenarioState,
        reason: str,
        event_time_ns: int,
        reference_price: float | None,
    ) -> None:
        setup = self.pending
        if setup is None:
            return
        self.emit(
            scenario_id=setup.signal.scenario_id,
            event_type="SCENARIO_TERMINATED",
            event_time_ns=event_time_ns,
            observed_time_ns=event_time_ns,
            previous_state=ScenarioState.CHOCH_PENDING.value,
            next_state=state.value,
            reason_code=reason,
            reference_price=reference_price,
            details={
                "confirmation_price": setup.confirmation_price,
                "invalidation_price": setup.invalidation_price,
                "expires_time_ns": setup.expires_time_ns,
            },
        )
        self.pending = None
