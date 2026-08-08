"""External spot-perpetual participation router.

Candidate 21 contributes only its NautilusTrader runner and actual aggTrade
execution clock.  The alpha distinguishes spot-led price discovery from
perpetual-led leverage crowding:

* spot-led discovery with expanding OI may continue after the first defended
  boundary retest;
* a perp-led break with widening premium and spot non-confirmation is only a
  crowding state.  It reverses after a strictly later premium contraction,
  balance re-entry, and flow reversal;
* every other interaction is unresolved/no trade.

State evidence, transition evidence, and entry evidence are separate completed
minutes.  Each traded branch defines entry, stop, and objective in the new leg.
"""
from __future__ import annotations

import math
from typing import Any

from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce

from fok_capped_strategy import Candidate18Config
from fok_capped_strategy import Candidate18Strategy as Candidate18FokStrategy
from logic import floor_quantity, net_r_at_price, planned_loss_per_unit
from spot_perp_router import (
    ParticipationRoute,
    classify_parent_participation,
    perp_crowding_failure_confirmed,
    spot_led_retest_confirmed,
)
from strategy_base import PendingSetup, _as_float


SPOT_LED_STATE = "SPOT_LED_ACCEPTANCE_STATE"
PERP_CROWDING_STATE = "PERP_LED_CROWDING_STATE"


class Candidate21Config(Candidate18Config, frozen=True):
    participation_balance_bars: int = 15
    participation_min_notional_burst: float = 1.25
    participation_min_directional_flow: float = 0.10
    participation_min_break_atr: float = 0.05
    participation_min_event_efficiency: float = 0.20
    participation_max_wait_bars: int = 6
    participation_retest_tolerance_atr: float = 0.10
    participation_acceptance_invalidation_atr: float = 0.08
    participation_entry_cap_bps: float = 5.0

    # Candidate 21's runner also creates phase features. They are not alpha
    # inputs here, but their construction contract remains configured.
    clock_period_minutes: int = 15
    clock_baseline_periods: int = 96
    clock_min_baseline_samples: int = 32


