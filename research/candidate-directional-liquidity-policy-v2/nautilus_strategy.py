"""NautilusTrader execution bridge for one predeclared liquidity plan.

The alpha policy is deliberately independent from execution. This bridge uses the
same Strategy in backtest and live nodes, submits one limit entry, and only after a
fill submits a reduce-only stop-market plus reduce-only take-profit limit. The two
children are managed as a local OCO because venue support is not assumed.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from risk_sizing import RiskSizing, size_three_percent_risk


@dataclass(frozen=True, slots=True)
class TradePlan:
    plan_id: str
    instrument_id: str
    side: str
    entry: float
    stop: float
    target: float

    def __post_init__(self) -> None:
        if self.side not in {"LONG", "SHORT"}:
            raise ValueError("side must be LONG or SHORT")
        if not all(value > 0.0 for value in (self.entry, self.stop, self.target)):
            raise ValueError("entry, stop and target must be positive")
        valid = self.stop < self.entry < self.target if self.side == "LONG" else self.target < self.entry < self.stop
        if not valid:
            raise ValueError("invalid long/short plan geometry")
        if abs(self.target - self.entry) / abs(self.entry - self.stop) < 1.0:
            raise ValueError("gross planned RR must be at least 1.0")


class LifecycleState(str, Enum):
    FLAT = "FLAT"
    ENTRY_WORKING = "ENTRY_WORKING"
    POSITION_PROTECTED = "POSITION_PROTECTED"
    CLOSED = "CLOSED"
    FAULT = "FAULT"


@dataclass(slots=True)
class BracketLifecycle:
    state: LifecycleState = LifecycleState.FLAT
    plan_id: str | None = None
    entry_order_id: str | None = None
    stop_order_id: str | None = None
    target_order_id: str | None = None

    def arm(self, plan_id: str, entry_order_id: str) -> None:
        if self.state not in {LifecycleState.FLAT, LifecycleState.CLOSED}:
            raise RuntimeError("global account slot is already occupied")
        self.state = LifecycleState.ENTRY_WORKING
        self.plan_id = plan_id
        self.entry_order_id = entry_order_id
        self.stop_order_id = None
        self.target_order_id = None

    def protect(self, stop_order_id: str, target_order_id: str) -> None:
        if self.state != LifecycleState.ENTRY_WORKING:
            raise RuntimeError("cannot install exits without a working/filled entry")
        self.stop_order_id = stop_order_id
        self.target_order_id = target_order_id
        self.state = LifecycleState.POSITION_PROTECTED

    def close(self, filled_order_id: str) -> str | None:
        if self.state != LifecycleState.POSITION_PROTECTED:
            return None
        sibling = self.target_order_id if filled_order_id == self.stop_order_id else self.stop_order_id
        self.state = LifecycleState.CLOSED
        return sibling

    def entry_terminal_without_fill(self) -> None:
        if self.state == LifecycleState.ENTRY_WORKING:
            self.state = LifecycleState.FLAT
            self.plan_id = None
            self.entry_order_id = None

    def fault(self) -> None:
        self.state = LifecycleState.FAULT


try:
    from nautilus_trader.config import StrategyConfig
    from nautilus_trader.model.enums import OrderSide, TimeInForce, TriggerType
    from nautilus_trader.model.identifiers import InstrumentId
    from nautilus_trader.trading.strategy import Strategy
except Exception:
    StrategyConfig = object  # type: ignore[assignment,misc]
    Strategy = object  # type: ignore[assignment,misc]
    InstrumentId = Any  # type: ignore[assignment,misc]
    OrderSide = TimeInForce = TriggerType = None  # type: ignore[assignment]


if Strategy is not object:

    class DirectionalLiquidityExecutionConfig(StrategyConfig, frozen=True):
        instrument_id: InstrumentId
        risk_fraction: float = 0.03
        stop_slippage_ticks: int = 2
        entry_fee_rate: float = 0.0002
        stop_fee_rate: float = 0.0005


    class DirectionalLiquidityExecutionStrategy(Strategy):
        """One global slot; no scale-in, scale-out, time exit or fallback entry."""

        def __init__(self, config: DirectionalLiquidityExecutionConfig) -> None:
            super().__init__(config)
            self.instrument_id = config.instrument_id
            self.lifecycle = BracketLifecycle()
            self.active_plan: TradePlan | None = None
            self.active_sizing: RiskSizing | None = None
            self._entry_order: Any | None = None
            self._stop_order: Any | None = None
            self._target_order: Any | None = None

        def arm_plan(self, plan: TradePlan, *, nav: float) -> RiskSizing:
            """Submit the predeclared limit entry using live instrument precision."""
            if str(self.instrument_id) != plan.instrument_id:
                raise ValueError("plan instrument does not match strategy instrument")
            if self.lifecycle.state not in {LifecycleState.FLAT, LifecycleState.CLOSED}:
                raise RuntimeError("single account slot is occupied")
            instrument = self.cache.instrument(self.instrument_id)
            if instrument is None:
                raise RuntimeError(f"instrument not in cache: {self.instrument_id}")
            tick_size = float(instrument.price_increment)
            quantity_step = float(instrument.size_increment)
            min_quantity = float(getattr(instrument, "min_quantity", 0.0) or 0.0)
            sizing = size_three_percent_risk(
                nav=nav,
                entry=plan.entry,
                stop=plan.stop,
                tick_size=tick_size,
                quantity_step=quantity_step,
                min_quantity=min_quantity,
                risk_fraction=float(self.config.risk_fraction),
                entry_fee_rate=float(self.config.entry_fee_rate),
                stop_fee_rate=float(self.config.stop_fee_rate),
                stop_slippage_ticks=int(self.config.stop_slippage_ticks),
            )
            side = OrderSide.BUY if plan.side == "LONG" else OrderSide.SELL
            entry_order = self.order_factory.limit(
                instrument_id=self.instrument_id,
                order_side=side,
                quantity=instrument.make_qty(sizing.quantity),
                price=instrument.make_price(plan.entry),
                time_in_force=TimeInForce.GTC,
                reduce_only=False,
                tags=[f"PLAN:{plan.plan_id}", "ROLE:ENTRY"],
            )
            self.active_plan = plan
            self.active_sizing = sizing
            self._entry_order = entry_order
            self.lifecycle.arm(plan.plan_id, str(entry_order.client_order_id))
            self.submit_order(entry_order)
            return sizing

        def cancel_pending_plan(self) -> None:
            """Cancel only a still-unfilled entry when its causal opportunity dies."""
            if self.lifecycle.state != LifecycleState.ENTRY_WORKING or self._entry_order is None:
                return
            self.cancel_order(self._entry_order)

        def _install_protection(self, quantity: Any) -> None:
            if self.active_plan is None or self.lifecycle.state != LifecycleState.ENTRY_WORKING:
                return
            instrument = self.cache.instrument(self.instrument_id)
            if instrument is None:
                self.lifecycle.fault()
                raise RuntimeError("instrument disappeared before protective orders")
            exit_side = OrderSide.SELL if self.active_plan.side == "LONG" else OrderSide.BUY
            stop_order = self.order_factory.stop_market(
                instrument_id=self.instrument_id,
                order_side=exit_side,
                quantity=quantity,
                trigger_price=instrument.make_price(self.active_plan.stop),
                trigger_type=TriggerType.LAST_PRICE,
                time_in_force=TimeInForce.GTC,
                reduce_only=True,
                tags=[f"PLAN:{self.active_plan.plan_id}", "ROLE:STOP"],
            )
            target_order = self.order_factory.limit(
                instrument_id=self.instrument_id,
                order_side=exit_side,
                quantity=quantity,
                price=instrument.make_price(self.active_plan.target),
                time_in_force=TimeInForce.GTC,
                reduce_only=True,
                tags=[f"PLAN:{self.active_plan.plan_id}", "ROLE:TARGET"],
            )
            self._stop_order = stop_order
            self._target_order = target_order
            self.lifecycle.protect(
                str(stop_order.client_order_id), str(target_order.client_order_id)
            )
            self.submit_order(stop_order)
            self.submit_order(target_order)

        def on_order_filled(self, event: Any) -> None:
            order_id = str(event.client_order_id)
            if order_id == self.lifecycle.entry_order_id:
                cached = self.cache.order(event.client_order_id)
                if cached is None or not cached.is_closed:
                    return
                quantity = cached.filled_qty
                if float(quantity) <= 0.0:
                    return
                self._install_protection(quantity)
                return
            if order_id in {self.lifecycle.stop_order_id, self.lifecycle.target_order_id}:
                sibling_id = self.lifecycle.close(order_id)
                if sibling_id is not None:
                    sibling = self.cache.order(sibling_id)
                    if sibling is not None and sibling.is_open:
                        self.cancel_order(sibling)

        def on_order_canceled(self, event: Any) -> None:
            if str(event.client_order_id) == self.lifecycle.entry_order_id:
                cached = self.cache.order(event.client_order_id)
                if cached is None or float(cached.filled_qty) == 0.0:
                    self.lifecycle.entry_terminal_without_fill()
                    self.active_plan = None
                    self.active_sizing = None

        def on_order_rejected(self, event: Any) -> None:
            order_id = str(event.client_order_id)
            if order_id == self.lifecycle.entry_order_id:
                self.lifecycle.entry_terminal_without_fill()
                self.active_plan = None
                self.active_sizing = None
                return
            if order_id in {self.lifecycle.stop_order_id, self.lifecycle.target_order_id}:
                self.lifecycle.fault()
                raise RuntimeError(f"protective order rejected: {order_id}: {event}")

        def on_stop(self) -> None:
            if self.lifecycle.state == LifecycleState.ENTRY_WORKING:
                self.cancel_pending_plan()
            elif self.lifecycle.state == LifecycleState.POSITION_PROTECTED:
                self.log.warning(
                    "strategy stop requested while a protected position is open; "
                    "no time-based market exit was submitted"
                )


else:

    class DirectionalLiquidityExecutionConfig:  # type: ignore[no-redef]
        pass


    class DirectionalLiquidityExecutionStrategy:  # type: ignore[no-redef]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("nautilus_trader is required for execution")


__all__ = [
    "TradePlan",
    "LifecycleState",
    "BracketLifecycle",
    "DirectionalLiquidityExecutionConfig",
    "DirectionalLiquidityExecutionStrategy",
]
