"""Cross-market external-liquidity exhaustion reversal.

The previous participation router correctly separated spot-led and perpetual-led
parents, but its strictly later retest/re-entry confirmation consumed the
remaining objective before an executable trade existed.  This replacement
preserves the causal spot/perpetual data and NautilusTrader execution path while
changing the economic mechanism:

1. a completed minute reaches a 60-minute perpetual external-liquidity edge;
2. both spot and perpetual move and trade aggressively in the same direction;
3. the move clears both a volatility-normalized and a transaction-cost-aware
   displacement hurdle, occurs on a volume climax, yet has low path efficiency;
4. this is an exhaustion *state*, not an entry;
5. a strictly later close breaks the preceding two-bar microstructure in the
   opposite direction while perpetual aggressor flow reverses;
6. entry, stop, and objective are all defined from that new reversal leg.

Ambiguous observations and shocks which retain directional efficiency remain
UNRESOLVED / NO TRADE.
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
    classify_parent_exhaustion,
    exhaustion_transition_confirmed,
)
from strategy_base import PendingSetup, _as_float


EXHAUSTION_STATE = "EXTERNAL_LIQUIDITY_EXHAUSTION_STATE"


class Candidate21Config(Candidate18Config, frozen=True):
    exhaustion_balance_bars: int = 60
    exhaustion_min_return_bps: float = 30.0
    exhaustion_min_displacement_atr: float = 4.0
    exhaustion_min_notional_burst: float = 5.0
    exhaustion_min_perp_flow: float = 0.30
    exhaustion_min_spot_flow: float = 0.20
    exhaustion_max_efficiency: float = 0.15
    exhaustion_max_wait_bars: int = 40
    exhaustion_transition_structure_bars: int = 2
    exhaustion_stop_buffer_atr: float = 0.10
    exhaustion_entry_cap_bps: float = 5.0
    exhaustion_entry_cap_atr: float = 1.0
    exhaustion_episode_cooldown_bars: int = 60

    # The inherited runner still creates quarter-hour phase features.  They are
    # not alpha inputs, but their causal construction contract remains active.
    clock_period_minutes: int = 15
    clock_baseline_periods: int = 96
    clock_min_baseline_samples: int = 32


class Candidate21Strategy(Candidate18FokStrategy):
    """Trade only failed cross-market flow climaxes at external liquidity."""

    def __init__(self, config: Candidate21Config) -> None:
        super().__init__(config=config)
        if config.exhaustion_balance_bars < 15:
            raise ValueError("exhaustion_balance_bars must be at least fifteen")
        if config.exhaustion_min_return_bps <= 0.0:
            raise ValueError("exhaustion_min_return_bps must be positive")
        if config.exhaustion_min_displacement_atr <= 0.0:
            raise ValueError("exhaustion_min_displacement_atr must be positive")
        if config.exhaustion_min_notional_burst < 1.0:
            raise ValueError("exhaustion_min_notional_burst must exceed baseline")
        if not 0.0 < config.exhaustion_min_perp_flow <= 1.0:
            raise ValueError("exhaustion_min_perp_flow must be in (0, 1]")
        if not 0.0 < config.exhaustion_min_spot_flow <= 1.0:
            raise ValueError("exhaustion_min_spot_flow must be in (0, 1]")
        if not 0.0 < config.exhaustion_max_efficiency < 1.0:
            raise ValueError("exhaustion_max_efficiency must be in (0, 1)")
        if config.exhaustion_max_wait_bars < 1:
            raise ValueError("exhaustion_max_wait_bars must be positive")
        if config.exhaustion_transition_structure_bars < 1:
            raise ValueError("exhaustion_transition_structure_bars must be positive")
        if config.exhaustion_entry_cap_bps <= 0.0:
            raise ValueError("exhaustion_entry_cap_bps must be positive")
        if config.exhaustion_entry_cap_atr <= 0.0:
            raise ValueError("exhaustion_entry_cap_atr must be positive")
        if config.exhaustion_episode_cooldown_bars < 1:
            raise ValueError("exhaustion_episode_cooldown_bars must be positive")

        self.last_exhaustion_parent_index = -10**9
        self.diagnostics.update(
            {
                "exhaustion_parent_candidates": 0,
                "exhaustion_external_edge_rejected": 0,
                "exhaustion_unresolved_states": 0,
                "exhaustion_states": 0,
                "exhaustion_later_observations": 0,
                "exhaustion_transitions": 0,
                "exhaustion_states_expired": 0,
                "exhaustion_target_consumed": 0,
                "exhaustion_geometry_rejected": 0,
                "exhaustion_fok_entries": 0,
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
        if (
            self.bar_index - self.last_exhaustion_parent_index
            < self.config.exhaustion_episode_cooldown_bars
        ):
            return

        atr = self._atr()
        close = float(row["close"])
        if not math.isfinite(atr) or atr <= 0.0 or close <= 0.0:
            return
        lookback = self.config.exhaustion_balance_bars
        rows = list(self.bars)
        if len(rows) < lookback + 1:
            return
        prior = rows[-(lookback + 1) : -1]
        prior_high = max(float(item["high"]) for item in prior)
        prior_low = min(float(item["low"]) for item in prior)
        width = prior_high - prior_low
        if width <= 0.0:
            return

        perp_return = self._feature("ret_60s_bps")
        if not math.isfinite(perp_return) or perp_return == 0.0:
            return
        direction = 1 if perp_return > 0.0 else -1
        perp_touched_edge = (
            float(row["high"]) >= prior_high
            if direction > 0
            else float(row["low"]) <= prior_low
        )
        if not perp_touched_edge:
            return
        self.diagnostics["exhaustion_parent_candidates"] = int(
            self.diagnostics["exhaustion_parent_candidates"],
        ) + 1

        spot_high = self._feature("spot_high")
        spot_low = self._feature("spot_low")
        spot_edge = self._feature(
            "spot_prior_15m_high" if direction > 0 else "spot_prior_15m_low",
        )
        spot_touched_edge = (
            spot_high >= spot_edge if direction > 0 else spot_low <= spot_edge
        ) if all(math.isfinite(value) for value in (spot_high, spot_low, spot_edge)) else False
        if not spot_touched_edge:
            self.diagnostics["exhaustion_external_edge_rejected"] = int(
                self.diagnostics["exhaustion_external_edge_rejected"],
            ) + 1

        parent = classify_parent_exhaustion(
            direction=direction,
            perp_return_bps=perp_return,
            atr_bps=atr / close * 10_000.0,
            perp_flow=self._feature("flow_60s"),
            spot_return_bps=self._feature("spot_ret_1m_bps"),
            spot_flow=self._feature("spot_flow_60s"),
            notional_burst=self._feature("notional_burst"),
            efficiency=self._feature("efficiency_60s"),
            perp_touched_external_edge=perp_touched_edge,
            spot_touched_external_edge=spot_touched_edge,
            min_return_bps=self.config.exhaustion_min_return_bps,
            min_displacement_atr=self.config.exhaustion_min_displacement_atr,
            min_perp_flow=self.config.exhaustion_min_perp_flow,
            min_spot_flow=self.config.exhaustion_min_spot_flow,
            min_notional_burst=self.config.exhaustion_min_notional_burst,
            max_efficiency=self.config.exhaustion_max_efficiency,
        )
        if parent.route is ParticipationRoute.UNRESOLVED:
            self.diagnostics["exhaustion_unresolved_states"] = int(
                self.diagnostics["exhaustion_unresolved_states"],
            ) + 1
            return

        self.last_exhaustion_parent_index = self.bar_index
        boundary = prior_high if direction > 0 else prior_low
        opposite = prior_low if direction > 0 else prior_high
        event_extreme = float(row["high"]) if direction > 0 else float(row["low"])
        self.scenario_counter += 1
        scenario_id = f"external-exhaustion-{self.scenario_counter:07d}"
        details = {
            "external_alpha": "CROSS_MARKET_EXTERNAL_LIQUIDITY_EXHAUSTION",
            "parent_route": parent.route.value,
            "parent_reason": parent.reason,
            "event_direction": direction,
            "prior_balance_high": prior_high,
            "prior_balance_low": prior_low,
            "prior_balance_width": width,
            "boundary": boundary,
            "opposite_edge": opposite,
            "event_extreme": event_extreme,
            "event_close": close,
            "event_ts": int(row["ts"]),
            "event_index": self.bar_index,
            "event_perp_flow": self._feature("flow_60s"),
            "event_spot_flow": self._feature("spot_flow_60s"),
            "event_perp_return_bps": perp_return,
            "event_spot_return_bps": self._feature("spot_ret_1m_bps"),
            "event_atr_bps": atr / close * 10_000.0,
            "event_notional_burst": self._feature("notional_burst"),
            "event_efficiency": self._feature("efficiency_60s"),
            "event_basis_bps": self._feature("perp_spot_basis_bps"),
            "event_basis_change_1m_bps": self._feature(
                "perp_spot_basis_change_1m_bps",
            ),
            "state_evidence_role": "PARENT_EXHAUSTION_CLASSIFICATION_ONLY",
            "later_evidence_role": "REVERSAL_LEG_TRANSITION_ONLY",
        }
        self.pending = PendingSetup(
            scenario_id=scenario_id,
            branch=EXHAUSTION_STATE,
            side=-direction,
            swept_kind="HIGH" if direction > 0 else "LOW",
            pool_id=f"external-exhaustion-{scenario_id}",
            pool_level=boundary,
            created_index=self.bar_index,
            expires_index=self.bar_index + self.config.exhaustion_max_wait_bars,
            sweep_extreme=event_extreme,
            structure=opposite,
            atr=atr,
            hold_count=0,
            retrace_armed=False,
            details=details,
        )
        self.diagnostics["exhaustion_states"] = int(
            self.diagnostics["exhaustion_states"],
        ) + 1
        self._transition(
            scenario_id,
            "EXTERNAL_LIQUIDITY_EXHAUSTION_CLASSIFIED",
            int(row["ts"]),
            int(row["ts"]),
            "WAITING_FOR_DISTINCT_REVERSAL_LEG",
            parent.reason,
            boundary,
            details,
        )

    def _process_pending(self, row: dict[str, float | int]) -> bool:
        setup = self.pending
        if setup is None:
            return False
        if setup.branch != EXHAUSTION_STATE:
            return super()._process_pending(row)
        if self.bar_index <= setup.created_index:
            return True
        if self.bar_index > setup.expires_index:
            self.diagnostics["exhaustion_states_expired"] = int(
                self.diagnostics["exhaustion_states_expired"],
            ) + 1
            self._close_exhaustion_state(
                setup,
                row,
                "EXHAUSTION_STATE_EXPIRED",
                "NO_DISTINCT_REVERSAL_LEG_WITHIN_CAUSAL_EPISODE",
            )
            return True

        self.diagnostics["exhaustion_later_observations"] = int(
            self.diagnostics["exhaustion_later_observations"],
        ) + 1
        direction = int(setup.details["event_direction"])
        if direction > 0:
            setup.sweep_extreme = max(setup.sweep_extreme, float(row["high"]))
        else:
            setup.sweep_extreme = min(setup.sweep_extreme, float(row["low"]))

        structure_bars = self.config.exhaustion_transition_structure_bars
        rows = list(self.bars)
        if len(rows) < structure_bars + 1:
            return True
        prior = rows[-(structure_bars + 1) : -1]
        prior_high = max(float(item["high"]) for item in prior)
        prior_low = min(float(item["low"]) for item in prior)
        perp_flow = self._feature("flow_60s")
        observation = {
            "bar_index": self.bar_index,
            "ts": int(row["ts"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "prior_structure_high": prior_high,
            "prior_structure_low": prior_low,
            "perp_flow": perp_flow,
            "spot_flow": self._feature("spot_flow_60s"),
            "basis_bps": self._feature("perp_spot_basis_bps"),
            "basis_change_1m_bps": self._feature("perp_spot_basis_change_1m_bps"),
        }
        setup.details["latest_later_observation"] = observation

        confirmed = exhaustion_transition_confirmed(
            event_direction=direction,
            close=float(row["close"]),
            prior_high=prior_high,
            prior_low=prior_low,
            perp_flow=perp_flow,
        )
        if not confirmed:
            return True

        self.diagnostics["exhaustion_transitions"] = int(
            self.diagnostics["exhaustion_transitions"],
        ) + 1
        side = -direction
        atr = self._atr()
        stop = setup.sweep_extreme - side * self.config.exhaustion_stop_buffer_atr * atr
        target = float(setup.details["opposite_edge"])
        self._transition(
            setup.scenario_id,
            "EXHAUSTION_REVERSAL_LEG_CONFIRMED",
            int(row["ts"]),
            int(row["ts"]),
            "REVERSAL_LEG_READY",
            "STRICTLY_LATER_MICROSTRUCTURE_BREAK_AND_PERP_FLOW_REVERSAL",
            float(row["close"]),
            {**setup.details, "transition_observation": observation},
        )
        return self._submit_exhaustion_entry(
            setup=setup,
            row=row,
            side=side,
            stop_raw=stop,
            target_raw=target,
        )

    def _close_exhaustion_state(
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

    def _submit_exhaustion_entry(
        self,
        *,
        setup: PendingSetup,
        row: dict[str, float | int],
        side: int,
        stop_raw: float,
        target_raw: float,
    ) -> bool:
        signal_close = float(row["close"])
        stop_price = self.instrument.make_price(stop_raw)
        stop = _as_float(stop_price)
        atr = self._atr()
        transition_range = max(0.0, float(row["high"]) - float(row["low"]))
        cap_distance = max(
            signal_close
            * max(
                self.config.exhaustion_entry_cap_bps,
                self.config.adverse_slippage_bps_each_side,
            )
            / 10_000.0,
            self.config.exhaustion_entry_cap_atr * atr,
            0.5 * transition_range,
        )
        entry_price = self.instrument.make_price(signal_close + side * cap_distance)
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
            self.diagnostics["exhaustion_target_consumed"] = int(
                self.diagnostics["exhaustion_target_consumed"],
            ) + 1
            self._close_exhaustion_state(
                setup,
                row,
                "EXHAUSTION_TARGET_CONSUMED_BEFORE_ENTRY",
                "ENTRY_STOP_TARGET_DO_NOT_BELONG_TO_ONE_REMAINING_REVERSAL_LEG",
            )
            return True

        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        adverse = self.config.adverse_slippage_bps_each_side / 10_000.0
        planned_loss = planned_loss_per_unit(entry, stop, side, cost_rate, adverse)
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self.diagnostics["exhaustion_geometry_rejected"] = int(
                self.diagnostics["exhaustion_geometry_rejected"],
            ) + 1
            self._close_exhaustion_state(
                setup,
                row,
                "EXHAUSTION_ENTRY_GEOMETRY_REJECTED",
                "INVALID_WORST_FILL_PLANNED_LOSS",
            )
            return True
        target_r = net_r_at_price(entry, target, side, planned_loss, cost_rate)
        if target_r + 1e-9 < self.config.min_target_net_r:
            self.diagnostics["exhaustion_geometry_rejected"] = int(
                self.diagnostics["exhaustion_geometry_rejected"],
            ) + 1
            self._close_exhaustion_state(
                setup,
                row,
                "EXHAUSTION_ENTRY_GEOMETRY_REJECTED",
                "NATURAL_EXTERNAL_LIQUIDITY_OBJECTIVE_BELOW_MINIMUM_NET_R",
            )
            return True

        equity = self._equity_value()
        risk_budget = equity * self.config.risk_fraction
        quantity_value = floor_quantity(
            risk_budget / planned_loss,
            int(self.instrument.size_precision),
        )
        if quantity_value <= 0.0 or quantity_value * entry < 10.0:
            self.diagnostics["exhaustion_geometry_rejected"] = int(
                self.diagnostics["exhaustion_geometry_rejected"],
            ) + 1
            self._close_exhaustion_state(
                setup,
                row,
                "EXHAUSTION_ENTRY_GEOMETRY_REJECTED",
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
            entry_tags=["ENTRY", "EXTERNAL_EXHAUSTION_FOK_PRICE_CAP"],
            tp_price=target_price,
            sl_trigger_price=stop_price,
        )
        self.submit_order_list(order_list)
        self.entry_pending = True
        self.entry_pending_index = self.bar_index
        self.last_entry_index = self.bar_index
        self.current_scenario_id = setup.scenario_id
        self.current_branch = "EXTERNAL_LIQUIDITY_EXHAUSTION_REVERSAL"
        self.current_pool_level = setup.pool_level
        self.pending = None
        self.diagnostics["entry_submissions"] = int(
            self.diagnostics["entry_submissions"],
        ) + 1
        self.diagnostics["exhaustion_fok_entries"] = int(
            self.diagnostics["exhaustion_fok_entries"],
        ) + 1
        self.diagnostics["max_simultaneous_entry_intents"] = max(
            int(self.diagnostics["max_simultaneous_entry_intents"]),
            1,
        )
        self._transition(
            setup.scenario_id,
            "EXHAUSTION_ENTRY_SUBMITTED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_PENDING",
            "EXTERNAL_LIQUIDITY_EXHAUSTION_REVERSAL",
            entry,
            {
                **setup.details,
                "side": side,
                "signal_close": signal_close,
                "entry_limit_worst_fill": entry,
                "entry_cap_distance": cap_distance,
                "entry_cap_atr_multiple": self.config.exhaustion_entry_cap_atr,
                "entry_transition_range": transition_range,
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
