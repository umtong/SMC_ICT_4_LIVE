"""Candidate 25 v2: state-first funding-window reset continuation.

Two development failures are corrected at the market-logic level.

First, a periodic flow event is not interpreted without the persistent book
state that precedes it.  The minute immediately before the 07:45/15:45/23:45
seed is classified causally against rolling prior-only terciles of displayed
liquidity withdrawal and absolute imbalance.  Only the calm state (neither
descriptor severe) admits the seed.  This makes order flow an overlay on a
pre-existing liquidity state rather than a universal signal.

Second, the first version entered a medium-horizon continuation but placed its
hard stop immediately beyond the thirty-minute reset extreme.  Normal noise
then invalidated a seven-and-a-half-hour hypothesis within minutes.  V2 treats
the complete reset range as one auction leg: invalidation requires one further
full reset-range extension beyond its extreme.  Entry, hard invalidation and
pre-next-funding time exit now belong to the same causal scenario.

No PnL, fill, account or portfolio logic is implemented here.  NautilusTrader,
Candidate 18's FOK bracket, inherited fees/slippage, and continuous NAV remain
in force.
"""
from __future__ import annotations

from collections import deque
import math
from typing import Any

import numpy as np
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import OrderType
from nautilus_trader.model.enums import TimeInForce

from funding_window_router import is_funding_window_seed_time
from funding_window_router import reset_confirmed
from isolated_funding_window_strategy import Candidate25Config as _Candidate25Config
from isolated_funding_window_strategy import Candidate25Strategy as _Candidate25Strategy
from logic import choose_liquidity_target
from logic import floor_quantity
from logic import planned_loss_per_unit
from strategy_base import PendingSetup


class Candidate25Config(_Candidate25Config, frozen=True):
    liquidity_state_history_bars: int = 2880
    liquidity_state_min_history_bars: int = 1440
    liquidity_state_quantile: float = 2.0 / 3.0


