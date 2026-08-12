"""Source-backed one-percent daily loss governor for EasyChart v11.

The supplied trading material repeatedly describes one percent as the total
loss allowed for a day: on a 10,000 account, lose 100 and stop. Software must
translate that human discipline into an account-level budget, not merely set a
one-percent quantity for every trade and continue indefinitely.

The governor uses UTC calendar days because the four-symbol crypto account is
continuous and the historical data are UTC. It clips each new plan's complete
estimated loss budget to the unused part of the day's one-percent starting-NAV
allowance. Realized losing trades consume the allowance; winning trades do not
restore or enlarge it. Existing positions retain their native stop, target,
opposing-structure exit, and 24-hour terminal protection.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, ROUND_DOWN
from typing import Any

from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce, TriggerType
from nautilus_trader.model.events import PositionClosed
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.orders.list import OrderList

from domain import Side
from mtf_strategy_day_v7 import EasyChartDayTradeStrategy
from mtf_strategy_exit_v9 import OpposingOrderBlockExitStrategy


DAILY_LOSS_CAP_FRACTION = Decimal("0.01")
DAILY_RISK_PROVENANCE = (
    "SOURCE_EXPLICIT:TOTAL_DAILY_ACCOUNT_LOSS_IS_LIMITED_TO_ONE_PERCENT"
)
DAILY_BOUNDARY_TRANSLATION = (
    "SOURCE_AMBIGUITY_TRANSLATION:CONTINUOUS_CRYPTO_DAY_USES_UTC_CALENDAR_DATE"
)


def unused_daily_loss_budget(
    day_start_nav: Decimal,
    cumulative_realized_losses: Decimal,
    per_trade_budget: Decimal,
    cap_fraction: Decimal = DAILY_LOSS_CAP_FRACTION,
) -> Decimal:
    """Return unused gross-loss capacity; gains never offset earlier losses."""
    if day_start_nav <= 0:
        raise ValueError("day-start NAV must be positive")
    if cumulative_realized_losses < 0:
        raise ValueError("cumulative realized losses cannot be negative")
    if per_trade_budget < 0:
        raise ValueError("per-trade budget cannot be negative")
    if not Decimal("0") < cap_fraction < Decimal("1"):
        raise ValueError("daily cap fraction must lie between zero and one")
    cap = day_start_nav * cap_fraction
    remaining = max(Decimal("0"), cap - cumulative_realized_losses)
    return min(per_trade_budget, remaining)


def realized_loss_amount(realized_pnl: Any) -> Decimal:
    """Extract the non-negative loss amount from a Nautilus Money value."""
    if realized_pnl is None:
        return Decimal("0")
    amount = Decimal(str(realized_pnl.as_double()))
    return max(Decimal("0"), -amount)


class DailyRiskGovernorMixin:
    """Clip and stop new entries at the source-explicit daily loss budget."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._daily_risk_date = None
        self._daily_start_nav: Decimal | None = None
        self._daily_realized_losses = Decimal("0")

    @staticmethod
    def _utc_date(time_ns: int):  # type: ignore[no-untyped-def]
        if time_ns < 0:
            raise ValueError("event timestamp cannot be negative")
        return datetime.fromtimestamp(time_ns / 1_000_000_000, tz=UTC).date()

    def _ensure_daily_session(self, time_ns: int) -> None:
        day = self._utc_date(time_ns)
        if self._daily_risk_date == day and self._daily_start_nav is not None:
            return
        nav = self._current_nav()
        self._daily_risk_date = day
        self._daily_start_nav = nav
        self._daily_realized_losses = Decimal("0")
        self._record(
            "daily_risk_session_started",
            utc_date=day.isoformat(),
            day_start_nav=float(nav),
            daily_loss_cap_fraction=float(DAILY_LOSS_CAP_FRACTION),
            daily_loss_cap_amount=float(nav * DAILY_LOSS_CAP_FRACTION),
            cumulative_realized_losses=0.0,
            provenance=DAILY_RISK_PROVENANCE,
            boundary_translation=DAILY_BOUNDARY_TRANSLATION,
        )

    def _remaining_daily_budget(self, nav: Decimal) -> Decimal:
        if self._daily_start_nav is None:
            raise RuntimeError("daily risk session is not initialized")
        return unused_daily_loss_budget(
            self._daily_start_nav,
            self._daily_realized_losses,
            nav * Decimal(str(self.config.risk_fraction)),
        )

    def _flush_bar_bucket(self) -> None:
        if self.bar_bucket_ts is not None and self.bar_bucket_ts >= self.config.trading_start_ns:
            self._ensure_daily_session(self.bar_bucket_ts)
        super()._flush_bar_bucket()

    def _quantity_for_budget(
        self,
        instrument: Any,
        plan: Any,
        budget: Decimal,
    ) -> Any | None:
        entry = Decimal(str(plan.entry))
        stop = Decimal(str(plan.stop))
        entry_slippage, stop_slippage = self._execution_reserves(instrument)
        per_unit = abs(entry - stop)
        per_unit += entry_slippage + stop_slippage
        per_unit += entry * Decimal(str(self.config.estimated_entry_fee_rate))
        per_unit += stop * Decimal(str(self.config.estimated_stop_fee_rate))
        per_unit += entry * Decimal(str(self.config.estimated_funding_rate))
        if per_unit <= 0 or budget <= 0:
            return None
        raw = budget / per_unit
        step = Decimal(str(instrument.size_increment))
        floored = (raw / step).to_integral_value(rounding=ROUND_DOWN) * step
        minimum = Decimal(str(instrument.min_quantity))
        maximum = Decimal(str(instrument.max_quantity)) if instrument.max_quantity is not None else None
        if floored <= 0 or floored < minimum:
            return None
        if maximum is not None and floored > maximum:
            return None
        return instrument.make_qty(floored)

    def _submit_plan(self, instrument_id: InstrumentId, plan: Any) -> bool:
        self._ensure_daily_session(plan.observed_time_ns)
        instrument = self.instruments[instrument_id]
        nav = self._current_nav()
        budget = self._remaining_daily_budget(nav)
        if budget <= 0:
            self._record(
                "plan_rejected_daily_loss_cap",
                plan_id=plan.plan_id,
                instrument_id=str(instrument_id),
                nav_at_submission=float(nav),
                day_start_nav=float(self._daily_start_nav),
                cumulative_realized_losses=float(self._daily_realized_losses),
                daily_loss_cap_fraction=float(DAILY_LOSS_CAP_FRACTION),
                provenance=DAILY_RISK_PROVENANCE,
            )
            return False

        entry_slippage, stop_slippage = self._execution_reserves(instrument)
        quantity = self._quantity_for_budget(instrument, plan, budget)
        if quantity is None:
            self._record(
                "plan_rejected_daily_budget_quantity",
                plan_id=plan.plan_id,
                instrument_id=str(instrument_id),
                nav_at_submission=float(nav),
                day_start_nav=float(self._daily_start_nav),
                cumulative_realized_losses=float(self._daily_realized_losses),
                remaining_daily_risk_budget=float(budget),
                estimated_entry_slippage=float(entry_slippage),
                estimated_stop_slippage=float(stop_slippage),
                provenance=DAILY_RISK_PROVENANCE,
            )
            return False

        plan_tag = f"PLAN:{plan.plan_id}"
        order_list: OrderList = self.order_factory.bracket(
            instrument_id=instrument_id,
            order_side=OrderSide.BUY if plan.side is Side.LONG else OrderSide.SELL,
            quantity=quantity,
            time_in_force=TimeInForce.GTC,
            sl_trigger_price=instrument.make_price(plan.stop),
            tp_price=instrument.make_price(plan.target),
            entry_order_type=OrderType.MARKET,
            entry_post_only=False,
            tp_post_only=False,
            emulation_trigger=TriggerType.NO_TRIGGER,
            entry_tags=[plan_tag, "ROLE:ENTRY"],
            sl_tags=[plan_tag, "ROLE:STOP_LOSS"],
            tp_tags=[plan_tag, "ROLE:TAKE_PROFIT"],
        )
        self.active_plan = plan
        self.active_instrument_id = instrument_id
        self.active_entry_id = order_list.first.client_order_id
        self.entry_cancel_requested = False
        self.emergency_exit_requested = False
        self.submit_order_list(order_list)
        self._record(
            "submitted",
            plan_id=plan.plan_id,
            instrument_id=str(instrument_id),
            quantity=str(quantity),
            entry_client_order_id=str(self.active_entry_id),
            nav_at_submission=float(nav),
            risk_budget=float(budget),
            configured_per_trade_risk_fraction=float(self.config.risk_fraction),
            daily_loss_cap_fraction=float(DAILY_LOSS_CAP_FRACTION),
            day_start_nav=float(self._daily_start_nav),
            cumulative_realized_losses=float(self._daily_realized_losses),
            remaining_daily_risk_budget_before_submission=float(budget),
            estimated_entry_slippage=float(entry_slippage),
            estimated_stop_slippage=float(stop_slippage),
            daily_risk_provenance=DAILY_RISK_PROVENANCE,
            daily_boundary_translation=DAILY_BOUNDARY_TRANSLATION,
        )
        return True

    def on_position_closed(self, event: PositionClosed) -> None:
        self._ensure_daily_session(event.ts_event)
        loss = realized_loss_amount(event.realized_pnl)
        self._daily_realized_losses += loss
        self._record(
            "daily_realized_loss_updated",
            instrument_id=str(event.instrument_id),
            position_id=str(event.position_id),
            utc_date=self._daily_risk_date.isoformat(),
            closed_trade_loss=float(loss),
            cumulative_realized_losses=float(self._daily_realized_losses),
            day_start_nav=float(self._daily_start_nav),
            daily_loss_cap_amount=float(self._daily_start_nav * DAILY_LOSS_CAP_FRACTION),
            provenance=DAILY_RISK_PROVENANCE,
        )
        super().on_position_closed(event)


class DailyRiskDayTradeStrategy(DailyRiskGovernorMixin, EasyChartDayTradeStrategy):
    pass


class DailyRiskOpposingOrderBlockExitStrategy(
    DailyRiskGovernorMixin,
    OpposingOrderBlockExitStrategy,
):
    pass
