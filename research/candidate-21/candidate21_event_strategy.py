"""Candidate 21 event-time strategy on native 10-second external bars.

This is a structural redesign, not a relaxed version of the completed-minute
router.  The first 10 seconds of a quarter-hour define a parent event and the
immediately following 10 seconds define its only response.  Entry therefore
occurs near second 20, while the same auction leg and natural objective still
exist.

NautilusTrader remains the sole order, fill, position, fee, margin, liquidation,
portfolio and NAV engine.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import math

from nautilus_trader.model.data import Bar
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce

from candidate21_response_strategy import Candidate21ResponseConfig
from candidate21_response_strategy import Candidate21ResponseStrategy
from event_time_router import EventDecision
from event_time_router import TenSecondEvent
from event_time_router import TenSecondResponse
from event_time_router import classify_response
from logic import floor_quantity
from logic import net_r_at_price
from logic import planned_loss_per_unit
from strategy_base import PendingSetup
from strategy_base import _as_float


class Candidate21EventConfig(Candidate21ResponseConfig, frozen=True):
    event_atr_period_bars: int = 180
    event_balance_bars: int = 90
    event_max_hold_bars: int = 1080


class Candidate21EventStrategy(Candidate21ResponseStrategy):
    """Resolve quarter-hour opening auctions from the next 10-second bar."""

    def __init__(self, config: Candidate21EventConfig) -> None:
        super().__init__(config=config)
        if config.event_atr_period_bars < 2:
            raise ValueError("event_atr_period_bars must be at least two")
        if config.event_balance_bars < 2:
            raise ValueError("event_balance_bars must be at least two")
        if config.event_max_hold_bars < 1:
            raise ValueError("event_max_hold_bars must be positive")
        self.event_state: TenSecondEvent | None = None
        self.diagnostics.update(
            {
                "candidate21_event_boundaries_seen": 0,
                "candidate21_event_attacks_armed": 0,
                "candidate21_event_attacks_rejected": 0,
                "candidate21_event_acceptance": 0,
                "candidate21_event_failed_auction": 0,
                "candidate21_event_unresolved": 0,
                "candidate21_event_target_consumed": 0,
                "candidate21_event_geometry_rejected": 0,
                "candidate21_event_market_entries": 0,
            },
        )

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self.event_state = None

    def _expire_pending(
        self,
        row: dict[str, float | int],
        reason: str,
    ) -> None:
        event_pending = (
            self.pending is not None
            and self.pending.branch == "EVENT_TIME"
        )
        super()._expire_pending(row, reason)
        if event_pending:
            self.event_state = None

    def _atr(self) -> float:
        rows = list(self.bars)
        period = int(self.config.event_atr_period_bars)
        if len(rows) < period + 1:
            return float("nan")
        selected = rows[-(period + 1):]
        values: list[float] = []
        for previous, current in zip(selected, selected[1:]):
            values.append(
                max(
                    float(current["high"]) - float(current["low"]),
                    abs(
                        float(current["high"])
                        - float(previous["close"])
                    ),
                    abs(
                        float(current["low"])
                        - float(previous["close"])
                    ),
                ),
            )
        return sum(values[-period:]) / period

    def on_bar(self, bar: Bar) -> None:
        """Run only the 10-second event-time state machine."""
        self.bar_index += 1
        row = {
            "ts": int(bar.ts_event),
            "open": _as_float(bar.open),
            "high": _as_float(bar.high),
            "low": _as_float(bar.low),
            "close": _as_float(bar.close),
            "volume": _as_float(bar.volume),
        }
        self.bars.append(row)
        self._advance_features(int(row["ts"]))
        self._record_equity(int(row["ts"]))

        if not self.portfolio.is_flat(self.config.instrument_id):
            self.diagnostics["max_open_positions_observed"] = max(
                int(self.diagnostics["max_open_positions_observed"]),
                1,
            )
            self._manage_open_position(row)
            return

        if self.entry_pending:
            self.diagnostics["max_simultaneous_entry_intents"] = max(
                int(self.diagnostics["max_simultaneous_entry_intents"]),
                1,
            )
            if self.bar_index - self.entry_pending_index > 2:
                self.cancel_all_orders(self.config.instrument_id)
                if self.current_scenario_id is not None:
                    self._transition(
                        self.current_scenario_id,
                        "ENTRY_EXPIRED",
                        int(row["ts"]),
                        int(row["ts"]),
                        "CLOSED",
                        "MARKET_ENTRY_NOT_FILLED_WITHIN_TWO_10S_BARS",
                        float(row["close"]),
                        {},
                    )
                self._clear_trade_state()
            return

        if not self._in_evaluation(int(row["ts"])):
            self.pending = None
            self.event_state = None
            return
        if self._funding_blackout(int(row["ts"])):
            self._expire_pending(row, "FUNDING_BLACKOUT")
            return
        if not self._features_ready(int(row["ts"])):
            return
        minimum = max(
            int(self.config.event_atr_period_bars) + 1,
            int(self.config.event_balance_bars) + 1,
        )
        if len(self.bars) < minimum:
            return

        if self.pending is not None and self.pending.branch == "EVENT_TIME":
            self._process_event_response(row)
            return

        cooldown = int(self.config.cooldown_bars) * 6
        if (
            self.pending is None
            and self.bar_index - self.last_entry_index >= cooldown
        ):
            self._detect_event(row)

    def _detect_event(self, row: dict[str, float | int]) -> None:
        if self._feature("event_boundary") < 0.5:
            return
        self.diagnostics["candidate21_event_boundaries_seen"] = int(
            self.diagnostics["candidate21_event_boundaries_seen"],
        ) + 1
        if self._feature("event_feature_ready") < 0.5:
            return

        phase_burst = self._feature("event_phase_burst")
        event_flow = self._feature("flow_10s")
        if (
            not math.isfinite(phase_burst)
            or phase_burst < self.config.clock_min_phase_burst
            or not math.isfinite(event_flow)
            or abs(event_flow) < self.config.clock_min_open_flow
        ):
            return

        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return
        count = int(self.config.event_balance_bars)
        rows = list(self.bars)
        prior = rows[-(count + 1):-1]
        if len(prior) != count:
            return
        prior_high = max(float(item["high"]) for item in prior)
        prior_low = min(float(item["low"]) for item in prior)
        balance_width = prior_high - prior_low
        if (
            not math.isfinite(balance_width)
            or balance_width <= 0.0
            or prior_low <= 0.0
        ):
            return

        high_crossed = (
            float(row["high"])
            >= prior_high
            + self.config.clock_min_penetration_atr * atr
        )
        low_crossed = (
            float(row["low"])
            <= prior_low
            - self.config.clock_min_penetration_atr * atr
        )
        if high_crossed and low_crossed:
            self.diagnostics["candidate21_event_attacks_rejected"] = int(
                self.diagnostics["candidate21_event_attacks_rejected"],
            ) + 1
            return
        if high_crossed and event_flow > 0.0:
            direction = 1
            boundary = prior_high
            opposite = prior_low
            acceptance_target = prior_high + balance_width
            kind = "HIGH"
            extreme = float(row["high"])
        elif low_crossed and event_flow < 0.0:
            direction = -1
            boundary = prior_low
            opposite = prior_high
            acceptance_target = prior_low - balance_width
            kind = "LOW"
            extreme = float(row["low"])
        else:
            return
        if acceptance_target <= 0.0:
            return

        self.scenario_counter += 1
        scenario_id = f"c21-event-{self.scenario_counter:07d}"
        event = TenSecondEvent(
            scenario_id=scenario_id,
            direction=direction,
            event_index=self.bar_index,
            boundary_level=boundary,
            range_opposite=opposite,
            acceptance_target=acceptance_target,
            rejection_target=opposite,
            atr=atr,
            event_open=float(row["open"]),
            event_high=float(row["high"]),
            event_low=float(row["low"]),
            event_close=float(row["close"]),
            event_flow=event_flow,
            event_notional=self._feature("notional"),
            phase_burst=phase_burst,
        )
        details = {
            "candidate21_parent": (
                "FIRST_10_SECONDS_OF_QUARTER_HOUR_BALANCE_ATTACK"
            ),
            "event_timeframe_seconds": 10,
            "event_index": self.bar_index,
            "event_observed_time_ns": int(row["ts"]),
            "prior_balance_bars": count,
            "prior_balance_high": prior_high,
            "prior_balance_low": prior_low,
            "prior_balance_width": balance_width,
            "event_phase_sample_count": self._feature(
                "event_phase_sample_count",
            ),
            "event_notional_baseline": self._feature(
                "event_notional_baseline",
            ),
            "event_phase_burst": phase_burst,
            "event_flow": event_flow,
            "boundary_level": boundary,
            "acceptance_target": acceptance_target,
            "rejection_target": opposite,
            "event": {
                **asdict(event),
                "decision": event.decision.value,
            },
        }
        self.event_state = event
        self.pending = PendingSetup(
            scenario_id=scenario_id,
            branch="EVENT_TIME",
            side=0,
            swept_kind=kind,
            pool_id=f"event-{scenario_id}",
            pool_level=boundary,
            created_index=self.bar_index,
            expires_index=self.bar_index + 1,
            sweep_extreme=extreme,
            structure=opposite,
            atr=atr,
            hold_count=0,
            retrace_armed=False,
            details=details,
        )
        self.diagnostics["candidate21_event_attacks_armed"] = int(
            self.diagnostics["candidate21_event_attacks_armed"],
        ) + 1
        self._transition(
            scenario_id,
            "EVENT_TIME_PARENT_OPENED",
            int(row["ts"]),
            int(row["ts"]),
            "IMMEDIATE_RESPONSE_WAITING",
            "OPENING_ATTACK_IS_PARENT_EVENT_NOT_ENTRY",
            boundary,
            details,
        )

    def _process_event_response(
        self,
        row: dict[str, float | int],
    ) -> None:
        setup = self.pending
        event = self.event_state
        if setup is None or event is None:
            self._expire_pending(row, "MISSING_EVENT_TIME_STATE")
            return
        expected_ts = int(setup.details["event_observed_time_ns"]) + 10_000_000_000
        if (
            self.bar_index != event.event_index + 1
            or int(row["ts"]) != expected_ts
        ):
            self.diagnostics["candidate21_event_unresolved"] = int(
                self.diagnostics["candidate21_event_unresolved"],
            ) + 1
            self._expire_pending(row, "IMMEDIATE_RESPONSE_WINDOW_MISSED")
            return

        response = TenSecondResponse(
            bar_index=self.bar_index,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            flow=self._feature("flow_10s"),
            return_bps=self._feature("return_10s_bps"),
            efficiency=self._feature("efficiency_10s"),
        )
        decision, reason, response_details = classify_response(
            event,
            response,
        )
        setup.details["event_time_response"] = response_details
        setup.details["event_time_decision"] = decision.value
        self._transition(
            setup.scenario_id,
            "EVENT_TIME_RESPONSE_CLASSIFIED",
            int(row["ts"]),
            int(row["ts"]),
            decision.value,
            reason,
            float(row["close"]),
            setup.details,
        )

        if decision is EventDecision.INVALIDATED:
            self.diagnostics["candidate21_event_target_consumed"] = int(
                self.diagnostics["candidate21_event_target_consumed"],
            ) + 1
            self.pending = None
            self.event_state = None
            return
        if decision is EventDecision.UNRESOLVED:
            self.diagnostics["candidate21_event_unresolved"] = int(
                self.diagnostics["candidate21_event_unresolved"],
            ) + 1
            self.pending = None
            self.event_state = None
            return

        if decision is EventDecision.ACCEPTANCE:
            if not self.config.enable_acceptance:
                self.pending = None
                self.event_state = None
                return
            branch = "ACCEPTANCE"
            side = event.direction
            target = event.acceptance_target
            target_source = "PRIOR_15M_BALANCE_MEASURED_EXPANSION"
            self.diagnostics["candidate21_event_acceptance"] = int(
                self.diagnostics["candidate21_event_acceptance"],
            ) + 1
        else:
            if not self.config.enable_rejection:
                self.pending = None
                self.event_state = None
                return
            branch = "REJECTION"
            side = -event.direction
            target = event.rejection_target
            target_source = "PRIOR_15M_BALANCE_OPPOSITE_EDGE"
            self.diagnostics["candidate21_event_failed_auction"] = int(
                self.diagnostics["candidate21_event_failed_auction"],
            ) + 1

        completed = PendingSetup(
            scenario_id=setup.scenario_id,
            branch=branch,
            side=side,
            swept_kind=setup.swept_kind,
            pool_id=setup.pool_id,
            pool_level=event.boundary_level,
            created_index=self.bar_index,
            expires_index=self.bar_index,
            sweep_extreme=setup.sweep_extreme,
            structure=setup.structure,
            atr=event.atr,
            hold_count=0,
            retrace_armed=False,
            details={
                **setup.details,
                "candidate21_branch": decision.value,
                "natural_target": target,
                "natural_target_source": target_source,
                "event_time_entry_after_seconds": 20,
            },
        )
        self.pending = completed
        self.event_state = event
        self._transition(
            completed.scenario_id,
            "EVENT_TIME_AUCTION_COMPLETED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_EVALUATION",
            reason,
            float(row["close"]),
            completed.details,
        )
        self._submit_event_entry(completed, row)

    def _submit_event_entry(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
    ) -> bool:
        event = self.event_state
        if event is None:
            self._expire_pending(row, "MISSING_EVENT_FOR_ENTRY")
            return False
        side = setup.side
        entry_estimate = float(row["close"])
        buffer = self.config.stop_buffer_atr * event.atr
        if setup.branch == "ACCEPTANCE":
            anchor = (
                min(event.boundary_level, event.event_low)
                if side > 0
                else max(event.boundary_level, event.event_high)
            )
        else:
            anchor = (
                event.event_low
                if side > 0
                else event.event_high
            )
        stop = anchor - side * buffer
        target = float(setup.details["natural_target"])

        if side > 0 and not (0.0 < stop < entry_estimate < target):
            self.diagnostics["candidate21_event_geometry_rejected"] = int(
                self.diagnostics["candidate21_event_geometry_rejected"],
            ) + 1
            self._expire_pending(row, "INVALID_LONG_EVENT_GEOMETRY")
            return False
        if side < 0 and not (0.0 < target < entry_estimate < stop):
            self.diagnostics["candidate21_event_geometry_rejected"] = int(
                self.diagnostics["candidate21_event_geometry_rejected"],
            ) + 1
            self._expire_pending(row, "INVALID_SHORT_EVENT_GEOMETRY")
            return False

        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        adverse_slippage_rate = (
            self.config.adverse_slippage_bps_each_side / 10_000.0
        )
        planned_loss = planned_loss_per_unit(
            entry_estimate,
            stop,
            side,
            cost_rate,
            adverse_slippage_rate,
        )
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self.diagnostics["candidate21_event_geometry_rejected"] = int(
                self.diagnostics["candidate21_event_geometry_rejected"],
            ) + 1
            self._expire_pending(row, "INVALID_EVENT_PLANNED_LOSS")
            return False
        target_net_r = net_r_at_price(
            entry_estimate,
            target,
            side,
            planned_loss,
            cost_rate,
        )
        if target_net_r < self.config.min_target_net_r:
            self.diagnostics["candidate21_event_geometry_rejected"] = int(
                self.diagnostics["candidate21_event_geometry_rejected"],
            ) + 1
            self._expire_pending(row, "EVENT_TARGET_BELOW_MINIMUM_NET_R")
            return False

        equity = self._equity_value()
        risk_budget = equity * self.config.risk_fraction
        quantity_value = floor_quantity(
            risk_budget / planned_loss,
            int(self.instrument.size_precision),
        )
        if (
            quantity_value <= 0.0
            or quantity_value * entry_estimate < 10.0
        ):
            self._expire_pending(row, "QUANTITY_BELOW_INSTRUMENT_MINIMUM")
            return False

        order_side = OrderSide.BUY if side > 0 else OrderSide.SELL
        order_list = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=order_side,
            quantity=self.instrument.make_qty(quantity_value),
            time_in_force=TimeInForce.GTC,
            tp_price=self.instrument.make_price(target),
            sl_trigger_price=self.instrument.make_price(stop),
        )
        self.submit_order_list(order_list)
        self.entry_pending = True
        self.entry_pending_index = self.bar_index
        self.last_entry_index = self.bar_index
        self.current_scenario_id = setup.scenario_id
        self.current_branch = setup.branch
        self.current_pool_level = setup.pool_level
        self.pending = None
        self.event_state = None
        self.diagnostics["entry_submissions"] = int(
            self.diagnostics["entry_submissions"],
        ) + 1
        self.diagnostics["candidate21_event_market_entries"] = int(
            self.diagnostics["candidate21_event_market_entries"],
        ) + 1
        self.diagnostics["max_simultaneous_entry_intents"] = max(
            int(self.diagnostics["max_simultaneous_entry_intents"]),
            1,
        )
        self._transition(
            setup.scenario_id,
            "ENTRY_SUBMITTED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_PENDING",
            "IMMEDIATE_10S_RESPONSE_MARKET_BRACKET",
            entry_estimate,
            {
                **setup.details,
                "branch": setup.branch,
                "side": side,
                "entry_estimate": entry_estimate,
                "entry_order_type": "MARKET",
                "stop": stop,
                "target": target,
                "target_net_r": target_net_r,
                "quantity": quantity_value,
                "equity": equity,
                "risk_budget": risk_budget,
                "planned_loss_per_unit": planned_loss,
                "planned_account_loss": quantity_value * planned_loss,
            },
        )
        return True

    def _manage_open_position(
        self,
        row: dict[str, float | int],
    ) -> None:
        moment = datetime.fromtimestamp(
            int(row["ts"]) / 1_000_000_000,
            tz=timezone.utc,
        )
        before_funding = (
            moment.hour in (7, 15, 23)
            and moment.minute >= self.config.funding_flatten_minute
        )
        timed_out = (
            self.position_open_index >= 0
            and self.bar_index - self.position_open_index
            >= self.config.event_max_hold_bars
        )
        evaluation_ended = int(row["ts"]) >= self.config.evaluation_end_ns
        if before_funding or timed_out or evaluation_ended:
            self.cancel_all_orders(self.config.instrument_id)
            self.close_all_positions(self.config.instrument_id)
            if self.current_scenario_id is not None:
                self._transition(
                    self.current_scenario_id,
                    "FORCED_DAYTRADE_EXIT",
                    int(row["ts"]),
                    int(row["ts"]),
                    "EXIT_PENDING",
                    "FUNDING_OR_EVENT_HOLD_OR_EVALUATION_BOUNDARY",
                    float(row["close"]),
                    {
                        "before_funding": before_funding,
                        "timed_out": timed_out,
                        "evaluation_ended": evaluation_ended,
                    },
                )


__all__ = ["Candidate21EventConfig", "Candidate21EventStrategy"]
