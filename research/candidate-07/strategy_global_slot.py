"""Portfolio-global slot for multiple unchanged event-signal strategies.

Each instrument keeps the exact cost-viable MIT execution path already used in
its standalone NautilusTrader replay. This adapter changes only eligibility:
across every configured project instrument, at most one new-entry order or open
position may exist. The first causally delivered eligible signal reserves the
slot; no model score, symbol priority, notional cap, risk multiplier, or outcome
lookahead is used.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from nautilus_trader.model.data import CustomData
from nautilus_trader.model.events import OrderRejected, PositionClosed

from event_signal_data import CausalTradeSignal
from strategy_event_signal_cost_viable import Candidate07CostViableMITStrategy


@dataclass(slots=True)
class PortfolioGlobalSlot:
    """One synchronous reservation shared by all strategy instances."""

    owner_instrument_id: str | None = None
    owner_scenario_id: str | None = None
    reserved_at_ns: int | None = None
    nav_series: list[dict[str, Any]] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    reservation_history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_free(self) -> bool:
        return self.owner_instrument_id is None

    def reserve(
        self,
        *,
        instrument_id: str,
        scenario_id: str,
        timestamp_ns: int,
    ) -> bool:
        if timestamp_ns <= 0:
            raise ValueError("timestamp_ns must be positive")
        if not instrument_id or not scenario_id:
            raise ValueError("instrument_id and scenario_id must be non-empty")
        if not self.is_free:
            return False
        self.owner_instrument_id = instrument_id
        self.owner_scenario_id = scenario_id
        self.reserved_at_ns = int(timestamp_ns)
        self.reservation_history.append(
            {
                "action": "RESERVE",
                "instrument_id": instrument_id,
                "scenario_id": scenario_id,
                "timestamp_ns": int(timestamp_ns),
            }
        )
        return True

    def release(
        self,
        *,
        instrument_id: str,
        timestamp_ns: int,
        reason: str,
    ) -> bool:
        if self.owner_instrument_id != instrument_id:
            return False
        self.reservation_history.append(
            {
                "action": "RELEASE",
                "instrument_id": instrument_id,
                "scenario_id": self.owner_scenario_id,
                "timestamp_ns": int(timestamp_ns),
                "reason": reason,
                "reserved_at_ns": self.reserved_at_ns,
            }
        )
        self.owner_instrument_id = None
        self.owner_scenario_id = None
        self.reserved_at_ns = None
        return True

    def record_nav(
        self,
        *,
        timestamp_ns: int,
        nav: float,
        observer_instrument_id: str,
    ) -> None:
        if timestamp_ns <= 0 or nav <= 0.0:
            return
        point = {
            "timestamp_ns": int(timestamp_ns),
            "nav": float(nav),
            "observer_instrument_id": observer_instrument_id,
        }
        if self.nav_series and self.nav_series[-1]["timestamp_ns"] == int(timestamp_ns):
            self.nav_series[-1] = point
        else:
            self.nav_series.append(point)

    def record_trades(
        self,
        *,
        instrument_id: str,
        new_trades: Iterable[dict[str, Any]],
    ) -> None:
        for raw in new_trades:
            item = dict(raw)
            item["instrument_id"] = instrument_id
            self.trades.append(item)


_GLOBAL_SLOT = PortfolioGlobalSlot()


def reset_portfolio_global_slot() -> PortfolioGlobalSlot:
    """Reset process-local state before constructing one BacktestEngine run."""
    global _GLOBAL_SLOT
    _GLOBAL_SLOT = PortfolioGlobalSlot()
    return _GLOBAL_SLOT


def portfolio_global_slot() -> PortfolioGlobalSlot:
    return _GLOBAL_SLOT


class Candidate07PortfolioSlotStrategy(Candidate07CostViableMITStrategy):
    """Exact per-instrument execution with a single portfolio-wide slot."""

    def on_data(self, data: Any) -> None:
        payload = data.data if isinstance(data, CustomData) else data
        if not isinstance(payload, CausalTradeSignal):
            return
        if payload.instrument_id != self.config.instrument_id:
            return

        now = int(payload.ts_event)
        self._record_nav(now)
        in_window = (
            self.config.trade_start_ns
            <= payload.observed_time_ns
            < self.config.trade_end_ns
        )
        local_flat = self.portfolio.is_flat(self.config.instrument_id)
        slot = portfolio_global_slot()
        eligible = (
            in_window
            and local_flat
            and self._active_signal is None
            and not self._exit_pending
            and slot.is_free
        )
        if not eligible:
            self._diagnostics.append(
                {
                    "scenario_id": payload.scenario_id,
                    "reason": "PORTFOLIO_GLOBAL_SLOT_OR_WINDOW_INELIGIBLE",
                    "ts_event_ns": now,
                    "in_window": in_window,
                    "local_portfolio_flat": local_flat,
                    "local_active_signal": (
                        None
                        if self._active_signal is None
                        else self._active_signal.scenario_id
                    ),
                    "global_owner_instrument_id": slot.owner_instrument_id,
                    "global_owner_scenario_id": slot.owner_scenario_id,
                    "global_reserved_at_ns": slot.reserved_at_ns,
                }
            )
            slot.diagnostics.append(dict(self._diagnostics[-1]))
            return
        if self._last_bar is None:
            item = {
                "scenario_id": payload.scenario_id,
                "reason": "NO_COMPLETED_EXECUTION_BAR",
                "ts_event_ns": now,
                "instrument_id": str(self.config.instrument_id),
            }
            self._diagnostics.append(item)
            slot.diagnostics.append(dict(item))
            return

        instrument_id = str(self.config.instrument_id)
        if not slot.reserve(
            instrument_id=instrument_id,
            scenario_id=payload.scenario_id,
            timestamp_ns=now,
        ):
            raise RuntimeError("portfolio slot changed during synchronous reservation")

        self._submit_signal(payload, self._last_bar)
        # Geometry/cost rejection creates no order and therefore no later order
        # callback. Release immediately only when the inherited strategy did not
        # retain an active signal and the instrument is still flat.
        if self._active_signal is None and self.portfolio.is_flat(
            self.config.instrument_id
        ):
            slot.release(
                instrument_id=instrument_id,
                timestamp_ns=now,
                reason="SUBMISSION_DECLINED_BEFORE_ORDER",
            )

    def on_position_closed(self, event: PositionClosed) -> None:
        if event.instrument_id != self.config.instrument_id:
            return
        before = len(self._trades)
        super().on_position_closed(event)
        slot = portfolio_global_slot()
        slot.record_trades(
            instrument_id=str(self.config.instrument_id),
            new_trades=self._trades[before:],
        )
        slot.release(
            instrument_id=str(self.config.instrument_id),
            timestamp_ns=int(event.ts_event),
            reason="POSITION_CLOSED",
        )

    def on_order_rejected(self, event: OrderRejected) -> None:
        if event.instrument_id != self.config.instrument_id:
            return
        super().on_order_rejected(event)
        if self._active_signal is None and self.portfolio.is_flat(
            self.config.instrument_id
        ):
            portfolio_global_slot().release(
                instrument_id=str(self.config.instrument_id),
                timestamp_ns=int(event.ts_event),
                reason="ORDER_REJECTED",
            )

    def _record_nav(self, timestamp_ns: int) -> None:
        super()._record_nav(timestamp_ns)
        portfolio_global_slot().record_nav(
            timestamp_ns=int(timestamp_ns),
            nav=float(self._last_nav),
            observer_instrument_id=str(self.config.instrument_id),
        )


class PortfolioStrategyEvidence:
    """Read-only aggregate view consumed by the existing metrics function."""

    def __init__(
        self,
        strategies: Iterable[Candidate07PortfolioSlotStrategy],
    ) -> None:
        self._strategies = tuple(strategies)

    @property
    def nav_series(self) -> tuple[dict[str, Any], ...]:
        return tuple(portfolio_global_slot().nav_series)

    @property
    def trade_diagnostics(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            sorted(
                portfolio_global_slot().trades,
                key=lambda item: (
                    int(item.get("opened_ns") or 0),
                    str(item.get("instrument_id") or ""),
                    str(item.get("scenario_id") or ""),
                ),
            )
        )

    @property
    def research_events(self) -> tuple[Any, ...]:
        events = [
            event
            for strategy in self._strategies
            for event in strategy.research_events
        ]
        return tuple(
            sorted(
                events,
                key=lambda item: (
                    int(item.event_time_ns),
                    str(item.instrument_id),
                    str(item.scenario_id),
                ),
            )
        )

    @property
    def scenario_diagnostics(self) -> tuple[dict[str, Any], ...]:
        items = [
            {
                **dict(item),
                "instrument_id": str(strategy.config.instrument_id),
            }
            for strategy in self._strategies
            for item in strategy.scenario_diagnostics
        ]
        return tuple(items)


__all__ = [
    "Candidate07PortfolioSlotStrategy",
    "PortfolioGlobalSlot",
    "PortfolioStrategyEvidence",
    "portfolio_global_slot",
    "reset_portfolio_global_slot",
]
