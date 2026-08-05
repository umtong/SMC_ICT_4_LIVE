"""Exact aggregate-trade ADSE state machine and NAV accounting."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Callable, Iterable

from adse_data import AggTradeArchiveStream
from adse_model import (
    AdseConfig, ExitReason, ExitProfile, MinuteBar, NS_PER_DAY, NS_PER_MINUTE,
    Position, ScenarioSignal, ScenarioState, TradeRecord,
)

Emit = Callable[..., None]


def expected_loss_budget_per_unit(
    config: AdseConfig,
    profile: ExitProfile,
    entry_raw_price: float,
    stop_trigger_price: float,
    direction: int,
) -> tuple[float, float, float, float]:
    fee = config.taker_fee_bps / 10_000.0
    slippage = config.slippage_impact_bps / 10_000.0
    funding = config.funding_bps_per_8h / 10_000.0
    entry_fill = entry_raw_price * (1.0 + direction * slippage)
    stop_fill = stop_trigger_price * (1.0 - direction * slippage)
    if direction * (entry_fill - stop_trigger_price) <= 0:
        raise ValueError("stop is not adverse to entry")
    maximum_funding = entry_fill * funding * profile.max_holding_minutes / 480.0
    loss = abs(entry_fill - stop_fill) + entry_fill * fee + stop_fill * fee + maximum_funding
    if not isfinite(loss) or loss <= 0: raise ValueError("expected loss must be positive")
    return entry_fill, stop_fill, maximum_funding, loss


def target_trigger_price(
    config: AdseConfig,
    profile: ExitProfile,
    entry_fill: float,
    loss_per_unit: float,
    direction: int,
    maximum_funding_per_unit: float,
) -> float:
    fee = config.taker_fee_bps / 10_000.0
    slippage = config.slippage_impact_bps / 10_000.0
    reward = profile.target_net_r * loss_per_unit + maximum_funding_per_unit
    if direction > 0:
        return (reward + entry_fill * (1.0 + fee)) / ((1.0 - slippage) * (1.0 - fee))
    return (entry_fill * (1.0 - fee) - reward) / ((1.0 + slippage) * (1.0 + fee))


def lock_trigger_price(
    config: AdseConfig,
    profile: ExitProfile,
    entry_fill: float,
    loss_per_unit: float,
    direction: int,
    accrued_funding_per_unit: float,
) -> float:
    fee = config.taker_fee_bps / 10_000.0
    slippage = config.slippage_impact_bps / 10_000.0
    reward = profile.protection_lock_net_r * loss_per_unit + accrued_funding_per_unit
    if direction > 0:
        return (reward + entry_fill * (1.0 + fee)) / ((1.0 - slippage) * (1.0 - fee))
    return (entry_fill * (1.0 - fee) - reward) / ((1.0 + slippage) * (1.0 + fee))


class AdseReplay:
    def __init__(
        self,
        config: AdseConfig,
        futures_minutes: dict[int, MinuteBar],
        signals: list[ScenarioSignal],
        emit: Emit,
        evaluation_start_ns: int,
        evaluation_end_ns: int,
    ) -> None:
        config.validate()
        self.config = config; self.minutes = futures_minutes
        self.signals = list(signals); self.emit = emit
        self.start_ns = evaluation_start_ns; self.end_ns = evaluation_end_ns
        self.nav = config.initial_nav
        self.closed_peak = config.initial_nav; self.closed_max_drawdown = 0.0
        self.mark_peak = config.initial_nav; self.mark_min = config.initial_nav; self.mark_max_drawdown = 0.0
        self.last_equity = config.initial_nav
        self.trades: list[TradeRecord] = []
        self.signal_index = 0; self.pending: ScenarioSignal | None = None; self.position: Position | None = None
        self.last_position_minute_ns: int | None = None; self.last_trade: tuple[int, float, int] | None = None
        self.blocked_signals = 0; self.invalidated_before_entry = 0
        self.protection_activations = 0; self.stop_updates = 0
        self.daily_equity: list[dict[str, Any]] = []
        self.next_day_boundary_ns = evaluation_start_ns + NS_PER_DAY

    @staticmethod
    def _confirmed_state(signal: ScenarioSignal) -> str:
        return (
            ScenarioState.CASCADE_CONFIRMED.value
            if signal.scenario_kind == "LCPT"
            else ScenarioState.REACCELERATION_CONFIRMED.value
        )

    def _emit_signal_prefix(self, signal: ScenarioSignal) -> None:
        details = signal.details()
        if signal.scenario_kind == "LCPT":
            self.emit(
                scenario_id=signal.scenario_id,
                event_type="CASCADE_IGNITION_CANDIDATE",
                event_time_ns=signal.hypothesis_time_ns,
                observed_time_ns=signal.hypothesis_time_ns,
                previous_state=ScenarioState.IDLE.value,
                next_state=ScenarioState.IGNITION_CANDIDATE.value,
                reason_code="PRICE_FLOW_AND_OPEN_INTEREST_CONTRACTION",
                reference_price=None,
                details=details,
            )
            self.emit(
                scenario_id=signal.scenario_id,
                event_type="CASCADE_PROPAGATION_CONFIRMED",
                event_time_ns=signal.confirmation_time_ns,
                observed_time_ns=signal.confirmation_time_ns,
                previous_state=ScenarioState.IGNITION_CANDIDATE.value,
                next_state=ScenarioState.CASCADE_CONFIRMED.value,
                reason_code="SECOND_INTERVAL_PRICE_FLOW_AND_OI_CONTINUATION",
                reference_price=None,
                details=details,
            )
        else:
            self.emit(
                scenario_id=signal.scenario_id,
                event_type="DELEVERAGING_PULLBACK_OBSERVED",
                event_time_ns=signal.hypothesis_time_ns,
                observed_time_ns=signal.hypothesis_time_ns,
                previous_state=ScenarioState.IDLE.value,
                next_state=ScenarioState.PULLBACK_OBSERVED.value,
                reason_code="HIGH_OI_TURNOVER_DRIFT_WITH_COUNTER_MOVE",
                reference_price=None,
                details=details,
            )
            self.emit(
                scenario_id=signal.scenario_id,
                event_type="REACCELERATION_CONFIRMED",
                event_time_ns=signal.confirmation_time_ns,
                observed_time_ns=signal.confirmation_time_ns,
                previous_state=ScenarioState.PULLBACK_OBSERVED.value,
                next_state=ScenarioState.REACCELERATION_CONFIRMED.value,
                reason_code="SPOT_AND_FUTURES_FLOW_REALIGNED_AFTER_PULLBACK",
                reference_price=None,
                details=details,
            )

    def _record_day_boundaries(self, event_time_ns: int) -> None:
        while self.next_day_boundary_ns <= min(event_time_ns, self.end_ns):
            day = datetime.fromtimestamp((self.next_day_boundary_ns - 1) / 1e9, tz=timezone.utc).date().isoformat()
            self.daily_equity.append({"date": day, "nav": self.last_equity})
            self.next_day_boundary_ns += NS_PER_DAY

    def _update_mark_equity(self, raw_price: float, event_time_ns: int) -> float:
        position = self.position
        if position is None:
            equity = self.nav
        else:
            direction = position.signal.direction
            fee = self.config.taker_fee_bps / 10_000.0
            slippage = self.config.slippage_impact_bps / 10_000.0
            funding_rate = self.config.funding_bps_per_8h / 10_000.0
            exit_fill = raw_price * (1.0 - direction * slippage)
            holding = max(0.0, (event_time_ns - position.entry_time_ns) / NS_PER_MINUTE)
            funding = position.quantity * position.entry_fill_price * funding_rate * holding / 480.0
            pnl = (
                position.quantity * direction * (exit_fill - position.entry_fill_price)
                - position.quantity * position.entry_fill_price * fee
                - position.quantity * exit_fill * fee
                - funding
            )
            equity = self.nav + pnl
        self.mark_peak = max(self.mark_peak, equity); self.mark_min = min(self.mark_min, equity)
        if self.mark_peak > 0:
            self.mark_max_drawdown = max(self.mark_max_drawdown, 1.0 - equity / self.mark_peak)
        self.last_equity = equity
        return equity

    def _process_signals(self, event_time_ns: int) -> None:
        while self.signal_index < len(self.signals) and self.signals[self.signal_index].confirmation_time_ns <= event_time_ns:
            signal = self.signals[self.signal_index]; self.signal_index += 1
            self._emit_signal_prefix(signal)
            confirmed_state = self._confirmed_state(signal)
            if self.pending is not None or self.position is not None:
                self.blocked_signals += 1
                self.emit(
                    scenario_id=signal.scenario_id,
                    event_type="SCENARIO_BLOCKED",
                    event_time_ns=signal.confirmation_time_ns,
                    observed_time_ns=signal.confirmation_time_ns,
                    previous_state=confirmed_state,
                    next_state=ScenarioState.BLOCKED.value,
                    reason_code="GLOBAL_SINGLE_SLOT_ALREADY_OCCUPIED",
                    reference_price=None,
                    details={"single_slot": True, "scenario_kind": signal.scenario_kind},
                )
                continue
            self.pending = signal
            self.emit(
                scenario_id=signal.scenario_id,
                event_type="ENTRY_BUFFER_STARTED",
                event_time_ns=signal.confirmation_time_ns,
                observed_time_ns=signal.confirmation_time_ns,
                previous_state=confirmed_state,
                next_state=ScenarioState.ENTRY_BUFFER.value,
                reason_code="REQUIRE_ONE_COMPLETED_MINUTE_WITHOUT_INVALIDATION",
                reference_price=signal.stop_trigger_price,
                details={
                    "buffer_minutes": self.config.entry_buffer_minutes,
                    "directional_close_required": signal.buffer_direction_required,
                    "stop_trigger_price": signal.stop_trigger_price,
                },
            )

    def _buffer_direction_valid(self, signal: ScenarioSignal) -> bool:
        if not signal.buffer_direction_required: return True
        bar = self.minutes.get(signal.confirmation_time_ns)
        if bar is None or bar.open <= 0: return False
        return signal.direction * (bar.close / bar.open - 1.0) > 0

    def _update_structural_stop(self, boundary_ns: int) -> None:
        position = self.position
        if position is None or not position.protection_active: return
        profile = position.signal.exit_profile
        starts = [boundary_ns - offset * NS_PER_MINUTE for offset in range(profile.structural_trail_minutes, 0, -1)]
        if any(start not in self.minutes for start in starts): return
        bars = [self.minutes[start] for start in starts]
        atr_starts = [boundary_ns - offset * NS_PER_MINUTE for offset in range(self.config.atr_minutes, 0, -1)]
        if any(start not in self.minutes for start in atr_starts):
            current_atr = position.signal.atr
        else:
            true_ranges: list[float] = []; previous_close: float | None = None
            first = atr_starts[0] - NS_PER_MINUTE
            if first in self.minutes: previous_close = self.minutes[first].close
            for start in atr_starts:
                bar = self.minutes[start]; tr = bar.high - bar.low
                if previous_close is not None: tr = max(tr, abs(bar.high - previous_close), abs(bar.low - previous_close))
                true_ranges.append(tr); previous_close = bar.close
            current_atr = sum(true_ranges) / len(true_ranges)
        elapsed = max(0.0, (boundary_ns - position.entry_time_ns) / NS_PER_MINUTE)
        accrued_funding = (
            position.entry_fill_price * (self.config.funding_bps_per_8h / 10_000.0) * elapsed / 480.0
        )
        locked = lock_trigger_price(
            self.config, profile, position.entry_fill_price,
            position.expected_loss_per_unit, position.signal.direction, accrued_funding,
        )
        previous_close = self.minutes[boundary_ns - NS_PER_MINUTE].close
        old_stop = position.current_stop_price
        if position.signal.direction > 0:
            structural = min(bar.low for bar in bars) - profile.structural_trail_buffer_atr * current_atr
            position.current_stop_price = min(max(old_stop, locked, structural), previous_close * (1.0 - 1e-10))
        else:
            structural = max(bar.high for bar in bars) + profile.structural_trail_buffer_atr * current_atr
            position.current_stop_price = max(min(old_stop, locked, structural), previous_close * (1.0 + 1e-10))
        if abs(position.current_stop_price - old_stop) > max(1e-12, old_stop * 1e-12):
            self.stop_updates += 1
            self.emit(
                scenario_id=position.signal.scenario_id,
                event_type="STRUCTURAL_STOP_UPDATED",
                event_time_ns=boundary_ns,
                observed_time_ns=boundary_ns,
                previous_state=ScenarioState.POSITION_ACTIVE.value,
                next_state=ScenarioState.POSITION_ACTIVE.value,
                reason_code="COMPLETED_STRUCTURE_AND_NET_PROFIT_LOCK",
                reference_price=position.current_stop_price,
                details={
                    "old_stop_price": old_stop,
                    "new_stop_price": position.current_stop_price,
                    "locked_net_r": profile.protection_lock_net_r,
                    "trail_minutes": profile.structural_trail_minutes,
                    "current_atr": current_atr,
                },
            )

    def _close(self, aggregate_id: int, raw_price: float, event_time_ns: int, reason: ExitReason) -> None:
        position = self.position
        if position is None: raise RuntimeError("cannot close an empty portfolio")
        direction = position.signal.direction; fee = self.config.taker_fee_bps / 10_000.0
        slippage = self.config.slippage_impact_bps / 10_000.0
        funding_rate = self.config.funding_bps_per_8h / 10_000.0
        exit_fill = raw_price * (1.0 - direction * slippage)
        holding = max(0.0, (event_time_ns - position.entry_time_ns) / NS_PER_MINUTE)
        funding_cost = position.quantity * position.entry_fill_price * funding_rate * holding / 480.0
        net_pnl = (
            position.quantity * direction * (exit_fill - position.entry_fill_price)
            - position.quantity * position.entry_fill_price * fee
            - position.quantity * exit_fill * fee
            - funding_cost
        )
        nav_before = self.nav; self.nav += net_pnl
        if self.nav <= 0: raise RuntimeError("NAV became non-positive")
        self.closed_peak = max(self.closed_peak, self.nav)
        self.closed_max_drawdown = max(self.closed_max_drawdown, 1.0 - self.nav / self.closed_peak)
        self.mark_peak = max(self.mark_peak, self.nav); self.mark_min = min(self.mark_min, self.nav)
        self.mark_max_drawdown = max(self.mark_max_drawdown, 1.0 - self.nav / self.mark_peak)
        self.last_equity = self.nav
        record = TradeRecord(
            scenario_id=position.signal.scenario_id,
            scenario_kind=position.signal.scenario_kind,
            direction="LONG" if direction > 0 else "SHORT",
            hypothesis_time_ns=position.signal.hypothesis_time_ns,
            confirmation_time_ns=position.signal.confirmation_time_ns,
            entry_time_ns=position.entry_time_ns,
            exit_time_ns=event_time_ns,
            entry_trade_id=position.entry_trade_id,
            exit_trade_id=aggregate_id,
            entry_raw_price=position.entry_raw_price,
            entry_fill_price=position.entry_fill_price,
            exit_raw_price=raw_price,
            exit_fill_price=exit_fill,
            initial_stop_price=position.signal.stop_trigger_price,
            final_stop_price=position.current_stop_price,
            target_trigger_price=position.target_trigger_price,
            quantity=position.quantity,
            nav_before=nav_before,
            nav_after=self.nav,
            planned_loss=position.planned_loss,
            expected_loss_per_unit=position.expected_loss_per_unit,
            net_pnl=net_pnl,
            net_r=net_pnl / position.planned_loss,
            holding_minutes=holding,
            funding_cost=funding_cost,
            mfe_r=position.mfe_r,
            mae_r=position.mae_r,
            exit_reason=reason.value,
            feature_details=position.signal.details(),
        )
        self.trades.append(record)
        self.emit(
            scenario_id=position.signal.scenario_id,
            event_type="POSITION_CLOSED",
            event_time_ns=event_time_ns,
            observed_time_ns=event_time_ns,
            previous_state=ScenarioState.POSITION_ACTIVE.value,
            next_state=ScenarioState.CLOSED.value,
            reason_code=reason.value,
            reference_price=exit_fill,
            details={
                "exit_trade_id": aggregate_id,
                "exit_raw_price": raw_price,
                "exit_fill_price": exit_fill,
                "net_pnl": net_pnl,
                "net_r": record.net_r,
                "nav_before": nav_before,
                "nav_after": self.nav,
                "holding_minutes": holding,
                "funding_cost": funding_cost,
            },
        )
        self.position = None; self.last_position_minute_ns = None

    def run(self, futures_paths: Iterable[str]) -> dict[str, Any]:
        stream = AggTradeArchiveStream(futures_paths)
        for trade in stream:
            if trade.event_time_ns < self.start_ns: continue
            if trade.event_time_ns >= self.end_ns: break
            self.last_trade = (trade.aggregate_id, trade.price, trade.event_time_ns)
            self._record_day_boundaries(trade.event_time_ns)
            minute_ns = (trade.event_time_ns // NS_PER_MINUTE) * NS_PER_MINUTE

            if self.position is not None:
                if self.last_position_minute_ns is None:
                    self.last_position_minute_ns = minute_ns
                elif minute_ns > self.last_position_minute_ns:
                    boundary = self.last_position_minute_ns + NS_PER_MINUTE
                    while boundary <= minute_ns and self.position is not None:
                        self._update_structural_stop(boundary); boundary += NS_PER_MINUTE
                    self.last_position_minute_ns = minute_ns
                if self.position is not None and trade.event_time_ns >= self.position.expiry_time_ns:
                    self._close(trade.aggregate_id, trade.price, trade.event_time_ns, ExitReason.TIME)

            self._process_signals(trade.event_time_ns)

            entered_now = False
            if self.pending is not None:
                signal = self.pending
                buffer_end = signal.confirmation_time_ns + self.config.entry_buffer_minutes * NS_PER_MINUTE
                invalidated = (
                    signal.direction > 0 and trade.price <= signal.stop_trigger_price
                ) or (
                    signal.direction < 0 and trade.price >= signal.stop_trigger_price
                )
                if trade.event_time_ns < buffer_end:
                    if invalidated:
                        self.invalidated_before_entry += 1
                        self.emit(
                            scenario_id=signal.scenario_id,
                            event_type="ENTRY_BUFFER_INVALIDATED",
                            event_time_ns=trade.event_time_ns,
                            observed_time_ns=trade.event_time_ns,
                            previous_state=ScenarioState.ENTRY_BUFFER.value,
                            next_state=ScenarioState.INVALIDATED.value,
                            reason_code="SCENARIO_STOP_FAILED_BEFORE_ENTRY",
                            reference_price=trade.price,
                            details={"trade_id": trade.aggregate_id},
                        )
                        self.pending = None
                else:
                    directional_failure = not self._buffer_direction_valid(signal)
                    if invalidated or directional_failure:
                        self.invalidated_before_entry += 1
                        self.emit(
                            scenario_id=signal.scenario_id,
                            event_type="ENTRY_BUFFER_INVALIDATED",
                            event_time_ns=trade.event_time_ns,
                            observed_time_ns=trade.event_time_ns,
                            previous_state=ScenarioState.ENTRY_BUFFER.value,
                            next_state=ScenarioState.INVALIDATED.value,
                            reason_code=(
                                "ENTRY_GAPPED_BEYOND_INVALIDATION"
                                if invalidated
                                else "REACCELERATION_DID_NOT_SURVIVE_BUFFER"
                            ),
                            reference_price=trade.price,
                            details={"trade_id": trade.aggregate_id},
                        )
                        self.pending = None
                    else:
                        profile = signal.exit_profile
                        entry_fill, stop_fill, maximum_funding, loss = expected_loss_budget_per_unit(
                            self.config, profile, trade.price, signal.stop_trigger_price, signal.direction,
                        )
                        planned_loss = self.nav * self.config.risk_fraction
                        self.position = Position(
                            signal=signal,
                            entry_trade_id=trade.aggregate_id,
                            entry_time_ns=trade.event_time_ns,
                            entry_raw_price=trade.price,
                            entry_fill_price=entry_fill,
                            quantity=planned_loss / loss,
                            planned_loss=planned_loss,
                            expected_loss_per_unit=loss,
                            expected_stop_fill_price=stop_fill,
                            target_trigger_price=target_trigger_price(
                                self.config, profile, entry_fill, loss, signal.direction, maximum_funding,
                            ),
                            current_stop_price=signal.stop_trigger_price,
                            expiry_time_ns=trade.event_time_ns + profile.max_holding_minutes * NS_PER_MINUTE,
                            maximum_funding_per_unit=maximum_funding,
                        )
                        self.last_position_minute_ns = minute_ns
                        self.emit(
                            scenario_id=signal.scenario_id,
                            event_type="ENTRY_FILLED",
                            event_time_ns=trade.event_time_ns,
                            observed_time_ns=trade.event_time_ns,
                            previous_state=ScenarioState.ENTRY_BUFFER.value,
                            next_state=ScenarioState.POSITION_ACTIVE.value,
                            reason_code="FIRST_FUTURES_AGGREGATE_TRADE_AFTER_VALID_BUFFER",
                            reference_price=entry_fill,
                            details={
                                "entry_trade_id": trade.aggregate_id,
                                "entry_raw_price": trade.price,
                                "entry_fill_price": entry_fill,
                                "expected_stop_fill_price": stop_fill,
                                "target_trigger_price": self.position.target_trigger_price,
                                "quantity": self.position.quantity,
                                "planned_loss": planned_loss,
                                "risk_fraction": self.config.risk_fraction,
                                "expected_loss_per_unit": loss,
                                "maximum_funding_per_unit": maximum_funding,
                                "nav_before": self.nav,
                            },
                        )
                        self.pending = None; entered_now = True

            if self.position is not None and not entered_now:
                position = self.position; direction = position.signal.direction
                stop_hit = (
                    direction > 0 and trade.price <= position.current_stop_price
                ) or (
                    direction < 0 and trade.price >= position.current_stop_price
                )
                if stop_hit:
                    raw_exit = (
                        min(trade.price, position.current_stop_price)
                        if direction > 0 else max(trade.price, position.current_stop_price)
                    )
                    reason = (
                        ExitReason.STOP
                        if abs(position.current_stop_price - position.signal.stop_trigger_price)
                        <= max(1e-12, position.signal.stop_trigger_price * 1e-12)
                        else ExitReason.TRAIL
                    )
                    self._close(trade.aggregate_id, raw_exit, trade.event_time_ns, reason)
                    continue
                favorable_r = (
                    (trade.price - position.entry_fill_price) / position.expected_loss_per_unit
                    if direction > 0
                    else (position.entry_fill_price - trade.price) / position.expected_loss_per_unit
                )
                adverse_r = (
                    (position.entry_fill_price - trade.price) / position.expected_loss_per_unit
                    if direction > 0
                    else (trade.price - position.entry_fill_price) / position.expected_loss_per_unit
                )
                position.mfe_r = max(position.mfe_r, favorable_r)
                position.mae_r = max(position.mae_r, adverse_r)
                profile = position.signal.exit_profile
                if not position.protection_active and position.mfe_r >= profile.protection_activation_r:
                    position.protection_active = True; self.protection_activations += 1
                    self.emit(
                        scenario_id=position.signal.scenario_id,
                        event_type="PROFIT_PROTECTION_ACTIVATED",
                        event_time_ns=trade.event_time_ns,
                        observed_time_ns=trade.event_time_ns,
                        previous_state=ScenarioState.POSITION_ACTIVE.value,
                        next_state=ScenarioState.POSITION_ACTIVE.value,
                        reason_code="SCENARIO_REACHED_PROTECTION_ACTIVATION_R",
                        reference_price=trade.price,
                        details={"mfe_r": position.mfe_r, "activation_r": profile.protection_activation_r},
                    )
                target_hit = (
                    direction > 0 and trade.price >= position.target_trigger_price
                ) or (
                    direction < 0 and trade.price <= position.target_trigger_price
                )
                if target_hit:
                    self._close(
                        trade.aggregate_id, position.target_trigger_price,
                        trade.event_time_ns, ExitReason.TARGET,
                    )
                    continue
            self._update_mark_equity(trade.price, trade.event_time_ns)

        if self.pending is not None:
            signal = self.pending
            self.emit(
                scenario_id=signal.scenario_id,
                event_type="ENTRY_BUFFER_INVALIDATED",
                event_time_ns=self.end_ns - 1,
                observed_time_ns=self.end_ns - 1,
                previous_state=ScenarioState.ENTRY_BUFFER.value,
                next_state=ScenarioState.INVALIDATED.value,
                reason_code="EVALUATION_ENDED_BEFORE_ENTRY",
                reference_price=None,
                details={},
            )
            self.pending = None
        if self.position is not None:
            if self.last_trade is None: raise RuntimeError("position exists without an observed trade")
            aggregate_id, price, event_time_ns = self.last_trade
            self._close(aggregate_id, price, min(event_time_ns, self.end_ns - 1), ExitReason.END_OF_RUN)
        self.last_equity = self.nav; self._record_day_boundaries(self.end_ns)
        while len(self.daily_equity) < 7:
            boundary = self.start_ns + (len(self.daily_equity) + 1) * NS_PER_DAY
            day = datetime.fromtimestamp((boundary - 1) / 1e9, tz=timezone.utc).date().isoformat()
            self.daily_equity.append({"date": day, "nav": self.nav})
        return {
            "signals": len(self.signals),
            "blocked_signals": self.blocked_signals,
            "invalidated_before_entry": self.invalidated_before_entry,
            "protection_activations": self.protection_activations,
            "structural_stop_updates": self.stop_updates,
            "final_nav": self.nav,
            "closed_nav_max_drawdown": self.closed_max_drawdown,
            "max_drawdown": self.mark_max_drawdown,
            "minimum_mark_to_market_nav": self.mark_min,
            "daily_equity": self.daily_equity[:7],
            "trades_detail": [asdict(trade) for trade in self.trades],
            "single_slot_enforced": True,
        }