class Candidate21Strategy(Candidate18FokStrategy):
    """Route spot-led acceptance and failed perp crowding as distinct states."""

    def __init__(self, config: Candidate21Config) -> None:
        super().__init__(config=config)
        if config.participation_balance_bars < 5:
            raise ValueError("participation_balance_bars must be at least five")
        if config.participation_min_notional_burst < 1.0:
            raise ValueError("participation_min_notional_burst must be at least baseline")
        if not 0.0 < config.participation_min_directional_flow <= 1.0:
            raise ValueError("participation_min_directional_flow must be in (0, 1]")
        if config.participation_min_break_atr <= 0.0:
            raise ValueError("participation_min_break_atr must be positive")
        if config.participation_max_wait_bars < 1:
            raise ValueError("participation_max_wait_bars must be positive")
        if config.participation_entry_cap_bps <= 0.0:
            raise ValueError("participation_entry_cap_bps must be positive")
        self.diagnostics.update(
            {
                "spot_perp_parent_breaks": 0,
                "spot_perp_parent_oi_rejected": 0,
                "spot_perp_spot_led_states": 0,
                "spot_perp_perp_crowding_states": 0,
                "spot_perp_unresolved_states": 0,
                "spot_perp_later_observations": 0,
                "spot_perp_spot_led_retests": 0,
                "spot_perp_spot_led_invalidations": 0,
                "spot_perp_crowding_failures": 0,
                "spot_perp_crowding_accepted_without_trade": 0,
                "spot_perp_states_expired": 0,
                "spot_perp_target_consumed": 0,
                "spot_perp_geometry_rejected": 0,
                "spot_perp_fok_entries": 0,
            },
        )

    def _detect_sweep(
        self,
        row: dict[str, float | int],
        previous_close: float,
    ) -> None:
        del previous_close
        if self.pending is not None:
            return
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return
        lookback = self.config.participation_balance_bars
        rows = list(self.bars)
        if len(rows) < lookback + 1:
            return
        prior = rows[-(lookback + 1) : -1]
        prior_high = max(float(item["high"]) for item in prior)
        prior_low = min(float(item["low"]) for item in prior)
        prior_close = float(prior[-1]["close"])
        width = prior_high - prior_low
        if width <= 0.0 or not prior_low <= prior_close <= prior_high:
            return

        high_break = (
            float(row["high"])
            >= prior_high + self.config.participation_min_break_atr * atr
            and float(row["close"]) > prior_high
        )
        low_break = (
            float(row["low"])
            <= prior_low - self.config.participation_min_break_atr * atr
            and float(row["close"]) < prior_low
        )
        if high_break == low_break:
            return
        direction = 1 if high_break else -1
        boundary = prior_high if direction > 0 else prior_low
        opposite = prior_low if direction > 0 else prior_high
        event_extreme = float(row["high"]) if direction > 0 else float(row["low"])
        self.diagnostics["spot_perp_parent_breaks"] = int(
            self.diagnostics["spot_perp_parent_breaks"],
        ) + 1

        perp_flow = self._feature("flow_60s")
        burst = self._feature("notional_burst")
        efficiency = self._feature("efficiency_60s")
        oi_change = self._feature("oi_change_15m")
        metrics_age = self._feature("metrics_age_seconds")
        if (
            not math.isfinite(perp_flow)
            or direction * perp_flow < self.config.participation_min_directional_flow
            or not math.isfinite(burst)
            or burst < self.config.participation_min_notional_burst
            or not math.isfinite(efficiency)
            or efficiency < self.config.participation_min_event_efficiency
            or not math.isfinite(oi_change)
            or oi_change <= 0.0
            or not math.isfinite(metrics_age)
            or metrics_age > self.config.positioning_max_age_seconds
        ):
            self.diagnostics["spot_perp_parent_oi_rejected"] = int(
                self.diagnostics["spot_perp_parent_oi_rejected"],
            ) + 1
            return

        spot_close = self._feature("spot_close")
        spot_edge = self._feature(
            "spot_prior_15m_high" if direction > 0 else "spot_prior_15m_low",
        )
        spot_accepted = (
            spot_close > spot_edge if direction > 0 else spot_close < spot_edge
        ) if math.isfinite(spot_close) and math.isfinite(spot_edge) else False
        parent = classify_parent_participation(
            direction=direction,
            spot_accepted_edge=spot_accepted,
            perp_return_bps=self._feature("ret_60s_bps"),
            spot_return_bps=self._feature("spot_ret_1m_bps"),
            perp_flow=perp_flow,
            spot_flow=self._feature("spot_flow_60s"),
            basis_change_bps=self._feature("perp_spot_basis_change_1m_bps"),
        )
        if parent.route is ParticipationRoute.UNRESOLVED:
            self.diagnostics["spot_perp_unresolved_states"] = int(
                self.diagnostics["spot_perp_unresolved_states"],
            ) + 1
            return

        branch = (
            SPOT_LED_STATE
            if parent.route is ParticipationRoute.SPOT_LED_ACCEPTANCE
            else PERP_CROWDING_STATE
        )
        side = direction if branch == SPOT_LED_STATE else -direction
        self.scenario_counter += 1
        scenario_id = f"spot-perp-{self.scenario_counter:07d}"
        details = {
            "external_alpha": "SPOT_PERP_PARTICIPATION_ROUTER",
            "parent_route": parent.route.value,
            "parent_reason": parent.reason,
            "event_direction": direction,
            "prior_balance_high": prior_high,
            "prior_balance_low": prior_low,
            "prior_balance_width": width,
            "boundary": boundary,
            "opposite_edge": opposite,
            "event_extreme": event_extreme,
            "event_close": float(row["close"]),
            "event_ts": int(row["ts"]),
            "event_index": self.bar_index,
            "event_perp_flow": perp_flow,
            "event_spot_flow": self._feature("spot_flow_60s"),
            "event_perp_return_bps": self._feature("ret_60s_bps"),
            "event_spot_return_bps": self._feature("spot_ret_1m_bps"),
            "event_spot_accepted_edge": spot_accepted,
            "event_basis_bps": self._feature("perp_spot_basis_bps"),
            "event_basis_change_1m_bps": self._feature(
                "perp_spot_basis_change_1m_bps",
            ),
            "event_oi_change_15m": oi_change,
            "event_notional_burst": burst,
            "event_efficiency": efficiency,
            "state_evidence_role": "PARENT_CLASSIFICATION_ONLY",
            "later_evidence_role": "STATE_TRANSITION_ONLY",
        }
        self.pending = PendingSetup(
            scenario_id=scenario_id,
            branch=branch,
            side=side,
            swept_kind="HIGH" if direction > 0 else "LOW",
            pool_id=f"spot-perp-{scenario_id}",
            pool_level=boundary,
            created_index=self.bar_index,
            expires_index=self.bar_index + self.config.participation_max_wait_bars,
            sweep_extreme=event_extreme,
            structure=opposite,
            atr=atr,
            hold_count=0,
            retrace_armed=False,
            details=details,
        )
        key = (
            "spot_perp_spot_led_states"
            if branch == SPOT_LED_STATE
            else "spot_perp_perp_crowding_states"
        )
        self.diagnostics[key] = int(self.diagnostics[key]) + 1
        self._transition(
            scenario_id,
            "SPOT_PERP_PARENT_STATE_CLASSIFIED",
            int(row["ts"]),
            int(row["ts"]),
            "WAITING_FOR_DISTINCT_LATER_TRANSITION",
            parent.reason,
            boundary,
            details,
        )

    def _process_pending(self, row: dict[str, float | int]) -> bool:
        setup = self.pending
        if setup is None:
            return False
        if setup.branch not in {SPOT_LED_STATE, PERP_CROWDING_STATE}:
            return super()._process_pending(row)
        if self.bar_index <= setup.created_index:
            return True
        if self.bar_index > setup.expires_index:
            self.diagnostics["spot_perp_states_expired"] = int(
                self.diagnostics["spot_perp_states_expired"],
            ) + 1
            self._close_state(
                setup,
                row,
                "SPOT_PERP_STATE_EXPIRED",
                "NO_DISTINCT_LATER_TRANSITION",
            )
            return True

        self.diagnostics["spot_perp_later_observations"] = int(
            self.diagnostics["spot_perp_later_observations"],
        ) + 1
        direction = int(setup.details["event_direction"])
        if direction > 0:
            setup.sweep_extreme = max(setup.sweep_extreme, float(row["high"]))
        else:
            setup.sweep_extreme = min(setup.sweep_extreme, float(row["low"]))
        observation = {
            "bar_index": self.bar_index,
            "ts": int(row["ts"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "perp_flow": self._feature("flow_60s"),
            "spot_flow": self._feature("spot_flow_60s"),
            "spot_close": self._feature("spot_close"),
            "basis_bps": self._feature("perp_spot_basis_bps"),
            "basis_change_1m_bps": self._feature("perp_spot_basis_change_1m_bps"),
            "oi_change_15m": self._feature("oi_change_15m"),
        }
        setup.details["latest_later_observation"] = observation
        self._transition(
            setup.scenario_id,
            "SPOT_PERP_LATER_OBSERVATION",
            int(row["ts"]),
            int(row["ts"]),
            "WAITING_FOR_DISTINCT_LATER_TRANSITION",
            "STRICTLY_LATER_COMPLETED_SPOT_AND_PERP_OBSERVATION",
            float(row["close"]),
            {**setup.details, "current_observation": observation},
        )

        if setup.branch == SPOT_LED_STATE:
            return self._process_spot_led_state(setup, row, observation)
        return self._process_perp_crowding_state(setup, row, observation)

    def _process_spot_led_state(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
        observation: dict[str, float | int],
    ) -> bool:
        side = setup.side
        boundary = setup.pool_level
        atr = self._atr()
        invalidated = (
            float(row["close"])
            < boundary - self.config.participation_acceptance_invalidation_atr * atr
            if side > 0
            else float(row["close"])
            > boundary + self.config.participation_acceptance_invalidation_atr * atr
        )
        if invalidated:
            self.diagnostics["spot_perp_spot_led_invalidations"] = int(
                self.diagnostics["spot_perp_spot_led_invalidations"],
            ) + 1
            self._close_state(
                setup,
                row,
                "SPOT_LED_ACCEPTANCE_INVALIDATED",
                "PRICE_REENTERED_PRIOR_BALANCE_BEYOND_TOLERANCE",
            )
            return True

        confirmed = spot_led_retest_confirmed(
            side=side,
            boundary=boundary,
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            atr=atr,
            touch_tolerance_atr=self.config.participation_retest_tolerance_atr,
            spot_flow=float(observation["spot_flow"]),
            perp_flow=float(observation["perp_flow"]),
            basis_change_bps=float(observation["basis_change_1m_bps"]),
        )
        if not confirmed:
            return True
        self.diagnostics["spot_perp_spot_led_retests"] = int(
            self.diagnostics["spot_perp_spot_led_retests"],
        ) + 1
        stop = (
            float(row["low"]) - self.config.stop_buffer_atr * atr
            if side > 0
            else float(row["high"]) + self.config.stop_buffer_atr * atr
        )
        target = setup.pool_level + side * float(setup.details["prior_balance_width"])
        return self._submit_participation_entry(
            setup=setup,
            row=row,
            side=side,
            stop_raw=stop,
            target_raw=target,
            route="SPOT_LED_FIRST_DEFENDED_RETEST",
        )

    def _process_perp_crowding_state(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
        observation: dict[str, float | int],
    ) -> bool:
        event_direction = int(setup.details["event_direction"])
        spot_edge = self._feature(
            "spot_prior_15m_high" if event_direction > 0 else "spot_prior_15m_low",
        )
        spot_close = float(observation["spot_close"])
        spot_now_accepted = (
            spot_close > spot_edge if event_direction > 0 else spot_close < spot_edge
        ) if math.isfinite(spot_close) and math.isfinite(spot_edge) else False
        if (
            spot_now_accepted
            and event_direction * float(observation["perp_flow"]) > 0.0
            and event_direction * float(observation["basis_change_1m_bps"]) >= 0.0
        ):
            self.diagnostics["spot_perp_crowding_accepted_without_trade"] = int(
                self.diagnostics["spot_perp_crowding_accepted_without_trade"],
            ) + 1
            self._close_state(
                setup,
                row,
                "PERP_CROWDING_RESOLVED_AS_ACCEPTANCE_NO_TRADE",
                "SPOT_LATER_ACCEPTED_THE_BREAK_WITHOUT_A_DEFENDED_RETEST",
            )
            return True

        confirmed = perp_crowding_failure_confirmed(
            event_direction=event_direction,
            boundary=setup.pool_level,
            close=float(row["close"]),
            spot_flow=float(observation["spot_flow"]),
            perp_flow=float(observation["perp_flow"]),
            basis_change_bps=float(observation["basis_change_1m_bps"]),
        )
        if not confirmed:
            return True
        self.diagnostics["spot_perp_crowding_failures"] = int(
            self.diagnostics["spot_perp_crowding_failures"],
        ) + 1
        side = -event_direction
        atr = self._atr()
        stop = setup.sweep_extreme - side * self.config.stop_buffer_atr * atr
        target = float(setup.details["opposite_edge"])
        return self._submit_participation_entry(
            setup=setup,
            row=row,
            side=side,
            stop_raw=stop,
            target_raw=target,
            route="PERP_CROWDING_PREMIUM_CONTRACTION_AND_BALANCE_REENTRY",
        )

    def _close_state(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
        event_type: str,
        reason: str,
    ) -> None:
        self._transition(
            setup.scenario_id,
            event_type,
            int(row["ts"]),
            int(row["ts"]),
            "CLOSED",
            reason,
            float(row["close"]),
            setup.details,
        )
        self.pending = None

    def _submit_participation_entry(
        self,
        *,
        setup: PendingSetup,
        row: dict[str, float | int],
        side: int,
        stop_raw: float,
        target_raw: float,
        route: str,
    ) -> bool:
        signal_close = float(row["close"])
        stop_price = self.instrument.make_price(stop_raw)
        stop = _as_float(stop_price)
        cap_rate = max(
            self.config.participation_entry_cap_bps,
            self.config.adverse_slippage_bps_each_side,
        ) / 10_000.0
        entry_price = self.instrument.make_price(signal_close * (1.0 + side * cap_rate))
        entry = _as_float(entry_price)
        increment = _as_float(self.instrument.price_increment)
        if side > 0 and entry <= signal_close:
            entry_price = self.instrument.make_price(signal_close + increment)
            entry = _as_float(entry_price)
        elif side < 0 and entry >= signal_close:
            entry_price = self.instrument.make_price(signal_close - increment)
            entry = _as_float(entry_price)
        target_price = self.instrument.make_price(target_raw)
        target = _as_float(target_price)

        if (side > 0 and not stop < signal_close < entry < target) or (
            side < 0 and not target < entry < signal_close < stop
        ):
            self.diagnostics["spot_perp_target_consumed"] = int(
                self.diagnostics["spot_perp_target_consumed"],
            ) + 1
            self._close_state(
                setup,
                row,
                "SPOT_PERP_TARGET_CONSUMED_BEFORE_ENTRY",
                "ENTRY_STOP_TARGET_DO_NOT_BELONG_TO_ONE_REMAINING_TRADEABLE_LEG",
            )
            return True

        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        adverse = self.config.adverse_slippage_bps_each_side / 10_000.0
        planned_loss = planned_loss_per_unit(entry, stop, side, cost_rate, adverse)
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self.diagnostics["spot_perp_geometry_rejected"] = int(
                self.diagnostics["spot_perp_geometry_rejected"],
            ) + 1
            self._close_state(
                setup,
                row,
                "SPOT_PERP_ENTRY_GEOMETRY_REJECTED",
                "INVALID_WORST_FILL_PLANNED_LOSS",
            )
            return True
        target_r = net_r_at_price(entry, target, side, planned_loss, cost_rate)
        if target_r + 1e-9 < self.config.min_target_net_r:
            self.diagnostics["spot_perp_geometry_rejected"] = int(
                self.diagnostics["spot_perp_geometry_rejected"],
            ) + 1
            self._close_state(
                setup,
                row,
                "SPOT_PERP_ENTRY_GEOMETRY_REJECTED",
                "NATURAL_OBJECTIVE_BELOW_MINIMUM_NET_R",
            )
            return True

        equity = self._equity_value()
        risk_budget = equity * self.config.risk_fraction
        quantity_value = floor_quantity(
            risk_budget / planned_loss,
            int(self.instrument.size_precision),
        )
        if quantity_value <= 0.0 or quantity_value * entry < 10.0:
            self._close_state(
                setup,
                row,
                "SPOT_PERP_ENTRY_GEOMETRY_REJECTED",
                "QUANTITY_BELOW_INSTRUMENT_MINIMUM",
            )
            return True

        order_list = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=OrderSide.BUY if side > 0 else OrderSide.SELL,
            quantity=self.instrument.make_qty(quantity_value),
            entry_order_type=OrderType.LIMIT,
            entry_price=entry_price,
            time_in_force=TimeInForce.FOK,
            entry_post_only=False,
            entry_tags=["ENTRY", "SPOT_PERP_FOK_PRICE_CAP"],
            tp_price=target_price,
            sl_trigger_price=stop_price,
        )
        self.submit_order_list(order_list)
        self.entry_pending = True
        self.entry_pending_index = self.bar_index
        self.last_entry_index = self.bar_index
        self.current_scenario_id = setup.scenario_id
        self.current_branch = route
        self.current_pool_level = setup.pool_level
        self.pending = None
        self.diagnostics["entry_submissions"] = int(
            self.diagnostics["entry_submissions"],
        ) + 1
        self.diagnostics["spot_perp_fok_entries"] = int(
            self.diagnostics["spot_perp_fok_entries"],
        ) + 1
        self.diagnostics["max_simultaneous_entry_intents"] = max(
            int(self.diagnostics["max_simultaneous_entry_intents"]),
            1,
        )
        self._transition(
            setup.scenario_id,
            "SPOT_PERP_ENTRY_SUBMITTED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_PENDING",
            route,
            entry,
            {
                **setup.details,
                "entry_route": route,
                "side": side,
                "signal_close": signal_close,
                "entry_limit_worst_fill": entry,
                "entry_time_in_force": "FOK",
                "entry_all_or_none": True,
                "stop": stop,
                "target": target,
                "target_net_r": target_r,
                "quantity": quantity_value,
                "equity": equity,
                "risk_budget": risk_budget,
                "planned_loss_per_unit_at_worst_fill": planned_loss,
                "planned_account_loss_at_worst_fill": quantity_value * planned_loss,
                "strictly_later_entry_evidence": True,
            },
        )
        return True


__all__ = ["Candidate21Config", "Candidate21Strategy"]
