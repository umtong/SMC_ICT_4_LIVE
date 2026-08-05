"""One-slot execution and project-prescribed loss-budget sizing."""
from __future__ import annotations

from typing import Any, Callable

from far_detector import snapshot_details
from far_model import (
    AbsorptionSignal,
    Direction,
    ExitReason,
    FarConfig,
    FarTrade,
    Position,
    ScenarioState,
)

Emit = Callable[..., None]
NS_PER_MINUTE = 60_000_000_000


class FarPortfolio:
    def __init__(self, config: FarConfig, emit: Emit) -> None:
        self.config = config
        self.emit = emit
        self.nav = config.initial_nav
        self.peak_nav = config.initial_nav
        self.max_drawdown = 0.0
        self.position: Position | None = None
        self.trades: list[FarTrade] = []

    def open(self, signal: AbsorptionSignal, aggregate_id: int, price: float, event_time_ns: int) -> None:
        if self.position is not None:
            raise RuntimeError("single-slot constraint violated")
        direction = signal.direction
        fee = self.config.taker_fee_bps / 10_000.0
        slippage = self.config.slippage_impact_bps / 10_000.0
        entry_fill = price * (1.0 + direction.sign * slippage)
        stop = (
            signal.snapshot.low - self.config.stop_buffer_atr * signal.snapshot.atr
            if direction is Direction.LONG
            else signal.snapshot.high + self.config.stop_buffer_atr * signal.snapshot.atr
        )
        if direction.sign * (entry_fill - stop) <= 0:
            raise RuntimeError("causal stop is not adverse to entry")
        stop_fill = stop * (1.0 - direction.sign * slippage)
        max_funding_per_unit = (
            entry_fill
            * (self.config.funding_bps_per_8h / 10_000.0)
            * (self.config.max_holding_minutes / 480.0)
        )
        expected_loss_per_unit = (
            abs(entry_fill - stop_fill)
            + entry_fill * fee
            + stop_fill * fee
            + max_funding_per_unit
        )
        planned_loss = self.nav * self.config.risk_fraction
        quantity = planned_loss / expected_loss_per_unit
        target = self._target_price(entry_fill, expected_loss_per_unit, direction)
        self.position = Position(
            scenario_id=signal.scenario_id,
            direction=direction,
            signal_time_ns=signal.snapshot.observed_time_ns,
            entry_time_ns=event_time_ns,
            entry_trade_id=aggregate_id,
            entry_raw_price=price,
            entry_fill_price=entry_fill,
            stop_trigger_price=stop,
            target_trigger_price=target,
            quantity=quantity,
            nav_before=self.nav,
            planned_loss=planned_loss,
            expected_loss_per_unit=expected_loss_per_unit,
            entry_fee=quantity * entry_fill * fee,
            signal_low=signal.snapshot.low,
            signal_high=signal.snapshot.high,
            signal_atr=signal.snapshot.atr,
            feature_details=snapshot_details(signal),
        )
        self.emit(
            scenario_id=signal.scenario_id,
            event_type="ENTRY_FILLED",
            event_time_ns=event_time_ns,
            observed_time_ns=event_time_ns,
            previous_state=ScenarioState.ENTRY_PENDING.value,
            next_state=ScenarioState.POSITION_ACTIVE.value,
            reason_code="FIRST_AGGREGATE_TRADE_AFTER_CONFIRMED_MINUTE",
            reference_price=entry_fill,
            details={
                "entry_trade_id": aggregate_id,
                "entry_raw_price": price,
                "entry_fill_price": entry_fill,
                "stop_trigger_price": stop,
                "expected_stop_fill_price": stop_fill,
                "target_trigger_price": target,
                "quantity": quantity,
                "nav_before": self.nav,
                "risk_fraction": self.config.risk_fraction,
                "planned_loss": planned_loss,
                "expected_loss_per_unit": expected_loss_per_unit,
                "max_expected_funding_per_unit": max_funding_per_unit,
            },
        )

    def _target_price(self, entry_fill: float, loss: float, direction: Direction) -> float:
        fee = self.config.taker_fee_bps / 10_000.0
        slippage = self.config.slippage_impact_bps / 10_000.0
        reward = self.config.target_net_r * loss
        if direction is Direction.LONG:
            return (reward + entry_fill * (1.0 + fee)) / ((1.0 - slippage) * (1.0 - fee))
        return (entry_fill * (1.0 - fee) - reward) / ((1.0 + slippage) * (1.0 + fee))

    def process(self, aggregate_id: int, price: float, event_time_ns: int) -> None:
        position = self.position
        if position is None or aggregate_id <= position.entry_trade_id:
            return
        expiry_ns = position.entry_time_ns + self.config.max_holding_minutes * NS_PER_MINUTE
        if event_time_ns >= expiry_ns:
            self._close(aggregate_id, event_time_ns, price, ExitReason.TIME)
        elif position.direction is Direction.LONG:
            if price <= position.stop_trigger_price:
                self._close(aggregate_id, event_time_ns, min(price, position.stop_trigger_price), ExitReason.STOP)
            elif price >= position.target_trigger_price:
                self._close(aggregate_id, event_time_ns, position.target_trigger_price, ExitReason.TARGET)
        else:
            if price >= position.stop_trigger_price:
                self._close(aggregate_id, event_time_ns, max(price, position.stop_trigger_price), ExitReason.STOP)
            elif price <= position.target_trigger_price:
                self._close(aggregate_id, event_time_ns, position.target_trigger_price, ExitReason.TARGET)

    def force_close(self, aggregate_id: int, price: float, event_time_ns: int) -> None:
        if self.position is not None:
            self._close(aggregate_id, event_time_ns, price, ExitReason.END_OF_RUN)

    def _close(self, aggregate_id: int, event_time_ns: int, raw_exit: float, reason: ExitReason) -> None:
        position = self.position
        if position is None:
            raise RuntimeError("cannot close an empty portfolio")
        fee = self.config.taker_fee_bps / 10_000.0
        slippage = self.config.slippage_impact_bps / 10_000.0
        exit_fill = raw_exit * (1.0 - position.direction.sign * slippage)
        holding_minutes = max(0.0, (event_time_ns - position.entry_time_ns) / NS_PER_MINUTE)
        funding_cost = (
            position.quantity
            * position.entry_fill_price
            * (self.config.funding_bps_per_8h / 10_000.0)
            * holding_minutes
            / 480.0
        )
        price_pnl = position.quantity * position.direction.sign * (
            exit_fill - position.entry_fill_price
        )
        exit_fee = position.quantity * exit_fill * fee
        net_pnl = price_pnl - position.entry_fee - exit_fee - funding_cost
        nav_after = position.nav_before + net_pnl
        if nav_after <= 0:
            raise RuntimeError("NAV became non-positive")
        net_r = net_pnl / position.planned_loss
        record = FarTrade(
            scenario_id=position.scenario_id,
            direction=position.direction,
            signal_time_ns=position.signal_time_ns,
            entry_time_ns=position.entry_time_ns,
            exit_time_ns=event_time_ns,
            entry_trade_id=position.entry_trade_id,
            exit_trade_id=aggregate_id,
            entry_raw_price=position.entry_raw_price,
            entry_fill_price=position.entry_fill_price,
            exit_raw_price=raw_exit,
            exit_fill_price=exit_fill,
            stop_trigger_price=position.stop_trigger_price,
            target_trigger_price=position.target_trigger_price,
            quantity=position.quantity,
            nav_before=position.nav_before,
            nav_after=nav_after,
            planned_loss=position.planned_loss,
            net_pnl=net_pnl,
            net_r=net_r,
            holding_minutes=holding_minutes,
            funding_cost=funding_cost,
            exit_reason=reason,
            feature_details=position.feature_details,
        )
        self.nav = nav_after
        self.peak_nav = max(self.peak_nav, nav_after)
        self.max_drawdown = max(self.max_drawdown, 1.0 - nav_after / self.peak_nav)
        self.trades.append(record)
        self.position = None
        self.emit(
            scenario_id=position.scenario_id,
            event_type="POSITION_CLOSED",
            event_time_ns=event_time_ns,
            observed_time_ns=event_time_ns,
            previous_state=ScenarioState.POSITION_ACTIVE.value,
            next_state=ScenarioState.CLOSED.value,
            reason_code=reason.value,
            reference_price=exit_fill,
            details={
                "exit_trade_id": aggregate_id,
                "exit_raw_price": raw_exit,
                "exit_fill_price": exit_fill,
                "net_pnl": net_pnl,
                "net_r": net_r,
                "nav_before": position.nav_before,
                "nav_after": nav_after,
                "holding_minutes": holding_minutes,
                "funding_cost": funding_cost,
            },
        )
