"""Candidate 21: quarter-hour flow-conditioned auction state router.

The alpha is independent from Candidate 19.  Candidate 18 contributes only its
validated NautilusTrader order lifecycle conventions; this module replaces the
parent event, state classification, invalidation, and target policy.

Scenario:
    prior 15-minute balance
    -> boundary first-10-second flow burst attacks one balance edge
    -> strictly later price/flow/book response
       -> true acceptance and measured balance expansion
       -> failed auction and rotation to the opposite balance edge
       -> unresolved/no trade
    -> all-or-none price-capped FOK bracket sized to 3% current NAV risk
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import math
from typing import Any

from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import OrderType
from nautilus_trader.model.enums import TimeInForce

from fok_capped_strategy import Candidate18Config
from fok_capped_strategy import Candidate18Strategy as Candidate18FokStrategy
from logic import floor_quantity
from logic import net_r_at_price
from logic import planned_loss_per_unit
from quarter_hour_router import ClockAuction
from quarter_hour_router import ClockDecision
from quarter_hour_router import ClockObservation
from quarter_hour_router import ClockThresholds
from quarter_hour_router import advance_clock_auction
from strategy_base import PendingSetup


class Candidate21Config(Candidate18Config, frozen=True):
    clock_period_minutes: int = 15
    clock_baseline_periods: int = 96
    clock_min_baseline_samples: int = 32
    clock_min_phase_burst: float = 1.25
    clock_min_open_flow: float = 0.10
    clock_min_penetration_atr: float = 0.05
    clock_max_wait_bars: int = 3
    clock_acceptance_min_progress_atr: float = 0.18
    clock_acceptance_min_flow: float = 0.06
    clock_acceptance_min_efficiency: float = 0.30
    clock_acceptance_min_close_location: float = 0.56
    clock_failure_reentry_atr: float = 0.02
    clock_failure_max_event_efficiency: float = 0.45
    clock_failure_max_extension_atr: float = 0.30
    clock_failure_min_reverse_flow: float = 0.04
    clock_failure_min_reverse_efficiency: float = 0.20
    clock_failure_min_close_location: float = 0.56


class Candidate21Strategy(Candidate18FokStrategy):
    """Trade only resolved quarter-hour balance auctions."""

    def __init__(self, config: Candidate21Config) -> None:
        super().__init__(config=config)
        if config.clock_period_minutes < 1 or 60 % config.clock_period_minutes != 0:
            raise ValueError("clock_period_minutes must be a positive divisor of 60")
        if config.clock_baseline_periods < 1:
            raise ValueError("clock_baseline_periods must be positive")
        if not 1 <= config.clock_min_baseline_samples <= config.clock_baseline_periods:
            raise ValueError("clock baseline sample count is invalid")
        if config.clock_min_phase_burst <= 0.0:
            raise ValueError("clock_min_phase_burst must be positive")
        if not 0.0 < config.clock_min_open_flow <= 1.0:
            raise ValueError("clock_min_open_flow must be in (0, 1]")
        if config.clock_min_penetration_atr <= 0.0:
            raise ValueError("clock_min_penetration_atr must be positive")

        self.clock_thresholds = ClockThresholds(
            max_wait_bars=config.clock_max_wait_bars,
            acceptance_min_progress_atr=config.clock_acceptance_min_progress_atr,
            acceptance_min_flow=config.clock_acceptance_min_flow,
            acceptance_min_efficiency=config.clock_acceptance_min_efficiency,
            acceptance_min_close_location=config.clock_acceptance_min_close_location,
            failure_reentry_atr=config.clock_failure_reentry_atr,
            failure_max_event_efficiency=config.clock_failure_max_event_efficiency,
            failure_max_extension_atr=config.clock_failure_max_extension_atr,
            failure_min_reverse_flow=config.clock_failure_min_reverse_flow,
            failure_min_reverse_efficiency=config.clock_failure_min_reverse_efficiency,
            failure_min_close_location=config.clock_failure_min_close_location,
        )
        self.clock_auction: ClockAuction | None = None
        self.diagnostics.update(
            {
                "candidate21_clock_boundaries_seen": 0,
                "candidate21_clock_events_armed": 0,
                "candidate21_clock_events_rejected": 0,
                "candidate21_clock_observations": 0,
                "candidate21_acceptance_confirmed": 0,
                "candidate21_failed_auction_confirmed": 0,
                "candidate21_unresolved": 0,
                "candidate21_target_consumed_before_entry": 0,
                "candidate21_fok_entries": 0,
                "candidate21_natural_target_geometry_rejected": 0,
            },
        )

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self.clock_auction = None

    def _expire_pending(self, row: dict[str, float | int], reason: str) -> None:
        clock_pending = self.pending is not None and self.pending.branch == "CLOCK_AUCTION"
        super()._expire_pending(row, reason)
        if clock_pending:
            self.clock_auction = None

    @staticmethod
    def _clock_minute(ts_ns: int) -> int:
        return datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc).minute

    @staticmethod
    def _ahead_depth_field(direction: int) -> str:
        if direction not in (-1, 1):
            raise ValueError("direction must be -1 or +1")
        return "ask_depth_change_1_1m" if direction > 0 else "bid_depth_change_1_1m"

    def _clock_feature_ready(self) -> bool:
        if self._feature("qh_feature_ready") < 0.5:
            return False
        samples = self._feature("qh_phase_sample_count")
        return math.isfinite(samples) and samples >= self.config.clock_min_baseline_samples

    def _detect_sweep(self, row: dict[str, float | int], previous_close: float) -> None:
        """Replace inherited arbitrary-time sweeps with a quarter-hour balance event."""
        del previous_close
        if self.clock_auction is not None or self.pending is not None:
            return
        if self._clock_minute(int(row["ts"])) % self.config.clock_period_minutes != 0:
            return
        self.diagnostics["candidate21_clock_boundaries_seen"] = int(
            self.diagnostics["candidate21_clock_boundaries_seen"],
        ) + 1
        if not self._clock_feature_ready():
            return

        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return
        rows = list(self.bars)
        lookback = int(self.config.clock_period_minutes)
        if len(rows) < lookback + 1:
            return
        prior = rows[-(lookback + 1) : -1]
        prior_high = max(float(item["high"]) for item in prior)
        prior_low = min(float(item["low"]) for item in prior)
        prior_close = float(prior[-1]["close"])
        balance_width = prior_high - prior_low
        if balance_width <= 0.0 or not prior_low <= prior_close <= prior_high:
            return

        phase_burst = self._feature("qh_open_notional_burst")
        open_flow = self._feature("qh_open_flow")
        event_efficiency = self._feature("qh_open_impact_efficiency")
        if (
            not math.isfinite(phase_burst)
            or phase_burst < self.config.clock_min_phase_burst
            or not math.isfinite(open_flow)
            or abs(open_flow) < self.config.clock_min_open_flow
            or not math.isfinite(event_efficiency)
        ):
            return

        high_crossed = (
            float(row["high"])
            >= prior_high + self.config.clock_min_penetration_atr * atr
        )
        low_crossed = (
            float(row["low"])
            <= prior_low - self.config.clock_min_penetration_atr * atr
        )
        if high_crossed and low_crossed:
            self.diagnostics["candidate21_clock_events_rejected"] = int(
                self.diagnostics["candidate21_clock_events_rejected"],
            ) + 1
            return
        if high_crossed and open_flow > 0.0:
            direction = 1
            boundary = prior_high
            opposite = prior_low
            acceptance_target = prior_high + balance_width
            kind = "HIGH"
            event_extreme = float(row["high"])
        elif low_crossed and open_flow < 0.0:
            direction = -1
            boundary = prior_low
            opposite = prior_high
            acceptance_target = prior_low - balance_width
            kind = "LOW"
            event_extreme = float(row["low"])
        else:
            return
        if acceptance_target <= 0.0:
            return

        extension_atr = direction * (event_extreme - boundary) / atr
        self.scenario_counter += 1
        scenario_id = f"c21-qh-{self.scenario_counter:07d}"
        state = ClockAuction(
            scenario_id=scenario_id,
            direction=direction,
            boundary_index=self.bar_index,
            last_index=self.bar_index,
            expires_index=self.bar_index + self.config.clock_max_wait_bars,
            boundary_level=boundary,
            range_opposite=opposite,
            acceptance_target=acceptance_target,
            rejection_target=opposite,
            atr=atr,
            event_high=float(row["high"]),
            event_low=float(row["low"]),
            event_close=float(row["close"]),
            event_open_flow=open_flow,
            event_phase_burst=phase_burst,
            event_efficiency=event_efficiency,
            event_extension_atr=extension_atr,
            max_extension_atr=extension_atr,
        )
        details = {
            "candidate21_parent": "QUARTER_HOUR_BALANCE_EDGE_AUCTION",
            "clock_period_minutes": self.config.clock_period_minutes,
            "clock_phase_sample_count": self._feature("qh_phase_sample_count"),
            "clock_open_notional_baseline": self._feature("qh_open_notional_baseline"),
            "clock_open_notional_burst": phase_burst,
            "clock_open_flow": open_flow,
            "clock_open_return_bps": self._feature("qh_open_return_bps"),
            "clock_open_range_bps": self._feature("qh_open_range_bps"),
            "clock_open_impact_efficiency": event_efficiency,
            "prior_balance_high": prior_high,
            "prior_balance_low": prior_low,
            "prior_balance_width": balance_width,
            "parent_direction": direction,
            "boundary_level": boundary,
            "event_extension_atr": extension_atr,
            "acceptance_target": acceptance_target,
            "rejection_target": opposite,
            "interaction_bar_index": self.bar_index,
            "interaction_ts_event": int(row["ts"]),
        }
        self.clock_auction = state
        self.pending = PendingSetup(
            scenario_id=scenario_id,
            branch="CLOCK_AUCTION",
            side=0,
            swept_kind=kind,
            pool_id=f"clock-{scenario_id}",
            pool_level=boundary,
            created_index=self.bar_index,
            expires_index=state.expires_index,
            sweep_extreme=event_extreme,
            structure=opposite,
            atr=atr,
            hold_count=0,
            retrace_armed=False,
            details=details,
        )
        self.diagnostics["candidate21_clock_events_armed"] = int(
            self.diagnostics["candidate21_clock_events_armed"],
        ) + 1
        self._transition(
            scenario_id,
            "CLOCK_AUCTION_OPENED",
            int(row["ts"]),
            int(row["ts"]),
            "CLOCK_RESPONSE_WAITING",
            "BOUNDARY_FLOW_EVENT_IS_NOT_AN_ENTRY",
            boundary,
            details,
        )

    def _process_pending(self, row: dict[str, float | int]) -> bool:
        if self.pending is not None and self.pending.branch == "CLOCK_AUCTION":
            return self._process_clock_auction(row)
        return super()._process_pending(row)

    def _process_clock_auction(self, row: dict[str, float | int]) -> bool:
        setup = self.pending
        state = self.clock_auction
        if setup is None or state is None:
            self._expire_pending(row, "MISSING_CLOCK_AUCTION_STATE")
            return True
        if self.bar_index <= setup.created_index:
            return True

        if state.direction > 0:
            setup.sweep_extreme = max(setup.sweep_extreme, float(row["high"]))
        else:
            setup.sweep_extreme = min(setup.sweep_extreme, float(row["low"]))
        state = advance_clock_auction(
            state,
            ClockObservation(
                bar_index=self.bar_index,
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                flow_60s=self._feature("flow_60s"),
                ret_60s_bps=self._feature("ret_60s_bps"),
                efficiency_60s=self._feature("efficiency_60s"),
                depth_imbalance_1=self._feature("depth_imbalance_1"),
                liquidity_ahead_change_1m=self._feature(
                    self._ahead_depth_field(state.direction),
                ),
            ),
            self.clock_thresholds,
        )
        self.clock_auction = state
        terminal = asdict(state)
        terminal["decision"] = state.decision.value
        setup.details["latest_clock_router_state"] = terminal
        self.diagnostics["candidate21_clock_observations"] = int(
            self.diagnostics["candidate21_clock_observations"],
        ) + 1
        self._transition(
            setup.scenario_id,
            "CLOCK_AUCTION_OBSERVED",
            int(row["ts"]),
            int(row["ts"]),
            "CLOCK_RESPONSE_WAITING"
            if state.decision is ClockDecision.WAITING
            else state.decision.value,
            state.reason,
            float(row["close"]),
            setup.details,
        )
        if state.decision is ClockDecision.WAITING:
            return True
        if state.decision in (ClockDecision.INVALIDATED, ClockDecision.UNRESOLVED):
            if state.decision is ClockDecision.INVALIDATED:
                self.diagnostics["candidate21_target_consumed_before_entry"] = int(
                    self.diagnostics["candidate21_target_consumed_before_entry"],
                ) + 1
            else:
                self.diagnostics["candidate21_unresolved"] = int(
                    self.diagnostics["candidate21_unresolved"],
                ) + 1
            self.pending = None
            self.clock_auction = None
            return True

        if state.decision is ClockDecision.ACCEPTANCE:
            if not self.config.enable_acceptance:
                self.pending = None
                self.clock_auction = None
                return True
            branch = "ACCEPTANCE"
            side = state.direction
            natural_target = state.acceptance_target
            self.diagnostics["candidate21_acceptance_confirmed"] = int(
                self.diagnostics["candidate21_acceptance_confirmed"],
            ) + 1
        else:
            if not self.config.enable_rejection:
                self.pending = None
                self.clock_auction = None
                return True
            branch = "REJECTION"
            side = -state.direction
            natural_target = state.rejection_target
            self.diagnostics["candidate21_failed_auction_confirmed"] = int(
                self.diagnostics["candidate21_failed_auction_confirmed"],
            ) + 1

        completed = PendingSetup(
            scenario_id=setup.scenario_id,
            branch=branch,
            side=side,
            swept_kind=setup.swept_kind,
            pool_id=setup.pool_id,
            pool_level=setup.pool_level,
            created_index=self.bar_index,
            expires_index=self.bar_index,
            sweep_extreme=setup.sweep_extreme,
            structure=setup.structure,
            atr=setup.atr,
            hold_count=0,
            retrace_armed=False,
            details={
                **setup.details,
                "candidate21_branch": state.decision.value,
                "natural_target": natural_target,
                "natural_target_source": (
                    "PRIOR_BALANCE_MEASURED_EXPANSION"
                    if branch == "ACCEPTANCE"
                    else "PRIOR_BALANCE_OPPOSITE_EDGE"
                ),
                "terminal_clock_router_state": terminal,
            },
        )
        self.pending = completed
        self.clock_auction = None
        self._transition(
            completed.scenario_id,
            "CLOCK_AUCTION_COMPLETED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_EVALUATION",
            state.reason,
            float(row["close"]),
            completed.details,
        )
        self._submit_entry(completed, row)
        return True

    def _submit_entry(self, setup: PendingSetup, row: dict[str, float | int]) -> bool:
        """Submit a 3%-risk FOK bracket only when the causal target remains viable."""
        atr = self._atr()
        side = setup.side
        signal_close = float(row["close"])
        if setup.branch == "REJECTION":
            stop = setup.sweep_extreme - side * self.config.stop_buffer_atr * atr
        else:
            stop = setup.pool_level - side * self.config.stop_buffer_atr * atr

        structural_risk = abs(signal_close - stop)
        if not math.isfinite(structural_risk) or structural_risk <= 0.0:
            self._expire_pending(row, "INVALID_CLOCK_STOP_GEOMETRY")
            return False
        cap_distance = max(
            self.config.entry_rearm_atr * atr,
            self.config.entry_limit_risk_expansion * structural_risk,
        )
        entry_limit = signal_close + side * cap_distance
        natural_target = float(setup.details.get("natural_target", float("nan")))
        if not math.isfinite(natural_target) or natural_target <= 0.0:
            self._expire_pending(row, "MISSING_CLOCK_NATURAL_TARGET")
            return False

        if side > 0 and not (stop < signal_close < entry_limit < natural_target):
            self.diagnostics["candidate21_natural_target_geometry_rejected"] = int(
                self.diagnostics["candidate21_natural_target_geometry_rejected"],
            ) + 1
            self._expire_pending(row, "LONG_NATURAL_TARGET_ALREADY_CONSUMED")
            return False
        if side < 0 and not (natural_target < entry_limit < signal_close < stop):
            self.diagnostics["candidate21_natural_target_geometry_rejected"] = int(
                self.diagnostics["candidate21_natural_target_geometry_rejected"],
            ) + 1
            self._expire_pending(row, "SHORT_NATURAL_TARGET_ALREADY_CONSUMED")
            return False

        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        adverse_slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        planned_loss = planned_loss_per_unit(
            entry_limit,
            stop,
            side,
            cost_rate,
            adverse_slippage_rate,
        )
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self._expire_pending(row, "INVALID_CLOCK_PLANNED_LOSS")
            return False
        target_net_r = net_r_at_price(
            entry_limit,
            natural_target,
            side,
            planned_loss,
            cost_rate,
        )
        if target_net_r < self.config.min_target_net_r:
            self.diagnostics["candidate21_natural_target_geometry_rejected"] = int(
                self.diagnostics["candidate21_natural_target_geometry_rejected"],
            ) + 1
            self._expire_pending(row, "NATURAL_TARGET_BELOW_MINIMUM_NET_R")
            return False

        equity = self._equity_value()
        risk_budget = equity * self.config.risk_fraction
        raw_quantity = risk_budget / planned_loss
        quantity_value = floor_quantity(raw_quantity, int(self.instrument.size_precision))
        if quantity_value <= 0.0 or quantity_value * entry_limit < 10.0:
            self._expire_pending(row, "QUANTITY_BELOW_INSTRUMENT_MINIMUM")
            return False

        order_side = OrderSide.BUY if side > 0 else OrderSide.SELL
        order_list = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=order_side,
            quantity=self.instrument.make_qty(quantity_value),
            entry_order_type=OrderType.LIMIT,
            entry_price=self.instrument.make_price(entry_limit),
            time_in_force=TimeInForce.FOK,
            entry_post_only=False,
            entry_tags=["ENTRY", "CANDIDATE21_CLOCK_FOK_PRICE_CAP"],
            tp_price=self.instrument.make_price(natural_target),
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
        self.diagnostics["entry_submissions"] = int(
            self.diagnostics["entry_submissions"],
        ) + 1
        self.diagnostics["candidate21_fok_entries"] = int(
            self.diagnostics["candidate21_fok_entries"],
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
            "RESOLVED_CLOCK_AUCTION_WITH_ALL_OR_NONE_PRICE_CAP",
            entry_limit,
            {
                **setup.details,
                "branch": setup.branch,
                "side": side,
                "signal_close": signal_close,
                "entry_limit_worst_fill": entry_limit,
                "entry_time_in_force": "FOK",
                "entry_all_or_none": True,
                "stop": stop,
                "target": natural_target,
                "target_net_r": target_net_r,
                "quantity": quantity_value,
                "equity": equity,
                "risk_budget": risk_budget,
                "planned_loss_per_unit_at_worst_fill": planned_loss,
                "planned_account_loss_at_worst_fill": quantity_value * planned_loss,
            },
        )
        return True


__all__ = ["Candidate21Config", "Candidate21Strategy"]