class Candidate25Strategy(_Candidate25Strategy):
    """Admit QH flow only from a calm prior book and use reset-leg invalidation."""

    def __init__(self, config: Candidate25Config) -> None:
        super().__init__(config=config)
        if config.liquidity_state_history_bars < 1440:
            raise ValueError("liquidity_state_history_bars must cover at least one day")
        if not 0 < config.liquidity_state_min_history_bars < config.liquidity_state_history_bars:
            raise ValueError("invalid liquidity state minimum history")
        if not 0.5 < config.liquidity_state_quantile < 1.0:
            raise ValueError("liquidity_state_quantile must be in (0.5, 1)")

        self._state_withdrawal_history: deque[float] = deque(
            maxlen=config.liquidity_state_history_bars,
        )
        self._state_abs_imbalance_history: deque[float] = deque(
            maxlen=config.liquidity_state_history_bars,
        )
        self.diagnostics.update(
            {
                "candidate25_state_history_unavailable": 0,
                "candidate25_prior_state_calm": 0,
                "candidate25_prior_state_noncalm": 0,
                "candidate25_reset_leg_range_stops": 0,
                "candidate25_invalid_reset_geometry": 0,
            },
        )

    def on_bar(self, bar) -> None:
        # During super().on_bar the histories contain observations only through
        # the previous completed minute.  Seed admission therefore cannot use
        # the seed minute's own book response as its context.
        super().on_bar(bar)
        self._append_current_liquidity_state_observation()

    def _append_current_liquidity_state_observation(self) -> None:
        try:
            bid_change = self._feature("bid_depth_change_1_5m")
            ask_change = self._feature("ask_depth_change_1_5m")
            imbalance = self._feature("depth_imbalance_1")
        except (KeyError, RuntimeError, TypeError, ValueError):
            return
        withdrawal = -(bid_change + ask_change) / 2.0
        abs_imbalance = abs(imbalance)
        if not math.isfinite(withdrawal) or not math.isfinite(abs_imbalance):
            return
        self._state_withdrawal_history.append(withdrawal)
        self._state_abs_imbalance_history.append(abs_imbalance)

    def _prior_liquidity_state(self) -> tuple[str, dict[str, float | int]]:
        minimum = self.config.liquidity_state_min_history_bars
        if (
            len(self._state_withdrawal_history) <= minimum
            or len(self._state_abs_imbalance_history) <= minimum
        ):
            return "UNAVAILABLE", {
                "history_bars": min(
                    len(self._state_withdrawal_history),
                    len(self._state_abs_imbalance_history),
                ),
                "minimum_history_bars": minimum,
            }

        withdrawal_values = np.asarray(
            list(self._state_withdrawal_history),
            dtype=float,
        )
        imbalance_values = np.asarray(
            list(self._state_abs_imbalance_history),
            dtype=float,
        )
        # The final element is the strictly previous minute.  Thresholds use
        # only observations preceding that descriptor, so even the state
        # discretization is prior-only.
        prior_withdrawal = float(withdrawal_values[-1])
        prior_abs_imbalance = float(imbalance_values[-1])
        reference_withdrawal = withdrawal_values[:-1]
        reference_imbalance = imbalance_values[:-1]
        quantile = self.config.liquidity_state_quantile
        withdrawal_cut = float(np.quantile(reference_withdrawal, quantile))
        imbalance_cut = float(np.quantile(reference_imbalance, quantile))
        severe_count = int(prior_withdrawal > withdrawal_cut) + int(
            prior_abs_imbalance > imbalance_cut,
        )
        state = "CALM" if severe_count == 0 else "MIXED" if severe_count == 1 else "STRESSED"
        return state, {
            "history_bars": int(reference_withdrawal.size),
            "quantile": quantile,
            "prior_withdrawal": prior_withdrawal,
            "prior_abs_imbalance": prior_abs_imbalance,
            "withdrawal_cut": withdrawal_cut,
            "abs_imbalance_cut": imbalance_cut,
            "severe_descriptor_count": severe_count,
        }

    def _maybe_arm_funding_seed(self, row: dict[str, float | int]) -> None:
        ts = int(row["ts"])
        hour, minute = self._utc_clock(ts)
        if not is_funding_window_seed_time(hour=hour, minute=minute):
            return
        state, state_details = self._prior_liquidity_state()
        if state == "UNAVAILABLE":
            self.diagnostics["candidate25_state_history_unavailable"] = int(
                self.diagnostics["candidate25_state_history_unavailable"],
            ) + 1
            return
        if state != "CALM":
            self.diagnostics["candidate25_prior_state_noncalm"] = int(
                self.diagnostics["candidate25_prior_state_noncalm"],
            ) + 1
            return
        self.diagnostics["candidate25_prior_state_calm"] = int(
            self.diagnostics["candidate25_prior_state_calm"],
        ) + 1
        before = self.funding_seed_counter
        super()._maybe_arm_funding_seed(row)
        if self.funding_seed_counter > before and self.funding_seed is not None:
            self.funding_seed_state_details = state_details
            self._transition(
                self.funding_seed.scenario_id,
                "FUNDING_WINDOW_PRIOR_LIQUIDITY_STATE",
                ts,
                ts,
                "THIRTY_MINUTE_RESET_PENDING",
                "PRIOR_MINUTE_BOOK_STATE_CALM",
                float(row["close"]),
                {
                    **self._seed_details(self.funding_seed, row),
                    "prior_liquidity_state": state,
                    **state_details,
                },
            )

    def _advance_funding_seed(self, row: dict[str, float | int]) -> None:
        seed = self.funding_seed
        if seed is None or self.bar_index <= seed.created_index:
            return

        seed.reset_low = min(seed.reset_low, float(row["low"]))
        seed.reset_high = max(seed.reset_high, float(row["high"]))
        age = self.bar_index - seed.created_index
        if age < self.config.quarter_hour_reset_bars:
            return
        if age > self.config.quarter_hour_reset_bars:
            self._close_seed(row, "THIRTY_BAR_RESET_DECISION_WAS_MISSED")
            return

        self.diagnostics["candidate25_resets_observed"] = int(
            self.diagnostics["candidate25_resets_observed"],
        ) + 1
        if not reset_confirmed(
            side=seed.side,
            seed_close=seed.seed_close,
            reset_close=float(row["close"]),
        ):
            self.diagnostics["candidate25_resets_rejected"] = int(
                self.diagnostics["candidate25_resets_rejected"],
            ) + 1
            self._transition(
                seed.scenario_id,
                "FUNDING_WINDOW_RESET_REJECTED",
                int(row["ts"]),
                int(row["ts"]),
                "CLOSED",
                "FIRST_THIRTY_MINUTES_DID_NOT_MOVE_AGAINST_SEED_IMBALANCE",
                float(row["close"]),
                self._seed_details(seed, row),
            )
            self.funding_seed = None
            return

        busy = (
            self.pending is not None
            or self.entry_pending
            or not self.portfolio.is_flat(self.config.instrument_id)
            or self.bar_index - self.last_entry_index < self.config.cooldown_bars
        )
        if busy or not self._in_evaluation(int(row["ts"])) or self._funding_blackout(
            int(row["ts"]),
        ):
            self.diagnostics["candidate25_resets_skipped_busy"] = int(
                self.diagnostics["candidate25_resets_skipped_busy"],
            ) + 1
            self._transition(
                seed.scenario_id,
                "FUNDING_WINDOW_RESET_CLOSED",
                int(row["ts"]),
                int(row["ts"]),
                "CLOSED",
                "RESET_CONFIRMED_BUT_GLOBAL_ACCOUNT_OR_TIME_WINDOW_NOT_AVAILABLE",
                float(row["close"]),
                self._seed_details(seed, row),
            )
            self.funding_seed = None
            return

        counter_extreme = seed.reset_low if seed.side > 0 else seed.reset_high
        state_details = getattr(self, "funding_seed_state_details", {})
        setup = PendingSetup(
            scenario_id=seed.scenario_id,
            branch="ACCEPTANCE",
            side=seed.side,
            swept_kind="LOW" if seed.side > 0 else "HIGH",
            pool_id=f"funding-window-{seed.seed_ts}",
            pool_level=counter_extreme,
            created_index=self.bar_index,
            expires_index=self.bar_index,
            sweep_extreme=counter_extreme,
            structure=seed.seed_close,
            atr=self._atr(),
            hold_count=0,
            retrace_armed=True,
            details={
                **self._seed_details(seed, row),
                "candidate25_branch": "STATE_FIRST_POST_FUNDING_RESET_CONTINUATION",
                "prior_liquidity_state": "CALM",
                **state_details,
                "countermove_extreme": counter_extreme,
                "entry_clock": "THIRTY_COMPLETED_BARS_AFTER_SEED",
                "time_exit": "INHERITED_PRE_NEXT_FUNDING_FLAT",
            },
        )
        self.pending = setup
        self.diagnostics["candidate25_resets_confirmed"] = int(
            self.diagnostics["candidate25_resets_confirmed"],
        ) + 1
        self._transition(
            setup.scenario_id,
            "FUNDING_WINDOW_RESET_CONFIRMED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_EVALUATION",
            "CALM_STATE_RESET_CREATED_MEDIUM_HORIZON_CONTINUATION_GEOMETRY",
            float(row["close"]),
            setup.details,
        )
        submitted = self._submit_entry(setup, row)
        if submitted:
            self.diagnostics["candidate25_fok_entries"] = int(
                self.diagnostics["candidate25_fok_entries"],
            ) + 1
        self.funding_seed = None
        self.funding_seed_state_details = {}

    def _submit_entry(self, setup: PendingSetup, row: dict[str, float | int]) -> bool:
        atr = self._atr()
        side = setup.side
        signal_close = float(row["close"])
        reset_low = float(setup.details["reset_low"])
        reset_high = float(setup.details["reset_high"])
        reset_range = reset_high - reset_low
        if not math.isfinite(reset_range) or reset_range <= 0.0:
            self.diagnostics["candidate25_invalid_reset_geometry"] = int(
                self.diagnostics["candidate25_invalid_reset_geometry"],
            ) + 1
            self._expire_pending(row, "INVALID_RESET_RANGE")
            return False

        # A second complete reset-range extension, not the first retest extreme,
        # invalidates the medium-horizon continuation hypothesis.
        if side > 0:
            stop = reset_low - reset_range - self.config.stop_buffer_atr * atr
        else:
            stop = reset_high + reset_range + self.config.stop_buffer_atr * atr
        structural_risk = abs(signal_close - stop)
        if not math.isfinite(structural_risk) or structural_risk <= 0.0:
            self.diagnostics["candidate25_invalid_reset_geometry"] = int(
                self.diagnostics["candidate25_invalid_reset_geometry"],
            ) + 1
            self._expire_pending(row, "INVALID_RESET_LEG_STOP_GEOMETRY")
            return False

        cap_distance = max(
            self.config.entry_rearm_atr * atr,
            self.config.entry_limit_risk_expansion * structural_risk,
        )
        entry_limit = signal_close + side * cap_distance
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
            self._expire_pending(row, "INVALID_STATE_FIRST_FOK_GEOMETRY")
            return False
        equity = self._equity_value()
        risk_budget = equity * self.config.risk_fraction
        raw_quantity = risk_budget / planned_loss
        quantity_value = floor_quantity(raw_quantity, int(self.instrument.size_precision))
        if quantity_value <= 0.0 or quantity_value * entry_limit < 10.0:
            self._expire_pending(row, "QUANTITY_BELOW_INSTRUMENT_MINIMUM")
            return False

        target, target_source, target_r = choose_liquidity_target(
            entry=entry_limit,
            side=side,
            pools=list(self.active_pools.values()),
            planned_loss=planned_loss,
            cost_rate=cost_rate,
            min_net_r=self.config.min_target_net_r,
            max_net_r=self.config.max_target_net_r,
            fallback_net_r=self.config.acceptance_target_net_r,
        )
        if side > 0 and not (stop < signal_close < entry_limit < target):
            self._expire_pending(row, "INVALID_LONG_STATE_FIRST_FOK_BRACKET")
            return False
        if side < 0 and not (target < entry_limit < signal_close < stop):
            self._expire_pending(row, "INVALID_SHORT_STATE_FIRST_FOK_BRACKET")
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
            entry_tags=["ENTRY", "CANDIDATE25_STATE_FIRST_FOK"],
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
        self.diagnostics["entry_submissions"] = int(
            self.diagnostics["entry_submissions"],
        ) + 1
        self.diagnostics["candidate18_fok_limit_entries"] = int(
            self.diagnostics["candidate18_fok_limit_entries"],
        ) + 1
        self.diagnostics["candidate25_reset_leg_range_stops"] = int(
            self.diagnostics["candidate25_reset_leg_range_stops"],
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
            "STATE_FIRST_RESET_LEG_WITH_ALL_OR_NONE_PRICE_CAP",
            entry_limit,
            {
                **setup.details,
                "candidate25_version": "v2-state-first-reset-leg",
                "branch": setup.branch,
                "side": side,
                "signal_close": signal_close,
                "entry_limit_worst_fill": entry_limit,
                "entry_time_in_force": "FOK",
                "entry_all_or_none": True,
                "stop": stop,
                "reset_range": reset_range,
                "hard_invalidation": "SECOND_FULL_RESET_RANGE_EXTENSION",
                "target": target,
                "target_source": target_source,
                "target_net_r": target_r,
                "quantity": quantity_value,
                "equity": equity,
                "risk_budget": risk_budget,
                "planned_loss_per_unit_at_worst_fill": planned_loss,
                "planned_account_loss_at_worst_fill": quantity_value * planned_loss,
            },
        )
        return True


__all__ = ["Candidate25Config", "Candidate25Strategy"]
